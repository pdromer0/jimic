"""Doubles and invented data for Todoist: nothing here is real.

`FakeTodoistApi` imitates the part of `TodoistAPIAsync` that jimic uses, over a list of tasks
in memory, and records every call. The tasks are the library's own `Task`, built from the JSON
that API v1 sends, so the mapping of the code is tested against the real model. A double that
the code calls in a loop has a cap that raises a clear error.
"""

import asyncio
import copy
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any, Literal

import httpx
from todoist_api_python.models import Task

from jimic.tools.todoist import TaskList, TaskPhoto

PRIVATE_TITLE = "Llamar-al-abogado-inventado-5528"
"""A title that must never reach a log, a code or a `repr`."""
TEST_PROJECT = "proyecto-de-pruebas"
REAL_PROJECT = "proyecto-real-de-pedro"

MAX_CALLS = 80
"""Calls a fake accepts before it assumes the code is in a loop."""


class RunawayDouble(BaseException):
    """The code called a double more than `MAX_CALLS` times. A `BaseException` on purpose: the
    reader turns every `Exception` into an `unavailable` list, which would hide the loop."""


def raw_task(
    task_id: str = "t1",
    title: str = "Tarea inventada",
    project_id: str = TEST_PROJECT,
    due: str | None = None,
    recurring: bool = False,
    parent_id: str | None = None,
    duration: int | None = None,
    description: str = "",
    due_string: str = "inventado",
    due_lang: str = "en",
) -> dict[str, Any]:
    """The JSON of a task in API v1. `due` is `2030-01-07`, `2030-01-07T09:00:00` (floating)
    or `2030-01-07T09:00:00Z` (UTC)."""
    return {
        "id": task_id,
        "content": title,
        "description": description,
        "project_id": project_id,
        "section_id": None,
        "parent_id": parent_id,
        "labels": [],
        "priority": 1,
        "due": None
        if due is None
        else {"date": due, "string": due_string, "lang": due_lang, "is_recurring": recurring},
        "deadline": None,
        "duration": None if duration is None else {"amount": duration, "unit": "minute"},
        "checked": False,
        "added_at": "2030-01-01T00:00:00.000000Z",
        "updated_at": "2030-01-01T00:00:00.000000Z",
        "completed_at": None,
        "added_by_uid": "u1",
        "responsible_uid": None,
        "assigned_by_uid": None,
        "child_order": 1,
        "day_order": 1,
        "collapsed": False,
    }


def http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.todoist.com/api/v1/tasks")
    response = httpx.Response(status, request=request, content=PRIVATE_TITLE.encode())
    return httpx.HTTPStatusError(PRIVATE_TITLE, request=request, response=response)


class _Pages:
    def __init__(self, api: FakeTodoistApi, pages: list[list[Task]]) -> None:
        self._api = api
        self._pages = iter(pages)

    def __aiter__(self) -> _Pages:
        return self

    async def __anext__(self) -> list[Task]:
        self._api.count_call("get_tasks (página)")
        self._api.raise_if_set()
        await self._api.wait()
        try:
            return next(self._pages)
        except StopIteration:
            raise StopAsyncIteration from None


