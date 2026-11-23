"""The message of the plan of the day, painted by the code (decision 1.14-b; format of the spec).

```text
¡Pedro! <saludo>

📅 Tu día (de tu Todoist y tu calendario)
🍽️ 14:00 Comida

🧩 Te propongo añadir
1️⃣ 🏋️ Gimnasio · 12:00–13:15 (toca pierna)
2️⃣ ⚠️ «Webinar» 10:00 → 9:50 · manda el calendario

😅 <un hábito clave que hoy no entra>
🧠 Desde ayer he aprendido: …
🏋️ <una línea sobre el entreno>
```

The agent gives only its judgement (the greeting, what it proposes with its icon and a short
reason, what is left out, the training line, icons); the code reads «Tu día» itself, writes every
line, numbers the proposals and puts the buttons of the plan (`[n ✅] [n ✏️] [n ⏭]`, move and
delete as in the conversation, and `[✅ Créalo todo] [⏭ Hoy sin plan]`). The free text of the
agent never goes in the plan. At most `MAX_LINES` lines of text: if «Tu día» does not fit, its
last entries go as «… y N más». Without proposals, the plan is plain text, without buttons.

«🧠» (decision 1.14-d) is informative and comes from `changes`, without a model: the memory
changes saved since the previous plan, without those undone, the reversals or `ideas.md`.
"""

import sqlite3
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from jimic.agent.conversation import WEEKDAYS
from jimic.agent.proposals import due_value
from jimic.channel.message import InteractiveMessage, fit_titles, render
from jimic.clock import local_day_bounds, to_db
from jimic.execution.plan_message import DAY_GLOBAL, plan_item, proposed_due, when
from jimic.memory.changes import IDEAS_FILE
from jimic.memory.notices import last_sent_of
from jimic.plan.day import DayView, clip, entry_line, hour, icon_of, normalized

MAX_LINES = 25
GREETING_START = "¡Pedro!"
DEFAULT_GREETING = "Vamos con el día."
HEADER = "🧩 Te propongo añadir"
LEFT_OUT_MARK = "😅"
LEARNED_MARK = "🧠"
TRAINING_MARK = "🏋️"
UNMARKED_TEXT = "{count} de esta mañana sin marcar: las vemos en el cierre"
MORE_TEXT = "… y {count} más"
LEARNED_FILES = ("rutina.md", "objetivos.md", "preferencias.md", "intenciones.yaml", "limites.yaml")
"""The memory files whose changes count as learned (not `ideas.md`: an idea is not learning)."""
SEE_IT = "(lo ves y lo deshaces con /memoria)"
DEFAULT_ICONS = {"create": "▫️", "move": "🔄", "delete": "🗑️"}


@dataclass(frozen=True)
class PlanContent:
    """What the plan says, before it is painted."""

    greeting: str
    entries: Sequence[dict[str, Any]]
    """The proposals, as `propose_plan` leaves them in the run (with their snapshot)."""
    left_out: Sequence[str] = ()
    training: str | None = None
    learned: str | None = None
    day_icons: dict[str, str] | None = None
    """The agent's icons, keyed by `normalized` title."""


# --- The lines -------------------------------------------------------------------------------


def _without(mark: str, line: str) -> str:
    """The agent's line without the mark the code puts before it, if it wrote it too."""
    return line.removeprefix(mark).strip() or line


def greeting_line(greeting: str) -> str:
    text = " ".join(greeting.split())
    return text if text.startswith(GREETING_START) else f"{GREETING_START} {text}"


def short_when(due: object, today: date, timezone: ZoneInfo) -> str:
    """`9:50` today, `hoy` for today without a time, and as `when` for another day."""
    if isinstance(due, datetime) and due.astimezone(timezone).date() == today:
        return hour(due.astimezone(timezone))
    return when(due, today, timezone)  # pyright: ignore[reportArgumentType] - a due or None


def _span(entry: dict[str, Any], today: date, timezone: ZoneInfo) -> str:
    due = proposed_due(str(entry["due"]), timezone)
    minutes = entry.get("duration_minutes")
    if isinstance(due, datetime) and due.date() == today and minutes:
        end = due + timedelta(minutes=int(minutes))
        return f"{hour(due)}–{hour(end)}"
    return short_when(due, today, timezone)


def proposal_line(
    entry: dict[str, Any], today: date, timezone: ZoneInfo, table: dict[str, str], agent: dict
) -> str:
    """The line of a proposal in the plan; what it does lives in its data, not here."""
    action = entry["action"]
    reason = " ".join(str(entry["reason"]).split())
    if action == "create":
        icon = entry.get("icon") or icon_of(str(entry["title"]), table, agent)
        return f"{icon} {clip(str(entry['title']))} · {_span(entry, today, timezone)} ({reason})"
    icon = entry.get("icon") or DEFAULT_ICONS[action]
    title = clip(str(entry["snapshot"]["title"]))
    before = short_when(due_value(entry["snapshot"]["due"]), today, timezone)
    if action == "move":
        after = short_when(proposed_due(str(entry["due"]), timezone), today, timezone)
        return f"{icon} «{title}» {before} → {after} · {reason}"
    return f"{icon} «{title}» · {before} · {reason}"


def day_header(view: DayView, late: bool) -> str:
    what = "Lo que queda de tu día" if late else "Tu día"
    if view.calendar_down and view.tasks_down:
        return f"📅 {what}: ahora no puedo leer ni tu Todoist ni tu calendario"
    if view.tasks_down:
        return f"📅 {what} (de tu calendario; Todoist no responde)"
    if view.calendar_down:
        return f"📅 {what} (de tu Todoist; el calendario no responde)"
    return f"📅 {what} (de tu Todoist y tu calendario)"


