"""The message of the heartbeat with key tasks (decision 1.15-c): the model's text and, for each
key task in it, two rows of buttons.

`[🏃 Voy ahora] [⏰ Más tarde]` and `[✅ Ya entrené] [⏭ Hoy no]` (`[✅ Hecho]` if the habit is not
the training). With two key tasks, each button starts with the icon of its habit (`## Iconos` of
`preferencias.md`, by its Todoist task), or with its number. A `notice` in the same message gets
no buttons. Each button acts only on its key task: what it needs travels in the item's `data`
(the intention, the habit, today's task as the heartbeat saw it). It all expires at the end of
the local day. What each tap does is `jimic.execution.key_tasks`.
"""

from collections.abc import Mapping, Sequence

from jimic.channel.message import InteractiveMessage, Item, Option
from jimic.heartbeat.key_tasks import KeyTaskStatus
from jimic.memory.schemas import Habit, Intentions, KeyTask
from jimic.plan.day import normalized

KEY_KIND = "key_task"
GO = Option(key="go", label="🏃 Voy ahora")
LATER = Option(key="later", label="⏰ Más tarde", approves=False)
TRAINED = Option(key="done", label="✅ Ya entrené")
DONE = Option(key="done", label="✅ Hecho")
SKIP = Option(key="skip", label="⏭ Hoy no", approves=False)


def _habit_of(intentions: Intentions, intent: str) -> tuple[KeyTask | None, Habit | None]:
    intention = next(
        (item for item in intentions.intentions if item.id == intent and isinstance(item, KeyTask)),
        None,
    )
    if intention is None:
        return None, None
    habit = next((habit for habit in intentions.habits if habit.id == intention.habit), None)
    return intention, habit


def key_item(status: KeyTaskStatus, intentions: Intentions, icons: Mapping[str, str]) -> Item:
    """The item of one key task: its rows of buttons and what its taps need."""
    _, habit = _habit_of(intentions, status.intent)
    name = habit.name if habit is not None else status.intent
    icon = icons.get(normalized(habit.todoist_task)) if habit is not None else None
    training = habit is not None and habit.training
    task = status.task
    return Item(
        title=f"{icon} {name}" if icon else name,
        options=(GO, LATER, TRAINED if training else DONE, SKIP),
        icon=icon,
        data={
            "intent": status.intent,
            "habit": name,
            "training": training,
            "task_id": None if task is None else task.id,
            "task_due": None if task is None or task.due is None else task.due.isoformat(),
        },
    )


def key_message(
    text: str,
    statuses: Sequence[KeyTaskStatus],
    intentions: Intentions,
    icons: Mapping[str, str],
) -> InteractiveMessage:
    """The message of a heartbeat that sends `text` with these key tasks, in their order."""
    return InteractiveMessage(
        body=text,
        items=tuple(key_item(status, intentions, icons) for status in statuses),
        style="key",
    )