class FakeTodoistApi:
    """The tasks live in `tasks` (raw JSON). `calls` records `(method, arguments)`.
    `error` is raised by every call that goes to the network.

    It behaves as the real API did in the contract of 19-nov-2026: with `free_plan` (the default)
    it does not keep `duration`, and moving a recurring task without `due_string` makes it a plain
    one.
    """

    def __init__(
        self,
        tasks: list[dict[str, Any]] | None = None,
        page_size: int = 200,
        error: Exception | None = None,
        free_plan: bool = True,
    ) -> None:
        self.free_plan = free_plan
        self.tasks = copy.deepcopy(tasks) if tasks is not None else []
        self.page_size = page_size
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self._next_id = 100
        self.hang = False

    # --- bookkeeping ---

    def count_call(self, name: str) -> None:
        if len(self.calls) > MAX_CALLS:
            raise RunawayDouble(f"FakeTodoistApi recibió más de {MAX_CALLS} llamadas")

    async def wait(self) -> None:
        """A call that never answers, to test the timeout."""
        if self.hang:
            await asyncio.sleep(3600)

    def raise_if_set(self) -> None:
        if self.error is not None:
            raise self.error

    def record(self, name: str, **arguments: Any) -> None:
        self.calls.append((name, arguments))
        self.count_call(name)
        self.raise_if_set()

    def calls_of(self, name: str) -> list[dict[str, Any]]:
        return [arguments for method, arguments in self.calls if method == name]

    def find(self, task_id: str) -> dict[str, Any] | None:
        return next((t for t in self.tasks if t["id"] == task_id), None)

    # --- what jimic uses of the library ---

    async def get_task(self, task_id: str) -> Task:
        self.record("get_task", task_id=task_id)
        await self.wait()
        raw = self.find(task_id)
        if raw is None:
            raise http_status_error(404)
        return Task.from_dict(raw)

    async def get_tasks(
        self,
        *,
        project_id: str | None = None,
        parent_id: str | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[list[Task]]:
        self.record("get_tasks", project_id=project_id, parent_id=parent_id, limit=limit)
        await self.wait()
        chosen = [
            Task.from_dict(raw)
            for raw in self.tasks
            if (project_id is None or raw.get("project_id") == project_id)
            and (parent_id is None or raw.get("parent_id") == parent_id)
        ]
        size = min(limit or self.page_size, self.page_size)
        return _Pages(self, [chosen[i : i + size] for i in range(0, len(chosen), size)])

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
    ) -> Task:
        self.record(
            "add_task",
            content=content,
            description=description,
            project_id=project_id,
            due_date=due_date,
            due_datetime=due_datetime,
            duration=duration,
            duration_unit=duration_unit,
        )
        await self.wait()
        self._next_id += 1
        due = due_date.isoformat() if due_date else None
        if due_datetime is not None:  # as the API stores it: UTC, with a `Z`
            due = due_datetime.astimezone(UTC).isoformat().replace("+00:00", "Z")
        raw = raw_task(
            f"t{self._next_id}",
            content,
            project_id or "inbox",
            due=due,
            duration=None if self.free_plan else duration,
            description=description or "",
        )
        self.tasks.append(raw)
        return Task.from_dict(raw)

    async def update_task(
        self,
        task_id: str,
        *,
        due_string: str | None = None,
        due_lang: str | None = None,
        due_date: date | None = None,
        due_datetime: datetime | None = None,
    ) -> Task:
        self.record(
            "update_task",
            task_id=task_id,
            due_string=due_string,
            due_lang=due_lang,
            due_date=due_date,
            due_datetime=due_datetime,
        )
        await self.wait()
        raw = self.find(task_id)
        if raw is None:
            raise http_status_error(404)
        if due_string == "no date" and due_date is None and due_datetime is None:
            raw["due"] = None  # what API v1 does with «no date»
            return Task.from_dict(raw)
        when = due_date or due_datetime
        assert when is not None
        if due_datetime is not None:  # as the API stores it: UTC, with a `Z`
            stored = due_datetime.astimezone(UTC).isoformat().replace("+00:00", "Z")
        else:
            stored = when.isoformat()
        was_recurring = raw["due"] is not None and raw["due"]["is_recurring"]
        keeps = was_recurring and due_string is not None  # what the contract showed
        raw["due"] = {
            "date": stored,
            "string": due_string if keeps else stored,
            "lang": "en",
            "is_recurring": keeps,
        }
        return Task.from_dict(raw)

    async def complete_task(self, task_id: str) -> bool:
        self.record("complete_task", task_id=task_id)
        await self.wait()
        raw = self.find(task_id)
        if raw is None:
            raise http_status_error(404)
        if raw["due"] is None or not raw["due"]["is_recurring"]:
            self.tasks.remove(raw)
        return True

    async def delete_task(self, task_id: str) -> bool:
        self.record("delete_task", task_id=task_id)
        await self.wait()
        raw = self.find(task_id)
        if raw is None:
            raise http_status_error(404)
        self.tasks.remove(raw)
        return True

    async def close(self) -> None:
        self.closed = True


class FixedTodoistReader:
    """The third implementation of the reader: it returns the list it is given."""

    def __init__(self, tasks: TaskList | None = None) -> None:
        self._tasks = tasks if tasks is not None else TaskList(available=True)

    async def read_today(self, project_id: str | None = None) -> TaskList:
        return self._tasks

    async def read_project(self, project_id: str) -> TaskList:
        return self._tasks

    async def get_task(self, task_id: str):
        return next((t for t in self._tasks.tasks if t.id == task_id), None)

    async def get_photo(self, task_id: str):
        task = await self.get_task(task_id)
        if task is None:
            return None
        return TaskPhoto(task=task, description="", labels=(), priority=1)