def unmarked_line(count: int) -> str | None:
    if count <= 0:
        return None
    things = "1 cosa" if count == 1 else f"{count} cosas"
    return UNMARKED_TEXT.format(count=things)


def _day_lines(
    view: DayView, late: bool, table: dict[str, str], agent: dict[str, str], shown: int
) -> list[str]:
    lines = [day_header(view, late)]
    if not (view.calendar_down and view.tasks_down):
        entries = view.entries[:shown]
        lines += [entry_line(entry, table, agent) for entry in entries]
        hidden = len(view.entries) - len(entries)
        if hidden:
            lines.append(MORE_TEXT.format(count=hidden))
        elif not view.entries:
            lines.append(f"▫️ {'Nada más por hoy' if late else 'Nada apuntado para hoy'}")
    unmarked = unmarked_line(view.unmarked) if late else None
    if unmarked is not None:
        lines.append(unmarked)
    return lines


# --- The message -----------------------------------------------------------------------------


def _footer(content: PlanContent) -> str | None:
    lines = [f"{LEFT_OUT_MARK} {_without(LEFT_OUT_MARK, line)}" for line in content.left_out]
    if content.learned:
        lines.append(content.learned)
    if content.training:
        lines.append(f"{TRAINING_MARK} {_without(TRAINING_MARK, content.training)}")
    return "\n".join(lines) or None


def line_count(plan: InteractiveMessage | str) -> int:
    text = plan if isinstance(plan, str) else render(plan, 1).text
    return len(text.split("\n"))


def _build(
    content: PlanContent,
    view: DayView,
    late: bool,
    now: datetime,
    table: dict[str, str],
    shown: int,
) -> InteractiveMessage | str:
    timezone = now.tzinfo
    assert isinstance(timezone, ZoneInfo)
    agent = content.day_icons or {}
    body = "\n".join(
        [greeting_line(content.greeting), "", *_day_lines(view, late, table, agent, shown)]
    )
    footer = _footer(content)
    if not content.entries:
        return body if footer is None else f"{body}\n\n{footer}"
    today = now.date()
    items = tuple(
        plan_item(entry, today, timezone).model_copy(
            update={"title": proposal_line(entry, today, timezone, table, agent)}
        )
        for entry in content.entries
    )
    return InteractiveMessage(
        body=body,
        header=HEADER,
        items=items,
        footer=footer,
        style="plan",
        global_options=DAY_GLOBAL,
    )


def paint(
    content: PlanContent, view: DayView, late: bool, now: datetime, table: dict[str, str]
) -> InteractiveMessage | str:
    """The plan, with «Tu día» cut until it has `MAX_LINES` lines at most. `table` is the icon
    table, keyed by `normalized` title; `now` is aware and local. Raises `MessageTooLong` only if
    the titles of the proposals cannot fit in one Telegram message even when cut."""
    shown = len(view.entries)
    plan = _build(content, view, late, now, table, shown)
    while line_count(plan) > MAX_LINES and shown > 0:
        shown -= 1
        plan = _build(content, view, late, now, table, shown)
    return plan if isinstance(plan, str) else fit_titles(plan)


def icon_table(rows: Sequence[tuple[str, str]]) -> dict[str, str]:
    """The rows of `## Iconos` keyed by `normalized` task; the first row of a task wins."""
    table: dict[str, str] = {}
    for title, icon in rows:
        table.setdefault(normalized(title), icon)
    return table


# --- 🧠 (decision 1.14-d) ---------------------------------------------------------------------


def _since_text(previous: date, today: date) -> str:
    if previous == today:
        return "Desde el plan de antes"
    if previous == today - timedelta(days=1):
        return "Desde ayer"
    return f"Desde el {WEEKDAYS[previous.weekday()]} {previous:%d/%m}"


def learned_line(
    conn: sqlite3.Connection, now: datetime, plan_intents: Collection[str]
) -> str | None:
    """The line 🧠, or `None` if nothing was learned since the previous plan (the last one sent
    of `plan_intents`); without one, since the start of yesterday."""
    timezone = now.tzinfo
    assert isinstance(timezone, ZoneInfo)
    today = now.date()
    previous = last_sent_of(conn, plan_intents, now)
    since = (
        previous
        if previous is not None
        else local_day_bounds(today - timedelta(days=1), timezone)[0]
    )
    marks = ", ".join("?" for _ in LEARNED_FILES)
    rows = conn.execute(
        "SELECT file FROM changes WHERE created_at > ? AND created_at <= ?"
        " AND reverted_at IS NULL AND operation IN ('add', 'replace', 'remove')"
        f" AND file IN ({marks}) ORDER BY id",
        (to_db(since), to_db(now), *LEARNED_FILES),
    ).fetchall()
    if not rows:
        return None
    assert IDEAS_FILE not in LEARNED_FILES
    files = list(dict.fromkeys(file for (file,) in rows))
    text = _since_text(since.astimezone(timezone).date(), today)
    if len(rows) == 1:
        return f"{LEARNED_MARK} {text} he aprendido: {files[0]} · cambiado {SEE_IT}"
    return (
        f"{LEARNED_MARK} {text} he aprendido {len(rows)} cosas ({', '.join(files)}): {SEE_IT[1:-1]}"
    )


def late_after(window: tuple[time, time] | None, default_time: time, now: datetime) -> bool:
    """A late plan (H1-7): after the end of the window `(start, end)` or, without one, after its
    default time; compared to the minute, so a heartbeat a fraction of a second after 8:00 is
    still 8:00. A window with `start == end` is the whole day (decision 105): never late. One
    that crosses midnight is late only between its end and its start."""
    moment = now.time().replace(second=0, microsecond=0)
    if window is None:
        return moment > default_time
    start, end = window
    if start == end:
        return False
    if start < end:
        return moment > end
    return end < moment < start
