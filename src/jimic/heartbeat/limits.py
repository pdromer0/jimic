"""The hard limits of the heartbeat, in two passes (1.3 and 6.3 of the design).

The **first pass** runs before the model and ends the cycle without calling it: quiet hours, an
event in progress, or no room left even for the day close. The **second pass** runs on what the
decision proposes and guarantees the limits whatever the model says: the cap per intention, no
repeating, the slot kept for the day close (decision 1.7-a), grouping (decision 1.7-b) and, for a
key task (task 1.15), done, no longer today's, «hoy no», paused or before it may insist. What the
model picks must also be due now (`not_due`, task 1.15-b); the templates of the plan B and what
waited in the queue are not checked against it.

Every verdict carries a reason that is a code. Logs say codes and intention ids, never the text
of a message.
"""

import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jimic.channel.message import TELEGRAM_TEXT_LIMIT, telegram_length
from jimic.heartbeat.key_tasks import KeyTaskStatus
from jimic.memory.notices import SentToday
from jimic.memory.schemas import WEEKDAYS, Intention, Intentions, Limits, Notice, Window
from jimic.tools.calendar import CalendarDay

logger = logging.getLogger(__name__)

URGENT_TYPES = frozenset({"daily_plan", "day_close", "key_task"})
"""They never wait in the group window. A `notice` is the only kind that does (1.7-b)."""

HANDOVER_TYPES = frozenset({"daily_plan", "day_close"})
"""They are not sent: the heartbeat hands them over to their hook (decision 1.8-b), and that
counts as attended today; the plan and the close count as a message when they go out (tasks 1.14
and 1.16)."""

SEPARATOR = "\n\n"
"""Between the blocks of one message: a blank line (decision 1.8-d)."""


type StopReason = Literal["quiet_hours", "daily_cap", "in_event"]
type Action = Literal["send", "wait", "block", "handover"]


class Outgoing(BaseModel):
    """What the decision proposes to send: an intention and its text."""

    model_config = ConfigDict(frozen=True, strict=True)

    intent: str
    text: str = Field(repr=False)


@dataclass(frozen=True)
class Verdict:
    """What the second pass says: send, wait, block or hand over to the agent, always with a
    code."""

    action: Action
    reason: str
    items: tuple[Outgoing, ...]

    @property
    def intents(self) -> tuple[str, ...]:
        return tuple(item.intent for item in self.items)


# --- First pass ------------------------------------------------------------------------------


def in_window(moment: time, window: Window) -> bool:
    """`[start, end)`, crossing midnight when `start > end`. When they are equal the window is
    the whole day (decision 80): for quiet hours, the discreet reading of an edit gone wrong."""
    if window.start == window.end:
        return True
    if window.start < window.end:
        return window.start <= moment < window.end
    return moment >= window.start or moment < window.end


def event_in_progress(calendar: CalendarDay, now: datetime) -> bool:
    """An event with times (not all-day), not declined, with `start <= now < end`.

    The calendar of Todoist never gets here: the reader skips it (decision 1.5-e).
    """
    return any(
        not event.all_day
        and not event.declined
        and isinstance(event.start, datetime)
        and isinstance(event.end, datetime)
        and event.start <= now < event.end
        for event in calendar.events
    )


@dataclass(frozen=True)
class Quota:
    """The messages left today, with the last one kept for the day close (decision 1.7-a).

    The slot is kept only while an enabled `day_close` has not been attended today: once it went
    out, or was handed over with nothing to close (task 1.16), or if there is none, the cap is
    the same for everything.
    """

    max_messages: int
    sent: int
    close_pending: bool

    @classmethod
    def of(cls, limits: Limits, intentions: Intentions, sent: SentToday) -> Quota:
        close_pending = any(
            intention.type == "day_close" and intention.enabled and sent.count(intention.id) == 0
            for intention in intentions.intentions
        )
        return cls(limits.max_messages_per_day, sent.message_count, close_pending)

    def refusal(self, is_close: bool) -> str | None:
        """`None` if one more message fits; otherwise `daily_cap` or `close_slot`."""
        if self.sent >= self.max_messages:
            return "daily_cap"
        if not is_close and self.close_pending and self.sent >= self.max_messages - 1:
            return "close_slot"
        return None

    @property
    def left_for_others(self) -> int:
        """Messages left for what is not the day close."""
        kept = 1 if self.close_pending else 0
        return max(0, self.max_messages - self.sent - kept)

    @property
    def close_slot_kept(self) -> bool:
        return self.close_pending and self.sent < self.max_messages

    def after_sending(self, is_close: bool) -> Quota:
        return Quota(self.max_messages, self.sent + 1, self.close_pending and not is_close)


