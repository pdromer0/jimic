"""The state of each key task of the day (CU-02, task 1.15): what the code knows, computed here.

For each active `key_task` the code works out, without the model:

- **Today's task** in Todoist: open, due **today** (yesterday's overdue one is not today's), with
  the title of the habit's `todoist_task` (`normalized`); with several, the first one with a time,
  else the first without.
- **Done** (`done_by`, decision 1.15-e), in this order: Pedro's button (`button`); a task with
  that title completed today in Todoist and none of today still open (`todoist`); or, for the
  habit with `training: true`, a workout of today in APEX (`apex`).
- **No longer today's** (`rescheduled`, decision 1.15-j): the heartbeat saw today's task open
  (`seen`) and now that same task is open on a later day, and JimiC did not move it. A recurring
  task completed in the app never shows among the completed (contract of 23-nov-2026): it jumps
  to its next day. So does a task Pedro put off in the app. It stops the insistence like done,
  but the day close (1.16) must not count it as done. Known limit: a task completed before any
  heartbeat saw it is never `seen`, so the jump is not noticed.
- `off_day`: its habit has `days` and today is not one of them (decision F2-a): it does not
  insist today, whatever else.
- `not_today` ([Hoy no]), `paused_until` ([Voy ahora] plus the habit's duration), `insist_from`
  (the time of today's task, or `default_time` without one, plus `grace`), `tone_level` (the
  notices of this intention sent today plus one, at most 3) and `sent_today`.

`refusal` is what the second pass enforces with it (decision 1.15-i and the stops of 1.15-e to
1.15-g). Titles never leave this module except as the model's JSON (the `repr` hides them).
"""

import asyncio
import logging
from collections.abc import Collection, Iterable
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from jimic.execution.rules import due_day
from jimic.memory.key_marks import IntentMarks
from jimic.memory.schemas import Habit, KeyTask
from jimic.plan.day import normalized
from jimic.tools.apex_schema import ApexSnapshot, TodayWorkout
from jimic.tools.failures import ToolFailure
from jimic.tools.todoist import TaskList, TodoistReader, TodoistTask

logger = logging.getLogger(__name__)

MAX_TONE = 3
"""1 amable, 2 directo, 3 con reto o humor (decision 1.15-h, H1-11)."""

type DoneBy = Literal["button", "todoist", "apex"]
type KeyRefusal = Literal[
    "key_off_day", "key_done", "key_rescheduled", "key_not_today", "key_paused", "key_too_early"
]


def _named(tasks: Iterable[TodoistTask], name: str) -> list[TodoistTask]:
    wanted = normalized(name)
    return [task for task in tasks if normalized(task.title) == wanted]


def todays_task(tasks: TaskList, habit: Habit | None, today: date) -> TodoistTask | None:
    """The open task of today of `habit`: the first with a time, else the first without."""
    if not tasks.available or habit is None:
        return None
    mine = [
        task
        for task in _named(tasks.tasks, habit.todoist_task)
        if task.due is not None and due_day(task.due) == today
    ]
    timed = sorted(
        (task for task in mine if isinstance(task.due, datetime)),
        key=lambda task: (task.due, task.id),
    )
    return (timed or mine or [None])[0]


