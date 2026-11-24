"""The message of the close of the day, painted by the code (decision 1.16-a; format of CU-14).

```text
🌙 Cierre del día, Pedro

✅ Hecho
🍽️ Comida

⏳ Sin marcar
1️⃣ 🧘 Estiramientos (21:30)
2️⃣ 📞 Llamar al seguro

📈 Por ahora: 4 de 7 del plan
🎯 <la nota del agente, 1–2 líneas>
🔮 Mañana: calendario libre · 🏋️ Pierna · fuerza

¿Cómo ha ido el día?
[1] [2] [3] [4] [5]
[1 ✅ Hecha] [1 🗣️ La haré] [1 ➡️ Mañana]
[2 ✅ Hecha] [2 🗣️ La haré] [2 ➡️ Mañana]
[➡️ Todo lo pendiente a mañana]
```

The agent gives only the note (its free text, cut here to `NOTE_LINES` lines); the code writes
everything else. «📈 Por ahora» counts the tasks of the plan done and goes up with each [Hecha]
in the same message (`Tally`); without a plan, or with an empty one, there is no such line. At
most `MAX_PENDING` tasks get buttons (the rating is the eighth item); the rest are counted in the
header and stay as they are. What an item does lives in its data, never in what is shown.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from jimic.channel.message import (
    MAX_ITEMS,
    GlobalOption,
    InteractiveMessage,
    Item,
    Option,
    Tally,
    fit_titles,
)
from jimic.close.day import CloseView
from jimic.plan.day import clip, hour, icon_of, normalized
from jimic.tools.todoist import TodoistTask

CLOSE_KIND = "close"
HEADER = "🌙 Cierre del día, Pedro"
DONE_HEADER = "✅ Hecho"
NOTHING_DONE = "▫️ Nada marcado todavía"
PENDING_HEADER = "⏳ Sin marcar"
NOTHING_PENDING = "▫️ Nada pendiente: día redondo"
TASKS_DOWN = "⏳ Ahora no puedo leer tu Todoist: lo de hoy, mañana en el plan"
MORE_DONE = "… y {count} más"
MORE_PENDING = " (y {count} más en Todoist)"
TALLY_TEXT = "📈 Por ahora: {done} de {total} del plan"
NOTE_MARK = "🎯"
FIXED_NOTE = "Día cerrado: mañana seguimos."
"""The note when the agent cannot write one."""
NOTE_LINES = 2
NOTE_CHARS = 200
QUESTION = "¿Cómo ha ido el día?"
LEFT_ALONE = "sin tocar"
"""What a task untouched says when the close expires: it stays as it is."""
NO_RATING = "sin valoración"
MAX_PENDING = MAX_ITEMS - 1
"""The tasks with buttons: the rating is the last item."""
MAX_DONE = 8

DONE = Option(key="done", label="✅ Hecha")
WILL_DO = Option(key="will", label="🗣️ La haré", approves=False)
TOMORROW = Option(key="tmrw", label="➡️ Mañana")
ALL_TOMORROW = GlobalOption(key="all", label="➡️ Todo lo pendiente a mañana", option="tmrw")
RATINGS = tuple(Option(key=f"r{n}", label=str(n)) for n in range(1, 6))


def note_lines(text: str) -> list[str]:
    """The lines of the agent's text that say something."""
    return [line for line in (" ".join(raw.split()) for raw in text.splitlines()) if line]


def cut_note(text: str | None) -> str | None:
    """The note as it goes in the close: its first `NOTE_LINES` lines, each at most
    `NOTE_CHARS`; `None` if there is nothing (the fixed note goes instead)."""
    lines = note_lines(text or "")[:NOTE_LINES]
    if not lines:
        return None
    return "\n".join(
        clip(line.removeprefix(NOTE_MARK).strip() or line, NOTE_CHARS) for line in lines
    )


@dataclass(frozen=True)
class CloseContent:
    view: CloseView
    note: str | None
    """The agent's note, already cut; `None` for the fixed one."""
    tomorrow: str
    """The line «🔮 Mañana»."""
    plan: frozenset[str] | None
    """The tasks of today's plan; `None` without a plan."""
    keys: Mapping[str, str]
    """The intention of each key task by its task's `normalized` title: [Hecha] on its task
    marks it done too, and [Mañana] counts as «hoy no» (decision 145)."""


def task_line(task: TodoistTask, table: dict[str, str], timezone: ZoneInfo) -> str:
    """`🧘 Estiramientos (21:30)`, or without a time, `📞 Llamar al seguro`."""
    icon = icon_of(task.title, table, {})
    text = f"{icon} {clip(task.title)}"
    if isinstance(task.due, datetime):
        text += f" ({hour(task.due.astimezone(timezone))})"
    return text


def _task_item(
    task: TodoistTask, day: date, content: CloseContent, table: dict[str, str], tz: ZoneInfo
) -> Item:
    data: dict[str, Any] = {
        "task_id": task.id,
        "day": day.isoformat(),
        "due": None if task.due is None else task.due.isoformat(),
        "in_plan": content.plan is not None and task.id in content.plan,
    }
    intent = content.keys.get(normalized(task.title))
    if intent is not None:
        data["intent"] = intent
    return Item(title=task_line(task, table, tz), options=(DONE, WILL_DO, TOMORROW), data=data)


def _done_lines(titles: Sequence[str], table: dict[str, str]) -> list[str]:
    shown = [f"{icon_of(title, table, {})} {clip(title)}" for title in titles[:MAX_DONE]]
    if len(titles) > MAX_DONE:
        shown.append(MORE_DONE.format(count=len(titles) - MAX_DONE))
    return shown or [NOTHING_DONE]


def provisional(view: CloseView, plan: Collection[str] | None) -> int:
    """The tasks of the plan done so far (the base of «📈 Por ahora»)."""
    return 0 if plan is None else len(set(plan) & view.done_ids)


def paint(
    content: CloseContent, day: date, timezone: ZoneInfo, table: dict[str, str]
) -> InteractiveMessage:
    """The close of `day`. `table` is the icon table, keyed by `normalized` title."""
    view = content.view
    body = [HEADER, ""]
    header: str | None = PENDING_HEADER
    pending = view.pending[:MAX_PENDING]
    if view.tasks_down:
        body.append(TASKS_DOWN)
        header, pending = None, ()
    else:
        body += [DONE_HEADER, *_done_lines(view.done, table)]
        if not view.pending:
            body += ["", PENDING_HEADER, NOTHING_PENDING]
            header = None
        elif len(view.pending) > MAX_PENDING:
            header += MORE_PENDING.format(count=len(view.pending) - MAX_PENDING)
    items = [_task_item(task, day, content, table, timezone) for task in pending]
    rating = Item(
        title=QUESTION,
        options=RATINGS,
        question=True,
        expired_text=NO_RATING,
        data={"day": day.isoformat()},
    )
    tally = None
    if content.plan:
        tally = Tally(
            text=TALLY_TEXT,
            base=provisional(view, content.plan),
            total=len(content.plan),
            option=DONE.key,
            items=tuple(i for i, item in enumerate(items) if (item.data or {}).get("in_plan")),
        )
    note = content.note if content.note else FIXED_NOTE
    footer = f"{NOTE_MARK} {note}\n{content.tomorrow}"
    return fit_titles(
        InteractiveMessage(
            body="\n".join(body),
            header=header,
            items=(*items, rating),
            footer=footer,
            style="plan",
            global_options=(ALL_TOMORROW,) if items else (),
            expired_text=LEFT_ALONE,
            tally=tally,
        )
    )
