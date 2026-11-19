"""Todoist: a reader and a writer that are two different objects.

The agent is given **only the reader** (task 1.10): the functions that change Todoist (create,
move, complete, delete) belong to the code and run after Pedro's button or message (H1-1). A
test checks that the reader has no method that writes.

When Todoist cannot be read the list says so with a code and the service goes on (`auth`,
`timeout`, `http_503`...: a type of failure, never content); it goes to the log once. A write
that fails raises `ToolFailure` with the same kind of code: whoever executes must know.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable
from datetime import date, datetime, time
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

import httpx
from dataclass_wizard.errors import JSONWizardError
from pydantic import BaseModel, ConfigDict, Field
from todoist_api_python.models import Task

from jimic.clock import Clock
from jimic.tools.failures import MAX_PAGES, ToolFailure, TooManyPages, WarnOnce

logger = logging.getLogger(__name__)

CALL_TIMEOUT = 20.0
"""Seconds each request to Todoist may take; there are no retries. The library fixes its own
timeout inside every request (60 s to read), so this one is ours."""
PAGE_SIZE = 200
"""Tasks per page (the most Todoist gives): `MAX_PAGES` pages are up to 2000 tasks."""
MINUTES_PER_DAY = 24 * 60

type Due = date | datetime
"""A day (`date`) or a moment (`datetime` with a zone)."""


class TodoistTask(BaseModel):
    """A task. Its title and its project are Pedro's, so the `repr` hides them."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str = Field(repr=False)
    project_id: str = Field(repr=False)
    due: Due | None
    """A `date` for a day, or a `datetime` in the zone of the settings; `None` if undated."""
    is_recurring: bool
    has_subtasks: bool
    duration_minutes: int | None


class TaskList(BaseModel):
    """What Todoist says. When `available` is false only the reason is set."""

    model_config = ConfigDict(frozen=True)

    available: bool
    unavailable_reason: str | None = None
    """A code (`auth`, `timeout`, `http_503`...), never part of the content."""
    tasks: list[TodoistTask] = Field(default_factory=list, repr=False)

    @classmethod
    def unavailable(cls, reason: str) -> TaskList:
        return cls(available=False, unavailable_reason=reason)

    def to_json(self) -> str:
        """The compact JSON the model gets (no empty fields)."""
        return self.model_dump_json(exclude_none=True)


class TodoistReader(Protocol):
    """What the agent may know about Todoist: it can be read, and nothing else."""

    async def read_today(self, project_id: str | None = None) -> TaskList:
        """Today's tasks and the overdue ones (a due day up to today, in the zone of the
        settings), limited to `project_id` if given. It does not raise."""
        ...

    async def read_project(self, project_id: str) -> TaskList:
        """The active tasks of a project. It does not raise."""
        ...

    async def get_task(self, task_id: str) -> TodoistTask | None:
        """One task, or `None` if it does not exist. It is for the code that executes, not for
        the model, so it raises `ToolFailure` like the writer."""
        ...


class TodoistWriter(Protocol):
    """What only the code may do. Each one raises `ToolFailure` if it fails."""

    async def create(
        self, content: str, project_id: str, due: Due, duration_minutes: int | None = None
    ) -> TodoistTask:
        """Create a task. `due` is a day or a moment with a zone."""
        ...

    async def move(self, task_id: str, due: Due) -> None:
        """Change the day or the moment of a task."""
        ...

    async def complete(self, task_id: str) -> None:
        """Close a task; a recurring one goes to its next time instead of disappearing."""
        ...

    async def delete(self, task_id: str) -> None:
        """Delete a task for good: Todoist has no bin."""
        ...


class TodoistApi(Protocol):
    """What this module uses of `TodoistAPIAsync`, so a double can stand in for it."""

    async def get_task(self, task_id: str) -> Task: ...

    async def get_tasks(
        self,
        *,
        project_id: str | None = None,
        parent_id: str | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[list[Task]]: ...

    async def add_task(
        self,
        content: str,
        *,
        project_id: str | None = None,
        due_date: date | None = None,
        due_datetime: datetime | None = None,
        duration: int | None = None,
        duration_unit: Literal["minute", "day"] | None = None,
    ) -> Task: ...

    async def update_task(
        self, task_id: str, *, due_date: date | None = None, due_datetime: datetime | None = None
    ) -> Task: ...

    async def complete_task(self, task_id: str) -> bool: ...

    async def delete_task(self, task_id: str) -> bool: ...

    async def close(self) -> None: ...


def failure_code(error: Exception) -> str:
    """The code of a failure: its type, never its message (a message can quote the content)."""
    if isinstance(error, ToolFailure):
        return error.code
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return "auth" if status in (401, 403) else f"http_{status}"
    if isinstance(error, TimeoutError | httpx.TimeoutException):
        return "timeout"
    if isinstance(error, httpx.TransportError | OSError):
        return "network"
    if isinstance(error, JSONWizardError | TypeError | ValueError):
        return "invalid_format"  # the library could not read what Todoist answered
    return f"unexpected:{type(error).__name__}"


async def _call[T](awaitable: Awaitable[T]) -> T:
    """Await a request with the timeout; any failure becomes a `ToolFailure` with its code."""
    try:
        async with asyncio.timeout(CALL_TIMEOUT):
            return await awaitable
    except ToolFailure:
        raise
    except Exception as error:
        raise ToolFailure(failure_code(error)) from None


async def _all_pages(api: TodoistApi, project_id: str | None) -> list[Task]:
    """Every active task (of a project, if given), at most `MAX_PAGES` pages."""
    pages = await _call(api.get_tasks(project_id=project_id, limit=PAGE_SIZE))
    tasks: list[Task] = []
    for _ in range(MAX_PAGES):
        page = await _next_page(pages)
        if page is None:
            return tasks
        tasks.extend(page)
    if await _next_page(pages) is not None:
        raise TooManyPages
    return tasks


async def _next_page(pages: AsyncIterator[list[Task]]) -> list[Task] | None:
    async def advance() -> list[Task] | None:
        try:
            return await anext(pages)
        except StopAsyncIteration:
            return None

    return await _call(advance())


def _due(task: Task, timezone: ZoneInfo) -> Due | None:
    """The day, or the moment in the zone of the settings: Todoist's floating times (no zone)
    are the user's own, and its UTC ones are converted."""
    if task.due is None:
        return None
    value = task.due.date
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone)
        return value.astimezone(timezone)
    return value