class KeyTaskStatus(BaseModel):
    """What the code knows today of one key task."""

    model_config = ConfigDict(frozen=True)

    intent: str
    task: TodoistTask | None
    """Today's open task, if there is one."""
    done_by: DoneBy | None
    rescheduled: bool
    not_today: bool
    paused_until: datetime | None
    insist_from: datetime
    """Aware, local: it may fall tomorrow (a task late at night), and then it is not today."""
    tone_level: int
    sent_today: int
    off_day: bool = False
    """Its habit's `days` leave today out (decision F2-a)."""
    next_from: datetime | None = None
    """Its last notice of today plus its `spacing` (or `grace`), local: before it, it is not due
    (`too_soon`, decision 149). It is `due_now`'s, not `refusal`'s, so the second pass says
    `not_due`, and the plan B, which only goes out at `insist_from`, is not affected."""

    def refusal(self, now: datetime) -> KeyRefusal | None:
        """Why the code does not let it go out now, or `None`. In this order."""
        if self.off_day:
            return "key_off_day"
        if self.done_by is not None:
            return "key_done"
        if self.rescheduled:
            return "key_rescheduled"
        if self.not_today:
            return "key_not_today"
        if self.paused_until is not None and now < self.paused_until:
            return "key_paused"
        if now < self.insist_from:
            return "key_too_early"
        return None

    def to_json(self, now: datetime) -> dict[str, Any]:
        """For the model: only what is set; hours `HH:MM`, local. Not `insist_from`: whether it
        may insist now is the intention's `due_now` (task 1.15-b), so the model compares no hour."""
        document: dict[str, Any] = {}
        if self.task is not None:
            due = self.task.due
            document["task"] = due.strftime("%H:%M") if isinstance(due, datetime) else "hoy"
        if self.done_by is not None:
            document["done_by"] = self.done_by
        if self.rescheduled:
            document["rescheduled"] = True
        if self.not_today:
            document["not_today"] = True
        if self.paused_until is not None and now < self.paused_until:
            document["paused_until"] = self.paused_until.strftime("%H:%M")
        document["tone_level"] = self.tone_level
        document["sent_today"] = self.sent_today
        return document


def key_task_status(
    *,
    now: datetime,
    intention: KeyTask,
    habit: Habit | None,
    tasks: TaskList,
    completed: TaskList,
    apex: ApexSnapshot,
    marks: IntentMarks,
    jumped: Collection[str],
    moved: Collection[str],
    sent_today: int,
    last_sent_at: datetime | None = None,
) -> KeyTaskStatus:
    """The status of `intention` at `now` (aware, local). `jumped` are the ids of the tasks seen
    today that are open on a later day; `moved`, those JimiC moved today; `last_sent_at`, its
    last notice of today."""
    today = now.date()
    task = todays_task(tasks, habit, today)
    done_by: DoneBy | None = None
    if marks.done:
        done_by = "button"
    elif (
        habit is not None
        and tasks.available
        and completed.available
        and task is None
        and _named(completed.tasks, habit.todoist_task)
    ):
        done_by = "todoist"
    elif (
        habit is not None
        and habit.training
        and apex.available
        and apex.today_workout == TodayWorkout.TRAINED
    ):
        done_by = "apex"
    rescheduled = any(task_id in jumped and task_id not in moved for task_id in marks.seen) and (
        task is None
    )
    paused_until = None
    if marks.going_at is not None:
        duration = habit.duration if habit is not None else timedelta(0)
        paused_until = (marks.going_at + duration).astimezone(now.tzinfo)
    if task is not None and isinstance(task.due, datetime):
        start = task.due.astimezone(now.tzinfo)
    else:
        start = datetime.combine(today, intention.default_time, tzinfo=now.tzinfo)
    return KeyTaskStatus(
        intent=intention.id,
        task=task,
        done_by=done_by,
        rescheduled=rescheduled,
        not_today=marks.not_today,
        paused_until=paused_until,
        insist_from=start + intention.grace,
        tone_level=min(sent_today + 1, MAX_TONE),
        sent_today=sent_today,
        off_day=habit is not None and not habit.on(today),
        next_from=None
        if last_sent_at is None
        else (last_sent_at + intention.gap).astimezone(now.tzinfo),
    )


async def read_jumped(reader: TodoistReader, ids: Collection[str], today: date) -> frozenset[str]:
    """The ids, among `ids`, of the tasks that are open on a day after `today`. One that cannot
    be read does not count: when in doubt, it has not jumped."""

    async def later(task_id: str) -> bool:
        try:
            task = await reader.get_task(task_id)
        except ToolFailure as error:
            logger.info("key task: jump unknown (%s)", error.code)
            return False
        return task is not None and task.due is not None and due_day(task.due) > today

    chosen = sorted(ids)
    found = await asyncio.gather(*(later(task_id) for task_id in chosen))
    return frozenset(task_id for task_id, yes in zip(chosen, found, strict=True) if yes)
