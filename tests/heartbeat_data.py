"""Doubles and invented data for the heartbeat: nothing here is real.

`limits` and `intentions` build the two YAML files as the schemas read them. The sources are
fixed doubles that count their reads; `SpyDecide` stands in for the model and has a cap, like
every double the code calls in a loop.
"""

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apex_data import invented_snapshot
from calendar_data import SECRET_TITLE
from todoist_data import FixedTodoistReader

from jimic.clock import to_db
from jimic.heartbeat.limits import Outgoing
from jimic.heartbeat.sources import Sources
from jimic.heartbeat.state import DayState
from jimic.memory.schemas import Intentions, Limits
from jimic.tools.apex_schema import ApexSnapshot
from jimic.tools.calendar import CalendarDay, CalendarEvent, CalendarReader
from jimic.tools.todoist import TaskList, TodoistTask, TodoistTools

MADRID = ZoneInfo("Europe/Madrid")
MONDAY = date(2030, 1, 7)
SECRET_TEXT = "Texto-del-aviso-secreto-8821"
"""The text of a message: it must never reach a log or a `repr`."""
MAX_DECISIONS = 200
"""Calls `SpyDecide` accepts: a day has at most 100 heartbeats."""


class RunawayDouble(BaseException):
    """The code called a double more times than any day allows."""


def madrid(moment: str) -> datetime:
    return datetime.fromisoformat(moment).replace(tzinfo=MADRID)


LIMITS: dict[str, Any] = {
    "max_messages_per_day": 8,
    "quiet_hours": {"from": "23:00", "to": "07:00"},
    "silent_during_events": True,
    "group_window": "30m",
    "max_insist_per_task": 3,
    "max_deletions_per_day": 5,
    "budget": {
        "monthly_target_usd": 30.0,
        "warn_at": 0.8,
        "daily_cap_usd": 3.0,
        "max_session_usd": 0.5,
    },
    "tool_levels": {"read_calendar": "free"},
    "sensitive_categories": {},
}


def limits(**changes: Any) -> Limits:
    return Limits.model_validate({**LIMITS, **changes})


PLAN = {
    "id": "plan_del_dia",
    "type": "daily_plan",
    "purpose": "Proponer el plan",
    "window": {"from": "07:30", "to": "09:30"},
    "default_time": "08:00",
    "priority": "high",
}
CLOSE = {
    "id": "cierre_del_dia",
    "type": "day_close",
    "purpose": "Cerrar el día",
    "default_time": "22:00",
    "priority": "high",
}
GYM = {
    "id": "insistir_gimnasio",
    "type": "key_task",
    "purpose": "Que no se me pase el gimnasio",
    "habit": "gimnasio",
    "grace": "45m",
    "max_per_day": 3,
    "default_time": "13:00",
    "priority": "high",
}
PLANTS = {
    "id": "regar",
    "type": "notice",
    "purpose": "Recordar regar las plantas",
    "days": ["mon", "thu"],
    "default_time": "19:00",
    "priority": "low",
}
BINS = {
    "id": "basura",
    "type": "notice",
    "purpose": "Sacar la basura",
    "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "default_time": "21:00",
    "priority": "medium",
}
HABIT = {
    "id": "gimnasio",
    "name": "Gimnasio",
    "priority": "high",
    "duration": "75m",
    "moment": "midday",
    "todoist_task": "Gimnasio",
}


def intentions(*items: dict[str, Any], **changes: Any) -> Intentions:
    """The plan, the close, the gym, two notices and the gym habit; `items` replaces them."""
    chosen = list(items) if items else [PLAN, CLOSE, GYM, PLANTS, BINS]
    return Intentions.model_validate({"intentions": chosen, "habits": [HABIT], **changes})


def outgoing(intent: str, text: str = SECRET_TEXT) -> Outgoing:
    return Outgoing(intent=intent, text=text)


def add_notice(
    conn: sqlite3.Connection, intent: str, moment: datetime, message_id: int | None = None
) -> None:
    conn.execute(
        "INSERT INTO notices (sent_at, intent, telegram_message_id) VALUES (?, ?, ?)",
        (to_db(moment), intent, message_id),
    )


def event(
    start: datetime,
    minutes: int = 60,
    title: str = SECRET_TITLE,
    declined: bool = False,
) -> CalendarEvent:
    return CalendarEvent(
        title=title,
        start=start,
        end=start + timedelta(minutes=minutes),
        all_day=False,
        organizer_is_me=True,
        declined=declined,
    )


def all_day(day: date, title: str = SECRET_TITLE) -> CalendarEvent:
    return CalendarEvent(
        title=title,
        start=day,
        end=day + timedelta(days=1),
        all_day=True,
        organizer_is_me=True,
        declined=False,
    )


def calendar_with(*events: CalendarEvent) -> CalendarDay:
    return CalendarDay(available=True, calendar_count=1, events=list(events))


def task(task_id: str, title: str = SECRET_TITLE, due: Any = MONDAY) -> TodoistTask:
    return TodoistTask(
        id=task_id,
        title=title,
        project_id="proyecto-inventado",
        due=due,
        is_recurring=False,
        has_subtasks=False,
        duration_minutes=None,
    )


class CountingCalendar:
    def __init__(self, day: CalendarDay | None = None) -> None:
        self.day = day if day is not None else calendar_with()
        self.reads = 0

    async def read_day(self, day: date) -> CalendarDay:
        self.reads += 1
        return self.day


class CountingApex:
    def __init__(self, snapshot: ApexSnapshot | None = None) -> None:
        self.snapshot = snapshot if snapshot is not None else invented_snapshot()
        self.reads = 0

    def read_today(self) -> ApexSnapshot:
        self.reads += 1
        return self.snapshot


class CountingTasks(FixedTodoistReader):
    def __init__(self, tasks: TaskList | None = None) -> None:
        super().__init__(tasks)
        self.reads = 0

    async def read_today(self, project_id: str | None = None) -> TaskList:
        self.reads += 1
        return await super().read_today(project_id)


class NoWriter:
    """The cycle only reads: any write is a bug."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the cycle tried to write in Todoist ({name})")


async def _nothing() -> None:
    return None


def sources(
    calendar: CalendarDay | CalendarReader | None = None,
    tasks: TaskList | None = None,
    apex: ApexSnapshot | None = None,
) -> Sources:
    """Fixed sources. `calendar` may be a `CalendarDay` or a whole reader."""
    reader = (
        CountingCalendar(calendar)
        if calendar is None or isinstance(calendar, CalendarDay)
        else calendar
    )
    return Sources(
        todoist=TodoistTools(CountingTasks(tasks), NoWriter(), _nothing),  # pyright: ignore[reportArgumentType] - a writer that refuses
        calendar=reader,
        apex=CountingApex(apex),
    )


class SpyDecide:
    """Stands in for the model: records each state it gets and proposes `answer`."""

    def __init__(self, answer: Outgoing | None = None) -> None:
        self.answer = answer
        self.states: list[DayState] = []

    async def __call__(self, state: DayState) -> Outgoing | None:
        self.states.append(state)
        if len(self.states) > MAX_DECISIONS:
            raise RunawayDouble(f"decide called more than {MAX_DECISIONS} times")
        return self.answer

    @property
    def calls(self) -> int:
        return len(self.states)