def _to_task(raw: Task, timezone: ZoneInfo, has_subtasks: bool) -> TodoistTask:
    duration = raw.duration
    minutes = None
    if duration is not None:
        minutes = duration.amount * (MINUTES_PER_DAY if duration.unit == "day" else 1)
    return TodoistTask(
        id=raw.id,
        title=raw.content,
        project_id=raw.project_id,
        due=_due(raw, timezone),
        is_recurring=raw.due is not None and raw.due.is_recurring,
        has_subtasks=has_subtasks,
        duration_minutes=minutes,
    )


def _day(due: Due) -> date:
    return due.date() if isinstance(due, datetime) else due


def _order(task: TodoistTask) -> tuple[date, bool, time, str]:
    """By day; on the same day the undated-time ones first, then by time; the ID breaks ties."""
    due = task.due
    assert due is not None  # only dated tasks are ordered
    moment = due.time() if isinstance(due, datetime) else time.min
    return _day(due), isinstance(due, datetime), moment, task.id


class ApiTodoistReader:
    """Reads Todoist's active tasks. Only reads: `read_*` never raise; `get_task` does."""

    def __init__(self, api: TodoistApi, clock: Clock, timezone: ZoneInfo) -> None:
        self._api = api
        self._clock = clock
        self._timezone = timezone
        self._warn = WarnOnce(logger, "Todoist")

    async def read_today(self, project_id: str | None = None) -> TaskList:
        return await self._read(project_id, today_only=True)

    async def read_project(self, project_id: str) -> TaskList:
        return await self._read(project_id, today_only=False)

    async def get_task(self, task_id: str) -> TodoistTask | None:
        try:
            raw = await _call(self._api.get_task(task_id))
        except ToolFailure as error:
            if error.code == "http_404":
                return None
            raise
        children = await _call(self._api.get_tasks(parent_id=task_id, limit=1))
        return _to_task(raw, self._timezone, has_subtasks=bool(await _next_page(children)))

    async def _read(self, project_id: str | None, today_only: bool) -> TaskList:
        try:
            raws = await _all_pages(self._api, project_id)
            parents = {raw.parent_id for raw in raws if raw.parent_id is not None}
            tasks = [_to_task(raw, self._timezone, raw.id in parents) for raw in raws]
            if today_only:
                today = self._clock.now().astimezone(self._timezone).date()
                tasks = [t for t in tasks if t.due is not None and _day(t.due) <= today]
                tasks.sort(key=_order)
            result = TaskList(available=True, tasks=tasks)
        except Exception as error:  # a failure here must not take the service down
            result = TaskList.unavailable(failure_code(error))
        self._warn.update(result.unavailable_reason)
        return result


def _split_due(due: Due) -> tuple[date | None, datetime | None]:
    """`(due_date, due_datetime)` as the library takes them: only one of the two is set."""
    if isinstance(due, datetime):
        if due.tzinfo is None:
            raise ToolFailure("invalid_due")  # which zone? the caller must say
        return None, due
    return due, None


class ApiTodoistWriter:
    """Changes Todoist. It belongs to the code, never to the agent."""

    def __init__(self, api: TodoistApi, timezone: ZoneInfo) -> None:
        self._api = api
        self._timezone = timezone

    async def create(
        self, content: str, project_id: str, due: Due, duration_minutes: int | None = None
    ) -> TodoistTask:
        if duration_minutes is not None and duration_minutes < 1:
            raise ToolFailure("invalid_duration")
        due_date, due_datetime = _split_due(due)
        raw = await _call(
            self._api.add_task(
                content,
                project_id=project_id,
                due_date=due_date,
                due_datetime=due_datetime,
                duration=duration_minutes,
                duration_unit="minute" if duration_minutes is not None else None,
            )
        )
        return _to_task(raw, self._timezone, has_subtasks=False)

    async def move(self, task_id: str, due: Due) -> None:
        due_date, due_datetime = _split_due(due)
        await _call(self._api.update_task(task_id, due_date=due_date, due_datetime=due_datetime))

    async def complete(self, task_id: str) -> None:
        if not await _call(self._api.complete_task(task_id)):
            raise ToolFailure("not_completed")

    async def delete(self, task_id: str) -> None:
        if not await _call(self._api.delete_task(task_id)):
            raise ToolFailure("not_deleted")
