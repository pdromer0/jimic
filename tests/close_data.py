"""Invented data for the close of the day (task 1.16): the e08 of the chat's notes, made up.

A Thursday, 10-ene-2030, close at 21:30. Done today: «Meditación» (7:10, marked at 9:30),
«Comida» (14:00, marked at 13:20, before its time) and «Trabajo en el proyecto» (15:30, marked at
16:45). Not marked: «Revisar ofertas» 14:30 (30 min), «Proyecto personal» 17:15, «Paseo 5 km»
19:00 and «Cena» 21:30. Tomorrow: a free calendar, «Gimnasio» at 13:00 (75 min) and APEX
«Pierna · fuerza». Nothing here is real.
"""

import asyncio
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from agent_data import FakeAgent
from apex_data import FixedApexReader
from conftest import FixedClock
from execution_data import AssistantWorld
from heartbeat_data import CLOSE as CLOSE_INTENT
from heartbeat_data import GYM, MADRID, day_state, intentions, limits
from heartbeat_data import PLAN as PLAN_INTENT
from todoist_data import FakeTodoistApi, raw_task

from jimic.channel import store
from jimic.channel.bot import SendError
from jimic.channel.message import InteractiveMessage, Tap
from jimic.channel.taps import TapResult, process_tap
from jimic.close.day import close_view, morning_after
from jimic.close.message import CLOSE_KIND, CloseContent, paint
from jimic.close.runner import DayCloseRunner
from jimic.execution.close import CloseActions
from jimic.heartbeat.sources import Sources
from jimic.memory import day_plans
from jimic.memory.live import LiveConfig
from jimic.tools.apex_schema import ApexSnapshot, TodayWorkout
from jimic.tools.calendar import CalendarDay
from jimic.tools.todoist import (
    ApiTodoistReader,
    ApiTodoistWriter,
    TaskList,
    TodoistTask,
    TodoistTools,
)

THURSDAY = date(2030, 1, 10)
FRIDAY = date(2030, 1, 11)
CLOSE_AT = "2030-01-10T21:30"


def at(moment: str) -> datetime:
    return datetime.fromisoformat(moment).replace(tzinfo=MADRID)


def todo(
    task_id: str,
    title: str,
    due: str | None,
    minutes: int | None = None,
    done_at: str | None = None,
    recurring: bool = False,
) -> TodoistTask:
    """A task; `due` is `YYYY-MM-DD` or a local `YYYY-MM-DDTHH:MM`."""
    when: Any = None
    if due is not None:
        when = at(due) if "T" in due else date.fromisoformat(due)
    return TodoistTask(
        id=task_id,
        title=title,
        project_id="proyecto-inventado",
        due=when,
        is_recurring=recurring,
        has_subtasks=False,
        duration_minutes=minutes,
        completed=done_at is not None,
        completed_at=None if done_at is None else at(done_at),
    )


MEDITATION = todo("t-meditacion", "Meditación", "2030-01-10T07:10", done_at="2030-01-10T09:30")
LUNCH = todo("t-comida", "Comida", "2030-01-10T14:00", done_at="2030-01-10T13:20")
WORK = todo("t-trabajo", "Trabajo en el proyecto", "2030-01-10T15:30", done_at="2030-01-10T16:45")
OFFERS = todo("t-ofertas", "Revisar ofertas", "2030-01-10T14:30", minutes=30)
PROJECT = todo("t-proyecto", "Proyecto personal", "2030-01-10T17:15")
WALK = todo("t-paseo", "Paseo 5 km", "2030-01-10T19:00")
DINNER = todo("t-cena", "Cena", "2030-01-10T21:30")
GYM_TOMORROW = todo("t-gimnasio-viernes", "Gimnasio", "2030-01-11T13:00", minutes=75)

DONE = (MEDITATION, LUNCH, WORK)
OPEN = (OFFERS, PROJECT, WALK, DINNER)
PLAN = (*DONE, *OPEN)


def open_today() -> TaskList:
    return TaskList(available=True, tasks=list(OPEN))


