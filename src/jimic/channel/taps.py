"""What a tap does, without Telegram: find the approval, respect its expiry, claim what it
touches, call the handler that was injected for its `kind`, save the new state and say what to
show.

A tap **claims before the effect** (task 1.11, pending of task 1.4): the items it touches are
saved as `running` (compare and swap on the payload, before the first `await`), and only then
does the handler run. A second tap, at once or later, finds them running or resolved and does
nothing, so an effect never runs twice; if the process dies in between, the item is never
retried on its own (it expires as unconfirmed).

A handler returns what happened as a short text (`✓ creada`), or `Failed` with a code (the item
stays pending with its note, and Pedro can try again), or `Renew` with the item as it is now (the
task changed: ask again), or `Declined` with a short text (looked at and not done, for good: the
item is resolved as a no). If it raises, it counts as `Failed`. Nothing in the log is content:
only codes, ids and counts.
"""

import logging
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from jimic.channel import store
from jimic.channel.message import (
    InteractiveMessage,
    Item,
    Outcome,
    Tap,
    View,
    claim,
    clip_result,
    expire,
    final_status,
    release,
    render,
    resolve,
    targets,
)
from jimic.clock import Clock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Failed:
    """The effect did not happen; `code` says why (`timeout`, `http_503`...), never content."""

    code: str


@dataclass(frozen=True)
class Renew:
    """The effect did not happen because the thing changed: show `item` (pending) and ask again."""

    item: Item


@dataclass(frozen=True)
class Declined:
    """The effect was looked at and not done, for good (task 1.12: [Guardar] on a file that
    changed): the item is resolved with `result`, as with its first option that is not a yes,
    so the approval ends `rejected`, not `approved`."""

    result: str


@dataclass(frozen=True)
class Show:
    """A text to send after the tap (task 1.12: a section of `/memoria`, or the notice of a task
    recreated by `/deshacer`). With `result` the item is resolved with it; without, it goes back
    to pending, so its button can be pressed again."""

    text: str
    result: str | None = None


type HandlerOutcome = str | Failed | Renew | Declined | Show
type TapHandler = Callable[[Item, str, int], Awaitable[HandlerOutcome]]
"""Called with the item, the key of the option chosen and the id of its approval."""


@dataclass(frozen=True)
class TapResult:
    """`view` is what to edit the message to; `None` means leave the message as it is."""

    code: str
    view: View | None = None
    send: tuple[str, ...] = ()
    """Texts to send after editing the message (`Show`), in the order of the items."""


def failure_note(code: str) -> str:
    return clip_result(f"falló ({code})")


async def _run(handler: TapHandler, item: Item, option: str, approval_id: int) -> HandlerOutcome:
    try:
        return await handler(item, option, approval_id)
    except Exception as error:
        logger.warning("tap handler failed (%s)", type(error).__name__)
        return Failed(f"unexpected:{type(error).__name__}")


def _declined_option(item: Item) -> str | None:
    return next((option.key for option in item.options if not option.approves), None)


def _apply(message: InteractiveMessage, outcomes: dict[int, tuple[str, HandlerOutcome]]):
    done: dict[int, Outcome] = {}
    for index, (option, outcome) in outcomes.items():
        if isinstance(outcome, str):
            done[index] = Outcome(option, outcome)
        elif isinstance(outcome, Show) and outcome.result is not None:
            done[index] = Outcome(option, outcome.result)
        elif isinstance(outcome, Declined):
            declined = _declined_option(message.items[index])
            if declined is not None:
                done[index] = Outcome(declined, outcome.result)
    updated = resolve(message, done)
    for index, (_, outcome) in outcomes.items():
        if isinstance(outcome, Show) and index not in done:
            updated = release(updated, index, updated.items[index])
        elif isinstance(outcome, Declined) and index not in done:
            # Nothing to resolve it with that is not a yes: it stays pending with the note.
            item = updated.items[index].model_copy(update={"note": clip_result(outcome.result)})
            updated = release(updated, index, item)
        elif isinstance(outcome, Failed):
            item = updated.items[index].model_copy(update={"note": failure_note(outcome.code)})
            updated = release(updated, index, item)
        elif isinstance(outcome, Renew):
            updated = release(updated, index, outcome.item)
    return updated, len(done)


