"""What the `notices` table says about the proactive messages of a day.

Only the heartbeat writes `notices`, one row per intention it sends: Pedro's conversation and
the confirmations he asks for never go there (4.3 of the design), so every row is proactive.
A grouped message (decision 1.7-b) is several rows with the same `telegram_message_id`, and it
counts as one message. A row without that id counts on its own: when in doubt, more discreet.
"""

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from jimic.clock import to_db


@dataclass(frozen=True)
class SentNotice:
    intent: str
    sent_at: datetime
    """Aware, in UTC, as the database keeps it."""


@dataclass(frozen=True)
class SentToday:
    """The proactive messages between two moments (a local day)."""

    notices: tuple[SentNotice, ...] = ()
    message_count: int = 0
    """Messages, not rows: the rows of a grouped message count once."""
    _per_intent: Counter[str] = field(default_factory=Counter, repr=False, compare=False)

    def count(self, intent: str) -> int:
        """How many times `intent` went out."""
        return self._per_intent[intent]

    @property
    def last_sent_at(self) -> datetime | None:
        return self.notices[-1].sent_at if self.notices else None


def sent_between(conn: sqlite3.Connection, start: datetime, end: datetime) -> SentToday:
    """The notices with `start <= sent_at < end`, oldest first. Both moments must be aware.

    `sent_at` is ISO text in UTC (`to_db`), so comparing the text compares the moments.
    """
    rows = conn.execute(
        "SELECT intent, sent_at, COALESCE(telegram_message_id, -id) FROM notices"
        " WHERE sent_at >= ? AND sent_at < ? ORDER BY sent_at, id",
        (to_db(start), to_db(end)),
    ).fetchall()
    notices = tuple(SentNotice(intent, datetime.fromisoformat(at)) for intent, at, _ in rows)
    return SentToday(
        notices=notices,
        message_count=len({message for _, _, message in rows}),
        _per_intent=Counter(notice.intent for notice in notices),
    )
