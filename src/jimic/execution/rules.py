"""The rules of a deletion that depend only on the task (H1-2), shared by `propose_plan` (so
Pedro never sees a button that will refuse) and by the deletion itself, which checks them again
with the task read afresh.
"""

from datetime import date, datetime
from typing import Literal

from jimic.tools.todoist import Due, TodoistTask

type Refusal = Literal["recurring", "subtasks", "not_today"]

RESULTS: dict[Refusal, str] = {
    "recurring": "recurrente: no la borro",
    "subtasks": "tiene subtareas: no la borro",
    "not_today": "ni de hoy ni vencida: bórrala en Todoist",
}
"""What the item says (at most 40 characters)."""

FOR_THE_AGENT: dict[Refusal, str] = {
    "recurring": (
        "es recurrente y borrarla se llevaría la serie entera: no se puede proponer; dile a"
        " Pedro que la gestione en Todoist"
    ),
    "subtasks": (
        "tiene subtareas y se borrarían con ella: no se puede proponer; dile a Pedro que la"
        " borre en Todoist"
    ),
    "not_today": (
        "solo se borran tareas de hoy o vencidas: dile a Pedro que la borre él en Todoist"
    ),
}


def due_day(due: Due) -> date:
    return due.date() if isinstance(due, datetime) else due


def is_today_or_overdue(due: Due | None, today: date) -> bool:
    """A task of today or earlier (local day); an undated one is neither."""
    return due is not None and due_day(due) <= today


def refusal(task: TodoistTask, today: date) -> Refusal | None:
    """Why `task` cannot be deleted, or `None`. In this order: recurring, subtasks, day."""
    if task.is_recurring:
        return "recurring"
    if task.has_subtasks:
        return "subtasks"
    if not is_today_or_overdue(task.due, today):
        return "not_today"
    return None
