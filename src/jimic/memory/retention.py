"""Retention: delete what is older than each table keeps (7.3 of the design).

Cutoffs are absolute: `now` in UTC minus N whole days, not N days of wall-clock time, so a
daylight-saving change does not move them by an hour. Day-keyed tables compare their local
`YYYY-MM-DD` with today's date in the configured zone. `changes` is kept forever.

The service runs `purge` once a day (task 1.8); it logs how many rows went from each table,
never their content.
"""

import logging
import sqlite3
from datetime import UTC, timedelta

from jimic.clock import Clock, to_db
from jimic.memory.database import transaction

logger = logging.getLogger(__name__)

MOMENT_RETENTION = (
    ("decisions", "created_at", 90),
    ("notices", "sent_at", 365),
    ("approvals", "created_at", 30),
    ("deleted_tasks", "deleted_at", 30),
    ("system_notices", "detected_at", 365),
    ("handovers", "handed_at", 90),
)
"""(table, timestamp column, days kept)."""

DAY_RETENTION = (
    ("plan_items", 365),
    ("day_ratings", 365),
    ("day_plans", 365),
    ("model_usage", 365),
    ("key_task_marks", 365),
    ("task_moves", 30),
    ("conversations", 7),
)
"""(table, days kept) for tables keyed by `day`; `plan_items` goes before its `day_plans`."""


def purge(conn: sqlite3.Connection, clock: Clock) -> dict[str, int]:
    """Delete the expired rows of every table in one transaction; return the count per table."""
    now = clock.now()
    now_utc = now.astimezone(UTC)
    today = now.date()
    deleted: dict[str, int] = {}
    with transaction(conn):
        for table, column, days in MOMENT_RETENTION:
            cutoff = to_db(now_utc - timedelta(days=days))
            cursor = conn.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))
            deleted[table] = cursor.rowcount
        for table, days in DAY_RETENTION:
            cutoff_day = (today - timedelta(days=days)).isoformat()
            cursor = conn.execute(f"DELETE FROM {table} WHERE day < ?", (cutoff_day,))
            deleted[table] = cursor.rowcount
    logger.info("purge: %s", ", ".join(f"{table}={n}" for table, n in deleted.items()))
    return deleted
