"""Doubles and invented data for the calendar: nothing here is real.

`FakeGoogleService` imitates the two calls the reader makes, `calendarList().list(**kw).execute()`
and `events().list(**kw).execute()`, and records the arguments of every request. A double that
the code calls in a loop has a cap that raises a clear error.
"""

import threading
import time
from collections.abc import Sequence
from datetime import date
from typing import Any

from jimic.tools.calendar import CalendarDay

SECRET_TITLE = "Cita-con-el-medico-secreta-7391"
"""A title that must never reach a log, a code or a `repr`."""

MAX_CALLS = 60
"""Requests a fake accepts before it assumes the code is in a loop."""


class RunawayDouble(BaseException):
    """The code called a double more than `MAX_CALLS` times. A `BaseException` on purpose: the
    reader turns every `Exception` into an `unavailable` day, which would hide the loop."""


def paged(*pages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Responses for a list: each one with its items and the token of the next page."""
    responses: list[dict[str, Any]] = []
    for number, items in enumerate(pages):
        response: dict[str, Any] = {"items": list(items)}
        if number < len(pages) - 1:
            response["nextPageToken"] = f"token-{number + 1}"
        responses.append(response)
    return responses


class _Request:
    def __init__(self, service: FakeGoogleService, kind: str, arguments: dict[str, Any]):
        self._service = service
        self._kind = kind
        self._arguments = arguments

    def execute(self) -> dict[str, Any]:
        return self._service.answer(self._kind, self._arguments)


class _Collection:
    def __init__(self, service: FakeGoogleService, kind: str) -> None:
        self._service = service
        self._kind = kind

    def list(self, **arguments: Any) -> _Request:
        return _Request(self._service, self._kind, arguments)


class FakeGoogleService:
    """`calendars` and `events` are the pages (see `paged`) of the calendar list and of each
    calendar's events. `error` is raised by the first request, to test failures."""

    def __init__(
        self,
        calendars: list[dict[str, Any]],
        events: dict[str, list[dict[str, Any]]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._calendars = calendars
        self._events = events or {}
        self._error = error
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.delay = 0.0
        """Seconds each request takes, to make two reads overlap."""
        self.max_in_flight = 0
        self._in_flight = 0
        self._guard = threading.Lock()

    def calendarList(self) -> _Collection:  # noqa: N802 - the name of the real API
        return _Collection(self, "calendarList")

    def events(self) -> _Collection:
        return _Collection(self, "events")

    def answer(self, kind: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._guard:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            time.sleep(self.delay)
            return self._answer(kind, arguments)
        finally:
            with self._guard:
                self._in_flight -= 1

    def _answer(self, kind: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((kind, arguments))
        if len(self.requests) > MAX_CALLS:
            raise RunawayDouble(f"FakeGoogleService recibió más de {MAX_CALLS} peticiones")
        if self._error is not None:
            raise self._error
        pages = self._calendars if kind == "calendarList" else self._events[arguments["calendarId"]]
        token = arguments.get("pageToken")
        number = 0 if token is None else int(token.removeprefix("token-"))
        return pages[number] if number < len(pages) else {"items": []}

    def requests_of(self, kind: str) -> list[dict[str, Any]]:
        return [arguments for name, arguments in self.requests if name == kind]


def calendar_item(
    calendar_id: str, selected: bool | None = True, name: object = None
) -> dict[str, Any]:
    """An entry of `calendarList`; Google leaves `selected` out of the ones that are not. `name`
    is its `summary`, left out when `None`."""
    item: dict[str, Any] = {"id": calendar_id}
    if selected is not None:
        item["selected"] = selected
    if name is not None:
        item["summary"] = name
    return item


def timed_event(title: str | None, start: str, end: str, **extra: Any) -> dict[str, Any]:
    event: dict[str, Any] = {"start": {"dateTime": start}, "end": {"dateTime": end}}
    if title is not None:
        event["summary"] = title
    return {**event, **extra}


def all_day_event(title: str, start: str, end: str, **extra: Any) -> dict[str, Any]:
    return {"summary": title, "start": {"date": start}, "end": {"date": end}, **extra}


class FixedCalendarReader:
    """The third implementation of the reader: it returns the day it is given."""

    def __init__(self, day: CalendarDay | None = None) -> None:
        self._day = day if day is not None else CalendarDay(available=True, calendar_count=1)

    async def read_day(self, day: date) -> CalendarDay:
        return self._day
