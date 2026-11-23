"""The conversation of each local day in `conversations` (decision 1.10-a).

One row per day: the id of the agent's session (`NULL` until its first answer), the last moment
Pedro wrote (decision 1.10-c) and whether the session has read external content (the calendar or
the tasks), which the origin rule of task 1.12 needs, and whether the onboarding was suggested
that day (decision 1.13-b). No text: ids, moments and two flags.
"""

import sqlite3
from datetime import date, datetime

from jimic.clock import to_db


def note_message(conn: sqlite3.Connection, day: date, moment: datetime) -> None:
    """Pedro wrote at `moment` (aware), on the local `day`."""
    conn.execute(
        "INSERT INTO conversations (day, last_message_at) VALUES (?, ?)"
        " ON CONFLICT (day) DO UPDATE SET last_message_at = excluded.last_message_at",
        (day.isoformat(), to_db(moment)),
    )


def last_message_at(conn: sqlite3.Connection) -> datetime | None:
    """The last moment Pedro wrote, of any day kept; aware, in UTC."""
    (moment,) = conn.execute("SELECT MAX(last_message_at) FROM conversations").fetchone()
    return None if moment is None else datetime.fromisoformat(moment)


def session_of(conn: sqlite3.Connection, day: date) -> str | None:
    row = conn.execute(
        "SELECT session_id FROM conversations WHERE day = ?", (day.isoformat(),)
    ).fetchone()
    return None if row is None else row[0]


def save_session(conn: sqlite3.Connection, day: date, session_id: str | None) -> None:
    """The session of `day` (its row exists: `note_message` wrote it); `None` forgets it."""
    conn.execute(
        "UPDATE conversations SET session_id = ? WHERE day = ?", (session_id, day.isoformat())
    )


def mark_external(conn: sqlite3.Connection, day: date) -> None:
    """The session of `day` has read the calendar or the tasks. Raises `LookupError` if the day
    has no row: a read that cannot be marked must not happen."""
    cursor = conn.execute(
        "UPDATE conversations SET read_external = 1 WHERE day = ?", (day.isoformat(),)
    )
    if cursor.rowcount != 1:
        raise LookupError("no conversation for that day")


def read_external(conn: sqlite3.Connection, day: date) -> bool:
    row = conn.execute(
        "SELECT read_external FROM conversations WHERE day = ?", (day.isoformat(),)
    ).fetchone()
    return row is not None and bool(row[0])


def claim_suggestion(conn: sqlite3.Connection, day: date) -> bool:
    """Whether the onboarding may be suggested on `day`, marking it: `True` only the first time
    (decision 1.13-b), also after a restart. A day without a row (Pedro has not written) gives
    `False` and marks nothing."""
    cursor = conn.execute(
        "UPDATE conversations SET onboarding_suggested = 1"
        " WHERE day = ? AND onboarding_suggested = 0",
        (day.isoformat(),),
    )
    return cursor.rowcount == 1