@dataclass(frozen=True)
class FirstPass:
    stop: StopReason | None
    calendar: CalendarDay | None
    """Read only if the cheaper checks let the cycle go on."""


async def first_pass(
    now: datetime,
    limits: Limits,
    quota: Quota,
    read_calendar: Callable[[], Awaitable[CalendarDay]],
) -> FirstPass:
    """From the cheapest check to the most expensive: the clock, SQLite, then the network.

    Without the calendar the cycle does not stop for an event (decision 1.7-c): the state says
    that the calendar is not available.
    """
    if in_window(now.time(), limits.quiet_hours):
        return FirstPass("quiet_hours", None)
    if quota.refusal(is_close=True) is not None:
        return FirstPass("daily_cap", None)
    calendar = await read_calendar()
    if limits.silent_during_events and calendar.available and event_in_progress(calendar, now):
        return FirstPass("in_event", calendar)
    return FirstPass(None, calendar)


# --- The cap of each intention ----------------------------------------------------------------


def cap_per_day(intention: Intention, limits: Limits) -> int:
    """`daily_plan`, `day_close` and each `notice`, once a day; a `key_task`, at most
    `min(max_per_day, max_insist_per_task)`."""
    if intention.type == "key_task":
        return min(intention.max_per_day, limits.max_insist_per_task)
    return 1


def applies_on(intention: Intention, day: date) -> bool:
    """Enabled, and a `notice` only on its days."""
    if not intention.enabled:
        return False
    return not isinstance(intention, Notice) or WEEKDAYS[day.weekday()] in intention.days


# --- The queue of the group window --------------------------------------------------------------


@dataclass(frozen=True)
class Waiting:
    item: Outgoing
    day: date


@dataclass
class WaitingNotices:
    """The notices waiting for the group window, in memory (decision 1.7-b).

    If the service restarts they are lost: at most a non-urgent notice, which the model will
    decide again. That is a known limit, not a table.
    """

    _items: list[Waiting] = field(default_factory=list)

    @property
    def items(self) -> tuple[Outgoing, ...]:
        return tuple(waiting.item for waiting in self._items)

    def add(self, item: Outgoing, day: date) -> None:
        self._items.append(Waiting(item, day))

    def has(self, intent: str) -> bool:
        return any(waiting.item.intent == intent for waiting in self._items)

    def take_all(self) -> tuple[Outgoing, ...]:
        items = self.items
        self._items.clear()
        return items

    def drop_other_days(self, day: date) -> int:
        """Drop what waits from another day (when `quiet_hours` does not separate the days)."""
        kept = [waiting for waiting in self._items if waiting.day == day]
        dropped = len(self._items) - len(kept)
        self._items[:] = kept
        return dropped


# --- Second pass -------------------------------------------------------------------------------


