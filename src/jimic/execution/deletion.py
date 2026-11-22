"""Deleting a task, with every rule of H1-2 (decision 56).

1. One task per approval, by its id: one item, one button; never in the global row.
2. Only of today or overdue, never recurring or with subtasks (`rules.py`), checked again with
   the task read afresh.
3. The snapshot of the proposal must match title, due and project; if the task changed, nothing
   is deleted and the item asks again with the task as it is now.
4. At most `max_deletions_per_day` a local day.
5. The whole photo goes to `deleted_tasks` (30 days) **before** the deletion, so that
   `/deshacer` (task 1.12) can recreate it. Counting and inserting are one transaction with no
   `await` between them, so two deletions at once (a button and a «sí») cannot both pass the cap.

If Todoist fails, the photo is taken back and the item can be tried again, unless the failure is
ambiguous (a timeout, the network, a 5xx): then the task is read again. Gone, the deletion did
happen: the photo stays and the item is done. Still there, the photo goes. Unknown, the photo
stays and the item can be tried again. A task that was already gone (404) is «ya no existe»,
without a photo: JimiC did not delete it.
"""

import json
import logging
import sqlite3
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from jimic.agent.proposals import TaskSnapshot
from jimic.channel.message import Item
from jimic.channel.taps import Failed, HandlerOutcome, Renew
from jimic.clock import Clock, local_day_bounds, to_db
from jimic.execution.plan_message import item_title
from jimic.execution.rules import RESULTS, refusal
from jimic.memory.database import transaction
from jimic.memory.schemas import Limits
from jimic.tools.failures import ToolFailure
from jimic.tools.todoist import TASK_NOT_FOUND, TaskPhoto, TodoistTools

logger = logging.getLogger(__name__)

DELETED = "✓ borrada"
GONE = "ya no existe"
CAP_REACHED = "tope de borrados de hoy"
CHANGED = "ha cambiado: ¿la borro así?"
NOT_FOUND = frozenset({TASK_NOT_FOUND, "http_404"})
AMBIGUOUS = frozenset({"timeout", "network"})


def ambiguous(code: str) -> bool:
    """A failure after which the deletion may or may not have happened: a timeout, the network
    or a 5xx (a gateway can answer 504 after Todoist did delete)."""
    return code in AMBIGUOUS or (code.startswith("http_5") and code[5:].isdigit())


def deletions_on(conn: sqlite3.Connection, day: date, timezone: ZoneInfo) -> int:
    start, end = local_day_bounds(day, timezone)
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM deleted_tasks WHERE deleted_at >= ? AND deleted_at < ?",
        (to_db(start), to_db(end)),
    ).fetchone()
    return count


def _duration(photo: TaskPhoto) -> str | None:
    minutes = photo.task.duration_minutes
    return None if minutes is None else f"{minutes}m"


def reserve(
    conn: sqlite3.Connection,
    clock: Clock,
    timezone: ZoneInfo,
    limits: Limits,
    photo: TaskPhoto,
    reason: str,
    approval_id: int,
) -> int | None:
    """Count today's deletions and save the photo, in one transaction; `None` if the cap is
    reached (nothing saved). Synchronous on purpose: nothing else runs in between."""
    now = clock.now()
    with transaction(conn):
        if deletions_on(conn, now.astimezone(timezone).date(), timezone) >= (
            limits.max_deletions_per_day
        ):
            return None
        task = photo.task
        cursor = conn.execute(
            "INSERT INTO deleted_tasks (deleted_at, todoist_task_id, content, description, due,"
            " duration, project_id, labels, priority, reason, approval_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                to_db(now),
                task.id,
                task.title,
                photo.description,
                None if task.due is None else task.due.isoformat(),
                _duration(photo),
                task.project_id,
                json.dumps(list(photo.labels), ensure_ascii=False),
                photo.priority,
                reason,
                approval_id,
            ),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid


def _take_back(conn: sqlite3.Connection, row_id: int) -> None:
    conn.execute("DELETE FROM deleted_tasks WHERE id = ?", (row_id,))


async def delete(
    *,
    conn: sqlite3.Connection,
    clock: Clock,
    timezone: ZoneInfo,
    limits: Limits,
    todoist: TodoistTools,
    item: Item,
    approval_id: int,
) -> HandlerOutcome:
    """Delete the task of `item` if every rule allows it; say what happened."""
    data: dict[str, Any] = dict(item.data or {})
    task_id = str(data["task_id"])
    try:
        photo = await todoist.reader.get_photo(task_id)
    except ToolFailure as error:
        return Failed(error.code)
    if photo is None:
        return GONE
    today = clock.now().astimezone(timezone).date()
    refused = refusal(photo.task, today)
    if refused is not None:
        logger.info("deletion refused (%s)", refused)
        return RESULTS[refused]
    now_snapshot = TaskSnapshot.of(photo.task)
    if not TaskSnapshot.model_validate(data["snapshot"]).matches(now_snapshot):
        logger.info("deletion refused (changed)")
        renewed = data | {"snapshot": now_snapshot.model_dump()}
        title = item_title(renewed, today, timezone)
        return Renew(item.model_copy(update={"title": title, "data": renewed, "note": CHANGED}))
    row_id = reserve(conn, clock, timezone, limits, photo, str(data.get("reason", "")), approval_id)
    if row_id is None:
        logger.info("deletion refused (cap)")
        return CAP_REACHED
    try:
        await todoist.writer.delete(task_id)
    except ToolFailure as error:
        if error.code in NOT_FOUND:
            # Someone else deleted it in between: not ours, and nothing to undo.
            _take_back(conn, row_id)
            return GONE
        state = await _state_after(todoist, task_id) if ambiguous(error.code) else "exists"
        if state == "gone":
            logger.info("deletion confirmed after %s", error.code)
            return DELETED
        if state == "exists":
            _take_back(conn, row_id)
        # Unknown: the photo stays (a photo of a task that still exists harms nothing, a task
        # gone without one is lost); a retry finds out.
        return Failed(error.code)
    logger.info("task deleted (photo %d)", row_id)
    return DELETED


async def _state_after(todoist: TodoistTools, task_id: str) -> str:
    """`gone`, `exists` or `unknown`: the task read again after an ambiguous failure."""
    try:
        return "gone" if await todoist.reader.get_task(task_id) is None else "exists"
    except ToolFailure:
        return "unknown"
