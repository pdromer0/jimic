"""What the agent proposes, as data (H1-1): a plan of tasks or a change of memory.

`propose_plan` validates here and adds its items to the run (task 1.11): when the run ends, the
code shows them under the agent's answer with their buttons, or executes one at once (decision
1.11-b). `propose_change` saves a pending row in `approvals`, without buttons until task 1.12.
Nothing is executed by the tools. A move or a delete carries the snapshot of the task as the
code read it (title, due and project), which the rule of H1-2 compares before deleting.

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
    requested_by_pedro: Annotated[
        bool,
        Field(
            description=(
                "true solo si Pedro ha pedido en este mensaje exactamente esta acción"
                " («crea…», «mueve…»); false si la propones tú"
            )
        ),
    ] = False

    @model_validator(mode="after")
    def _one_action_per_task(self) -> Self:
        """A task moved and deleted, or moved twice, would be two actions that contradict each
        other once Pedro approves them."""
        ids = [item.task_id for item in self.items if isinstance(item, MoveItem | DeleteItem)]
        if len(set(ids)) != len(ids):
            raise ValueError("each task_id may appear only once in a plan")
        return self


class TaskSnapshot(BaseModel):
    """The task as the code read it when the proposal was saved (H1-2)."""

    model_config = ConfigDict(frozen=True)

    title: str
    due: str | None
    project_id: str
    is_recurring: bool = False
    """Not compared (H1-2 compares title, due and project): it decides 1.11-b."""

    @classmethod
    def of(cls, task: TodoistTask) -> TaskSnapshot:
        due = None if task.due is None else task.due.isoformat()
        return cls(
            title=task.title, due=due, project_id=task.project_id, is_recurring=task.is_recurring
        )

    def matches(self, other: TaskSnapshot) -> bool:
        """Same title, due and project (H1-2): the due compared as moments, not as text."""
        return (
            self.title == other.title
            and self.project_id == other.project_id
            and same_due(self.due, other.due)
        )


def due_value(text: str | None) -> date | datetime | None:
    """A snapshot's due back as a value: a day, or an aware moment."""
    if text is None:
        return None
    return datetime.fromisoformat(text) if "T" in text else date.fromisoformat(text)


def same_due(a: str | None, b: str | None) -> bool:
    """`2030-01-07T18:00:00+01:00` and `2030-01-07T17:00:00+00:00` are the same moment."""
    first, second = due_value(a), due_value(b)
    if isinstance(first, datetime) != isinstance(second, datetime):
        return False
    return first == second


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
