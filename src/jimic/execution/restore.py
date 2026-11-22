"""`/deshacer` of a deleted task: recreate it from its photo in `deleted_tasks` (H1-2, task 1.12).

What is offered: the deletions of the last 30 days (an absolute cut, as the purge), **one per
Todoist task** (its newest photo: a retried deletion may have left two), not recreated yet and
not found still in Todoist.

Recreating:

1. The photo is claimed (`restored_at`, compare and swap) before the first `await`: two taps,
   or two messages of `/deshacer`, never recreate it twice.
2. The task is read first. If it is still in Todoist (a deletion that never happened), nothing is
   created: «sigue en Todoist: no la recreo», and it is not offered again.
3. Otherwise it is created again with its photo (title, description, due, duration, project,
   labels and priority): **a new id, without comments**, and Pedro is told so. In `dev`, the
   guard sends it to the test project.

If Todoist fails, the claim is released and Pedro can try again. Logs say ids and codes.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, timedelta

from jimic.agent.proposals import due_value
from jimic.channel.taps import Failed, HandlerOutcome, Show
from jimic.clock import Clock, to_db
from jimic.tools.failures import ToolFailure
from jimic.tools.todoist import Recreation, TodoistTools

logger = logging.getLogger(__name__)

WINDOW = timedelta(days=30)
RECREATED = "↩️ recreada con un ID nuevo"
STILL_THERE = "sigue en Todoist: no la recreo"
ALREADY = "ya estaba recreada"
RECREATED_TEXT = "He recreado «{title}» con un ID nuevo; los comentarios no vuelven."


@dataclass(frozen=True)
class DeletedRow:
    id: int
    deleted_at: str
    todoist_task_id: str
    content: str
    due: str | None


def offered(conn: sqlite3.Connection, clock: Clock) -> list[DeletedRow]:
    """What `/deshacer` offers, the newest first."""
    cutoff = to_db(clock.now().astimezone(UTC) - WINDOW)
    rows = conn.execute(
        "SELECT d.id, d.deleted_at, d.todoist_task_id, d.content, d.due FROM deleted_tasks d"
        " WHERE d.deleted_at >= ? AND d.restored_at IS NULL"
        " AND d.id = (SELECT MAX(e.id) FROM deleted_tasks e"
        "             WHERE e.todoist_task_id = d.todoist_task_id)"
        " ORDER BY d.id DESC",
        (cutoff,),
    ).fetchall()
    return [DeletedRow(*row) for row in rows]


def _minutes(duration: str | None) -> int | None:
    if duration is None or not duration.endswith("m"):
        return None
    return int(duration[:-1])


def _recreation(row: tuple) -> Recreation:
    content, description, due, duration, project_id, labels, priority = row
    return Recreation(
        content=content,
        description=description,
        due=due_value(due),
        duration_minutes=_minutes(duration),
        project_id=project_id,
        labels=tuple(json.loads(labels)),
        priority=priority,
    )


def _release(conn: sqlite3.Connection, row_id: int) -> None:
    conn.execute(
        "UPDATE deleted_tasks SET restored_at = NULL, restored_task_id = NULL WHERE id = ?",
        (row_id,),
    )


async def restore(
    conn: sqlite3.Connection, clock: Clock, todoist: TodoistTools, row_id: int
) -> HandlerOutcome:
    row = conn.execute(
        "SELECT todoist_task_id, content, description, due, duration, project_id, labels,"
        " priority FROM deleted_tasks WHERE id = ?",
        (row_id,),
    ).fetchone()
    if row is None:
        return Failed("unknown_deletion")
    old_id, photo = row[0], row[1:]
    claimed = conn.execute(
        "UPDATE deleted_tasks SET restored_at = ? WHERE id = ? AND restored_at IS NULL",
        (to_db(clock.now()), row_id),
    )
    if claimed.rowcount != 1:
        return ALREADY
    try:
        current = await todoist.reader.get_task(old_id)
    except ToolFailure as error:
        _release(conn, row_id)
        return Failed(error.code)
    if current is not None:
        logger.info("restore %d refused (still there)", row_id)
        return STILL_THERE
    recreation = _recreation(photo)
    try:
        created = await todoist.writer.recreate(recreation)
    except ToolFailure as error:
        _release(conn, row_id)
        logger.info("restore %d failed (%s)", row_id, error.code)
        return Failed(error.code)
    # One statement, so it is all or nothing: every photo of that task stops being offered, and
    # this one keeps the new id.
    conn.execute(
        "UPDATE deleted_tasks SET restored_at = COALESCE(restored_at, ?),"
        " restored_task_id = CASE WHEN id = ? THEN ? ELSE restored_task_id END"
        " WHERE todoist_task_id = ?",
        (to_db(clock.now()), row_id, created.id, old_id),
    )
    logger.info("restore %d: task recreated", row_id)
    return Show(RECREATED_TEXT.format(title=recreation.content), RECREATED)
