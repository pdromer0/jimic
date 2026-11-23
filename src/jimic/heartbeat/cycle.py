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
from jimic.heartbeat.key_tasks import read_jumped
from jimic.heartbeat.limits import (
    Outgoing,
    Quota,
    StopReason,
    Verdict,
    WaitingNotices,
    applies_on,
    first_pass,
    second_pass,
)
from jimic.heartbeat.sources import Sources
from jimic.heartbeat.state import DayState, build_day_state
from jimic.memory import key_marks
from jimic.memory.conversations import last_message_at
from jimic.memory.notices import sent_between
from jimic.memory.schemas import Intentions, KeyTask, Limits
from jimic.memory.usage import TokenUsage
from jimic.tools.todoist import TaskList

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


async def _not_read() -> TaskList:
    return TaskList.unavailable("not_read")


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
    reader = sources.todoist.reader
    has_key_tasks = any(
        isinstance(intention, KeyTask) and applies_on(intention, day)
        for intention in intentions.intentions
    )
    # APEX reads a file and may parse it: in a thread, so the loop keeps serving, and at the
    # same time as Todoist. The completed tasks only matter to a key task (task 1.15).
    tasks, apex, completed = await asyncio.gather(
        reader.read_today(),
        asyncio.to_thread(sources.apex.read_today),
        reader.read_completed_today() if has_key_tasks else _not_read(),
    )
    marks = key_marks.of_day(conn, day)
    open_ids = {task.id for task in tasks.tasks} if tasks.available else set()
    seen = {task_id for item in marks.by_intent.values() for task_id in item.seen}
    missing = seen - open_ids if tasks.available else set()
    jumped = await read_jumped(reader, missing, day) if missing else frozenset()
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
        completed=completed,
        marks=marks,
        jumped=jumped,
    )
    decision = await decide(state)
    verdicts = second_pass(
        now,
        decision.proposals,
        intentions,
        limits,
        sent,
        queue,
        last_message_at(conn),
        state.key_tasks,
    )
    return CycleResult(None, dropped, state, decision, tuple(verdicts))