async def process_tap(
    conn: sqlite3.Connection, clock: Clock, handlers: Mapping[str, TapHandler], tap: Tap
) -> TapResult:
    """Apply `tap` and return what to show. Never raises for a tap that cannot be applied."""
    try:
        approval = store.load(conn, tap.approval_id)
    except ValueError:
        logger.warning("approval %d has an invalid payload", tap.approval_id)
        return TapResult("invalid_payload")
    if approval is None:
        return TapResult("unknown_approval")
    now = clock.now()
    message = approval.message
    if approval.status == "expired":
        return TapResult("expired", render(message, approval.id))
    if approval.status == "pending" and now >= approval.expires_at:
        return _expire(conn, approval.id, message, now)
    if approval.status != "pending":
        return TapResult("already_resolved")
    chosen = targets(message, tap)
    if not chosen:
        if tap.item is not None and 1 <= tap.item <= len(message.items):
            if message.items[tap.item - 1].running:
                return TapResult("in_progress")
        return TapResult("nothing_to_do")
    handler = handlers.get(approval.kind)
    if handler is None:
        logger.warning("no handler for the kind of approval %d", approval.id)
        return TapResult("no_handler")
    # Claim: from here on no other tap touches these items. Nothing is awaited before this save.
    claimed = claim(message, {index for index, _ in chosen})
    if not store.save(conn, approval.id, claimed, None, now, expected=approval.payload):
        return TapResult("in_progress")
    outcomes: dict[int, tuple[str, HandlerOutcome]] = {}
    for index, option in chosen:
        outcomes[index] = (option, await _run(handler, message.items[index], option, approval.id))
    # Others may have changed the message meanwhile (another item, the sweep): apply on the
    # latest state, with no `await` between reading it and saving it.
    latest = store.load(conn, approval.id)
    if latest is None or latest.status != "pending":
        logger.warning("approval %d closed while its effect ran", approval.id)
        return TapResult("not_pending")
    updated, done = _apply(latest.message, outcomes)
    now = clock.now()
    if not store.save(conn, approval.id, updated, final_status(updated), now):
        return TapResult("not_pending")
    logger.info("approval %d: %d of %d item(s) done", approval.id, done, len(chosen))
    texts = tuple(
        outcome.text for _, (_, outcome) in sorted(outcomes.items()) if isinstance(outcome, Show)
    )
    if any(
        isinstance(outcome, Show) and outcome.result is None for _, outcome in outcomes.values()
    ):
        code = "shown"
    elif done == len(chosen):
        code = "resolved"
    elif any(isinstance(outcome, Failed) for _, outcome in outcomes.values()):
        code = "partially_resolved" if done else "handler_failed"
    else:
        code = "renewed"
    return TapResult(code, render(updated, approval.id), texts)


def _expire(
    conn: sqlite3.Connection, approval_id: int, message: InteractiveMessage, now: datetime
) -> TapResult:
    expired = expire(message)
    if not store.save(conn, approval_id, expired, "expired", now):
        return TapResult("not_pending")
    logger.info("approval %d expired", approval_id)
    return TapResult("expired", render(expired, approval_id))


@dataclass(frozen=True)
class Swept:
    """An approval the sweep closed: its message, if it has one, and what to edit it to."""

    approval_id: int
    telegram_message_id: int | None
    view: View | None


def sweep(conn: sqlite3.Connection, clock: Clock) -> list[Swept]:
    """Close as expired every pending approval whose time ran out (1.11-c, pending of task 1.4):
    a message gets its expired state and loses its keyboard; a row without a message (the
    proposals of task 1.10, the memory changes) or with a payload that is not a message is only
    marked. Says what to edit; it never touches Telegram."""
    now = clock.now()
    swept: list[Swept] = []
    for approval_id, message_id in store.due_to_expire(conn, now):
        view = None
        if message_id is not None:
            try:
                approval = store.load(conn, approval_id)
            except ValueError:
                logger.warning("approval %d has an invalid payload", approval_id)
                approval = None
            if approval is not None:
                expired = expire(approval.message)
                if store.save(conn, approval_id, expired, "expired", now):
                    swept.append(Swept(approval_id, message_id, render(expired, approval_id)))
                continue
        if store.mark_expired(conn, approval_id, now):
            swept.append(Swept(approval_id, message_id, view))
    if swept:
        logger.info("sweep: %d approval(s) expired", len(swept))
    return swept
