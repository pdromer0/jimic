"""The register of the system notices (decision 1.8-c): which kind went out, and when.

A system notice (the API is down, a source is not available, the budget reached its warning) is
a fixed text of the code, outside the daily cap, so it never goes to `notices`. At most one per
kind and local day, the budget one per month. A notice detected in the quiet hours waits with
`sent_at` NULL; a daily one still waiting the next day is not sent any more (it would be stale).
No text is kept: the kind, a code in `detail` and the moments.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from jimic.clock import to_db

type Kind = Literal["api_down", "source_down", "budget"]


@dataclass(frozen=True)
class PendingNotice:
    id: int
    kind: Kind
    detail: str


def period_of(kind: Kind, day: date) -> str:
    """The local day, or its month for the budget."""
    return day.strftime("%Y-%m") if kind == "budget" else day.isoformat()


def detect(
    conn: sqlite3.Connection, kind: Kind, day: date, moment: datetime, detail: str = ""
) -> bool:
    """Register a notice of `kind` for the period of `day`; `False` if it already had one."""
    # `ON CONFLICT` ignores only the repeated period: `OR IGNORE` would also swallow a `CHECK`.
    cursor = conn.execute(
        "INSERT INTO system_notices (kind, period, detail, detected_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (kind, period) DO NOTHING",
        (kind, period_of(kind, day), detail, to_db(moment)),
    )
    return cursor.rowcount == 1


def pending(conn: sqlite3.Connection, day: date) -> list[PendingNotice]:
    """What waits to go out on `day`: the daily ones of `day` and the budget of its month."""
    rows = conn.execute(
        "SELECT id, kind, detail FROM system_notices WHERE sent_at IS NULL"
        " AND ((kind != 'budget' AND period = ?) OR (kind = 'budget' AND period = ?))"
        " ORDER BY id",
        (period_of("api_down", day), period_of("budget", day)),
    ).fetchall()
    return [PendingNotice(id, kind, detail) for id, kind, detail in rows]


def mark_sent(conn: sqlite3.Connection, notice_id: int, moment: datetime) -> None:
    conn.execute("UPDATE system_notices SET sent_at = ? WHERE id = ?", (to_db(moment), notice_id))
