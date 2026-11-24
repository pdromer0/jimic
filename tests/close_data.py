"""Invented data for the close of the day (task 1.16): the e08 of the chat's notes, made up.

A Thursday, 10-ene-2030, close at 21:30. Done today: «Meditación» (7:10, marked at 9:30),
«Comida» (14:00, marked at 13:20, before its time) and «Trabajo en el proyecto» (15:30, marked at
16:45). Not marked: «Revisar ofertas» 14:30 (30 min), «Proyecto personal» 17:15, «Paseo 5 km»
19:00 and «Cena» 21:30. Tomorrow: a free calendar, «Gimnasio» at 13:00 (75 min) and APEX
«Pierna · fuerza». Nothing here is real.
"""

from datetime import date, datetime
from typing import Any

from heartbeat_data import MADRID

from jimic.tools.apex_schema import ApexSnapshot, TodayWorkout
from jimic.tools.calendar import CalendarDay
from jimic.tools.todoist import TaskList, TodoistTask

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
