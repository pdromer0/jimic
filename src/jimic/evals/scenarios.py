"""The scenarios of the heartbeat's evaluation: `evals/latido.yaml`, its schema and its states.

The file has a **base day** and the scenarios. Each scenario says only what changes over the base
day, or over an earlier scenario (`like`): a change replaces the whole list (events, tasks, sent
messages, what waits) or the whole section of APEX (`meta`, `plan`, `entrenamientos`...). `like`
inherits only the state, never `now`, `expected`, `why` or `pending`.

Each scenario becomes a `DayState` through the same `build_day_state` the heartbeat uses: the
sent messages and the marks of the key tasks go to a temporary SQLite in memory (one message per
hour, decision 92), APEX goes through `build_snapshot`, and the limits are the template of
`limites.yaml`. What the code knows of a key task (task 1.15) comes from `completed_today` (tasks
completed today in Todoist), `key_marks` (the taps of the day, and `seen` for today's task the
heartbeat saw open) and `later_tasks` (open tasks of a later day: a `seen` one there jumped).
Every value of the file is invented (decision 43): real cases will live outside the repository
with this format.
"""

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from jimic.clock import local_day_bounds, to_db
from jimic.heartbeat.decide import MAX_PICKS
from jimic.heartbeat.limits import Outgoing, WaitingNotices
from jimic.heartbeat.state import DayState, build_day_state
from jimic.memory import key_marks
from jimic.memory.database import load_migrations, migrate
from jimic.memory.notices import sent_between
from jimic.memory.personal import template
from jimic.memory.schemas import Hour, Intentions, Limits
from jimic.memory.yaml_io import YamlSyntaxError, parse_yaml
from jimic.tools.apex_schema import ApexData, ApexSnapshot, build_snapshot
from jimic.tools.calendar import CalendarDay, CalendarEvent
from jimic.tools.todoist import TaskList, TodoistTask

EVALS_FILE = Path("evals/latido.yaml")
"""Relative to the folder it is launched from: the root of the repository."""
WAITING_TEXT = "(inventado)"
"""The text of what waits in the group window: the model only sees the ids."""
PROJECT = "proyecto-inventado"

type Source = Literal["calendar", "tasks", "apex"]
ScenarioId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+$")]


class EvalError(Exception):
    """The evaluation cannot run; the message says why (paths, ids and fields, never data)."""


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventIn(_Input):
    """An event with times, in local time (without a zone)."""

    title: str
    start: datetime
    end: datetime
    organizer_is_me: bool = True
    declined: bool = False


class TaskIn(_Input):
    """An open task. `due` is a day, or a moment in local time (without a zone)."""

    id: str
    title: str
    due: date | datetime
    duration_minutes: int | None = None


class SentIn(_Input):
    """A proactive message sent today. Those at the same hour went out as one message."""

    intent: str
    at: Hour


class CompletedIn(_Input):
    """A task completed today in Todoist (task 1.15)."""

    id: str
    title: str


class MarkIn(_Input):
    """A mark of a key task today (task 1.15): a tap, or `seen` with the task the heartbeat saw."""

    intent: str
    mark: Literal["going", "done", "not_today", "seen"]
    at: Hour
    task_id: str | None = None


class _State(_Input):
    """What a scenario may change over the base day; `None` is «as the base day»."""

    day: date | None = None
    events: list[EventIn] | None = None
    tasks: list[TaskIn] | None = None
    apex: dict[str, Any] | None = None
    """Sections of `apex_db.json`: each one given replaces the base day's."""
    sent_today: list[SentIn] | None = None
    waiting: list[str] | None = None
    unavailable: dict[Source, str] | None = None
    """The sources that are down, with their code."""
    completed_today: list[CompletedIn] | None = None
    key_marks: list[MarkIn] | None = None
    later_tasks: list[TaskIn] | None = None


