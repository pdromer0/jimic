"""The fixed catalog of the checks of the evaluation of the plan (decision 1.14-f).

Each check is a function of the data of a run, never of its text: what the agent proposed (the
items of `propose_plan`, with their snapshot), the fields of the plan, the plan as the code
painted it, and the day of the scenario (its tasks, events and habits). A scenario declares its
checks in `evals/plan.yaml` (`no_overlap`, `{max_new: 4}`, `{creates: [Revisar ofertas, "14:30",
"15:00"]}`); the file is checked against this catalog when it is loaded, before spending.

Rules shared by the checks:

- Titles are compared without case and with their spaces joined (`normalized`); «contains» is a
  substring of the normalized title.
- Times are local. An interval is half-open, `[start, end)`: a meeting of 10:00 to 11:00 and a
  task at 11:00 do not overlap. A task or a proposal with a time and no duration is an instant,
  which overlaps an interval that contains it (`start <= t < end`) or the same instant.
- `no_overlap` compares what the run proposes with a time today (to create, or to move) with the
  events of today with a time that Pedro did not decline, with the tasks of today with a time, and
  with each other. A task that the run moves or deletes leaves its slot free, and an event with
  the same title as what is proposed is that same thing, not a clash (a task moved to the time of
  its event, H1-9).
- `no_duplicates` compares **titles only** (with the open tasks that stay, the events and the
  other creations): «by intention» («Andar 5 km» and «Paseo 5 km») is no function of the data, so
  the scenarios that test it say so with `deletes_exactly` or `no_create_title`.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from jimic.agent.proposals import parse_due
from jimic.memory.schemas import Habit
from jimic.plan.day import normalized
from jimic.tools.calendar import CalendarEvent
from jimic.tools.todoist import TodoistTask


class CheckError(ValueError):
    """A check of the file that is not in the catalog or has the wrong arguments."""


@dataclass(frozen=True)
class Check:
    name: str
    args: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"{self.name}({', '.join(self.args)})" if self.args else self.name


@dataclass(frozen=True)
class CheckInput:
    """What one graded run gives the checks."""

    now: datetime
    """Aware and local: the moment of the run."""
    items: Sequence[dict[str, Any]]
    tasks: Sequence[TodoistTask]
    events: Sequence[CalendarEvent]
    habits: Sequence[Habit]
    plan: dict[str, Any] = field(default_factory=dict)
    painted: str | None = None
    """The plan as the code painted it; `None` for a run that is not the plan."""
    memory_proposals: int = 0
    """Changes of memory and ideas proposed in **every** run of the scenario."""

    @property
    def timezone(self) -> ZoneInfo:
        assert isinstance(self.now.tzinfo, ZoneInfo)
        return self.now.tzinfo

    @property
    def today(self) -> date:
        return self.now.date()


type Span = tuple[datetime, datetime]
"""`[start, end)`; an instant has `start == end`."""
type Titled = tuple[Span, str]
"""A span and the normalized title of what fills it."""


def _creates(data: CheckInput) -> list[dict[str, Any]]:
    return [item for item in data.items if item["action"] == "create"]


def _of(data: CheckInput, action: str) -> list[dict[str, Any]]:
    return [item for item in data.items if item["action"] == action]


def _due(item: dict[str, Any], timezone: ZoneInfo) -> date | datetime:
    value = parse_due(str(item["due"]))
    return value.replace(tzinfo=timezone) if isinstance(value, datetime) else value


def _day_of(value: date | datetime, timezone: ZoneInfo) -> date:
    return value.astimezone(timezone).date() if isinstance(value, datetime) else value


def _span(start: datetime, minutes: int | None) -> Span:
    return start, start + timedelta(minutes=minutes or 0)


def _overlap(a: Span, b: Span) -> bool:
    (a1, a2), (b1, b2) = a, b
    if a1 == a2 and b1 == b2:
        return a1 == b1
    if a1 == a2:
        return b1 <= a1 < b2
    if b1 == b2:
        return a1 <= b1 < a2
    return a1 < b2 and b1 < a2


def _task(data: CheckInput, task_id: str) -> TodoistTask | None:
    return next((task for task in data.tasks if task.id == task_id), None)


def _touched(data: CheckInput) -> set[str]:
    return {str(item["task_id"]) for item in data.items if item["action"] in ("move", "delete")}


def _proposed_spans(data: CheckInput) -> list[Titled]:
    spans = []
    for item in data.items:
        if item["action"] not in ("create", "move"):
            continue
        due = _due(item, data.timezone)
        if not isinstance(due, datetime) or due.date() != data.today:
            continue
        if item["action"] == "create":
            minutes, title = item.get("duration_minutes"), str(item["title"])
        else:
            task = _task(data, str(item["task_id"]))
            minutes = task.duration_minutes if task is not None else None
            title = task.title if task is not None else str(item["snapshot"]["title"])
        spans.append((_span(due, minutes), normalized(title)))
    return spans


def _fixed_spans(data: CheckInput) -> list[Titled]:
    spans = []
    for event in data.events:
        if event.declined or event.all_day or not isinstance(event.start, datetime):
            continue
        assert isinstance(event.end, datetime)
        span = (event.start.astimezone(data.timezone), event.end.astimezone(data.timezone))
        spans.append((span, normalized(event.title)))
    touched = _touched(data)
    for task in data.tasks:
        if task.id in touched or not isinstance(task.due, datetime):
            continue
        start = task.due.astimezone(data.timezone)
        if start.date() == data.today:
            spans.append((_span(start, task.duration_minutes), normalized(task.title)))
    return spans


def _habit(data: CheckInput, habit_id: str) -> Habit:
    found = next((habit for habit in data.habits if habit.id == habit_id), None)
    if found is None:
        raise CheckError(f"el hábito {habit_id} no está en intenciones.yaml")
    return found


def _names(habit: Habit) -> tuple[str, str]:
    return normalized(habit.name), normalized(habit.todoist_task)


def _mentions(title: str, habit: Habit) -> bool:
    text = normalized(title)
    return any(name in text for name in _names(habit))


def _hhmm(text: str) -> time:
    return time.fromisoformat(text if len(text) == 5 else f"0{text}")


# --- The checks --------------------------------------------------------------------------------


def includes_habit(data: CheckInput, habit_id: str) -> bool:
    """A creation whose title contains the habit, or a move to today of a task that does."""
    habit = _habit(data, habit_id)
    if any(_mentions(str(item["title"]), habit) for item in _creates(data)):
        return True
    for item in _of(data, "move"):
        task = _task(data, str(item["task_id"]))
        title = task.title if task is not None else str(item["snapshot"]["title"])
        if (
            _mentions(title, habit)
            and _day_of(_due(item, data.timezone), data.timezone) == data.today
        ):
            return True
    return False


def left_out_has(data: CheckInput, habit_id: str) -> bool:
    _habit(data, habit_id)
    return any(entry.get("habit") == habit_id for entry in data.plan.get("left_out", []))


def includes_or_left_out(data: CheckInput, habit_id: str) -> bool:
    return includes_habit(data, habit_id) or left_out_has(data, habit_id)


def left_out_or_absent(data: CheckInput, habit_id: str) -> bool:
    """Not created nor moved to today: left out, or simply absent."""
    return not includes_habit(data, habit_id)


def max_new(data: CheckInput, count: str) -> bool:
    return len(_creates(data)) <= int(count)


def no_overlap(data: CheckInput) -> bool:
    proposed, fixed = _proposed_spans(data), _fixed_spans(data)
    for index, (span, title) in enumerate(proposed):
        if any(_overlap(span, other) for other, name in fixed if name != title):
            return False
        if any(_overlap(span, other) for other, _ in proposed[index + 1 :]):
            return False
    return True


def no_duplicates(data: CheckInput) -> bool:
    deleted = {str(item["task_id"]) for item in _of(data, "delete")}
    taken = {normalized(task.title) for task in data.tasks if task.id not in deleted}
    taken |= {normalized(event.title) for event in data.events}
    for item in _creates(data):
        title = normalized(str(item["title"]))
        if title in taken:
            return False
        taken.add(title)
    return True


def due_from_now(data: CheckInput) -> bool:
    """What is created or moved goes from now on: a time not before now, a day not before
    today."""
    for item in data.items:
        if item["action"] not in ("create", "move"):
            continue
        due = _due(item, data.timezone)
        if isinstance(due, datetime) and due < data.now:
            return False
        if _day_of(due, data.timezone) < data.today:
            return False
    return True


def no_move(data: CheckInput, task_id: str) -> bool:
    return all(str(item["task_id"]) != task_id for item in _of(data, "move"))


def no_item_on(data: CheckInput, *task_ids: str) -> bool:
    return not (_touched(data) & set(task_ids))


def deletes_exactly(data: CheckInput, *task_ids: str) -> bool:
    return {str(item["task_id"]) for item in _of(data, "delete")} == set(task_ids)


def no_delete(data: CheckInput, task_id: str) -> bool:
    return all(str(item["task_id"]) != task_id for item in _of(data, "delete"))


def no_deletes(data: CheckInput) -> bool:
    return not _of(data, "delete")


def moves(data: CheckInput, task_id: str, target: str) -> bool:
    """A move of the task to `hoy`, `mañana` or `HH:MM` today."""
    for item in _of(data, "move"):
        if str(item["task_id"]) != task_id:
            continue
        due = _due(item, data.timezone)
        day = _day_of(due, data.timezone)
        if target == "hoy" and day == data.today:
            return True
        if target == "mañana" and day == data.today + timedelta(days=1):
            return True
        if ":" in target and isinstance(due, datetime) and day == data.today:
            if due.astimezone(data.timezone).time() == _hhmm(target):
                return True
    return False


def no_create_title(data: CheckInput, *titles: str) -> bool:
    banned = [normalized(title) for title in titles]
    return not any(
        any(word in normalized(str(item["title"])) for word in banned) for item in _creates(data)
    )


def creates(data: CheckInput, title: str, at: str, ends_by: str) -> bool:
    """A creation whose title contains `title`, today at `at`, that ends by `ends_by`."""
    for item in _creates(data):
        if normalized(title) not in normalized(str(item["title"])):
            continue
        due = _due(item, data.timezone)
        if not isinstance(due, datetime) or due.date() != data.today:
            continue
        start, end = _span(due, item.get("duration_minutes"))
        if start.time() == _hhmm(at) and end <= datetime.combine(
            data.today, _hhmm(ends_by), tzinfo=data.timezone
        ):
            return True
    return False


def learned_line(data: CheckInput) -> bool:
    return data.painted is not None and "\n🧠 " in data.painted


def training_line(data: CheckInput) -> bool:
    return (
        bool(data.plan.get("training_line")) and data.painted is not None and "\n🏋️ " in data.painted
    )


def no_memory_proposal(data: CheckInput) -> bool:
    return data.memory_proposals == 0


CATALOG: dict[str, tuple[Callable[..., bool], int | None]] = {
    "includes_habit": (includes_habit, 1),
    "includes_or_left_out": (includes_or_left_out, 1),
    "left_out_or_absent": (left_out_or_absent, 1),
    "max_new": (max_new, 1),
    "no_overlap": (no_overlap, 0),
    "no_duplicates": (no_duplicates, 0),
    "due_from_now": (due_from_now, 0),
    "no_move": (no_move, 1),
    "no_item_on": (no_item_on, None),
    "deletes_exactly": (deletes_exactly, None),
    "no_delete": (no_delete, 1),
    "no_deletes": (no_deletes, 0),
    "moves": (moves, 2),
    "no_create_title": (no_create_title, None),
    "creates": (creates, 3),
    "learned_line": (learned_line, 0),
    "training_line": (training_line, 0),
    "no_memory_proposal": (no_memory_proposal, 0),
}
"""Name → (function, number of arguments; `None` for one or more)."""
PLAN_ONLY = frozenset({"learned_line", "training_line"})
"""They look at the painted plan: only in the checks of the plan."""


def parse_check(raw: object) -> Check:
    """`no_overlap`, `{max_new: 4}` or `{creates: [a, b, c]}` as a `Check`; `CheckError` if it is
    not in the catalog or does not take those arguments."""
    if isinstance(raw, str):
        name, args = raw, ()
    elif isinstance(raw, dict) and len(raw) == 1:
        ((name, value),) = raw.items()
        values = value if isinstance(value, list) else [value]
        args = tuple(str(item) for item in values)
    else:
        raise CheckError(f"comprobación mal escrita: {raw!r}")
    if name not in CATALOG:
        raise CheckError(f"comprobación desconocida: {name}")
    arity = CATALOG[name][1]
    if (arity is None and not args) or (arity is not None and len(args) != arity):
        raise CheckError(f"{name} no lleva {len(args)} argumentos")
    return Check(name, args)


def run_check(check: Check, data: CheckInput) -> bool:
    function, _ = CATALOG[check.name]
    return bool(function(data, *check.args))
