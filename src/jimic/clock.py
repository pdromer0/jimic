"""The injectable clock: the only place in the package that reads the system time.

Everything else asks a `Clock` for `now()`, so tests can travel to any hour without waiting.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current moment, timezone-aware, in the configured zone."""
        ...


class SystemClock:
    """The real clock, in the timezone of the settings."""

    def __init__(self, timezone: ZoneInfo) -> None:
        self._timezone = timezone

    def now(self) -> datetime:
        return datetime.now(self._timezone)


def to_db(moment: datetime) -> str:
    """Return `moment` as ISO 8601 text in UTC, to the second, as the database stores it.

    One offset for every row makes text order equal to time order, so SQL can compare it.
    """
    if moment.utcoffset() is None:
        raise ValueError("a naive datetime has no place in the database")
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def local_day_bounds(day: date, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    """Midnight of `day` and of the next day in `timezone`, as aware datetimes.

    The end comes from the next date, not from adding 24 hours, so the days the clocks change
    (23 or 25 hours) come out right.
    """
    start = datetime.combine(day, time.min, tzinfo=timezone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone)
    return start, end