class ScenarioIn(_State):
    id: ScenarioId
    title: str
    like: ScenarioId | None = None
    now: Hour
    expected: Annotated[list[list[str]], Field(min_length=1)]
    """The acceptable sets of intentions; `[]` is to stay silent."""
    why: str | None = None
    pending: str | None = None
    """The task that will let the scenario run: until then it is skipped."""

    @model_validator(mode="after")
    def _distinct(self) -> ScenarioIn:
        sets = [frozenset(alternative) for alternative in self.expected]
        if any(len(s) != len(a) for s, a in zip(sets, self.expected, strict=True)):
            raise ValueError("una alternativa repite una intención")
        if len(set(sets)) != len(sets):
            raise ValueError("dos alternativas son el mismo conjunto")
        if any(len(s) > MAX_PICKS for s in sets):
            raise ValueError(f"una alternativa pasa de {MAX_PICKS} intenciones")
        return self


class BaseDay(_Input):
    day: date
    timezone: str
    intentions: dict[str, Any]
    """As `intenciones.yaml`."""
    events: list[EventIn] = []
    tasks: list[TaskIn] = []
    apex: dict[str, Any]
    """As `apex_db.json`."""
    sent_today: list[SentIn] = []
    waiting: list[str] = []
    completed_today: list[CompletedIn] = []
    key_marks: list[MarkIn] = []
    later_tasks: list[TaskIn] = []


class EvalFile(_Input):
    base: BaseDay
    scenarios: list[ScenarioIn]


@dataclass(frozen=True)
class Scenario:
    """A scenario with its changes applied: everything needed to build its state."""

    id: str
    title: str
    now: datetime
    """Aware, in the zone of the file."""
    events: tuple[EventIn, ...]
    tasks: tuple[TaskIn, ...]
    apex: ApexData
    sent_today: tuple[SentIn, ...]
    waiting: tuple[str, ...]
    unavailable: dict[str, str]
    completed_today: tuple[CompletedIn, ...]
    key_marks: tuple[MarkIn, ...]
    later_tasks: tuple[TaskIn, ...]
    expected: tuple[frozenset[str], ...]
    why: str | None
    pending: str | None
    fingerprint: str
    """sha256 of what builds the state and grades it (not the title nor `why`): a report
    compares two runs only on the scenarios whose fingerprint did not change."""

    @property
    def silent_only(self) -> bool:
        """Staying silent is its only acceptable answer: it counts for «speaks when it should
        not» (decision 1.9-c)."""
        return self.expected == (frozenset(),)


@dataclass(frozen=True)
class Suite:
    timezone: ZoneInfo
    intentions: Intentions
    limits: Limits
    scenarios: tuple[Scenario, ...]

    def select(self, ids: Sequence[str] | None) -> tuple[Scenario, ...]:
        """The scenarios with these ids, in the order of the file; all of them if `None`."""
        if not ids:
            return self.scenarios
        known = {scenario.id for scenario in self.scenarios}
        unknown = sorted(set(ids) - known)
        if unknown:
            raise EvalError(f"escenarios desconocidos: {', '.join(unknown)}")
        return tuple(scenario for scenario in self.scenarios if scenario.id in ids)


def plain(value: object) -> object:
    """The YAML document as plain Python: dicts, lists and strings; dates as ISO text."""
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    if isinstance(value, date):  # `datetime` too: it is a `date`
        return value.isoformat()
    if isinstance(value, str):
        return str(value)
    return value


def _where(loc: Sequence[str | int], raw: object) -> str:
    """`escenario l08: now` for a field of a scenario, the dotted path otherwise."""
    parts = [str(part) for part in loc]
    if len(loc) >= 2 and loc[0] == "scenarios" and isinstance(loc[1], int):
        scenarios = raw.get("scenarios") if isinstance(raw, dict) else None
        if isinstance(scenarios, list) and loc[1] < len(scenarios):
            item = scenarios[loc[1]]
            name = item.get("id") if isinstance(item, dict) else None
            return f"escenario {name or loc[1]}: {'.'.join(parts[2:]) or '(entero)'}"
    return ".".join(parts) or "(fichero)"


def invalid_file(path: Path, error: ValidationError, raw: object) -> EvalError:
    lines = [f"  {_where(item['loc'], raw)}: {item['msg']}" for item in error.errors()]
    return EvalError(f"{path} no vale:\n" + "\n".join(lines))


def _as_text(value: object) -> object:
    """For `json.dumps`: models as dicts, and hours, days and moments as text."""
    if isinstance(value, BaseModel):
        return value.model_dump()
    return str(value)


