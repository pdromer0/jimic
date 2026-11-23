"""The scenarios of the evaluation of the plan: `evals/plan.yaml`, its schema and its sources.

The file has a **base day** and the scenarios (decision 1.14-f). The base day is the whole world
of an invented Pedro: the time, the personal files (`rutina.md`, `objetivos.md`,
`preferencias.md`, `intenciones.yaml`; `limites.yaml` is the template), the events, the open
tasks, APEX and the memory changes already saved. Each scenario says only what changes, over the
base day or over an earlier scenario (`like`, as in `evals/latido.yaml`): a list given replaces
the whole list, a file given replaces that file, and a section of APEX replaces that section.

A scenario may have messages of Pedro **before** the plan (`before`, in the same session, as he
wrote them that morning) and **after** it (`after`): `checks` grade the run of the plan and
`checks_after` the run of the last message after it. Every value is invented (decision 43).

The sources of a pass are fixed (`FixedSources`): the calendar gives the events of the day it is
asked for, Todoist its open tasks, APEX its snapshot at the moment of the clock, and the writer of
Todoist refuses: the evaluation never writes.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from jimic.clock import Clock
from jimic.evals.plan_checks import PLAN_ONLY, Check, CheckError, parse_check
from jimic.evals.scenarios import (
    EvalError,
    EventIn,
    ScenarioId,
    Source,
    TaskIn,
    fingerprint_of,
    invalid_file,
    local,
    read_document,
    template_limits,
)
from jimic.heartbeat.sources import Sources
from jimic.memory.schemas import Hour, Intentions
from jimic.memory.yaml_io import parse_yaml
from jimic.tools.apex_schema import ApexData, ApexSnapshot, build_snapshot
from jimic.tools.calendar import CalendarDay, CalendarEvent
from jimic.tools.todoist import Due, TaskList, TaskPhoto, TodoistTask, TodoistTools

PLAN_FILE = Path("evals/plan.yaml")
"""Relative to the folder it is launched from: the root of the repository."""
PROJECT = "proyecto-inventado"
PERSONAL_FILES = ("rutina.md", "objetivos.md", "preferencias.md", "intenciones.yaml")
"""The files a scenario writes; `limites.yaml` is always the template."""


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MessageIn(_Input):
    """A message of Pedro, at a local time of the day of the scenario."""

    at: Hour
    text: Annotated[str, Field(min_length=1)]


class ChangeIn(_Input):
    """A change of memory already saved (a row of `changes`), at a local moment."""

    at: datetime
    file: str
    after: str


class _State(_Input):
    events: list[EventIn] | None = None
    tasks: list[TaskIn] | None = None
    apex: dict[str, Any] | None = None
    files: dict[str, str] | None = None
    changes: list[ChangeIn] | None = None
    unavailable: dict[Source, str] | None = None


class ScenarioIn(_State):
    id: ScenarioId
    title: str
    like: ScenarioId | None = None
    now: Hour | None = None
    origin: str | None = None
    """Where it comes from: `nuevo`, or the id of the case of the pilot (`e01`…)."""
    before: list[MessageIn] = []
    after: list[MessageIn] = []
    checks: list[Any] = []
    checks_after: list[Any] = []
    why: str | None = None

    @model_validator(mode="after")
    def _graded(self) -> ScenarioIn:
        if not self.checks and not self.checks_after:
            raise ValueError("un escenario lleva checks, checks_after o los dos")
        return self


class BaseDay(_Input):
    day: date
    now: Hour
    timezone: str
    files: dict[str, str]
    events: list[EventIn] = []
    tasks: list[TaskIn] = []
    apex: dict[str, Any]
    changes: list[ChangeIn] = []


class PlanFile(_Input):
    base: BaseDay
    scenarios: list[ScenarioIn]


@dataclass(frozen=True)
class PlanScenario:
    id: str
    title: str
    origin: str | None
    now: datetime
    """Aware, in the zone of the file: the moment of the plan."""
    files: dict[str, str]
    intentions: Intentions
    events: tuple[CalendarEvent, ...]
    tasks: tuple[TodoistTask, ...]
    apex: ApexData | None
    """`None` when APEX is down."""
    unavailable: dict[str, str]
    changes: tuple[ChangeIn, ...]
    before: tuple[MessageIn, ...]
    after: tuple[MessageIn, ...]
    checks: tuple[Check, ...]
    checks_after: tuple[Check, ...]
    why: str | None
    fingerprint: str

    def at(self, moment: time) -> datetime:
        return datetime.combine(self.now.date(), moment, tzinfo=self.now.tzinfo)


@dataclass(frozen=True)
class PlanSuite:
    timezone: ZoneInfo
    limits_text: str
    scenarios: tuple[PlanScenario, ...]

    def select(self, ids: Sequence[str] | None) -> tuple[PlanScenario, ...]:
        if not ids:
            return self.scenarios
        unknown = sorted(set(ids) - {scenario.id for scenario in self.scenarios})
        if unknown:
            raise EvalError(f"escenarios desconocidos: {', '.join(unknown)}")
        return tuple(scenario for scenario in self.scenarios if scenario.id in ids)


STATE_FIELDS = ("events", "tasks", "changes", "unavailable")


def _resolve(item: ScenarioIn, earlier: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The state of `item`: the base day, then what `like` changed, then its own changes."""
    if item.like is not None and item.like not in earlier:
        raise EvalError(f"escenario {item.id}: like {item.like} no es un escenario anterior")
    inherited = earlier[item.like] if item.like is not None else {}
    own = {**inherited}
    for name in STATE_FIELDS:
        if getattr(item, name) is not None:
            own[name] = getattr(item, name)
    for name in ("files", "apex"):
        merged = {**inherited.get(name, {}), **(getattr(item, name) or {})}
        if merged:
            own[name] = merged
    earlier[item.id] = own
    return own


