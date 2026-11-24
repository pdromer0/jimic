"""The hard rules of the plan that are a count of hours (decision 1.16-e): one function each, shared
by `propose_plan` (which refuses them to the agent in the plan of the day) and by the catalog of the
evaluation (`jimic.evals.plan_checks`), so both count them the same way.

The items are those of `propose_plan` as data (`action`, `due`, `title`, `duration_minutes`,
`task_id`, and the `snapshot` of a move or a delete). Times are local:

- An interval is half-open, `[start, end)`: a meeting of 10:00 to 11:00 and a task at 11:00 do not
  overlap. A task or a proposal with a time and no duration is an instant, which overlaps an
  interval that contains it (`start <= t < end`) or the same instant.
- What is compared: what the items propose with a time **today** (to create, or to move) against
  the events of today with a time that Pedro did not decline, the tasks of today with a time, and
  each other. A task that the items move or delete leaves its slot free, and an event with the same
  title as what is proposed is that same thing, not a clash (a task moved to the time of its event,
  H1-9).
- An item starts before now when its time is before now, or its day before today.

`refusals` is what `propose_plan` tells the agent in the plan of the day: besides a clash and the
past, the task of a habit taken to a day not in its `days` (decision F2-a) and what touches a task
of today whose time has passed. That one, H1-7 as decision F2-b refines it: a task that is not a
habit's is the close's, untouched; the task of a habit may be moved, later today (to a free slot,
since the past and the clashes still apply) or to another of its days, never deleted (as the
overdue ones of 1.14-g). Titles reach only the agent, never a log.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from jimic.agent.proposals import parse_due
from jimic.memory.schemas import WEEKDAYS, Habit
from jimic.plan.day import clip, hour, normalized
from jimic.tools.calendar import CalendarEvent
from jimic.tools.todoist import TodoistTask

type Span = tuple[datetime, datetime]
"""`[start, end)`; an instant has `start == end`."""


@dataclass(frozen=True)
class Slot:
    """A span of today and the title of what fills it, as it is written."""

    span: Span
    title: str

    @property
    def key(self) -> str:
        return normalized(self.title)


@dataclass(frozen=True)
class Clash:
    """Item `index` steps on `other` (a fixed slot, or an earlier item)."""

    index: int
    other: Slot


def due_of(item: dict[str, Any], timezone: tzinfo) -> date | datetime:
    """The `due` of an item: a day, or an aware moment in `timezone`."""
    value = parse_due(str(item["due"]))
    return value.replace(tzinfo=timezone) if isinstance(value, datetime) else value


def day_of(value: date | datetime, timezone: tzinfo) -> date:
    return value.astimezone(timezone).date() if isinstance(value, datetime) else value


def span_of(start: datetime, minutes: int | None) -> Span:
    return start, start + timedelta(minutes=minutes or 0)


def overlap(a: Span, b: Span) -> bool:
    (a1, a2), (b1, b2) = a, b
    if a1 == a2 and b1 == b2:
        return a1 == b1
    if a1 == a2:
        return b1 <= a1 < b2
    if b1 == b2:
        return a1 <= b1 < a2
    return a1 < b2 and b1 < a2


def find_task(tasks: Iterable[TodoistTask], task_id: str) -> TodoistTask | None:
    return next((task for task in tasks if task.id == task_id), None)


def touched(items: Iterable[dict[str, Any]]) -> set[str]:
    """The tasks that the items move or delete."""
    return {str(item["task_id"]) for item in items if item["action"] in ("move", "delete")}


def proposed_slots(
    items: Sequence[dict[str, Any]], tasks: Sequence[TodoistTask], now: datetime
) -> list[tuple[int, Slot]]:
    """`(index, slot)` of each item that creates or moves something with a time today. A moved
    task takes its duration from `tasks`; without it there, it is an instant."""
    timezone = _zone(now)
    slots = []
    for index, item in enumerate(items):
        if item["action"] not in ("create", "move"):
            continue
        due = due_of(item, timezone)
        if not isinstance(due, datetime) or due.date() != now.date():
            continue
        if item["action"] == "create":
            minutes, title = item.get("duration_minutes"), str(item["title"])
        else:
            task = find_task(tasks, str(item["task_id"]))
            minutes = task.duration_minutes if task is not None else None
            title = task.title if task is not None else str(item["snapshot"]["title"])
        slots.append((index, Slot(span_of(due, minutes), title)))
    return slots


def fixed_slots(
    events: Sequence[CalendarEvent],
    tasks: Sequence[TodoistTask],
    freed: set[str],
    now: datetime,
) -> list[Slot]:
    """The events of today with a time that Pedro did not decline, and the tasks of today with a
    time that are not in `freed`."""
    timezone = _zone(now)
    slots = []
    for event in events:
        if event.declined or event.all_day or not isinstance(event.start, datetime):
            continue
        assert isinstance(event.end, datetime)
        span = (event.start.astimezone(timezone), event.end.astimezone(timezone))
        slots.append(Slot(span, event.title))
    for task in tasks:
        if task.id in freed or not isinstance(task.due, datetime):
            continue
        start = task.due.astimezone(timezone)
        if start.date() == now.date():
            slots.append(Slot(span_of(start, task.duration_minutes), task.title))
    return slots


def clashes(
    items: Sequence[dict[str, Any]],
    tasks: Sequence[TodoistTask],
    events: Sequence[CalendarEvent],
    now: datetime,
) -> list[Clash]:
    """Each item that steps on something, with the first thing it steps on: a fixed slot (not one
    with its same title), else an earlier item. `now` is aware and local: it says which day is
    today."""
    proposed = proposed_slots(items, tasks, now)
    fixed = fixed_slots(events, tasks, touched(items), now)
    found = []
    for position, (index, slot) in enumerate(proposed):
        other = next(
            (
                fixed_slot
                for fixed_slot in fixed
                if fixed_slot.key != slot.key and overlap(slot.span, fixed_slot.span)
            ),
            None,
        )
        if other is None:
            other = next(
                (earlier for _, earlier in proposed[:position] if overlap(slot.span, earlier.span)),
                None,
            )
        if other is not None:
            found.append(Clash(index, other))
    return found


def starts_before(item: dict[str, Any], now: datetime) -> bool:
    """A creation or a move whose time is before `now`, or whose day is before today."""
    if item["action"] not in ("create", "move"):
        return False
    timezone = _zone(now)
    due = due_of(item, timezone)
    if isinstance(due, datetime) and due < now:
        return True
    return day_of(due, timezone) < now.date()


def mentions(title: str, habit: Habit) -> bool:
    """Whether `title` is the task of `habit`: it contains its name or its `todoist_task`."""
    text = normalized(title)
    return any(name in text for name in (normalized(habit.name), normalized(habit.todoist_task)))


DAY_NAMES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
NAME_OF = dict(zip(WEEKDAYS, DAY_NAMES, strict=True))


def _when(span: Span) -> str:
    start, end = span
    return f"a las {hour(start)}" if start == end else f"{hour(start)}–{hour(end)}"


def _title_of(item: dict[str, Any], tasks: Sequence[TodoistTask]) -> str:
    if item["action"] == "create":
        return str(item["title"])
    task = find_task(tasks, str(item["task_id"]))
    return task.title if task is not None else str(item["snapshot"]["title"])


def _passed_today(task: TodoistTask | None, now: datetime) -> bool:
    """A task of today with a time before now."""
    if task is None or not isinstance(task.due, datetime):
        return False
    due = task.due.astimezone(_zone(now))
    return due.date() == now.date() and due < now


def refusals(
    items: Sequence[dict[str, Any]],
    start: int,
    tasks: Sequence[TodoistTask],
    events: Sequence[CalendarEvent],
    habits: Sequence[Habit],
    now: datetime,
) -> list[str]:
    """Why the items from `start` on (those of this call; the earlier ones were accepted) may not
    go in the plan of the day, one text per item, numbered from 1 at `start`. `tasks` are those of
    today and the ones the items move or delete; `now` is aware and local."""
    timezone = _zone(now)
    found: dict[int, list[str]] = {}
    for index in range(start, len(items)):
        item = items[index]
        why = found.setdefault(index, [])
        if item["action"] in ("move", "delete"):
            task = find_task(tasks, str(item["task_id"]))
            if task is not None and _passed_today(task, now):
                assert isinstance(task.due, datetime)
                was = f"«{clip(task.title)}» era hoy a las {hour(task.due.astimezone(timezone))}"
                if not any(mentions(task.title, habit) for habit in habits):
                    why.append(f"{was} y ya pasó: lo ve el cierre, no la toques")
                elif item["action"] == "delete":
                    why.append(f"{was}: no la borres, muévela a un hueco libre de más tarde")
        if starts_before(item, now):
            why.append(f"empieza antes de ahora ({hour(now)})")
        if item["action"] in ("create", "move"):
            title = _title_of(item, tasks)
            day = day_of(due_of(item, timezone), timezone)
            for habit in habits:
                if habit.days is not None and mentions(title, habit) and not habit.on(day):
                    names = ", ".join(NAME_OF[name] for name in habit.days)
                    why.append(
                        f"«{clip(habit.name)}» no toca el {DAY_NAMES[day.weekday()]} (sus días:"
                        f" {names})"
                    )
    for clash in clashes(items, tasks, events, now):
        if clash.index >= start:
            found[clash.index].append(f"pisa «{clip(clash.other.title)}» {_when(clash.other.span)}")
    return [
        f"El elemento {index - start + 1}: {'; '.join(why)}" for index, why in found.items() if why
    ]


def _zone(now: datetime) -> tzinfo:
    assert now.tzinfo is not None, "now must be aware"
    return now.tzinfo