def fingerprint_of(document: dict[str, Any]) -> str:
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, default=_as_text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def local(moment: datetime, timezone: ZoneInfo) -> datetime:
    return moment.replace(tzinfo=timezone) if moment.tzinfo is None else moment


def build_state(suite: Suite, scenario: Scenario) -> DayState:
    """The state of the day of `scenario`, as the heartbeat would build it."""
    tz = suite.timezone
    now = scenario.now
    day = now.date()
    down = scenario.unavailable
    calendar = (
        CalendarDay.unavailable(down["calendar"])
        if "calendar" in down
        else CalendarDay(
            available=True,
            calendar_count=1,
            events=[
                CalendarEvent(
                    title=event.title,
                    start=local(event.start, tz),
                    end=local(event.end, tz),
                    all_day=False,
                    organizer_is_me=event.organizer_is_me,
                    declined=event.declined,
                )
                for event in scenario.events
            ],
        )
    )
    tasks = (
        TaskList.unavailable(down["tasks"])
        if "tasks" in down
        else TaskList(available=True, tasks=[_task(task, tz) for task in scenario.tasks])
    )
    completed = (
        TaskList.unavailable(down["tasks"])
        if "tasks" in down
        else TaskList(
            available=True,
            tasks=[
                _task(TaskIn(id=done.id, title=done.title, due=day), tz)
                for done in scenario.completed_today
            ],
        )
    )
    jumped = frozenset(
        task.id
        for task in scenario.later_tasks
        if (task.due.date() if isinstance(task.due, datetime) else task.due) > day
    )
    apex = (
        ApexSnapshot.unavailable(down["apex"])
        if "apex" in down
        else build_snapshot(scenario.apex, now, tz)
    )
    queue = WaitingNotices()
    for intent in scenario.waiting:
        queue.add(Outgoing(intent=intent, text=WAITING_TEXT), day)
    conn = sqlite3.connect(":memory:", autocommit=True)
    try:
        migrate(conn, load_migrations())
        hours = sorted({sent.at for sent in scenario.sent_today})
        for sent in scenario.sent_today:
            moment = datetime.combine(day, sent.at, tzinfo=tz)
            conn.execute(
                "INSERT INTO notices (sent_at, intent, telegram_message_id) VALUES (?, ?, ?)",
                (to_db(moment), sent.intent, hours.index(sent.at) + 1),
            )
        sent_today = sent_between(conn, *local_day_bounds(day, tz))
        for mark in scenario.key_marks:
            moment = datetime.combine(day, mark.at, tzinfo=tz)
            if mark.mark == "seen" and mark.task_id is not None:
                key_marks.note_seen(conn, day, mark.intent, mark.task_id, moment)
            else:
                key_marks.record(conn, day, mark.intent, mark.mark, moment, mark.task_id)
        marks = key_marks.of_day(conn, day)
    finally:
        conn.close()
    return build_day_state(
        now,
        tz,
        calendar,
        tasks,
        apex,
        sent_today,
        suite.intentions,
        suite.limits,
        queue,
        completed=completed,
        marks=marks,
        jumped=jumped,
    )


def _task(task: TaskIn, tz: ZoneInfo) -> TodoistTask:
    return TodoistTask(
        id=task.id,
        title=task.title,
        project_id=PROJECT,
        due=local(task.due, tz) if isinstance(task.due, datetime) else task.due,
        is_recurring=False,
        has_subtasks=False,
        duration_minutes=task.duration_minutes,
    )


STATE_FIELDS = (
    "day",
    "events",
    "tasks",
    "sent_today",
    "waiting",
    "unavailable",
    "completed_today",
    "key_marks",
    "later_tasks",
)