def _checks(path: Path, scenario: str, raw: list[Any], plan: bool) -> tuple[Check, ...]:
    checks = []
    for entry in raw:
        try:
            check = parse_check(entry)
        except CheckError as error:
            raise EvalError(f"{path}: escenario {scenario}: {error}") from None
        if not plan and check.name in PLAN_ONLY:
            raise EvalError(f"{path}: escenario {scenario}: {check.name} solo vale en el plan")
        checks.append(check)
    return tuple(checks)


def _event(event: EventIn, timezone: ZoneInfo) -> CalendarEvent:
    return CalendarEvent(
        title=event.title,
        start=local(event.start, timezone),
        end=local(event.end, timezone),
        all_day=False,
        organizer_is_me=event.organizer_is_me,
        declined=event.declined,
    )


def _task(task: TaskIn, timezone: ZoneInfo) -> TodoistTask:
    due: Due = local(task.due, timezone) if isinstance(task.due, datetime) else task.due
    return TodoistTask(
        id=task.id,
        title=task.title,
        project_id=PROJECT,
        due=due,
        is_recurring=False,
        has_subtasks=False,
        duration_minutes=task.duration_minutes,
    )


def _intentions(path: Path, scenario: str, files: dict[str, str]) -> Intentions:
    try:
        return Intentions.model_validate(parse_yaml(files["intenciones.yaml"]))
    except (KeyError, ValidationError, ValueError) as error:
        raise EvalError(
            f"{path}: escenario {scenario}: intenciones.yaml no vale ({type(error).__name__})"
        ) from None