def completed_today() -> TaskList:
    return TaskList(available=True, tasks=list(DONE))


def tomorrow_tasks() -> TaskList:
    return TaskList(available=True, tasks=[GYM_TOMORROW])


def free_calendar() -> CalendarDay:
    return CalendarDay(available=True, calendar_count=1, events=[])


def apex_e08() -> ApexSnapshot:
    return ApexSnapshot(
        available=True,
        planned_today="Pierna · fuerza",
        planned_tomorrow="Pierna · fuerza",
        today_workout=TodayWorkout.NOT_RECORDED,
    )


# --- The world of the taps: Todoist over its fake, a fixed calendar, the handlers ---------------

RAW_OPEN = (
    ("t-ofertas", "Revisar ofertas", "2030-01-10T14:30:00", 30),
    ("t-proyecto", "Proyecto personal", "2030-01-10T17:15:00", None),
    ("t-paseo", "Paseo 5 km", "2030-01-10T19:00:00", None),
    ("t-cena", "Cena", "2030-01-10T21:30:00", None),
    ("t-gimnasio-viernes", "Gimnasio", "2030-01-11T13:00:00", 75),
)


def raw_e08() -> list[dict[str, Any]]:
    """The open tasks of the e08 (and tomorrow's gym) as Todoist's API sends them."""
    return [
        raw_task(task_id, title, project_id="proyecto-inventado", due=due, duration=minutes)
        for task_id, title, due, minutes in RAW_OPEN
    ]


class DayCalendar:
    """A calendar that gives, for each day, the events given for it (and counts its reads)."""

    def __init__(self, days: dict[date, CalendarDay] | None = None) -> None:
        self.days = days or {}
        self.reads: list[date] = []
        self.down: str | None = None

    async def read_day(self, day: date) -> CalendarDay:
        self.reads.append(day)
        if len(self.reads) > 50:
            raise AssertionError("the calendar was read in a loop")
        if self.down is not None:
            return CalendarDay.unavailable(self.down)
        return self.days.get(day, CalendarDay(available=True, calendar_count=1, events=[]))


class CloseWorld:
    def __init__(self, conn: sqlite3.Connection, tasks: list[dict[str, Any]] | None = None):
        self.conn = conn
        self.clock = FixedClock(at(CLOSE_AT))
        self.api = FakeTodoistApi(tasks if tasks is not None else raw_e08())
        self.todoist = TodoistTools(
            ApiTodoistReader(self.api, self.clock, MADRID),
            ApiTodoistWriter(self.api, MADRID),
            self.api.close,
        )
        self.calendar = DayCalendar()
        self.config = LiveConfig.fixed(intentions(), limits())
        self.actions = CloseActions(
            conn=conn,
            clock=self.clock,
            timezone=MADRID,
            todoist=self.todoist,
            calendar=self.calendar,
            config=self.config,
        )

    def paint(self, plan: frozenset[str] | None = None, keys=None) -> InteractiveMessage:
        tasks = asyncio.run(self.todoist.reader.read_today())
        content = CloseContent(
            view=close_view(tasks, completed_today(), [], THURSDAY),
            note=None,
            tomorrow="🔮 Mañana: calendario libre",
            plan=plan,
            keys=keys or {},
        )
        return paint(content, THURSDAY, MADRID, {})

    def show(self, message: InteractiveMessage, kind: str = CLOSE_KIND) -> int:
        expires = morning_after(THURSDAY, self.config.limits.quiet_hours, MADRID)
        approval_id = store.create(self.conn, self.clock, kind, message, expires)
        store.set_message_id(self.conn, approval_id, 700 + approval_id)
        return approval_id

    def tap(self, approval_id: int, item: int | None, option: str) -> TapResult:
        return asyncio.run(
            process_tap(
                self.conn, self.clock, self.actions.handlers, Tap(approval_id, item, option)
            )
        )

    def message(self, approval_id: int) -> InteractiveMessage:
        approval = store.load(self.conn, approval_id)
        assert approval is not None
        return approval.message

    def due(self, task_id: str) -> str | None:
        """The due of the task as the reader gives it, local: `2030-01-11T19:00:00`."""
        task = asyncio.run(self.todoist.reader.get_task(task_id))
        assert task is not None
        if isinstance(task.due, datetime):
            return task.due.astimezone(MADRID).strftime("%Y-%m-%dT%H:%M:%S")
        return None if task.due is None else task.due.isoformat()

    def touched(self, task_id: str) -> list[str]:
        return [
            name
            for name, arguments in self.api.calls
            if arguments.get("task_id") == task_id
            and name in ("update_task", "complete_task", "uncomplete_task", "delete_task")
        ]


