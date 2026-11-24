"""What the close of the day shows and where [➡️ Mañana] puts a task (task 1.16). Pure: no
clock, no I/O; everything comes in already read.

- **✅ Hecho:** the tasks completed today (by their completion date, whatever their due), and the
  key tasks done by Pedro's button or by APEX (decision 1.16 notes), by the habit's name and
  without repeating a task already listed. A key task that is no longer today's (the jump,
  decision 142) is not done and is not shown.
- **⏳ Sin marcar:** the open tasks due **today** (not the overdue ones of earlier days), by
  time, except the task of a key task already done.
- **Mañana:** the same time tomorrow; if it clashes with a timed event Pedro did not decline or
  a timed task of tomorrow, it jumps to the end of what it clashes with, until it fits. Intervals
  are half-open (a task at 14:15 does not clash with one that ends at 14:15); a task without a
  duration takes `NO_DURATION` (on both sides). With no room before midnight, tomorrow without a
  time; a task without a time today goes to tomorrow without a time.
- **🔮 Mañana:** the first timed event of tomorrow, else the first all-day one, else «calendario
  libre»; and the workout APEX plans for tomorrow.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from jimic.clock import local_day_bounds
from jimic.execution.rules import due_day
from jimic.memory.schemas import Window
from jimic.plan.day import clip, hour, normalized
from jimic.tools.apex_schema import ApexSnapshot
from jimic.tools.calendar import CalendarDay
from jimic.tools.todoist import Due, TaskList, TodoistTask

NO_DURATION = timedelta(minutes=15)
"""The slot of a timed task without a duration when looking for room tomorrow."""
FREE_DAY = "calendario libre"
CALENDAR_DOWN = "no puedo leer el calendario"
NO_TRAINING = "sin entreno previsto"
TOMORROW_TITLE = 40
"""Characters of the event in «🔮 Mañana»: the line stays one line."""

type Span = tuple[datetime, datetime]


@dataclass(frozen=True)
class KeyDone:
    """A key task done today by the button or by APEX (decision 1.15-e)."""

    name: str
    """The habit's name, as «✅ Hecho» shows it."""
    task_ids: frozenset[str] = frozenset()
    """The tasks of today it covers: its open task (done by APEX) or the one the button closed."""


@dataclass(frozen=True)
class CloseView:
    done: tuple[str, ...]
    """The lines of «✅ Hecho» (titles)."""
    pending: tuple[TodoistTask, ...]
    """The open tasks of today, by time."""
    done_ids: frozenset[str]
    """The tasks done today: completed in Todoist, or covered by a key task done."""
    marked_at: dict[str, datetime] = field(default_factory=dict)
    """When each completed task was marked (Todoist's `completed_at`)."""
    tasks_down: bool = False


def _when(task: TodoistTask) -> tuple[bool, time]:
    """All-day ones first, then by time."""
    due = task.due
    if isinstance(due, datetime):
        return True, due.time()
    return False, time.min


def close_view(
    open_tasks: TaskList, completed: TaskList, keys: Sequence[KeyDone], today: date
) -> CloseView:
    """What the close shows on `today` (local)."""
    if not open_tasks.available:
        return CloseView((), (), frozenset(), tasks_down=True)
    done_tasks = sorted(
        completed.tasks if completed.available else [], key=lambda t: (*_when(t), t.id)
    )
    covered = frozenset(task_id for key in keys for task_id in key.task_ids)
    done_ids = frozenset(t.id for t in done_tasks) | covered
    titles = [t.title for t in done_tasks]
    listed = {normalized(title) for title in titles}
    for key in keys:
        if normalized(key.name) not in listed:
            titles.append(key.name)
            listed.add(normalized(key.name))
    pending = tuple(
        task
        for task in open_tasks.tasks
        if task.due is not None and due_day(task.due) == today and task.id not in covered
    )
    marked = {t.id: t.completed_at for t in done_tasks if t.completed_at is not None}
    return CloseView(tuple(titles), pending, done_ids, marked)


