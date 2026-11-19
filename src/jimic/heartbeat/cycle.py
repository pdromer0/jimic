"""One heartbeat, end to end and thin: first pass, state of the day, decision, second pass.

The decision is injected (`decide`): task 1.8 brings Haiku. When the first pass stops, `decide`
is not called and Todoist and APEX are not even read. The cycle neither sends nor writes:
sending the verdicts and recording them in `decisions` and `notices` is task 1.8's.
"""

import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from jimic.clock import Clock, local_day_bounds
from jimic.heartbeat.limits import (
    Outgoing,
    Quota,
    StopReason,
    Verdict,
    WaitingNotices,
    first_pass,
    second_pass,
)
from jimic.heartbeat.sources import Sources
from jimic.heartbeat.state import DayState, build_day_state
from jimic.memory.notices import sent_between
from jimic.memory.schemas import Intentions, Limits

logger = logging.getLogger(__name__)

type Decide = Callable[[DayState], Awaitable[Outgoing | None]]


@dataclass(frozen=True)
class CycleResult:
    stopped: StopReason | None
    """Why the first pass ended the cycle, or `None` if it went on."""
    dropped: int
    """Notices that were waiting and were dropped (quiet hours or another day)."""
    state: DayState | None = None
    proposal: Outgoing | None = None
    verdicts: tuple[Verdict, ...] = ()


async def run_cycle(
    *,
    clock: Clock,
    timezone: ZoneInfo,
    limits: Limits,
    intentions: Intentions,
    sources: Sources,
    conn: sqlite3.Connection,
    queue: WaitingNotices,
    decide: Decide,
) -> CycleResult:
    now = clock.now().astimezone(timezone)
    day = now.date()
    dropped = queue.drop_other_days(day)
    if dropped:
        logger.info("dropped %d waiting notices (other_day)", dropped)
    sent = sent_between(conn, *local_day_bounds(day, timezone))
    first = await first_pass(
        now, limits, Quota.of(limits, intentions, sent), lambda: sources.calendar.read_day(day)
    )
    if first.stop is not None:
        if first.stop == "quiet_hours" and queue.items:
            silenced = len(queue.take_all())
            logger.info("dropped %d waiting notices (quiet_hours)", silenced)
            dropped += silenced
        logger.info("first pass: stop (%s)", first.stop)
        return CycleResult(first.stop, dropped)
    assert first.calendar is not None  # the first pass reads it whenever it goes on
    state = build_day_state(
        now,
        timezone,
        first.calendar,
        await sources.todoist.reader.read_today(),
        sources.apex.read_today(),
        sent,
        intentions,
        limits,
        queue,
    )
    proposal = await decide(state)
    verdicts = second_pass(now, proposal, intentions, limits, sent, queue)
    return CycleResult(None, dropped, state, proposal, tuple(verdicts))
