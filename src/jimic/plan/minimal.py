"""The minimal plan, without a model (decision 1.14-e): what goes out when the agent cannot (the
API down, the caps of its run, `daily_cap_usd` reached) or did not propose a plan.

The same template: a fixed greeting that says JimiC is at half steam, «Tu día» with what can be
read, the habits of **high** priority missing today as proposals for today without a time (their
task of Todoist, with its duration; only those whose `days` include today, decision F2-a), and
the training line with what APEX expects. Never a delete, an incoherence or a habit of medium or
low priority. A habit is missing when no open task of today or overdue has the name of its
`todoist_task` (without case, spaces joined). With Todoist down nothing is proposed: it cannot know
what is missing.

Known limit: a habit already ticked today is not among the open tasks, so it would be proposed
again; the plan goes out early in the day, before most of them.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from jimic.agent.proposals import MAX_NEW_TASKS
from jimic.memory.schemas import Habit
from jimic.plan.day import normalized
from jimic.tools.apex_schema import ApexSnapshot, TodayWorkout
from jimic.tools.todoist import TaskList

GREETING = "Hoy voy a medio gas: te dejo lo básico y lo demás lo vemos luego."
REASON = "hábito clave que falta hoy"


def missing_habits(habits: Sequence[Habit], tasks: TaskList, today: date) -> list[Habit]:
    """The high-priority habits of `today` without an open task today or overdue; none if Todoist
    is down."""
    if not tasks.available:
        return []
    present = {normalized(task.title) for task in tasks.tasks}
    return [
        habit
        for habit in habits
        if habit.priority == "high"
        and habit.on(today)
        and normalized(habit.todoist_task) not in present
    ][:MAX_NEW_TASKS]


def minimal_entries(habits: Sequence[Habit], tasks: TaskList, today: date) -> list[dict[str, Any]]:
    """The proposals of the minimal plan, as `propose_plan` would leave them."""
    return [
        {
            "action": "create",
            "title": habit.todoist_task,
            "due": today.isoformat(),
            "duration_minutes": int(habit.duration.total_seconds() // 60) or None,
            "reason": REASON,
            "icon": None,
            "follow_up": False,
        }
        for habit in missing_habits(habits, tasks, today)
    ]


def training_line(apex: ApexSnapshot) -> str | None:
    """What APEX expects today, or `None` if it cannot say."""
    if not apex.available:
        return None
    if apex.today_workout == TodayWorkout.TRAINED:
        return "Hoy ya has entrenado: bien hecho."
    if apex.planned_today:
        return f"Hoy toca {apex.planned_today}."
    return None
