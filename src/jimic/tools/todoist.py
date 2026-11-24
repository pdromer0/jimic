"""Todoist: a reader and a writer that are two different objects, and the guard of `dev`.

The agent is given **only the reader** (task 1.10): the functions that change Todoist (create,
move, complete, delete) belong to the code and run after Pedro's button or message (H1-1). A
test checks that the reader has no method that writes.

When Todoist cannot be read the list says so with a code and the service goes on (`auth`,
`timeout`, `http_503`...: a type of failure, never content); it goes to the log once. A write
that fails raises `ToolFailure` with the same kind of code: whoever executes must know.

With `ENTORNO=dev` the guard (1.5-b) wraps both so that the test copy only sees and touches the
project in `PROYECTO_PRUEBAS_TODOIST`: the account is Pedro's real one. `build_todoist` applies
it, so the service cannot forget it.
"""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

import httpx
from dataclass_wizard.errors import JSONWizardError
from pydantic import BaseModel, ConfigDict, Field
from todoist_api_python.api_async import TodoistAPIAsync
from todoist_api_python.models import Task

from jimic.clock import Clock
from jimic.config import Settings
from jimic.tools.failures import MAX_PAGES, ToolFailure, TooManyPages, WarnOnce

logger = logging.getLogger(__name__)

CALL_TIMEOUT = 20.0
"""Seconds each request to Todoist may take; there are no retries. The library fixes its own
timeout inside every request (60 s to read), so this one is ours."""
PAGE_SIZE = 200
"""Tasks per page (the most Todoist gives): `MAX_PAGES` pages are up to 2000 tasks."""
MINUTES_PER_DAY = 24 * 60
DURATION_LINE = re.compile(r"^Duración: ([0-9]{1,5}) min$", re.MULTILINE)
"""The line that carries the duration in the description: the free plan of Todoist does not
keep the `duration` field (contract of 19-nov-2026), so it is written here too."""

NO_DATE = "no date"
"""The `due_string` that takes the date off a task (API v1)."""
OUTSIDE_TEST_PROJECT = "outside_test_project"
TASK_NOT_FOUND = "task_not_found"

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
    completed: bool = Field(default=False, exclude=True)
    """Closed: a completed task is still readable by its id (contract of 23-nov-2026). For the
    code (the key tasks, task 1.15); it never goes to the model."""
    completed_at: datetime | None = Field(default=None, exclude=True)
    """When it was closed, in the zone of the settings: what tells a task marked before its time
    (task 1.16). For the code; it never goes to the model."""


class TaskPhoto(BaseModel):
    """Everything `deleted_tasks` keeps of a task (H1-2), for the code only: the model never
    sees the description, the labels or the priority."""

    model_config = ConfigDict(frozen=True)

    task: TodoistTask
    description: str = Field(repr=False)
    labels: tuple[str, ...] = Field(repr=False)
    priority: int


class Recreation(BaseModel):
    """What `/deshacer` gives back of a deleted task (task 1.12): its photo in `deleted_tasks`.
    The comments do not come back, and the id is new."""

    model_config = ConfigDict(frozen=True)

    content: str = Field(repr=False)
    description: str = Field(repr=False)
    due: Due | None
    duration_minutes: int | None
    project_id: str = Field(repr=False)
    labels: tuple[str, ...] = Field(repr=False)
    priority: int


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

    async def read_day(self, day: date, project_id: str | None = None) -> TaskList:
        """The open tasks due on `day` (local), by time like `read_today`: what is already on
        tomorrow when the close moves a task there (task 1.16). For the code. It does not raise."""
        ...

    async def get_task(self, task_id: str) -> TodoistTask | None:
        """One task, or `None` if it does not exist. It is for the code that executes, not for
        the model, so it raises `ToolFailure` like the writer."""
        ...

    async def get_photo(self, task_id: str) -> TaskPhoto | None:
        """`get_task` with what a deletion keeps; for the code only. Raises like `get_task`."""
        ...

    async def read_completed_today(self) -> TaskList:
        """The tasks completed today (local day, from 00:00 until now), by their completion
        date: what tells that a key task was done in the app (task 1.15). A recurring task
        appears once per completion. It does not raise."""
        ...


