"""One heartbeat, end to end and thin: first pass, state of the day, decision, second pass.

The decision is injected (`decide`: Haiku, in `jimic.heartbeat.decide`). When the first pass
stops, `decide` is not called and Todoist and APEX are not even read. The cycle neither sends
nor writes: sending the verdicts and recording them is `jimic.heartbeat.beat`'s.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
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
from jimic.memory.usage import TokenUsage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Decision:
    """What the decision of a heartbeat says, as it goes to `decisions` and the second pass.

    `speak` and `intent` are what was decided (by the model, or by the templates when the API
    is down); `reason` is the model's sentence or a code. The sentence can quote data of the
    day, so the `repr` hides it and it never reaches a log.
    """

    speak: bool
    intent: str | None
    reason: str = field(repr=False)
    proposals: tuple[Outgoing, ...] = ()
    """What goes to the second pass: nothing, the model's message or the templates."""
    usage: TokenUsage | None = None
    """What the call used, if the API answered."""
    api_error: str | None = None
    """If the API failed, a code: `down:<Type>`, or `rejected:<Type>` for a request it refuses
    (a key or a model that is wrong), which retrying will not fix."""

    @property
    def api_down(self) -> bool:
        return self.api_error is not None


type Decide = Callable[[DayState], Awaitable[Decision]]


@dataclass(frozen=True)
class CycleResult:
    stopped: StopReason | None
    """Why the first pass ended the cycle, or `None` if it went on."""
    dropped: int
    """Notices that were waiting and were dropped (quiet hours or another day)."""
    state: DayState | None = None
    decision: Decision | None = None
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
    # APEX reads a file and may parse it: in a thread, so the loop keeps serving, and at the
    # same time as Todoist.
    tasks, apex = await asyncio.gather(
        sources.todoist.reader.read_today(), asyncio.to_thread(sources.apex.read_today)
    )
    state = build_day_state(
        now,
        timezone,
        first.calendar,
        tasks,
        apex,
        sent,
        intentions,
        limits,
        queue,
    )
    decision = await decide(state)
    verdicts = second_pass(now, decision.proposals, intentions, limits, sent, queue)
    return CycleResult(None, dropped, state, decision, tuple(verdicts))
