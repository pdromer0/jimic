"""The state of interactive messages in `approvals`.

The row keeps the message as JSON in `payload`, so a tap works after a restart. Who creates the
message sets `expires_at` (RF-07); this module only respects it. A row leaves `pending` once:
`save` updates only rows still pending, so two taps cannot both close it. With `expected`, it
also compares the payload (compare and swap): that is how a tap claims its items before their
effect (task 1.11). The row keeps the id of its Telegram message once sent, so the code can edit
it without a tap (the written «sí», the sweep of what expired).
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from jimic.channel.message import FinalStatus, InteractiveMessage, ensure_fits
from jimic.clock import Clock, to_db


@dataclass(frozen=True)
class StoredApproval:
    id: int
    kind: str
    expires_at: datetime
    """Aware, in UTC."""
    status: str
    message: InteractiveMessage
    telegram_message_id: int | None = None
    payload: str = ""
    """The stored JSON as it is: what `save(expected=...)` compares."""


def create(
    conn: sqlite3.Connection,
    clock: Clock,
    kind: str,
    message: InteractiveMessage,
    expires_at: datetime,
) -> int:
    """Save `message` as a pending approval and return its id.

    Raises `MessageTooLong` if it cannot fit in a Telegram message in any of its states, and
    `ValueError` for an empty `kind` or an `expires_at` that is not in the future.
    """
    if not kind:
        raise ValueError("an approval needs a kind")
    now = clock.now()
    if expires_at <= now:
        raise ValueError("an approval must expire in the future")
    ensure_fits(message)
    cursor = conn.execute(
        "INSERT INTO approvals (created_at, expires_at, kind, payload) VALUES (?, ?, ?, ?)",
        (to_db(now), to_db(expires_at), kind, message.model_dump_json()),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


COLUMNS = "id, kind, expires_at, status, payload, telegram_message_id"


def _approval(row: tuple) -> StoredApproval:
    return StoredApproval(
        id=row[0],
        kind=row[1],
        expires_at=datetime.fromisoformat(row[2]),
        status=row[3],
        message=InteractiveMessage.model_validate_json(row[4]),
        telegram_message_id=row[5],
        payload=row[4],
    )


def load(conn: sqlite3.Connection, approval_id: int) -> StoredApproval | None:
    """The approval with that id, or `None`. A payload that is not a valid message raises
    `ValueError` (pydantic's `ValidationError` is one)."""
    row = conn.execute(f"SELECT {COLUMNS} FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    return None if row is None else _approval(row)


def by_message(conn: sqlite3.Connection, telegram_message_id: int) -> StoredApproval | None:
    """The approval shown in that Telegram message, or `None`; raises like `load`."""
    row = conn.execute(
        f"SELECT {COLUMNS} FROM approvals WHERE telegram_message_id = ? ORDER BY id DESC LIMIT 1",
        (telegram_message_id,),
    ).fetchone()
    return None if row is None else _approval(row)


def set_message_id(conn: sqlite3.Connection, approval_id: int, telegram_message_id: int) -> None:
    conn.execute(
        "UPDATE approvals SET telegram_message_id = ? WHERE id = ?",
        (telegram_message_id, approval_id),
    )


def due_to_expire(conn: sqlite3.Connection, now: datetime) -> list[tuple[int, int | None]]:
    """`(id, telegram message id)` of every approval still pending whose time ran out at `now`,
    of any kind (the proposals of task 1.10 and the memory changes too)."""
    return conn.execute(
        "SELECT id, telegram_message_id FROM approvals"
        " WHERE status = 'pending' AND expires_at <= ? ORDER BY id",
        (to_db(now),),
    ).fetchall()


def mark_expired(conn: sqlite3.Connection, approval_id: int, now: datetime) -> bool:
    """Close a pending approval as expired without touching its payload (one that is not a
    message, or that never reached Telegram)."""
    cursor = conn.execute(
        "UPDATE approvals SET status = 'expired', resolved_at = ?"
        " WHERE id = ? AND status = 'pending'",
        (to_db(now), approval_id),
    )
    return cursor.rowcount == 1


def save(
    conn: sqlite3.Connection,
    approval_id: int,
    message: InteractiveMessage,
    status: FinalStatus | None,
    now: datetime,
    expected: str | None = None,
) -> bool:
    """Write the new state of a still pending approval; `status` is its new state, or `None`
        while something is pending. With `expected` (a `payload` as loaded), only if the stored JSON
    is still that one.
        Returns `False` if nothing was written."""
    sql = (
        "UPDATE approvals SET payload = ?, status = COALESCE(?, status), resolved_at = ?"
        " WHERE id = ? AND status = 'pending'"
    )
    params: list[object] = [
        message.model_dump_json(),
        status,
        None if status is None else to_db(now),
        approval_id,
    ]
    if expected is not None:
        sql += " AND payload = ?"
        params.append(expected)
    cursor = conn.execute(sql, params)
    return cursor.rowcount == 1


def discard(conn: sqlite3.Connection, approval_id: int) -> None:
    """Delete an approval that never reached Telegram (the send failed): nobody can tap it."""
    conn.execute("DELETE FROM approvals WHERE id = ? AND status = 'pending'", (approval_id,))