class TodoistWriter(Protocol):
    """What only the code may do. Each one raises `ToolFailure` if it fails."""

    async def create(
        self, content: str, project_id: str | None, due: Due, duration_minutes: int | None = None
    ) -> TodoistTask:
        """Create a task; `project_id=None` is the inbox. `due` is a day or a moment with a
        zone."""
        ...

    async def move(self, task_id: str, due: Due | None) -> None:
        """Change the day or the moment of a task; `None` takes the date off (the [Deshacer]
        of a move of an undated task)."""
        ...

    async def complete(self, task_id: str) -> None:
        """Close a task; a recurring one goes to its next time instead of disappearing."""
        ...

    async def reopen(self, task_id: str) -> None:
        """Open again a task JimiC closed (the [Deshacer] of [Ya entrené], task 1.15)."""
        ...

    async def delete(self, task_id: str) -> None:
        """Delete a task for good: Todoist has no bin."""
        ...

    async def recreate(self, task: Recreation) -> TodoistTask:
        """Create again a deleted task from its photo, with a new id and no comments."""
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
        description: str | None = None,
        project_id: str | None = None,
        due_date: date | None = None,
        due_datetime: datetime | None = None,
        duration: int | None = None,
        duration_unit: Literal["minute", "day"] | None = None,
        labels: list[str] | None = None,
        priority: int | None = None,
        auto_parse_labels: bool | None = None,
    ) -> Task: ...

    async def update_task(
        self,
        task_id: str,
        *,
        due_string: str | None = None,
        due_lang: str | None = None,
        due_date: date | None = None,
        due_datetime: datetime | None = None,
    ) -> Task: ...

    async def complete_task(self, task_id: str) -> bool: ...

    async def uncomplete_task(self, task_id: str) -> bool: ...

    async def get_completed_tasks_by_completion_date(
        self, *, since: datetime, until: datetime, limit: int | None = None
    ) -> AsyncIterator[list[Task]]: ...

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
    return await _drain(await _call(api.get_tasks(project_id=project_id, limit=PAGE_SIZE)))


async def _drain(pages: AsyncIterator[list[Task]]) -> list[Task]:
    """Every page, at most `MAX_PAGES`."""
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


def _minutes(raw: Task) -> int | None:
    """The duration: Todoist's own field if it has one, else the line in the description."""
    if raw.duration is not None:
        return raw.duration.amount * (MINUTES_PER_DAY if raw.duration.unit == "day" else 1)
    found = DURATION_LINE.search(raw.description)
    return int(found[1]) if found is not None and int(found[1]) >= 1 else None


def _to_task(raw: Task, timezone: ZoneInfo, has_subtasks: bool) -> TodoistTask:
    return TodoistTask(
        id=raw.id,
        title=raw.content,
        project_id=raw.project_id,
        due=_due(raw, timezone),
        is_recurring=raw.due is not None and raw.due.is_recurring,
        has_subtasks=has_subtasks,
        duration_minutes=_minutes(raw),
        completed=raw.is_completed,
        completed_at=None if raw.completed_at is None else _local(raw.completed_at, timezone),
    )


