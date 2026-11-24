"""What the code knows of each key task on a local day (task 1.15), in `key_task_marks`.

- `going`: Pedro tapped [Voy ahora]; the pause counts from its moment (decision 1.15-g).
- `done`: [Ya entrené] or [Hecho] (1.15-e); `not_today`: [Hoy no] (1.15-f). Their [Deshacer]
  deletes the row.
- `seen`: a heartbeat saw today's task of Todoist open, with that id. If later the same task is
  open on a later day and JimiC did not move it (`task_moves`), it is no longer today's: Pedro
  completed a recurring one (which never shows among the completed) or put it off in the app
  (decision 1.15-j). One row per task and day.

When JimiC moves today's task of a key task to another day (a [Más tarde] answered «mañana», or
any `move` of the agent), the code writes `not_today` for that intention, with the task's id, so
the insistence of today stops (decision 145). Undoing that move takes away only that mark: a
[Hoy no] Pedro tapped has no task id and stays.

The days are local (`YYYY-MM-DD`), the moments UTC (`to_db`). No text of Pedro: ids and moments.
"""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from jimic.clock import to_db

type Mark = Literal["going", "done", "not_today", "seen"]


@dataclass(frozen=True)
class IntentMarks:
    """The marks of one key task on one day."""

    done: bool = False
    not_today: bool = False
    going_at: datetime | None = None
    """The last [Voy ahora]; aware, in UTC."""
    seen: frozenset[str] = frozenset()
    """The ids of the tasks of Todoist the heartbeat saw open as today's."""


@dataclass(frozen=True)
class DayMarks:
    by_intent: dict[str, IntentMarks] = field(default_factory=dict)
    moved: frozenset[str] = frozenset()
    """The ids of the tasks JimiC moved that day."""

    def of(self, intent: str) -> IntentMarks:
        return self.by_intent.get(intent, IntentMarks())


def record(
    conn: sqlite3.Connection,
    day: date,
    intent: str,
    mark: Mark,
    moment: datetime,
    task_id: str | None = None,
) -> int:
    """Write a mark of `intent` on the local `day`; return its id."""
    cursor = conn.execute(
        "INSERT INTO key_task_marks (day, intent, mark, marked_at, todoist_task_id)"
        " VALUES (?, ?, ?, ?, ?)",
        (day.isoformat(), intent, mark, to_db(moment), task_id),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def remove(conn: sqlite3.Connection, mark_id: int) -> bool:
    """Delete a mark (its [Deshacer]); `False` if it was not there."""
    return conn.execute("DELETE FROM key_task_marks WHERE id = ?", (mark_id,)).rowcount == 1


def note_seen(
    conn: sqlite3.Connection, day: date, intent: str, task_id: str, moment: datetime
) -> None:
    """The heartbeat saw `task_id` open as today's task of `intent`; once per task and day."""
    conn.execute(
        "INSERT INTO key_task_marks (day, intent, mark, marked_at, todoist_task_id)"
        " VALUES (?, ?, 'seen', ?, ?) ON CONFLICT DO NOTHING",
        (day.isoformat(), intent, to_db(moment), task_id),
    )


def note_moved(conn: sqlite3.Connection, day: date, task_id: str, moment: datetime) -> None:
    """JimiC moved `task_id` on the local `day`."""
    conn.execute(
        "INSERT INTO task_moves (day, todoist_task_id, moved_at) VALUES (?, ?, ?)",
        (day.isoformat(), task_id, to_db(moment)),
    )


def note_jimic_move(
    conn: sqlite3.Connection, day: date, task_id: str, moment: datetime, to_day: date | None
) -> int:
    """JimiC moved `task_id` on the local `day` to `to_day` (`None`: no date): a move of JimiC is
    not the jump of a key task (decision 1.15-j), and one out of `day` is «hoy no» for the key
    tasks that saw it as theirs (decision 145). Returns how many «hoy no» it wrote."""
    note_moved(conn, day, task_id, moment)
    if to_day == day:
        return 0
    return moved_off_today(conn, day, task_id, moment)


def of_day(conn: sqlite3.Connection, day: date) -> DayMarks:
    """Every mark of the local `day`, by intention, and the tasks JimiC moved."""
    rows = conn.execute(
        "SELECT intent, mark, marked_at, todoist_task_id FROM key_task_marks"
        " WHERE day = ? ORDER BY marked_at, id",
        (day.isoformat(),),
    ).fetchall()
    done: set[str] = set()
    not_today: set[str] = set()
    going: dict[str, datetime] = {}
    seen: dict[str, set[str]] = defaultdict(set)
    for intent, mark, marked_at, task_id in rows:
        if mark == "going":
            going[intent] = datetime.fromisoformat(marked_at)  # in order: the last one wins
        elif mark == "seen":
            seen[intent].add(task_id)
        elif mark == "done":
            done.add(intent)
        else:
            not_today.add(intent)
    by_intent = {
        intent: IntentMarks(
            done=intent in done,
            not_today=intent in not_today,
            going_at=going.get(intent),
            seen=frozenset(seen.get(intent, ())),
        )
        for intent in done | not_today | set(going) | set(seen)
    }
    moved = conn.execute(
        "SELECT DISTINCT todoist_task_id FROM task_moves WHERE day = ?", (day.isoformat(),)
    ).fetchall()
    return DayMarks(by_intent, frozenset(task_id for (task_id,) in moved))


def moved_off_today(conn: sqlite3.Connection, day: date, task_id: str, moment: datetime) -> int:
    """JimiC moved `task_id` out of the local `day`: «hoy no», with the task's id, for each key
    task that saw it as today's (decision 145). Returns how many it marked."""
    intents = conn.execute(
        "SELECT DISTINCT intent FROM key_task_marks"
        " WHERE day = ? AND mark = 'seen' AND todoist_task_id = ? ORDER BY intent",
        (day.isoformat(), task_id),
    ).fetchall()
    for (intent,) in intents:
        record(conn, day, intent, "not_today", moment, task_id)
    return len(intents)


def moved_back(conn: sqlite3.Connection, day: date, task_id: str) -> int:
    """The move of `task_id` was undone: away go the «hoy no» it wrote, and only those (a [Hoy no]
    of Pedro has no task id). Returns how many."""
    return conn.execute(
        "DELETE FROM key_task_marks WHERE day = ? AND mark = 'not_today' AND todoist_task_id = ?",
        (day.isoformat(), task_id),
    ).rowcount