def load_plan_suite(path: Path = PLAN_FILE) -> PlanSuite:
    """Read, validate and resolve `path`: every check against the catalog, every file of
    intentions against its schema and a `daily_plan` in it, before anything is spent."""
    raw = read_document(path)
    try:
        document = PlanFile.model_validate(raw)
    except ValidationError as error:
        raise invalid_file(path, error, raw) from None
    base = document.base
    try:
        timezone = ZoneInfo(base.timezone)
    except ZoneInfoNotFoundError, ValueError:
        raise EvalError(f"{path}: zona horaria desconocida en base.timezone") from None
    missing = sorted(set(PERSONAL_FILES) - set(base.files))
    if missing:
        raise EvalError(f"{path}: al día base le faltan ficheros: {', '.join(missing)}")
    limits_text, _ = template_limits()
    seen: set[str] = set()
    earlier: dict[str, dict[str, Any]] = {}
    scenarios = []
    for item in document.scenarios:
        if item.id in seen:
            raise EvalError(f"{path}: el escenario {item.id} está repetido")
        seen.add(item.id)
        own = _resolve(item, earlier)
        files = {**base.files, **own.get("files", {})}
        unknown = sorted(set(files) - set(PERSONAL_FILES))
        if unknown:
            raise EvalError(f"{path}: escenario {item.id}: ficheros desconocidos: {unknown}")
        intentions = _intentions(path, item.id, files)
        if not any(intention.type == "daily_plan" for intention in intentions.intentions):
            raise EvalError(f"{path}: escenario {item.id}: no hay intención daily_plan")
        down = dict(own.get("unavailable", {}))
        apex_doc = {**base.apex, **own.get("apex", {})}
        try:
            apex = None if "apex" in down else ApexData.model_validate(apex_doc)
        except ValidationError as error:
            raise EvalError(
                f"{path}: escenario {item.id}: APEX no vale ({error.error_count()} errores)"
            ) from None
        events = tuple(own.get("events", base.events))
        tasks = tuple(own.get("tasks", base.tasks))
        changes = tuple(own.get("changes", base.changes))
        now = item.now or base.now
        checks = _checks(path, item.id, item.checks, plan=True)
        checks_after = _checks(path, item.id, item.checks_after, plan=False)
        if checks_after and not item.after:
            raise EvalError(f"{path}: escenario {item.id}: checks_after sin mensajes after")
        scenarios.append(
            PlanScenario(
                id=item.id,
                title=item.title,
                origin=item.origin,
                now=datetime.combine(base.day, now, tzinfo=timezone),
                files=files,
                intentions=intentions,
                events=tuple(_event(event, timezone) for event in events),
                tasks=tuple(_task(task, timezone) for task in tasks),
                apex=apex,
                unavailable=down,
                changes=changes,
                before=tuple(item.before),
                after=tuple(item.after),
                checks=checks,
                checks_after=checks_after,
                why=item.why,
                fingerprint=fingerprint_of(
                    {
                        "timezone": base.timezone,
                        "limits": limits_text,
                        "day": base.day,
                        "now": now,
                        "files": files,
                        "events": events,
                        "tasks": tasks,
                        "apex": apex_doc,
                        "changes": changes,
                        "unavailable": down,
                        "before": item.before,
                        "after": item.after,
                        "checks": [str(check) for check in checks],
                        "checks_after": [str(check) for check in checks_after],
                    }
                ),
            )
        )
    return PlanSuite(timezone, limits_text, tuple(scenarios))


# --- The fixed sources of a pass -----------------------------------------------------------------


class FixedCalendar:
    def __init__(self, scenario: PlanScenario) -> None:
        self._scenario = scenario

    async def read_day(self, day: date) -> CalendarDay:
        down = self._scenario.unavailable
        if "calendar" in down:
            return CalendarDay.unavailable(down["calendar"])
        timezone = self._scenario.now.tzinfo
        events = [
            event
            for event in self._scenario.events
            if isinstance(event.start, datetime) and event.start.astimezone(timezone).date() == day
        ]
        return CalendarDay(available=True, calendar_count=1, events=events)


class FixedTasks:
    """The open tasks of the scenario: those of today and the overdue ones."""

    def __init__(self, scenario: PlanScenario) -> None:
        self._scenario = scenario

    def _list(self) -> TaskList:
        down = self._scenario.unavailable
        if "tasks" in down:
            return TaskList.unavailable(down["tasks"])
        return TaskList(available=True, tasks=list(self._scenario.tasks))

    async def read_today(self, project_id: str | None = None) -> TaskList:
        return self._list()

    async def read_project(self, project_id: str) -> TaskList:
        return self._list()

    async def get_task(self, task_id: str) -> TodoistTask | None:
        return next((task for task in self._scenario.tasks if task.id == task_id), None)

    async def get_photo(self, task_id: str) -> TaskPhoto | None:
        return None


class RefusingWriter:
    """The evaluation never writes in Todoist: anything here is a bug."""

    def __getattr__(self, name: str) -> Any:
        raise EvalError(f"la evaluación intentó escribir en Todoist ({name})")


class FixedApex:
    def __init__(self, scenario: PlanScenario, clock: Clock) -> None:
        self._scenario = scenario
        self._clock = clock

    def read_today(self) -> ApexSnapshot:
        apex = self._scenario.apex
        if apex is None:
            return ApexSnapshot.unavailable(self._scenario.unavailable.get("apex", "down"))
        timezone = self._scenario.now.tzinfo
        assert isinstance(timezone, ZoneInfo)
        return build_snapshot(apex, self._clock.now(), timezone)


async def _nothing() -> None:
    return None


def fixed_sources(scenario: PlanScenario, clock: Clock) -> Sources:
    todoist = TodoistTools(FixedTasks(scenario), RefusingWriter(), _nothing)  # pyright: ignore[reportArgumentType] - a writer that refuses
    return Sources(
        todoist=todoist, calendar=FixedCalendar(scenario), apex=FixedApex(scenario, clock)
    )
