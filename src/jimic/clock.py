"""The injectable clock: the only place in the package that reads the system time.

Everything else asks a `Clock` for `now()`, so tests can travel to any hour without waiting.
"""

from datetime import UTC, datetime
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
