"""What the agent proposes, as data (H1-1): a plan of tasks or a change of memory.

`propose_plan` and `propose_change` validate here and save a pending row in `approvals`; nothing
is executed. Task 1.11 executes a plan after Pedro's button or message, and task 1.12 a change
of memory. A move or a delete carries the snapshot of the task as the code read it when the
proposal was saved (title, due and project), which the rule of H1-2 compares before deleting.

An approval expires at the end of the local day (RF-07). What a proposal says never reaches a
log: only the kind, the id and the counts.
"""

import json
import re
import sqlite3
from datetime import date, datetime
from typing import Annotated, Any, Literal, Self, get_args
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from jimic.clock import Clock, local_day_bounds, to_db
from jimic.tools.todoist import TodoistTask

MAX_PLAN_ITEMS = 8
"""Items of one plan proposal: one row of buttons each in task 1.11 (decision 1.4)."""
MAX_TASK_MINUTES = 24 * 60
MAX_TEXT = 500
"""Characters of a title or a reason; a memory change may be longer (`MAX_CHANGE_TEXT`)."""
MAX_CHANGE_TEXT = 2000
PLAN_KIND = "plan"
CHANGE_KIND = "memory_change"
PersonalFile = Literal[
    "rutina.md", "objetivos.md", "preferencias.md", "intenciones.yaml", "limites.yaml"
]
PERSONAL_FILES: tuple[str, ...] = get_args(PersonalFile)

DUE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:T([0-9]{2}):([0-9]{2}))?")

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT)]
TaskId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


def parse_due(text: str) -> date | datetime:
    """`YYYY-MM-DD` (a day) or `YYYY-MM-DDTHH:MM` (a local moment, naive). Raises
    `ValueError` for anything else, a zone included."""
    match = DUE.fullmatch(text)
    if match is None:
        raise ValueError("due must be YYYY-MM-DD or YYYY-MM-DDTHH:MM")
    year, month, day, hour, minute = match.groups()
    if hour is None:
        return date(int(year), int(month), int(day))
    return datetime(int(year), int(month), int(day), int(hour), int(minute))


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Text


class _WithDue(_Item):
    due: Annotated[str, Field(description="YYYY-MM-DD, o YYYY-MM-DDTHH:MM en hora local")]

    @model_validator(mode="after")
    def _check_due(self) -> Self:
        parse_due(self.due)
        return self


class CreateItem(_WithDue):
    action: Literal["create"]
    title: Text
    duration_minutes: Annotated[int, Field(ge=1, le=MAX_TASK_MINUTES)] | None = None


class MoveItem(_WithDue):
    action: Literal["move"]
    task_id: TaskId


class DeleteItem(_Item):
    action: Literal["delete"]
    task_id: TaskId


PlanItem = Annotated[CreateItem | MoveItem | DeleteItem, Field(discriminator="action")]


class PlanProposal(BaseModel):
    """The arguments of `propose_plan`. A created task goes to the inbox (in `dev`, the guard
    sends it to the test project)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: Annotated[list[PlanItem], Field(min_length=1, max_length=MAX_PLAN_ITEMS)]


class TaskSnapshot(BaseModel):
    """The task as the code read it when the proposal was saved (H1-2)."""

    model_config = ConfigDict(frozen=True)

    title: str
    due: str | None
    project_id: str

    @classmethod
    def of(cls, task: TodoistTask) -> TaskSnapshot:
        due = None if task.due is None else task.due.isoformat()
        return cls(title=task.title, due=due, project_id=task.project_id)


class MemoryChange(BaseModel):
    """The arguments of `propose_change`; task 1.12 executes it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: PersonalFile
    operation: Literal["add", "replace", "remove"]
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CHANGE_TEXT)
    ]
    reason: Text


def expires_at(clock: Clock, timezone: ZoneInfo) -> datetime:
    """The end of the local day of now (RF-07)."""
    return local_day_bounds(clock.now().astimezone(timezone).date(), timezone)[1]


def save_proposal(
    conn: sqlite3.Connection, clock: Clock, timezone: ZoneInfo, kind: str, payload: dict[str, Any]
) -> int:
    """Save a pending approval of `kind` with `payload` and return its id."""
    cursor = conn.execute(
        "INSERT INTO approvals (created_at, expires_at, kind, payload) VALUES (?, ?, ?, ?)",
        (
            to_db(clock.now()),
            to_db(expires_at(clock, timezone)),
            kind,
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid
