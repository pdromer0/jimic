"""What the agent proposes, as data (H1-1): a plan of tasks or a change of memory.

`propose_plan` validates here and adds its items to the run (task 1.11): when the run ends, the
code shows them under the agent's answer with their buttons, or executes one at once (decision
1.11-b). `propose_change` validates its arguments here and the change against the file in
`jimic.memory.changes` (task 1.12); the code shows it with [Guardar]. Nothing is executed by the
tools. A move or a delete carries the snapshot of the task as the
code read it (title, due and project), which the rule of H1-2 compares before deleting.

An approval expires at the end of the local day (RF-07). What a proposal says never reaches a
log: only the kind, the id and the counts.
"""

import re
from datetime import date, datetime
from typing import Annotated, Literal, Self, get_args
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from jimic.clock import Clock, local_day_bounds
from jimic.tools.todoist import TodoistTask

MAX_PLAN_ITEMS = 8
"""Items of one plan proposal: one row of buttons each in task 1.11 (decision 1.4)."""
MAX_NEW_TASKS = 4
"""New tasks in the plan of the day (format of the plan, decision 1.14-b)."""
MAX_REASON_WORDS = 8
"""Words of the reason of an item in the plan of the day: it goes in brackets on one line."""
MAX_LEFT_OUT = 2
MAX_DAY_ICONS = 20
MAX_LINE = 120
"""Characters of a line of the plan the agent writes (greeting, left out, training)."""
MAX_TASK_MINUTES = 24 * 60
MAX_TEXT = 500
"""Characters of a title or a reason; a memory change may be longer (`MAX_CHANGE_TEXT`)."""
MAX_CHANGE_TEXT = 2000
PLAN_KIND = "plan"
PersonalFile = Literal[
    "rutina.md", "objetivos.md", "preferencias.md", "intenciones.yaml", "limites.yaml"
]
PERSONAL_FILES: tuple[str, ...] = get_args(PersonalFile)

DUE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:T([0-9]{2}):([0-9]{2}))?")

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT)]
TaskId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Icon = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=12, pattern=r"^[^\sA-Za-z0-9]+$"
    ),
    Field(description="Un emoji"),
]
Line = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_LINE, pattern=r"^[^\r\n]+$"
    ),
]
HabitId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,40}$")]


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
    icon: Annotated[
        Icon | None, Field(description="Solo en el plan del día: el icono de su línea")
    ] = None


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


class LeftOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    habit: Annotated[HabitId, Field(description="El id del hábito en intenciones.yaml")]
    line: Annotated[Line, Field(description="Una línea con humor: por qué hoy no entra")]


class DayIcon(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: Text
    icon: Icon


FOLLOW_UP = Field(
    description=(
        "true si quieres seguir hablando con Pedro cuando resuelva esta propuesta con sus botones"
    )
)


class PlanProposal(BaseModel):
    """The arguments of `propose_plan`. A created task goes to the inbox (in `dev`, the guard
    sends it to the test project).

    The fields after `requested_by_pedro` other than `follow_up` are those of the plan of the
    day (decision 1.14-b): the code paints the plan with them and ignores them in a conversation.
    In the plan, `items` may be empty (nothing to add today)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: Annotated[list[PlanItem], Field(max_length=MAX_PLAN_ITEMS)] = []
    requested_by_pedro: Annotated[
        bool,
        Field(
            description=(
                "true solo si Pedro ha pedido en este mensaje exactamente esta acción"
                " («crea…», «mueve…»); false si la propones tú"
            )
        ),
    ] = False
    follow_up: Annotated[bool, FOLLOW_UP] = False
    greeting: Annotated[
        Line | None, Field(description="Plan del día: el saludo, corto y con gracia")
    ] = None
    left_out: Annotated[
        list[LeftOut],
        Field(
            max_length=MAX_LEFT_OUT,
            description="Plan del día: hábitos de prioridad alta que hoy no entran",
        ),
    ] = []
    training_line: Annotated[
        Line | None, Field(description="Plan del día: una línea sobre el entreno")
    ] = None
    day_icons: Annotated[
        list[DayIcon],
        Field(
            max_length=MAX_DAY_ICONS,
            description="Plan del día: iconos de lo que ya hay hoy y no está en la tabla",
        ),
    ] = []

    @property
    def plan_fields(self) -> dict[str, object]:
        """What this call says of the plan of the day, without what it leaves out."""
        fields = self.model_dump(include={"greeting", "left_out", "training_line", "day_icons"})
        return {key: value for key, value in fields.items() if value not in (None, [])}

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


Fragment = Annotated[str, StringConstraints(min_length=1, max_length=MAX_CHANGE_TEXT)]
"""Exact text: never stripped, since it is compared with the file as it is."""


class MemoryChange(BaseModel):
    """The arguments of `propose_change` (CU-13): which file, which operation, the exact
    fragment before and after, and why. `add` takes only `after`, `remove` only `before`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: PersonalFile
    operation: Literal["add", "replace", "remove"]
    before: Annotated[
        Fragment | None,
        Field(description="El texto exacto que hay ahora en el fichero (replace y remove)"),
    ] = None
    after: Annotated[
        Fragment | None, Field(description="El texto nuevo, exacto (add y replace)")
    ] = None
    reason: Text
    follow_up: Annotated[bool, FOLLOW_UP] = False

    @model_validator(mode="after")
    def _fits_operation(self) -> Self:
        needs_before = self.operation in ("replace", "remove")
        needs_after = self.operation in ("add", "replace")
        if needs_before != (self.before is not None) or needs_after != (self.after is not None):
            raise ValueError(
                "add lleva solo after, replace lleva before y after, remove solo before"
            )
        for fragment in (self.before, self.after):
            if fragment is not None and not fragment.strip():
                raise ValueError("before y after no pueden ser solo espacios")
        if self.operation == "replace" and self.before == self.after:
            raise ValueError("before y after son iguales: no cambia nada")
        return self


def expires_at(clock: Clock, timezone: ZoneInfo) -> datetime:
    """The end of the local day of now (RF-07)."""
    return local_day_bounds(clock.now().astimezone(timezone).date(), timezone)[1]