def morning_after(day: date, quiet: Window, timezone: ZoneInfo) -> datetime:
    """«La mañana siguiente» of the close of `day` (decision 1.16-b, as task 1.16 reads it): the
    end of the quiet hours of the next day. Its buttons and its rating last until then, and the
    definitive figure of «plan cumplido» is worked out from then on."""
    return datetime.combine(day + timedelta(days=1), quiet.end, tzinfo=timezone)


# --- [➡️ Mañana] ---------------------------------------------------------------------------------


def _span(start: datetime, minutes: int | None) -> Span:
    return start, start + (timedelta(minutes=minutes) if minutes else NO_DURATION)


def busy_spans(
    calendar: CalendarDay, tasks: TaskList, day: date, timezone: ZoneInfo, but: str | None = None
) -> list[Span]:
    """What takes time on `day`: the timed events Pedro did not decline and the timed tasks
    (except `but`, the one being moved)."""
    spans: list[Span] = []
    for event in calendar.events:
        if event.declined or event.all_day or not isinstance(event.start, datetime):
            continue
        assert isinstance(event.end, datetime)
        spans.append((event.start.astimezone(timezone), event.end.astimezone(timezone)))
    for task in tasks.tasks:
        if task.id == but or not isinstance(task.due, datetime):
            continue
        start = task.due.astimezone(timezone)
        if start.date() == day:
            spans.append(_span(start, task.duration_minutes))
    return spans


def next_free(start: datetime, minutes: int | None, busy: Iterable[Span]) -> datetime | None:
    """The first moment from `start` on its day where the task fits without clashing; `None` if
    there is none before midnight."""
    taken = list(busy)
    timezone = start.tzinfo
    assert isinstance(timezone, ZoneInfo)
    end_of_day = local_day_bounds(start.date(), timezone)[1]
    moment = start
    while moment < end_of_day:
        begin, end = _span(moment, minutes)
        clashes = [span for span in taken if span[0] < end and begin < span[1]]
        if not clashes:
            return moment
        moment = max(span[1] for span in clashes)
    return None


def tomorrow_due(
    task: TodoistTask, day: date, calendar: CalendarDay, tomorrow_tasks: TaskList
) -> Due:
    """Where [Mañana] puts `task` of the close of `day`: the same time on the next day, or the
    next free moment; without a time (or without room), the next day."""
    tomorrow = day + timedelta(days=1)
    if not isinstance(task.due, datetime):
        return tomorrow
    timezone = task.due.tzinfo
    assert isinstance(timezone, ZoneInfo)
    start = datetime.combine(tomorrow, task.due.astimezone(timezone).time(), tzinfo=timezone)
    busy = busy_spans(calendar, tomorrow_tasks, tomorrow, timezone, but=task.id)
    found = next_free(start, task.duration_minutes, busy)
    return tomorrow if found is None else found


# --- 🔮 Mañana --------------------------------------------------------------------------------


def _first_event(calendar: CalendarDay, tomorrow: date, timezone: ZoneInfo) -> str:
    if not calendar.available:
        return CALENDAR_DOWN
    events = [event for event in calendar.events if not event.declined]
    timed = sorted(
        (
            (event.start.astimezone(timezone), event.title)
            for event in events
            if not event.all_day
            and isinstance(event.start, datetime)
            and event.start.astimezone(timezone).date() == tomorrow
        ),
        key=lambda pair: (pair[0], normalized(pair[1])),
    )
    if timed:
        moment, title = timed[0]
        return f"{hour(moment)} {clip(title, TOMORROW_TITLE)}"
    others = sorted(normalized(event.title) for event in events)
    if others:
        first = next(e.title for e in events if normalized(e.title) == others[0])
        return clip(first, TOMORROW_TITLE)
    return FREE_DAY


def tomorrow_line(
    calendar: CalendarDay, apex: ApexSnapshot, tomorrow: date, timezone: ZoneInfo
) -> str:
    """`🔮 Mañana: 9:00 Dentista · 🏋️ Pierna · fuerza`, in one line."""
    parts = [_first_event(calendar, tomorrow, timezone)]
    if apex.available:
        planned = apex.planned_tomorrow
        parts.append(f"🏋️ {clip(planned, TOMORROW_TITLE) if planned else NO_TRAINING}")
    return f"🔮 Mañana: {' · '.join(parts)}"
