"""What the `notices` table says about the proactive messages of a day.

The heartbeat writes `notices`, one row per intention it sends, and so does the plan of the day
when it goes out (task 1.14): Pedro's conversation and the confirmations he asks for never go
there (4.3 of the design), so every row is proactive.
A grouped message (decision 1.7-b) is several rows with the same `telegram_message_id`, and it
counts as one message. A row without that id counts on its own: when in doubt, more discreet.

A plan or a close that the heartbeat hands over to the agent is a row in `handovers`: it counts
as attended today for the cap of its intention (once, even with a row in `notices` too when tasks
1.14 and 1.16 send it), but never as a message.
"""

import sqlite3
from collections import Counter
from collections.abc import Collection
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
    handed: frozenset[str] = frozenset()
    """The intentions handed over to the agent today."""

    def count(self, intent: str) -> int:
        """How many times `intent` was attended today: its sends, or 1 if it was only handed
        over (the union of `notices` and `handovers`, never their sum)."""
        sent = self._per_intent[intent]
        return sent if sent else int(intent in self.handed)

    def sent_count(self, intent: str) -> int:
        """How many times `intent` actually went out (what the slot of the close looks at)."""
        return self._per_intent[intent]

    @property
    def last_sent_at(self) -> datetime | None:
        return self.notices[-1].sent_at if self.notices else None


def sent_between(conn: sqlite3.Connection, start: datetime, end: datetime) -> SentToday:
    """The notices with `start <= sent_at < end`, oldest first, and the handovers in the same
    span. Both moments must be aware.

    `sent_at` is ISO text in UTC (`to_db`), so comparing the text compares the moments.
    """
    rows = conn.execute(
        "SELECT intent, sent_at, COALESCE(telegram_message_id, -id) FROM notices"
        " WHERE sent_at >= ? AND sent_at < ? ORDER BY sent_at, id",
        (to_db(start), to_db(end)),
    ).fetchall()
    notices = tuple(SentNotice(intent, datetime.fromisoformat(at)) for intent, at, _ in rows)
    handed = conn.execute(
        "SELECT DISTINCT intent FROM handovers WHERE handed_at >= ? AND handed_at < ?",
        (to_db(start), to_db(end)),
    ).fetchall()
    return SentToday(
        notices=notices,
        message_count=len({message for _, _, message in rows}),
        _per_intent=Counter(notice.intent for notice in notices),
        handed=frozenset(intent for (intent,) in handed),
    )


def record_handover(conn: sqlite3.Connection, intent: str, moment: datetime) -> None:
    conn.execute("INSERT INTO handovers (handed_at, intent) VALUES (?, ?)", (to_db(moment), intent))


def record_sent(
    conn: sqlite3.Connection, intent: str, moment: datetime, message_id: int | None
) -> None:
    """A proactive message of `intent` went out at `moment` (the plan of the day, task 1.14): it
    counts in the quota like a notice of the heartbeat."""
    conn.execute(
        "INSERT INTO notices (sent_at, intent, telegram_message_id) VALUES (?, ?, ?)",
        (to_db(moment), intent, message_id),
    )


def last_sent_of(
    conn: sqlite3.Connection, intents: Collection[str], before: datetime
) -> datetime | None:
    """The last time one of `intents` went out before `before`; aware, in UTC."""
    if not intents:
        return None
    marks = ", ".join("?" for _ in intents)
    (moment,) = conn.execute(
        f"SELECT MAX(sent_at) FROM notices WHERE intent IN ({marks}) AND sent_at < ?",
        (*intents, to_db(before)),
    ).fetchone()
    return None if moment is None else datetime.fromisoformat(moment)
