"""«📅 Tu día» of the plan of the day (decision 1.14-b): what is already on today, as the code
reads it from Todoist and the calendar. Pure: no clock, no I/O.

- The events that take part of today and that Pedro did not decline, and the open tasks **due
  today** (the overdue ones only appear as proposals: moving or deleting them). An event that
  began before today goes with the all-day ones, without its time.
- The all-day ones (an all-day event, a task without a time) go first; then by time. At the same
  time, events before tasks, and then by title.
- In a late plan (H1-7) only the rest of the day stays: the events that have not ended and the
  tasks without a time or not yet due. The timed tasks of today already due are counted apart:
  «N cosas de esta mañana sin marcar» (the close looks at them).
- The icon of an entry: the table of `## Iconos` of `preferencias.md`, then the agent's
  (`day_icons`), then `▫️`. Titles are compared without case and with spaces joined.

A source that is down gives no entries and says so (decision 1.7-c): «no tasks» and «Todoist does
not answer» are not the same.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from jimic.clock import local_day_bounds
from jimic.tools.calendar import CalendarDay
from jimic.tools.todoist import TaskList

DEFAULT_ICON = "▫️"
MAX_TITLE = 60
"""Characters of a title in «Tu día»: one line on the phone."""


def normalized(title: str) -> str:
    """A title as it is compared: without case and with its spaces joined."""
    return " ".join(title.split()).casefold()


def clip(title: str, length: int = MAX_TITLE) -> str:
    line = " ".join(title.split())
    return line if len(line) <= length else line[: length - 1].rstrip() + "…"


def hour(moment: datetime | time) -> str:
    """`9:50`, `14:00`: as the plan and its buttons write a time."""
    return f"{moment.hour}:{moment.minute:02d}"


@dataclass(frozen=True)
class DayEntry:
    title: str
    start: datetime | None
    """Local; `None` for an all-day event or a task without a time."""
    is_event: bool


@dataclass(frozen=True)
class DayView:
    entries: tuple[DayEntry, ...]
    unmarked: int
    """In a late plan, today's timed tasks already due and still open."""
    calendar_down: bool
    tasks_down: bool


def _order(entry: DayEntry) -> tuple[bool, datetime | None, bool, str]:
    return (
        entry.start is not None,
        entry.start,
        not entry.is_event,
        normalized(entry.title),
    )


def day_view(calendar: CalendarDay, tasks: TaskList, now: datetime, late: bool) -> DayView:
    """What «Tu día» shows at `now` (aware, local)."""
    timezone = now.tzinfo
    assert isinstance(timezone, ZoneInfo)
    today = now.date()
    entries: list[DayEntry] = []
    for event in calendar.events if calendar.available else []:
        if event.declined:
            continue
        if event.all_day or not isinstance(event.start, datetime):
            entries.append(DayEntry(event.title, None, True))
            continue
        assert isinstance(event.end, datetime)
        start, end = event.start.astimezone(timezone), event.end.astimezone(timezone)
        day_start, day_end = local_day_bounds(today, timezone)
        if start >= day_end or end <= day_start:
            continue  # not today
        if late and end <= now:
            continue
        # One that began before today is under way: it goes with the all-day ones, without time.
        entries.append(DayEntry(event.title, start if start >= day_start else None, True))
    unmarked = 0
    for task in tasks.tasks if tasks.available else []:
        if task.due is None:
            continue
        if isinstance(task.due, datetime):
            start = task.due.astimezone(timezone)
            if start.date() != today:
                continue
            if late and start < now:
                unmarked += 1
                continue
            entries.append(DayEntry(task.title, start, False))
        elif isinstance(task.due, date) and task.due == today:
            entries.append(DayEntry(task.title, None, False))
    entries.sort(key=_order)
    return DayView(tuple(entries), unmarked, not calendar.available, not tasks.available)


def icon_of(title: str, table: dict[str, str], agent: dict[str, str]) -> str:
    """The table first, then the agent, then `▫️`; both keyed by `normalized`."""
    key = normalized(title)
    return table.get(key) or agent.get(key) or DEFAULT_ICON


def entry_line(entry: DayEntry, table: dict[str, str], agent: dict[str, str]) -> str:
    icon = icon_of(entry.title, table, agent)
    when = "" if entry.start is None else f"{hour(entry.start)} "
    return f"{icon} {when}{clip(entry.title)}"
