"""The state of interactive messages in `approvals`.

The row keeps the message as JSON in `payload`, so a tap works after a restart. Who creates the
message sets `expires_at` (RF-07); this module only respects it. A row leaves `pending` once:
`save` updates only rows still pending, so two taps cannot both close it.
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


def load(conn: sqlite3.Connection, approval_id: int) -> StoredApproval | None:
    """The approval with that id, or `None`. A payload that is not a valid message raises
    `ValueError` (pydantic's `ValidationError` is one)."""
    row = conn.execute(
        "SELECT id, kind, expires_at, status, payload FROM approvals WHERE id = ?", (approval_id,)
    ).fetchone()
    if row is None:
        return None
    return StoredApproval(
        id=row[0],
        kind=row[1],
        expires_at=datetime.fromisoformat(row[2]),
        status=row[3],
        message=InteractiveMessage.model_validate_json(row[4]),
    )


def save(
    conn: sqlite3.Connection,
    approval_id: int,
    message: InteractiveMessage,
    status: FinalStatus | None,
    now: datetime,
) -> bool:
    """Write the new state of a still pending approval; `status` is its new state, or `None`
    while something is pending. Returns `False` if the row was not pending (nothing written)."""
    cursor = conn.execute(
        "UPDATE approvals SET payload = ?, status = COALESCE(?, status), resolved_at = ?"
        " WHERE id = ? AND status = 'pending'",
        (
            message.model_dump_json(),
            status,
            None if status is None else to_db(now),
            approval_id,
        ),
    )
    return cursor.rowcount == 1


def discard(conn: sqlite3.Connection, approval_id: int) -> None:
    """Delete an approval that never reached Telegram (the send failed): nobody can tap it."""
    conn.execute("DELETE FROM approvals WHERE id = ? AND status = 'pending'", (approval_id,))
