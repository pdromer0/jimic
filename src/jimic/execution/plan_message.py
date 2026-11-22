"""From what the agent proposed to the interactive message that shows it (decision 1.11-a).

The code writes the exact list under the agent's answer: the title of the snapshot (move,
delete) or of the proposal (create), and when. The labels are the ones of the format of the plan:

- create: `n ✅` / `n ⏭`;
- move: `n 🔄 <when>` / `n 🆗 Déjalo`;
- delete: `n 🗑️ Bórrala` / `n 🆗 Déjala`;
- global: `[✅ Créalo todo] [⏭ Nada]`.

Create and move offer `ok`/`no`, delete offers `del`/`keep`, and the global row applies `ok` and
`no`: **a delete never offers what the global row applies**, so [Créalo todo] never deletes.
What an item does lives in its `data` (the proposal and its snapshot), never in what is shown.
"""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from jimic.agent.proposals import due_value, parse_due
from jimic.channel.message import GlobalOption, InteractiveMessage, Item, Option, fit_titles
from jimic.tools.todoist import Due

PLAN_KIND = "plan"
UNDO_KIND = "undo"
PROPOSAL_HEADER = "Te propongo"
DONE_HEADER = "✅ Hecho"
UNDO_EXPIRED = "Deshacer caducado"
"""What [Deshacer] says at midnight: a bare «caducado» would read as if the task had."""

OK = Option(key="ok", label="✅")
SKIP = Option(key="no", label="⏭", approves=False)
KEEP_MOVE = Option(key="no", label="🆗 Déjalo", approves=False)
DELETE = Option(key="del", label="🗑️ Bórrala")
KEEP = Option(key="keep", label="🆗 Déjala", approves=False)
UNDO = Option(key="undo", label="↩️ Deshacer")
GLOBAL = (
    GlobalOption(key="all", label="✅ Créalo todo", option="ok"),
    GlobalOption(key="none", label="⏭ Nada", option="no"),
)

ICONS = {"create": "➕", "move": "🔄", "delete": "🗑️"}


def proposed_due(text: str, timezone: ZoneInfo) -> Due:
    """The due of a proposal (`YYYY-MM-DD` or a local `YYYY-MM-DDTHH:MM`) as Todoist takes it."""
    value = parse_due(text)
    return value.replace(tzinfo=timezone) if isinstance(value, datetime) else value


def _hour(moment: datetime) -> str:
    return f"{moment.hour}:{moment.minute:02d}"


def when(due: Due | None, today: date, timezone: ZoneInfo) -> str:
    """`hoy`, `mañana 9:50`, `24/11`, `sin fecha`… in the local zone."""
    if due is None:
        return "sin fecha"
    if isinstance(due, datetime):
        local = due.astimezone(timezone)
        day, hour = local.date(), f" {_hour(local)}"
    else:
        day, hour = due, ""
    if day == today:
        name = "hoy"
    elif day == today + timedelta(days=1):
        name = "mañana"
    elif day == today - timedelta(days=1):
        name = "ayer"
    else:
        name = f"{day:%d/%m}"
    return name + hour


def move_label(due: Due, today: date, timezone: ZoneInfo) -> str:
    """`🔄 A las 9:50` (today), `🔄 Hoy`, `🔄 Mañana 9:50`, `🔄 24/11`…"""
    if isinstance(due, datetime):
        local = due.astimezone(timezone)
        if local.date() == today:
            return f"🔄 A las {_hour(local)}"
        text = when(local, today, timezone)
    else:
        text = when(due, today, timezone)
    return f"🔄 {text[0].upper()}{text[1:]}"


def _snapshot_due(entry: dict[str, Any]) -> Due | None:
    return due_value(entry["snapshot"]["due"])


def item_title(entry: dict[str, Any], today: date, timezone: ZoneInfo) -> str:
    """What the line of an item says; the exact task, as the code read it."""
    action = entry["action"]
    if action == "create":
        text = f"{entry['title']} · {when(proposed_due(entry['due'], timezone), today, timezone)}"
        if entry.get("duration_minutes"):
            text += f" ({entry['duration_minutes']} min)"
    elif action == "move":
        before = when(_snapshot_due(entry), today, timezone)
        after = when(proposed_due(entry["due"], timezone), today, timezone)
        text = f"{entry['snapshot']['title']} · {before} → {after}"
    else:
        text = f"{entry['snapshot']['title']} · {when(_snapshot_due(entry), today, timezone)}"
    return f"{ICONS[action]} {text}"


def plan_item(entry: dict[str, Any], today: date, timezone: ZoneInfo) -> Item:
    action = entry["action"]
    if action == "create":
        options = (OK, SKIP)
    elif action == "move":
        label = move_label(proposed_due(entry["due"], timezone), today, timezone)
        options = (Option(key="ok", label=label), KEEP_MOVE)
    else:
        options = (DELETE, KEEP)
    return Item(title=item_title(entry, today, timezone), options=options, data=entry)


def plan_message(
    entries: list[dict[str, Any]], today: date, timezone: ZoneInfo
) -> InteractiveMessage:
    """The proposal: one row per item and the global row; titles cut to fit if they must."""
    items = tuple(plan_item(entry, today, timezone) for entry in entries)
    return fit_titles(
        InteractiveMessage(header=PROPOSAL_HEADER, items=items, global_options=GLOBAL)
    )


def undo_message(title: str, data: dict[str, Any]) -> InteractiveMessage:
    """What was done at once, with its [Deshacer] until the end of the day (1.11-c)."""
    item = Item(title=title, options=(UNDO,), data=data)
    return fit_titles(
        InteractiveMessage(header=DONE_HEADER, items=(item,), expired_text=UNDO_EXPIRED)
    )
