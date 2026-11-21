"""What a tap does, without Telegram: find the approval, respect its expiry, call the handler
that was injected for its `kind`, save the new state and say what to show.

The real work (create, move, delete) belongs to the handlers that tasks 1.11 and 1.14 inject;
here a handler only returns the text that says what happened. Nothing in the log is content:
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
    expire,
    final_status,
    render,
    resolve,
    targets,
)
from jimic.clock import Clock

logger = logging.getLogger(__name__)

type TapHandler = Callable[[Item, str], Awaitable[str]]
"""Called with the item and the key of the option chosen; returns what happened, as a short text
(`✓ creada`). If it raises, the item stays pending."""


@dataclass(frozen=True)
class TapResult:
    """`view` is what to edit the message to; `None` means leave the message as it is."""

    code: str
    view: View | None = None


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
        return TapResult("nothing_to_do")
    handler = handlers.get(approval.kind)
    if handler is None:
        logger.warning("no handler for the kind of approval %d", approval.id)
        return TapResult("no_handler")
    outcomes: dict[int, Outcome] = {}
    for index, option in chosen:
        try:
            result = await handler(message.items[index], option)
        except Exception as error:
            logger.warning("tap handler failed (%s)", type(error).__name__)
            continue
        outcomes[index] = Outcome(option, result)
    if not outcomes:
        return TapResult("handler_failed")
    updated = resolve(message, outcomes)
    if not store.save(conn, approval.id, updated, final_status(updated), now):
        return TapResult("not_pending")
    logger.info("approval %d: %d item(s) resolved", approval.id, len(outcomes))
    code = "resolved" if len(outcomes) == len(chosen) else "partially_resolved"
    return TapResult(code, render(updated, approval.id))


def _expire(
    conn: sqlite3.Connection, approval_id: int, message: InteractiveMessage, now: datetime
) -> TapResult:
    expired = expire(message)
    if not store.save(conn, approval_id, expired, "expired", now):
        return TapResult("not_pending")
    logger.info("approval %d expired", approval_id)
    return TapResult("expired", render(expired, approval_id))