# --- The world of the hook: the assistant's, with the close runner --------------------------

RAW_DONE = (
    ("t-meditacion", "Meditación", "2030-01-10T07:10:00", "2030-01-10T08:30:00.000000Z"),
    ("t-comida", "Comida", "2030-01-10T14:00:00", "2030-01-10T12:20:00.000000Z"),
    ("t-trabajo", "Trabajo en el proyecto", "2030-01-10T15:30:00", "2030-01-10T15:45:00.000000Z"),
)
"""Completed at 9:30, 13:20 and 16:45 in Madrid (UTC + 1)."""


def raw_done() -> list[dict[str, Any]]:
    return [
        raw_task(task_id, title, project_id="proyecto-inventado", due=due)
        | {"checked": True, "completed_at": completed}
        for task_id, title, due, completed in RAW_DONE
    ]


class SpyCloseSender:
    """Stands in for the channel: records what it delivers and until when."""

    def __init__(self) -> None:
        self.delivered: list[tuple[str, InteractiveMessage, datetime]] = []
        self.tries = 0
        self.fail: str | None = None

    async def deliver(self, kind: str, content: InteractiveMessage, expires_at: datetime) -> int:
        self.tries += 1
        if self.tries > 10:
            raise AssertionError("the close was delivered in a loop")
        if self.fail is not None:
            raise SendError(self.fail)
        self.delivered.append((kind, content, expires_at))
        return 950 + len(self.delivered)


class CloseRunWorld(AssistantWorld):
    def __init__(
        self,
        conn: sqlite3.Connection,
        tmp_path: Path,
        agent: FakeAgent,
        tasks: list[dict[str, Any]] | None = None,
    ):
        chosen = tasks if tasks is not None else [*raw_e08(), *raw_done()]
        super().__init__(conn, tmp_path, agent, chosen)
        self.clock.moment = at(CLOSE_AT)
        self.config = LiveConfig.fixed(intentions(PLAN_INTENT, CLOSE_INTENT, GYM), limits())
        self.calendar = DayCalendar()
        self.apex = FixedApexReader(apex_e08())
        self.close_sources = Sources(todoist=self.todoist, calendar=self.calendar, apex=self.apex)
        self.sender = SpyCloseSender()
        self.runner = DayCloseRunner(
            conversation=self.conversation,
            sources=self.close_sources,
            config=self.config,
            conn=conn,
            clock=self.clock,
            timezone=MADRID,
            config_dir=self.paths.config_dir,
            sender=self.sender,
        )

    def with_plan(self, task_ids=tuple(task.id for task in PLAN)) -> None:
        scheduled = {task.id: task.due for task in PLAN if isinstance(task.due, datetime)}
        day_plans.record_plan(
            self.conn,
            THURSDAY,
            at("2030-01-10T07:45"),
            [(task_id, scheduled.get(task_id)) for task_id in task_ids],
            {},
        )

    def handover(self, intent: str = "cierre_del_dia") -> None:
        state = day_state(self.conn, self.clock.now(), self.config.intentions)
        asyncio.run(self.runner.handover(intent, state))

    def closes(self) -> list[InteractiveMessage]:
        return [message for _, message, _ in self.sender.delivered]

    def build_close(self, statuses=()):
        return asyncio.run(self.runner.build(statuses))

    def settle(self) -> None:
        asyncio.run(self.runner.settle())
