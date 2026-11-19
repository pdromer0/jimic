"""The calendar reader: read-only by design, and it never downloads what it must not show.

`CalendarReader` has one method, `read_day`, and no way to write: that is the guarantee, and a
test checks it. `GoogleCalendarReader` reads every calendar Pedro has marked as visible. Each
request carries a `fields` mask that asks for the title, the times, the status, whether he
organizes and whether he declined: the description, the place and the e-mail addresses are not
even downloaded (decision 34).

When Calendar cannot be read the day says so with a code and the service goes on. The code is a
type (`auth`, `timeout`, `http_503`...), never part of the content; it goes to the log once,
when Calendar stops being available. If one calendar fails the whole day does: a day with
events missing would look like a free one.
"""

import asyncio
import logging
from collections.abc import Callable, Iterator
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httplib2
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError
from pydantic import BaseModel, ConfigDict, Field

from jimic.paths import Paths
from jimic.tools.failures import MAX_PAGES, ToolFailure, TooManyPages, WarnOnce
from jimic.tools.google_auth import CalendarConnection

logger = logging.getLogger(__name__)

CALENDAR_FIELDS = "nextPageToken,items(id,selected)"
"""What is asked of `calendarList.list`: not even the names of the calendars."""

EVENT_FIELDS = (
    "nextPageToken,items(summary,start,end,status,organizer/self,attendees(self,responseStatus))"
)
"""What is asked of `events.list`: nothing else is downloaded."""

AUTH_HINT = "run `jimic google autorizar`"


class CalendarEvent(BaseModel):
    """One event of the day. Its title is Pedro's, so the `repr` hides it."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(repr=False)
    start: datetime | date
    """A `datetime` in the zone of the settings, or a `date` if the event lasts all day."""
    end: datetime | date
    all_day: bool
    organizer_is_me: bool
    declined: bool
    """Pedro declined it: the event is still here, marked, and the caller decides."""


class CalendarDay(BaseModel):
    """What Calendar says about a day. When `available` is false only the reason is set."""

    model_config = ConfigDict(frozen=True)

    available: bool
    unavailable_reason: str | None = None
    """A code (`auth`, `timeout`, `http_503`...), never part of the content."""
    calendar_count: int = 0
    """How many visible calendars were read: a count, to log and to check, never a name."""
    events: list[CalendarEvent] = Field(default_factory=list, repr=False)

    @classmethod
    def unavailable(cls, reason: str) -> CalendarDay:
        return cls(available=False, unavailable_reason=reason)

    def to_json(self) -> str:
        """The compact JSON the model gets (no empty fields)."""
        return self.model_dump_json(exclude_none=True)


class CalendarReader(Protocol):
    """What the rest of jimic knows about the calendar: it can be read, and nothing else."""

    async def read_day(self, day: date) -> CalendarDay:
        """The events of `day`, in the zone of the settings. It does not raise: failures come
        back as `unavailable`."""
        ...


class _InvalidFormat(Exception):
    """Google answered with something that is not what the mask asks for."""


type Service = Any
"""The Google `Resource`. `googleapiclient` builds its methods at run time and ships no types, so
this is the one `Any` of the module: it never leaves it, and the data that comes out of it is
checked field by field."""


def _pages(request_for: Callable[[str | None], Any]) -> Iterator[dict[str, Any]]:
    """The pages of a list, at most `MAX_PAGES`; one more is an error, not a loop."""
    token: str | None = None
    for _ in range(MAX_PAGES):
        page = request_for(token).execute()
        if not isinstance(page, dict):
            raise _InvalidFormat
        yield page
        token = page.get("nextPageToken")
        if not token:
            return
    raise TooManyPages


def _day_bounds(day: date, timezone: ZoneInfo) -> tuple[str, str]:
    """Midnight to midnight of `day` in `timezone` (RFC 3339, with offset). The end is the next
    day's midnight computed from the date, so a day of 23 or 25 hours comes out right."""
    start = datetime.combine(day, time.min, tzinfo=timezone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone)
    return start.isoformat(), end.isoformat()


def _moment(value: object, timezone: ZoneInfo) -> datetime | date:
    """A Google `start` or `end`: `dateTime` (with offset) or `date` (all-day)."""
    if not isinstance(value, dict):
        raise _InvalidFormat
    try:
        if "dateTime" in value:
            moment = datetime.fromisoformat(value["dateTime"])
            if moment.tzinfo is None:  # Google allows it if `timeZone` says which
                moment = moment.replace(tzinfo=ZoneInfo(value["timeZone"]))
            return moment.astimezone(timezone)
        if "date" in value:
            return date.fromisoformat(value["date"])
    except ValueError, TypeError, KeyError, ZoneInfoNotFoundError:
        raise _InvalidFormat from None
    raise _InvalidFormat