class _Pass:
    """The bookkeeping of one second pass: what has gone out today, this cycle included."""

    def __init__(
        self,
        now: datetime,
        intentions: Intentions,
        limits: Limits,
        sent: SentToday,
        queue: WaitingNotices,
        last_chat: datetime | None,
        key_tasks: Mapping[str, KeyTaskStatus],
        due: Collection[str] | None,
    ) -> None:
        self.now = now
        self.due = due
        self.key_tasks = key_tasks
        self.last_chat = last_chat
        self.day = now.date()
        self.limits = limits
        self.by_id = {intention.id: intention for intention in intentions.intentions}
        self.sent = sent
        self.queue = queue
        self.quota = Quota.of(limits, intentions, sent)
        self.last_sent_at = sent.last_sent_at
        self.verdicts: list[Verdict] = []

    def refusal_of(self, item: Outgoing) -> str | None:
        """Why this intention cannot go out today, whatever the quota says."""
        intention = self.by_id.get(item.intent)
        if intention is None:
            return "unknown_intent"
        if not intention.enabled:
            return "disabled"
        if not applies_on(intention, self.day):
            return "not_today"
        status = self.key_tasks.get(intention.id)
        if status is not None:
            refusal = status.refusal(self.now)
            if refusal is not None:
                return refusal
        if self.sent.count(intention.id) >= cap_per_day(intention, self.limits):
            return "insist_cap" if intention.type == "key_task" else "repeated"
        return None

    def chatting(self) -> bool:
        """Pedro wrote less than `quiet_while_chatting` ago (decision 1.10-c)."""
        if self.last_chat is None:
            return False
        return self.now - self.last_chat < self.limits.quiet_while_chatting

    def window_open(self) -> bool:
        if self.last_sent_at is None:
            return True
        return self.now - self.last_sent_at >= self.limits.group_window

    def decide(self, action: Action, reason: str, *items: Outgoing) -> None:
        self.verdicts.append(Verdict(action, reason, items))

    def send(self, reason: str, *items: Outgoing, is_close: bool = False) -> None:
        self.decide("send", reason, *items)
        self.quota = self.quota.after_sending(is_close)
        self.last_sent_at = self.now

    def refusal_in_batch(self, item: Outgoing, taken: set[str]) -> str | None:
        refusal = self.refusal_of(item)
        if refusal is None and self.queue.has(item.intent):
            refusal = "already_waiting"
        if refusal is None and item.intent in taken:
            refusal = "duplicate"
        if refusal is None and self.due is not None and item.intent not in self.due:
            refusal = "not_due"
        return refusal

    def batch(self, proposals: Sequence[Outgoing]) -> None:
        """What this heartbeat proposes, as one message (decision 1.8-d).

        The urgent ones go out now. A `notice` goes with them if the group window has passed
        and Pedro is not chatting (decision 1.10-c); otherwise it waits. With the window passed,
        what waited goes in the same message too: one heartbeat, one message, which counts 1.
        While Pedro chats, an urgent one goes out alone and the queue keeps waiting.
        """
        urgent: list[Outgoing] = []
        notices: list[Outgoing] = []
        taken: set[str] = set()
        for item in proposals:
            refusal = self.refusal_in_batch(item, taken)
            if refusal is not None:
                self.decide("block", refusal, item)
                continue
            taken.add(item.intent)
            kind = self.by_id[item.intent].type
            if kind in HANDOVER_TYPES:
                # What the hook will send must fit. The plan and the close go out (tasks 1.14 and
                # 1.16): they take their message now, so the rest of this batch sees one fewer.
                is_close = kind == "day_close"
                refusal = self.quota.refusal(is_close=is_close)
                self.decide("block" if refusal else "handover", refusal or "ok", item)
                if refusal is None:
                    self.quota = self.quota.after_sending(is_close=is_close)
            else:
                (urgent if kind in URGENT_TYPES else notices).append(item)
        released: list[Outgoing] = []
        window_open, chatting = self.window_open(), self.chatting()
        if window_open and not chatting:
            for item in self.queue.take_all():
                refusal = self.refusal_of(item)
                if refusal is None:
                    released.append(item)
                else:
                    self.decide("block", refusal, item)
        else:
            reason = "group_window" if not window_open else "chatting"
            for item in notices:
                self.queue.add(item, self.day)
                self.decide("wait", reason, item)
            notices = []
        self.message(urgent, released + notices, bool(released))

    def message(self, urgent: list[Outgoing], others: list[Outgoing], released: bool) -> None:
        """Send `urgent` and `others` together, as much as fits in one Telegram message: the
        `notice` that does not fit waits for the next one."""
        if not urgent and not others:
            return
        items: list[Outgoing] = []
        length = 0
        for item in urgent + others:
            added = telegram_length(item.text) + (len(SEPARATOR) if items else 0)
            if length + added <= TELEGRAM_TEXT_LIMIT:
                items.append(item)
                length += added
            elif self.by_id[item.intent].type in URGENT_TYPES:
                self.decide("block", "message_full", item)
            else:
                self.queue.add(item, self.day)
                self.decide("wait", "message_full", item)
        if not items:
            return
        is_close = any(self.by_id[item.intent].type == "day_close" for item in items)
        refusal = self.quota.refusal(is_close)
        if refusal is not None:
            return self.decide("block", refusal, *items)
        self.send("released" if released else "ok", *items, is_close=is_close)


def second_pass(
    now: datetime,
    proposals: Sequence[Outgoing],
    intentions: Intentions,
    limits: Limits,
    sent: SentToday,
    queue: WaitingNotices,
    last_chat: datetime | None = None,
    key_tasks: Mapping[str, KeyTaskStatus] | None = None,
    due: Collection[str] | None = None,
) -> list[Verdict]:
    """The verdicts of this heartbeat for the proposals and for what waits. `queue` changes.
    `last_chat` is the last moment Pedro wrote, if any; `key_tasks`, what the state of the day
    knows of each key task (task 1.15); `due`, the ids due now (task 1.15-b): given, a proposal
    that is not among them is blocked with `not_due`. `None` checks nothing (the plan B).

    The proposals (the model's picks, or the templates of a heartbeat with the API down) are
    one batch: whatever goes out goes out as one message, so a heartbeat never sends two.
    """
    state = _Pass(now, intentions, limits, sent, queue, last_chat, key_tasks or {}, due)
    state.batch(proposals)
    for verdict in state.verdicts:
        logger.info(
            "second pass: %s %s (%s)", verdict.action, ",".join(verdict.intents), verdict.reason
        )
    return state.verdicts
