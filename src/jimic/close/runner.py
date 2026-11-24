"""The close of the day, end to end (task 1.16): the hook `on_handover` for a `day_close`, and
the definitive figure of «plan cumplido» the next morning.

When the heartbeat hands the close over, `DayCloseRunner.handover` reads Todoist (today's open
tasks, the ones completed today), tomorrow's calendar and APEX itself, asks the agent **only for
the note 🎯** (decision 1.16-a: in today's conversation, with a fixed note of the code, without
origin, mode `close`), paints the whole message (`close/message.py`) and sends it once; it counts
in the quota (a row in `notices`) and expires «la mañana siguiente» (`morning_after`). If the
agent cannot (it fails, the daily cap, an empty answer), the fixed note goes instead.

- **One close a day:** the heartbeat attends it once; if one already went out today, nothing more.
- **Nothing to close:** without a plan today and without tasks of today (open or completed), no
  message: the close counts as attended and Pedro is not bothered.
- **Retries**, as the plan's: one retry with the agent, then the fixed note, then only the log.
- **The plan's tasks** done at the close get `done` (with Todoist's `completed_at`).
- **The definitive figure** (`settle`, after each heartbeat): for each day before today whose
  morning has come, each task of its plan read afresh: completed in Todoist counts; a recurring
  one (which never shows as completed) counts if it was marked done (button, APEX or [Hecha]);
  «la haré» never. A read that fails leaves the day for the next heartbeat.

The note to the agent carries counts and the key tasks by their habit's name, never a task's
title (external content: the agent reads them with its tools if it wants). Logs say codes and
counts, never what the close says.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from jimic.agent.conversation import Conversation
from jimic.channel.message import InteractiveMessage
from jimic.clock import Clock, local_day_bounds
from jimic.close.day import CloseView, KeyDone, close_view, morning_after, tomorrow_line
from jimic.close.message import CLOSE_KIND, CloseContent, cut_note, paint, provisional
from jimic.heartbeat.beat import log_handover
from jimic.heartbeat.key_tasks import KeyTaskStatus
from jimic.heartbeat.sources import Sources
from jimic.heartbeat.state import DayState
from jimic.memory import day_plans, key_marks
from jimic.memory.live import LiveConfig
from jimic.memory.notices import record_sent, sent_between
from jimic.memory.schemas import DayClose, KeyTask
from jimic.plan.day import normalized
from jimic.plan.runner import read_icons
from jimic.plan.tries import DailyTries
from jimic.tools.failures import ToolFailure

logger = logging.getLogger(__name__)

CLOSE_NOTE = (
    "Toca la nota del cierre del día: 1 o 2 líneas sobre cómo va Pedro con sus metas, con humor,"
    " sin culpar ni sermonear y sin cifras de salud al principio. El mensaje del cierre (lo hecho,"
    " lo pendiente, mañana y sus botones) lo pinta el código: tu texto entero será la nota, así"
    " que no propongas nada ni digas que has movido o marcado nada. Hoy: {summary}."
)
MAX_AGENT_TRIES = 2
MAX_TRIES = 3


class CloseSender(Protocol):
    async def deliver(self, kind: str, content: InteractiveMessage, expires_at: datetime) -> int:
        """Send it; return the id of its message. Raises `SendError`."""
        ...


@dataclass(frozen=True)
class BuiltClose:
    message: InteractiveMessage | None
    """`None`: nothing to close today."""
    code: str
    """`agent`, `fixed:<why>` (`no_agent`, `empty`, the code of the run) or `nothing`."""
    agent_text: str | None = None
    """What the agent wrote, before the cut (the evaluation grades it)."""
    view: CloseView | None = None
    day: date | None = None


def _summary(
    view: CloseView,
    plan: frozenset[str] | None,
    keys: Iterable[tuple[str, str]],
    training: str | None,
) -> str:
    parts = []
    if plan:
        parts.append(f"{provisional(view, plan)} de {len(plan)} tareas del plan hechas")
    elif plan is None:
        parts.append("sin plan del día")
    if view.tasks_down:
        parts.append("Todoist no responde")
    else:
        parts.append(f"{len(view.done)} cosas hechas y {len(view.pending)} sin marcar")
    parts += [f"{name}: {state}" for name, state in keys]
    if training:
        parts.append(f"mañana toca {training}")
    return "; ".join(parts)


def _key_state(status: KeyTaskStatus) -> str:
    if status.done_by is not None:
        return "hecho"
    if status.not_today:
        return "hoy no"
    if status.rescheduled:
        return "pasado a otro día"
    return "sin hacer"


class DayCloseRunner:
    def __init__(
        self,
        *,
        conversation: Conversation,
        sources: Sources,
        config: LiveConfig,
        conn: sqlite3.Connection,
        clock: Clock,
        timezone: ZoneInfo,
        config_dir: Path,
        sender: CloseSender | None = None,
    ) -> None:
        """Without `sender` it only builds (the evaluation)."""
        self._conversation = conversation
        self._sources = sources
        self._config = config
        self._conn = conn
        self._clock = clock
        self._timezone = timezone
        self._config_dir = config_dir
        self._sender = sender
        self._tries = DailyTries()

    def _now(self) -> datetime:
        return self._clock.now().astimezone(self._timezone)

    def intention(self, intent: str) -> DayClose | None:
        found = next((i for i in self._config.intentions.intentions if i.id == intent), None)
        return found if isinstance(found, DayClose) else None

    # --- The hook ---------------------------------------------------------------------------

    async def handover(self, intent: str, state: DayState) -> None:
        """The hook of the heartbeat for a close (others go to the log). Raises `SendError` if
        the close could not go out: it stays unattended for a later heartbeat."""
        if self.intention(intent) is None:
            await log_handover(intent, state)
            return
        if self._sender is None:
            raise RuntimeError("the close has nowhere to go")
        day = self._now().date()
        if sent_between(self._conn, *local_day_bounds(day, self._timezone)).sent_count(intent):
            logger.info("close: already sent today")
            return
        tries = self._tries.take(day, intent)
        if tries >= MAX_TRIES:
            logger.warning("close: given up today after %d tries", tries)
            return
        built = await self.build(state.key_tasks.values(), use_agent=tries < MAX_AGENT_TRIES)
        if built.message is None:
            logger.info("close: nothing to close")
            return
        logger.info("close: %s (try %d)", built.code, tries + 1)
        expires = morning_after(day, self._config.limits.quiet_hours, self._timezone)
        message_id = await self._sender.deliver(CLOSE_KIND, built.message, expires)
        record_sent(self._conn, intent, self._clock.now(), message_id)
        assert built.view is not None
        self._note_done(day, built.view)

    def _note_done(self, day: date, view: CloseView) -> None:
        """The plan's tasks done at the close, with when they were marked."""
        now = self._clock.now()
        for item in day_plans.items(self._conn, day):
            if item.status == "pending" and item.task_id in view.done_ids:
                marked = view.marked_at.get(item.task_id, now)
                day_plans.set_status(self._conn, day, item.task_id, "done", marked)

    # --- Building it ------------------------------------------------------------------------

    def _keys(self, statuses: Iterable[KeyTaskStatus], day: date):
        intentions = self._config.intentions
        habits = {habit.id: habit for habit in intentions.habits}
        by_id = {i.id: i for i in intentions.intentions if isinstance(i, KeyTask)}
        closed = key_marks.done_tasks(self._conn, day)
        done: list[KeyDone] = []
        states: list[tuple[str, str]] = []
        for status in statuses:
            intention = by_id.get(status.intent)
            habit = habits.get(intention.habit) if intention is not None else None
            if habit is None:
                continue
            if status.off_day and status.done_by is None:
                continue  # its habit does not apply today (decision F2-a): nothing to say
            states.append((habit.name, _key_state(status)))
            if status.done_by in ("button", "apex"):
                ids = set(closed.get(status.intent, ()))
                if status.task is not None:
                    ids.add(status.task.id)
                done.append(KeyDone(habit.name, frozenset(ids), habit.todoist_task))
        names = {
            normalized(habits[i.habit].todoist_task): i.id
            for i in by_id.values()
            if i.habit in habits
        }
        return done, states, names

    async def build(
        self, statuses: Iterable[KeyTaskStatus] = (), use_agent: bool = True
    ) -> BuiltClose:
        """The close for now, or `None` in `message` if there is nothing to close. Never raises
        for the agent."""
        now = self._now()
        day = now.date()
        tomorrow = day + timedelta(days=1)
        sources = self._sources
        reader = sources.todoist.reader
        open_tasks, completed, calendar, apex = await asyncio.gather(
            reader.read_today(),
            reader.read_completed_today(),
            sources.calendar.read_day(tomorrow),
            asyncio.to_thread(sources.apex.read_today),
        )
        done, states, names = self._keys(list(statuses), day)
        view = close_view(open_tasks, completed, done, day)
        plan = (
            frozenset(item.task_id for item in day_plans.items(self._conn, day))
            if day_plans.has_plan(self._conn, day)
            else None
        )
        nothing = completed.available and not view.pending and not view.done
        if plan is None and not view.tasks_down and nothing:
            return BuiltClose(None, "nothing", view=view, day=day)
        agent_text = None
        code = "fixed:no_agent"
        if use_agent:
            training = apex.planned_tomorrow if apex.available else None
            summary = _summary(view, plan, states, training)
            turn = await self._conversation.run_note(CLOSE_NOTE.format(summary=summary), "close")
            if turn is not None and turn.code == "ok":
                agent_text = turn.text
                code = "agent" if cut_note(turn.text) else "fixed:empty"
            else:
                code = f"fixed:{'skipped' if turn is None else turn.code}"
        content = CloseContent(
            view=view,
            note=cut_note(agent_text),
            tomorrow=tomorrow_line(calendar, apex, tomorrow, self._timezone),
            plan=plan,
            keys=names,
        )
        message = paint(content, day, self._timezone, read_icons(self._config_dir))
        return BuiltClose(message, code, agent_text, view, day)

    # --- The definitive figure (H1-14) ------------------------------------------------------

    async def settle(self) -> None:
        """The figure of each past day whose morning has come. Never raises."""
        try:
            now = self._now()
            quiet = self._config.limits.quiet_hours
            for day in day_plans.unsettled(self._conn, now.date()):
                if now < morning_after(day, quiet, self._timezone):
                    continue
                fulfilled = await self._fulfilled(day, morning_after(day, quiet, self._timezone))
                day_plans.settle(self._conn, day, fulfilled, self._clock.now())
                logger.info("close: day settled")
        except ToolFailure as error:
            logger.info("close: settle waits (%s)", error.code)
        except Exception as error:
            logger.error("close: settle failed (%s)", type(error).__name__)

    async def _fulfilled(self, day: date, morning: datetime) -> int:
        """How many tasks of the plan of `day` were marked before its `morning`; raises
        `ToolFailure` if one cannot be read (nothing is written then). One marked later (the
        figure settled late, after the service was down) is not of that day."""
        reader = self._sources.todoist.reader
        items = day_plans.items(self._conn, day)
        tasks = await asyncio.gather(*(reader.get_task(item.task_id) for item in items))
        count = 0
        now = self._clock.now()
        for item, task in zip(items, tasks, strict=True):
            in_time = task is not None and (
                task.completed_at is None or task.completed_at < morning
            )
            if task is not None and task.completed and in_time:
                count += 1
                if item.status != "done":
                    marked = task.completed_at or now
                    day_plans.set_status(self._conn, day, item.task_id, "done", marked)
            elif task is not None and task.is_recurring and item.status == "done":
                count += 1
            elif item.status == "done":
                day_plans.set_status(self._conn, day, item.task_id, "pending")
        return count