def _resolve(item: ScenarioIn, earlier: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The state of `item`: the base day, then what `like` changed, then its own changes."""
    if item.like is not None and item.like not in earlier:
        raise EvalError(f"escenario {item.id}: like {item.like} no es un escenario anterior")
    inherited = earlier[item.like] if item.like is not None else {}
    changes = {
        name: getattr(item, name) for name in STATE_FIELDS if getattr(item, name) is not None
    }
    apex_changes = {**inherited.get("apex", {}), **(item.apex or {})}
    own = {**inherited, **changes}
    if apex_changes:
        own["apex"] = apex_changes
    earlier[item.id] = own
    return own


def read_document(path: Path) -> object:
    """The YAML document of `path` as plain Python; `EvalError` if it cannot be read."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise EvalError(f"no existe {path} (lánzalo desde la raíz del repositorio)") from None
    except (OSError, UnicodeDecodeError) as error:
        raise EvalError(f"no se puede leer {path} ({type(error).__name__})") from None
    try:
        return plain(parse_yaml(text))
    except YamlSyntaxError as error:
        raise EvalError(f"{path}: YAML inválido ({error.kind}, línea {error.line})") from None


def template_limits() -> tuple[str, Limits]:
    """The text of the template of `limites.yaml` and its limits: those of every evaluation."""
    text = template("limites.yaml")
    return text, Limits.model_validate(plain(parse_yaml(text)))


def load_suite(path: Path = EVALS_FILE) -> Suite:
    """Read, validate and resolve `path`. Every scenario builds its state here, and the expected
    intentions of those that are not pending must be active that day: a typo fails before
    spending. A pending one may expect what its task has not brought yet."""
    raw = read_document(path)
    try:
        document = EvalFile.model_validate(raw)
        base = document.base
        intentions = Intentions.model_validate(base.intentions)
        apex_base = ApexData.model_validate(base.apex)
    except ValidationError as error:
        raise invalid_file(path, error, raw) from None
    try:
        timezone = ZoneInfo(base.timezone)
    except ZoneInfoNotFoundError, ValueError:
        raise EvalError(f"{path}: zona horaria desconocida en base.timezone") from None
    limits_text, limits = template_limits()
    seen: set[str] = set()
    earlier: dict[str, dict[str, Any]] = {}
    scenarios = []
    for item in document.scenarios:
        if item.id in seen:
            raise EvalError(f"{path}: el escenario {item.id} está repetido")
        seen.add(item.id)
        own = _resolve(item, earlier)
        apex_doc = {**base.apex, **own.get("apex", {})}
        try:
            apex = ApexData.model_validate(apex_doc) if "apex" in own else apex_base
        except ValidationError as error:
            raise EvalError(
                f"{path}: escenario {item.id}: APEX no vale ({error.error_count()} errores)"
            ) from None
        day = own.get("day", base.day)
        state_fields = {
            "events": tuple(own.get("events", base.events)),
            "tasks": tuple(own.get("tasks", base.tasks)),
            "sent_today": tuple(own.get("sent_today", base.sent_today)),
            "waiting": tuple(own.get("waiting", base.waiting)),
            "unavailable": dict(own.get("unavailable", {})),
            "completed_today": tuple(own.get("completed_today", base.completed_today)),
            "key_marks": tuple(own.get("key_marks", base.key_marks)),
            "later_tasks": tuple(own.get("later_tasks", base.later_tasks)),
        }
        expected = tuple(frozenset(alternative) for alternative in item.expected)
        fingerprint = fingerprint_of(
            {
                "timezone": base.timezone,
                "intentions": base.intentions,
                "limits": limits_text,
                "day": day,
                "now": item.now,
                "apex": apex_doc,
                **state_fields,
                "expected": sorted(sorted(alternative) for alternative in expected),
                "pending": item.pending,
            }
        )
        scenarios.append(
            Scenario(
                id=item.id,
                title=item.title,
                now=datetime.combine(day, item.now, tzinfo=timezone),
                apex=apex,
                expected=expected,
                why=item.why,
                pending=item.pending,
                fingerprint=fingerprint,
                **state_fields,
            )
        )
    suite = Suite(timezone, intentions, limits, tuple(scenarios))
    for scenario in suite.scenarios:
        _check_expected(path, suite, scenario)
    return suite


def _check_expected(path: Path, suite: Suite, scenario: Scenario) -> None:
    try:
        state = build_state(suite, scenario)
    except ValidationError as error:
        raise EvalError(
            f"{path}: escenario {scenario.id}: el estado no se puede montar"
            f" ({error.error_count()} errores)"
        ) from None
    if scenario.pending is not None:
        return
    active = {intention.id for intention in state.intentions}
    for alternative in scenario.expected:
        missing = sorted(alternative - active)
        if missing:
            raise EvalError(
                f"{path}: escenario {scenario.id}: espera intenciones que no están activas"
                f" ese día: {', '.join(missing)}"
            )
