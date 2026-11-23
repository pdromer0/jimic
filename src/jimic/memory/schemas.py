"""Schemas of the two YAML personal files: `intenciones.yaml` and `limites.yaml`.

Every model is strict (no coercion: `"8"` or `true` as a cap is an error) and forbids unknown
keys. Hours and durations are text in the files and are parsed here with ASCII-only patterns.
The custom error types (`hour_format`, `duration_format`, ...) carry no value in their message,
but callers still never print `msg`: only `loc` and `type`.
"""

import re
from datetime import time, timedelta
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, PlainValidator, StringConstraints
from pydantic_core import PydanticCustomError

HOUR = re.compile(r"([0-9]{1,2}):([0-9]{2})")
DURATION = re.compile(r"([0-9]+)([mh])")


def _parse_hour(value: object) -> time:
    """`H:MM` or `HH:MM`, 00:00 to 23:59. Anything else, a number included, is `hour_format`."""
    match = HOUR.fullmatch(value) if isinstance(value, str) else None
    if match is None or int(match[1]) > 23 or int(match[2]) > 59:
        raise PydanticCustomError("hour_format", "hour must be H:MM or HH:MM")
    return time(int(match[1]), int(match[2]))


def _parse_duration(value: object) -> timedelta:
    """`<n>m` or `<n>h` with `n >= 0`. A number too long for `int` or `timedelta` is an error."""
    match = DURATION.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise PydanticCustomError("duration_format", "duration must be like 30m or 2h")
    try:
        amount = int(match[1])
        return timedelta(minutes=amount) if match[2] == "m" else timedelta(hours=amount)
    except ValueError, OverflowError:
        # `int` refuses more than 4300 digits; `timedelta` overflows past ~2.7e9 days.
        raise PydanticCustomError("duration_format", "duration out of range") from None


def _parse_positive_duration(value: object) -> timedelta:
    duration = _parse_duration(value)
    if duration <= timedelta(0):
        raise PydanticCustomError("duration_not_positive", "duration must be greater than 0")
    return duration


Hour = Annotated[time, PlainValidator(_parse_hour)]
Duration = Annotated[timedelta, PlainValidator(_parse_duration)]
PositiveDuration = Annotated[timedelta, PlainValidator(_parse_positive_duration)]
# Pydantic checks `pattern` with its Rust engine, where `$` is the end of the text: `"a\n"` fails.
Id = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$")]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
Priority = Literal["high", "medium", "low"]
Moment = Literal["morning", "midday", "afternoon", "evening"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
Count = Annotated[int, Field(ge=0)]
Usd = Annotated[float, Field(ge=0, allow_inf_nan=False)]

ToolName = Literal[
    "read_calendar",
    "read_tasks",
    "read_apex_today",
    "propose_plan",
    "propose_change",
    "log_idea",
]
# No "with button" level: what needs Pedro's button are code functions, not agent tools
# (decision 55). A tool missing from `tool_levels` is blocked (task 1.11 enforces it).
ToolLevel = Literal["free", "blocked"]

TOOL_NAMES: tuple[str, ...] = get_args(ToolName)
"""The agent's tools, as in the table of the hito 1 specification."""


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class Window(_Strict):
    """A span of the day; `start` may be later than `end` when it crosses midnight."""

    start: Hour = Field(alias="from")
    end: Hour = Field(alias="to")


class Habit(_Strict):
    id: Id
    name: NonEmptyStr
    priority: Priority
    duration: PositiveDuration
    moment: Moment
    todoist_task: NonEmptyStr
    training: bool = False
    """The training (task 1.15): the workout of today in APEX counts as done, and its key task
    offers [Ya entrené] instead of [Hecho]. At most one habit has it."""


class _Intention(_Strict):
    id: Id
    purpose: NonEmptyStr
    default_time: Hour
    priority: Priority
    enabled: bool = True


class DailyPlan(_Intention):
    type: Literal["daily_plan"]
    window: Window | None = None


class DayClose(_Intention):
    type: Literal["day_close"]
    window: Window | None = None


class KeyTask(_Intention):
    """A habit that also insists; its Todoist task name comes from the habit."""

    type: Literal["key_task"]
    habit: Id
    grace: Duration
    max_per_day: Count


class Notice(_Intention):
    type: Literal["notice"]
    days: Annotated[list[Weekday], Field(min_length=1)]


Intention = Annotated[DailyPlan | DayClose | KeyTask | Notice, Field(discriminator="type")]


class Intentions(_Strict):
    """`intenciones.yaml`. Habits and intentions have separate id spaces."""

    intentions: list[Intention]
    habits: list[Habit]


class Budget(_Strict):
    monthly_target_usd: Usd
    warn_at: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    daily_cap_usd: Usd
    max_session_usd: Usd


class Limits(_Strict):
    """`limites.yaml`. The whole file is the protected zone: talking, it only gets more discreet.

    Every field is required: the protected zone never falls back to a default in silence.
    """

    max_messages_per_day: Count
    quiet_hours: Window
    silent_during_events: bool
    group_window: Duration
    quiet_while_chatting: Duration
    """While Pedro wrote less than this ago, a `notice` waits (decision 1.10-c); `0m` turns it
    off. Raising it is more discreet."""
    max_insist_per_task: Count
    max_deletions_per_day: Count
    budget: Budget
    tool_levels: dict[ToolName, ToolLevel]
    # Its senders and keywords arrive with the mail, in hito 4; until then it must be empty.
    sensitive_categories: Annotated[dict[str, str], Field(max_length=0)]


def is_free(limits: Limits, name: str) -> bool:
    """Whether the agent's tool `name` is `free` in `tool_levels`: one that is missing counts as
    blocked (6.5 of the design). The guard and `allowed_tools` both ask here."""
    levels = {str(tool): level for tool, level in limits.tool_levels.items()}
    return levels.get(name) == "free"


SCHEMA_MODELS: tuple[type[BaseModel], ...] = (
    Window,
    Habit,
    DailyPlan,
    DayClose,
    KeyTask,
    Notice,
    Intentions,
    Budget,
    Limits,
)

Loc = tuple[str | int, ...]


def field_names() -> frozenset[str]:
    """Every key the schemas define, as it is written in the files (aliases included)."""
    return frozenset(
        field.alias or name for model in SCHEMA_MODELS for name, field in model.model_fields.items()
    )


def cross_check(intentions: Intentions) -> list[tuple[Loc, str]]:
    """Return `(loc, type)` for repeated ids, repeated days, `habit:` that names no habit and a
    second habit with `training: true`.

    The second appearance of an id (or of `training`) is the one reported; `habit:` looks only
    among the habits.
    """
    problems: list[tuple[Loc, str]] = []
    for section, items in (("habits", intentions.habits), ("intentions", intentions.intentions)):
        seen: set[str] = set()
        for index, item in enumerate(items):
            if item.id in seen:
                problems.append(((section, index, "id"), "duplicate_id"))
            seen.add(item.id)
    trainings = [index for index, habit in enumerate(intentions.habits) if habit.training]
    problems += [(("habits", index, "training"), "second_training") for index in trainings[1:]]
    habit_ids = {habit.id for habit in intentions.habits}
    for index, intention in enumerate(intentions.intentions):
        if isinstance(intention, KeyTask) and intention.habit not in habit_ids:
            problems.append((("intentions", index, "habit"), "unknown_habit"))
        if isinstance(intention, Notice) and len(set(intention.days)) != len(intention.days):
            problems.append((("intentions", index, "days"), "repeated_days"))
    return problems
