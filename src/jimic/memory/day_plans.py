"""The plan of each local day and its tasks (CU-01 step 6, CU-14; task 1.16), in SQLite.

- `day_plans`: one row per day, written when the plan goes out (also the minimal one, also if
  Pedro then taps [Hoy sin plan]). `plan` keeps only codes and counts, never a title. The
  definitive figure of «plan cumplido» (H1-14) goes in `planned`, `fulfilled` and `settled_at`
  the next morning (decision 1.16-b).
- `plan_items`: one row per task of Todoist in the plan (`pending`, `done`, `will_do`, `moved`),
  with `scheduled_at` (its time, if it has one) and, once done, `marked_at` and `marked_early`
  (marked before its time: only noted, H1-8).
- `day_ratings`: «¿Cómo ha ido el día?», 1 to 5, once per day.

Days are local (`YYYY-MM-DD`), moments UTC (`to_db`). An item needs the row of its day (a
foreign key): without a plan that day, nothing is added.
"""

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from jimic.clock import to_db

type Status = Literal["pending", "done", "will_do", "moved"]


@dataclass(frozen=True)
class PlanItem:
    task_id: str
    status: Status
    scheduled_at: datetime | None
    """Aware, in UTC; `None` for a task without a time."""
    marked_at: datetime | None
    marked_early: bool


@dataclass(frozen=True)
class Figures:
    """The definitive figure of a day, once settled."""

    planned: int
    fulfilled: int
    settled_at: datetime


def _moment(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


def has_plan(conn: sqlite3.Connection, day: date) -> bool:
    row = conn.execute("SELECT 1 FROM day_plans WHERE day = ?", (day.isoformat(),)).fetchone()
    return row is not None


def record_plan(
    conn: sqlite3.Connection,
    day: date,
    moment: datetime,
    tasks: Iterable[tuple[str, datetime | None]],
    summary: dict[str, str | int],
) -> None:
    """The plan of `day` went out at `moment` with these tasks `(id, its time or None)`. A second
    plan the same day keeps the first row and adds the tasks that were missing."""
    conn.execute(
        "INSERT INTO day_plans (day, confirmed_at, plan) VALUES (?, ?, ?)"
        " ON CONFLICT (day) DO NOTHING",
        (day.isoformat(), to_db(moment), json.dumps(summary, sort_keys=True)),
    )
    for task_id, scheduled_at in tasks:
        conn.execute(
            "INSERT INTO plan_items (day, todoist_task_id, status, scheduled_at)"
            " VALUES (?, ?, 'pending', ?) ON CONFLICT (day, todoist_task_id) DO NOTHING",
            (day.isoformat(), task_id, None if scheduled_at is None else to_db(scheduled_at)),
        )


def add_item(
    conn: sqlite3.Connection, day: date, task_id: str, scheduled_at: datetime | None
) -> bool:
    """A task the plan's buttons put on `day` (created, or moved to it): pending, at its new
    time. `False` if `day` has no plan."""
    if not has_plan(conn, day):
        return False
    conn.execute(
        "INSERT INTO plan_items (day, todoist_task_id, status, scheduled_at)"
        " VALUES (?, ?, 'pending', ?) ON CONFLICT (day, todoist_task_id) DO UPDATE SET"
        " status = 'pending', scheduled_at = excluded.scheduled_at, marked_at = NULL,"
        " marked_early = 0",
        (day.isoformat(), task_id, None if scheduled_at is None else to_db(scheduled_at)),
    )
    return True


def remove_item(conn: sqlite3.Connection, day: date, task_id: str) -> bool:
    """The plan's buttons deleted the task: it is no longer in the plan."""
    return (
        conn.execute(
            "DELETE FROM plan_items WHERE day = ? AND todoist_task_id = ?",
            (day.isoformat(), task_id),
        ).rowcount
        == 1
    )


def items(conn: sqlite3.Connection, day: date) -> list[PlanItem]:
    rows = conn.execute(
        "SELECT todoist_task_id, status, scheduled_at, marked_at, marked_early FROM plan_items"
        " WHERE day = ? ORDER BY id",
        (day.isoformat(),),
    ).fetchall()
    return [
        PlanItem(task_id, status, _moment(scheduled), _moment(marked), bool(early))
        for task_id, status, scheduled, marked, early in rows
    ]


def is_early(scheduled_at: datetime | None, marked_at: datetime) -> bool:
    """Marked before its time; a task without a time never is."""
    return scheduled_at is not None and marked_at < scheduled_at


def set_status(
    conn: sqlite3.Connection,
    day: date,
    task_id: str,
    status: Status,
    marked_at: datetime | None = None,
) -> bool:
    """Put the state of a task of the plan; `done` takes the moment it was marked (and works out
    `marked_early`), the others clear it. `False` if the task is not in the plan of `day`."""
    found = conn.execute(
        "SELECT scheduled_at FROM plan_items WHERE day = ? AND todoist_task_id = ?",
        (day.isoformat(), task_id),
    ).fetchone()
    if found is None:
        return False
    if status == "done" and marked_at is not None:
        early = is_early(_moment(found[0]), marked_at)
        values = (status, to_db(marked_at), int(early))
    else:
        values = (status, None, 0)
    conn.execute(
        "UPDATE plan_items SET status = ?, marked_at = ?, marked_early = ?"
        " WHERE day = ? AND todoist_task_id = ?",
        (*values, day.isoformat(), task_id),
    )
    return True


def unsettled(conn: sqlite3.Connection, before: date) -> list[date]:
    """The days before `before` with a plan whose figure is not settled yet, oldest first."""
    rows = conn.execute(
        "SELECT day FROM day_plans WHERE settled_at IS NULL AND day < ? ORDER BY day",
        (before.isoformat(),),
    ).fetchall()
    return [date.fromisoformat(day) for (day,) in rows]


def settle(conn: sqlite3.Connection, day: date, fulfilled: int, moment: datetime) -> None:
    """The definitive figure of `day`: `fulfilled` of its tasks, at `moment`."""
    (planned,) = conn.execute(
        "SELECT COUNT(*) FROM plan_items WHERE day = ?", (day.isoformat(),)
    ).fetchone()
    conn.execute(
        "UPDATE day_plans SET planned = ?, fulfilled = ?, settled_at = ? WHERE day = ?",
        (planned, fulfilled, to_db(moment), day.isoformat()),
    )


def figures(conn: sqlite3.Connection, day: date) -> Figures | None:
    """The definitive figure of `day`, or `None` while it is not settled (or there is no plan)."""
    row = conn.execute(
        "SELECT planned, fulfilled, settled_at FROM day_plans"
        " WHERE day = ? AND settled_at IS NOT NULL",
        (day.isoformat(),),
    ).fetchone()
    if row is None:
        return None
    planned, fulfilled, settled_at = row
    return Figures(planned, fulfilled, datetime.fromisoformat(settled_at))


def rate(conn: sqlite3.Connection, day: date, rating: int, moment: datetime) -> bool:
    """«¿Cómo ha ido el día?» of `day`; once: `False` if it was already rated."""
    return (
        conn.execute(
            "INSERT INTO day_ratings (day, rating, rated_at) VALUES (?, ?, ?)"
            " ON CONFLICT (day) DO NOTHING",
            (day.isoformat(), rating, to_db(moment)),
        ).rowcount
        == 1
    )
