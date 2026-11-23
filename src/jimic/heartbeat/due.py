"""Whether each intention is due now, and why (decision 1.15b-a, task 1.15-b).

The code works out the hours, so the model never compares them: it gets `due_now` and a closed
code, `why`, for each active intention, and picks among those that are due. The first rule that
applies decides:

- **Not due:** a key task that is done, no longer today's, «hoy no», paused or before it may
  insist (`KeyTaskStatus.refusal`, without its `key_`); one that waits in the group window
  (`waiting`); one that may not go out again today (`insist_cap` for a key task, `sent_today` for
  the rest); the plan or the close before their window (`before_window`), a notice before its
  time (`before_time`); and anything but the close without a free message (`no_room`). The close
  has the slot kept for it (decision 78), so it is due while it fits.
- **Due:** the plan or the close in their window (`in_window`) or after it without having gone
  out (`late`, as `late_after` says: the same late plan as the plan's runner); a notice in its
  heartbeat's slot (`on_time`) or later, if it did not go out (`late`); a key task that may
  insist (`can_insist`).

The default time of the plan and the close does not count here: it is the time of the plan B
(RF-04), with the API down.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from jimic.heartbeat.key_tasks import KeyTaskStatus
from jimic.memory.schemas import DailyPlan, DayClose, Intention, KeyTask, Notice
from jimic.plan.day import late_after

SLOT = timedelta(minutes=15)
"""A heartbeat's slot: a notice is `on_time` while `default_time <= now < default_time + SLOT`."""

type DueWhy = Literal[
    "done",
    "rescheduled",
    "not_today",
    "paused",
    "too_early",
    "waiting",
    "insist_cap",
    "sent_today",
    "before_window",
    "before_time",
    "no_room",
    "in_window",
    "late",
    "on_time",
    "can_insist",
]
KEY_WHY: dict[str, DueWhy] = {
    "key_done": "done",
    "key_rescheduled": "rescheduled",
    "key_not_today": "not_today",
    "key_paused": "paused",
    "key_too_early": "too_early",
}


@dataclass(frozen=True)
class Due:
    due: bool
    why: DueWhy


def _minute(now: datetime) -> datetime:
    """Compared to the minute, like `late_after`: 8:00 and a fraction of a second is 8:00."""
    return now.replace(second=0, microsecond=0)


def _window_due(intention: DailyPlan | DayClose, now: datetime) -> Due:
    window = intention.window
    span = None if window is None else (window.start, window.end)
    if late_after(span, intention.default_time, now):
        return Due(True, "late")
    if window is not None and window.start < window.end and _minute(now).time() < window.start:
        return Due(False, "before_window")
    return Due(True, "in_window")


def _notice_due(intention: Notice, now: datetime) -> Due:
    start = datetime.combine(now.date(), intention.default_time, tzinfo=now.tzinfo)
    moment = _minute(now)
    if moment < start:
        return Due(False, "before_time")
    return Due(True, "on_time" if moment < start + SLOT else "late")


def due_now(
    intention: Intention,
    now: datetime,
    *,
    left_today: int,
    waiting: bool,
    key: KeyTaskStatus | None,
    messages_left: int,
    close_fits: bool,
) -> Due:
    """Whether `intention`, active today, is due at `now` (aware, local). `left_today` is how
    many times it may still go out, `waiting` whether it waits in the group window, `key` its
    status if it is a key task, `messages_left` the messages left for anything but the close and
    `close_fits` whether the close still fits."""
    if key is not None:
        refusal = key.refusal(now)
        if refusal is not None:
            return Due(False, KEY_WHY[refusal])
    if waiting:
        return Due(False, "waiting")
    if left_today <= 0:
        return Due(False, "insist_cap" if isinstance(intention, KeyTask) else "sent_today")
    if isinstance(intention, DailyPlan | DayClose):
        due = _window_due(intention, now)
    elif isinstance(intention, Notice):
        due = _notice_due(intention, now)
    else:
        due = Due(True, "can_insist")
    if not due.due:
        return due
    fits = close_fits if isinstance(intention, DayClose) else messages_left > 0
    return due if fits else Due(False, "no_room")