def _local(moment: datetime, timezone: ZoneInfo) -> datetime:
    """A moment of Todoist in the zone of the settings; one without a zone is UTC (API v1)."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(timezone)


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

    async def read_day(self, day: date, project_id: str | None = None) -> TaskList:
        result = await self._read(project_id, today_only=False)
        if not result.available:
            return result
        tasks = sorted(
            (t for t in result.tasks if t.due is not None and _day(t.due) == day), key=_order
        )
        return result.model_copy(update={"tasks": tasks})

    async def get_task(self, task_id: str) -> TodoistTask | None:
        photo = await self.get_photo(task_id)
        return None if photo is None else photo.task

    async def get_photo(self, task_id: str) -> TaskPhoto | None:
        try:
            raw = await _call(self._api.get_task(task_id))
        except ToolFailure as error:
            if error.code == "http_404":
                return None
            raise
        children = await _call(self._api.get_tasks(parent_id=task_id, limit=1))
        task = _to_task(raw, self._timezone, has_subtasks=bool(await _next_page(children)))
        return TaskPhoto(
            task=task,
            description=raw.description,
            labels=tuple(raw.labels or ()),
            priority=raw.priority,
        )

    async def read_completed_today(self) -> TaskList:
        try:
            now = self._clock.now().astimezone(self._timezone)
            start = datetime.combine(now.date(), time.min, tzinfo=self._timezone)
            pages = await _call(
                self._api.get_completed_tasks_by_completion_date(
                    since=start, until=now, limit=PAGE_SIZE
                )
            )
            raws = await _drain(pages)
            tasks = [_to_task(raw, self._timezone, has_subtasks=False) for raw in raws]
            result = TaskList(available=True, tasks=tasks)
        except Exception as error:  # a failure here must not take the service down
            result = TaskList.unavailable(failure_code(error))
        self._warn.update(result.unavailable_reason)
        return result

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
        self, content: str, project_id: str | None, due: Due, duration_minutes: int | None = None
    ) -> TodoistTask:
        if duration_minutes is not None and duration_minutes < 1:
            raise ToolFailure("invalid_duration")
        due_date, due_datetime = _split_due(due)
        raw = await _call(
            self._api.add_task(
                content,
                description=None
                if duration_minutes is None
                else f"Duración: {duration_minutes} min",
                project_id=project_id,
                due_date=due_date,
                due_datetime=due_datetime,
                duration=duration_minutes,
                duration_unit="minute" if duration_minutes is not None else None,
            )
        )
        return _to_task(raw, self._timezone, has_subtasks=False)

    async def recreate(self, task: Recreation) -> TodoistTask:
        due_date, due_datetime = (None, None) if task.due is None else _split_due(task.due)
        minutes = task.duration_minutes
        raw = await _call(
            self._api.add_task(
                task.content,
                description=task.description or None,
                project_id=task.project_id,
                due_date=due_date,
                due_datetime=due_datetime,
                duration=minutes,
                duration_unit="minute" if minutes is not None else None,
                labels=list(task.labels) or None,
                priority=task.priority,
                # The labels come from the photo: an `@word` in the title must not add another.
                auto_parse_labels=False,
            )
        )
        return _to_task(raw, self._timezone, has_subtasks=False)

    async def move(self, task_id: str, due: Due | None) -> None:
        if due is None:
            await self._clear_due(task_id)
            return
        due_date, due_datetime = _split_due(due)
        current = await self._current(task_id)
        # A new date alone turns a recurring task into a plain one (contract of 19-nov-2026):
        # with the text of its recurrence next to the date it stays recurring. The text is in the
        # language it was written in (a task made in the app can be in Spanish), so it goes
        # with that language.
        recurrence = current.due if current.due is not None and current.due.is_recurring else None
        await _call(
            self._api.update_task(
                task_id,
                due_string=None if recurrence is None else recurrence.string,
                due_lang=None if recurrence is None else recurrence.lang,
                due_date=due_date,
                due_datetime=due_datetime,
            )
        )

    async def _current(self, task_id: str) -> Task:
        try:
            return await _call(self._api.get_task(task_id))
        except ToolFailure as error:
            raise ToolFailure(TASK_NOT_FOUND if error.code == "http_404" else error.code) from None

    async def _clear_due(self, task_id: str) -> None:
        """Take the date off. Never on a recurring task: it would lose its recurrence."""
        current = await self._current(task_id)
        if current.due is not None and current.due.is_recurring:
            raise ToolFailure("invalid_due")
        await _call(self._api.update_task(task_id, due_string=NO_DATE, due_lang="en"))

    async def complete(self, task_id: str) -> None:
        if not await _call(self._api.complete_task(task_id)):
            raise ToolFailure("not_completed")

    async def reopen(self, task_id: str) -> None:
        if not await _call(self._api.uncomplete_task(task_id)):
            raise ToolFailure("not_reopened")

    async def delete(self, task_id: str) -> None:
        if not await _call(self._api.delete_task(task_id)):
            raise ToolFailure("not_deleted")


class GuardedReader:
    """The reader of the test copy: only the project in `PROYECTO_PRUEBAS_TODOIST` exists."""

    def __init__(self, inner: TodoistReader, project_id: str) -> None:
        self._inner = inner
        self._project_id = project_id

    async def read_today(self, project_id: str | None = None) -> TaskList:
        if project_id not in (None, self._project_id):
            return TaskList.unavailable(OUTSIDE_TEST_PROJECT)
        return self._only_the_project(await self._inner.read_today(self._project_id))

    async def read_project(self, project_id: str) -> TaskList:
        if project_id != self._project_id:
            return TaskList.unavailable(OUTSIDE_TEST_PROJECT)
        return self._only_the_project(await self._inner.read_project(self._project_id))

    async def read_day(self, day: date, project_id: str | None = None) -> TaskList:
        if project_id not in (None, self._project_id):
            return TaskList.unavailable(OUTSIDE_TEST_PROJECT)
        return self._only_the_project(await self._inner.read_day(day, self._project_id))

    async def get_task(self, task_id: str) -> TodoistTask | None:
        photo = await self.get_photo(task_id)
        return None if photo is None else photo.task

    async def get_photo(self, task_id: str) -> TaskPhoto | None:
        photo = await self._inner.get_photo(task_id)
        if photo is not None and photo.task.project_id != self._project_id:
            raise ToolFailure(OUTSIDE_TEST_PROJECT)  # the code only, not the title
        return photo

    async def read_completed_today(self) -> TaskList:
        """The endpoint takes no project: this barrier is the only one."""
        result = await self._inner.read_completed_today()
        kept = [t for t in result.tasks if t.project_id == self._project_id]
        return result.model_copy(update={"tasks": kept}) if result.available else result

    def _only_the_project(self, result: TaskList) -> TaskList:
        """A second barrier: the request was already limited, so nothing should be left to drop."""
        kept = [t for t in result.tasks if t.project_id == self._project_id]
        if len(kept) != len(result.tasks):
            logger.warning(
                "the guard dropped %d tasks outside the test project", len(result.tasks) - len(kept)
            )
            return result.model_copy(update={"tasks": kept})
        return result


class GuardedWriter:
    """The writer of the test copy: it creates in the test project and touches nothing else.

    Before it moves, completes or deletes it reads the task fresh and refuses if it is from
    another project (`reader` is the guarded one, which says so with a code and no title).
    """

    def __init__(self, inner: TodoistWriter, reader: GuardedReader, project_id: str) -> None:
        self._inner = inner
        self._reader = reader
        self._project_id = project_id

    async def create(
        self, content: str, project_id: str | None, due: Due, duration_minutes: int | None = None
    ) -> TodoistTask:
        return await self._inner.create(content, self._project_id, due, duration_minutes)

    async def recreate(self, task: Recreation) -> TodoistTask:
        return await self._inner.recreate(task.model_copy(update={"project_id": self._project_id}))

    async def move(self, task_id: str, due: Due | None) -> None:
        await self._check(task_id)
        await self._inner.move(task_id, due)

    async def complete(self, task_id: str) -> None:
        await self._check(task_id)
        await self._inner.complete(task_id)

    async def reopen(self, task_id: str) -> None:
        await self._check(task_id)
        await self._inner.reopen(task_id)

    async def delete(self, task_id: str) -> None:
        await self._check(task_id)
        await self._inner.delete(task_id)

    async def _check(self, task_id: str) -> None:
        if await self._reader.get_task(task_id) is None:
            raise ToolFailure(TASK_NOT_FOUND)


@dataclass(frozen=True)
class TodoistTools:
    """The reader, the writer and how to close the connection, as the service gets them."""

    reader: TodoistReader
    writer: TodoistWriter
    close: Callable[[], Awaitable[None]]


def guard(
    reader: TodoistReader,
    writer: TodoistWriter,
    project_id: str,
    close: Callable[[], Awaitable[None]],
) -> TodoistTools:
    """Wrap `reader` and `writer` so they only see and touch `project_id`."""
    guarded_reader = GuardedReader(reader, project_id)
    return TodoistTools(guarded_reader, GuardedWriter(writer, guarded_reader, project_id), close)


def build_todoist(settings: Settings, clock: Clock, api: TodoistApi | None = None) -> TodoistTools:
    """The Todoist of this copy: with `ENTORNO=dev` the guard is always applied."""
    real: TodoistApi = (
        api if api is not None else TodoistAPIAsync(settings.todoist_token.get_secret_value())
    )
    reader = ApiTodoistReader(real, clock, settings.timezone)
    writer = ApiTodoistWriter(real, settings.timezone)
    if settings.environment == "dev":
        if settings.todoist_test_project is None:
            raise ValueError("ENTORNO=dev sin PROYECTO_PRUEBAS_TODOIST")
        return guard(reader, writer, settings.todoist_test_project, real.close)
    return TodoistTools(reader, writer, real.close)