def _parse_event(raw: object, timezone: ZoneInfo) -> CalendarEvent | None:
    """The event, or `None` if it is cancelled (a cancelled one may come without times)."""
    if not isinstance(raw, dict):
        raise _InvalidFormat
    if raw.get("status") == "cancelled":
        return None
    start = _moment(raw.get("start"), timezone)
    end = _moment(raw.get("end"), timezone)
    all_day = not isinstance(start, datetime)
    if all_day == isinstance(end, datetime):
        raise _InvalidFormat  # one with a time and one without
    title = raw.get("summary", "")
    if not isinstance(title, str):
        raise _InvalidFormat
    organizer = raw.get("organizer")
    attendees = raw.get("attendees", [])
    if not isinstance(attendees, list):
        raise _InvalidFormat
    return CalendarEvent(
        title=title,
        start=start,
        end=end,
        all_day=all_day,
        organizer_is_me=isinstance(organizer, dict) and organizer.get("self") is True,
        declined=any(
            isinstance(attendee, dict)
            and attendee.get("self") is True
            and attendee.get("responseStatus") == "declined"
            for attendee in attendees
        ),
    )


def _sort_key(event: CalendarEvent, timezone: ZoneInfo) -> datetime:
    """All-day events count from midnight, so they come before the ones of that day."""
    if isinstance(event.start, datetime):
        return event.start
    return datetime.combine(event.start, time.min, tzinfo=timezone)


def failure_code(error: Exception) -> str:
    """The code of a failure: its type, never its message (a message can quote the content)."""
    if isinstance(error, ToolFailure):
        return error.code
    if isinstance(error, RefreshError):
        return "auth"
    if isinstance(error, HttpError):
        status = error.resp.status
        return "auth" if status == 401 else f"http_{status}"
    if isinstance(error, TimeoutError):  # a socket timeout; before OSError, its parent
        return "timeout"
    if isinstance(error, _InvalidFormat):
        return "invalid_format"
    if isinstance(error, OSError | httplib2.HttpLib2Error | TransportError):
        return "network"
    return f"unexpected:{type(error).__name__}"


class GoogleCalendarReader:
    """Reads the visible calendars of Pedro's Google account, in a thread (the library blocks).

    `connect` gives the authorized `Resource` (see `jimic.tools.google_auth`), or raises a
    `ToolFailure` if the permission is missing or no longer valid.
    """

    def __init__(self, connect: Callable[[], Service], timezone: ZoneInfo) -> None:
        self._connect = connect
        self._timezone = timezone
        self._warn = WarnOnce(logger, "Calendar")

    async def read_day(self, day: date) -> CalendarDay:
        result = await asyncio.to_thread(self._read_day, day)
        reason = result.unavailable_reason
        self._warn.update(reason, AUTH_HINT if reason == "auth" else "")
        return result

    def _read_day(self, day: date) -> CalendarDay:
        try:
            service = self._connect()
            calendars = self._visible_calendars(service)
            time_min, time_max = _day_bounds(day, self._timezone)
            events: list[CalendarEvent] = []
            for calendar_id in calendars:
                for page in _pages(
                    lambda token, calendar_id=calendar_id: service.events().list(
                        **_with_token(
                            {
                                "calendarId": calendar_id,
                                "timeMin": time_min,
                                "timeMax": time_max,
                                "singleEvents": True,
                                "orderBy": "startTime",
                                "fields": EVENT_FIELDS,
                            },
                            token,
                        )
                    )
                ):
                    for raw in page.get("items", []):
                        event = _parse_event(raw, self._timezone)
                        if event is not None:
                            events.append(event)
            events.sort(key=lambda event: _sort_key(event, self._timezone))
            return CalendarDay(available=True, calendar_count=len(calendars), events=events)
        except Exception as error:  # a failure here must not take the service down
            return CalendarDay.unavailable(failure_code(error))

    @staticmethod
    def _visible_calendars(service: Service) -> list[str]:
        """The calendars marked as visible. Google leaves `selected` out of the others, so only
        `true` counts."""
        ids: list[str] = []
        for page in _pages(
            lambda token: service.calendarList().list(
                **_with_token({"fields": CALENDAR_FIELDS}, token)
            )
        ):
            for item in page.get("items", []):
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise _InvalidFormat
                if item.get("selected") is True:
                    ids.append(item["id"])
        return ids


def _with_token(arguments: dict[str, Any], token: str | None) -> dict[str, Any]:
    """The arguments of a list request, with `pageToken` only from the second page on."""
    return arguments if token is None else {**arguments, "pageToken": token}


def build_calendar(paths: Paths, timezone: ZoneInfo) -> GoogleCalendarReader:
    """The calendar reader of this copy: its permission is the `google_token.json` of `paths`."""
    return GoogleCalendarReader(CalendarConnection(paths), timezone)
