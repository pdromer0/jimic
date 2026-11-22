"""`/memoria` and `/deshacer` (CU-15): answered by the code, never by the agent.

- `/memoria`: a short summary by sections (routine, goals, preferences, intentions, limits and
  last changes), one item each with [📖 Ver], which sends the text of the file (or the list of
  changes) in as many messages as it needs, and stays there to be pressed again. Haiku writes the
  four lines of the personal files (`summary.py`); the limits and the changes, the code.
  `limites.yaml` is shown, never changed from here.
- `/deshacer`: the last 10 changes not reverted and the deletions of the last 30 days
  (`restore.py`), each with [↩️ Deshacer], in messages of 8 at most. Undoing a change of a YAML
  file reloads the live configuration at once.

A command is the whole message (`/memoria`, or `/memoria@bot`) and counts as Pedro writing. Logs
say codes and counts, never the files.
"""

import logging
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jimic.agent.proposals import expires_at
from jimic.channel.bot import Outgoing, Reply
from jimic.channel.message import (
    MAX_ITEMS,
    InteractiveMessage,
    Item,
    Option,
    fit_titles,
)
from jimic.channel.taps import Failed, HandlerOutcome, Show, TapHandler
from jimic.clock import Clock
from jimic.execution import restore
from jimic.memory import changes, conversations, usage
from jimic.memory.changes import ChangeRefused, ChangeRow
from jimic.memory.live import LiveConfig
from jimic.memory.personal import YAML_SCHEMAS
from jimic.memory.usage import TokenUsage
from jimic.tools.todoist import TodoistTools

logger = logging.getLogger(__name__)

VIEW_KIND = "memory_view"
UNDO_LIST_KIND = "undo_list"
MEMORY_COMMAND = "/memoria"
UNDO_COMMAND = "/deshacer"
LAST_CHANGES = 10
MAX_DELETIONS_SHOWN = 24
"""Three messages of deletions at most; the older ones are only counted."""

VIEW = Option(key="view", label="📖 Ver")
UNDO = Option(key="undo", label="↩️ Deshacer")
MEMORY_HEADER = "Tu memoria"
UNDO_HEADER = "Para deshacer"
NO_SUMMARY = "No he podido resumir ahora: despliega cada sección."
NOTHING_TO_UNDO = "No hay nada que deshacer: ni cambios de memoria ni borrados de este mes."
NO_CHANGES = "Aún no hay cambios de memoria."

SECTIONS = (
    ("rutina.md", "Rutina"),
    ("objetivos.md", "Metas"),
    ("preferencias.md", "Preferencias"),
    ("intenciones.yaml", "Intenciones"),
    ("limites.yaml", "Límites"),
)
CHANGES_SECTION = "changes"
OPERATIONS = {"add": "añadir", "replace": "cambiar", "remove": "quitar", "undo": "deshacer"}
UNDO_RESULTS = {
    "undone": "↩️ deshecho",
    "already": "ya estaba deshecho",
    "changed": "ha cambiado: edítalo a mano",
    "invalid": "no vale: edítalo a mano",
}

type Summarizer = Callable[
    [dict[str, str | None]], Awaitable[tuple[dict[str, str] | None, TokenUsage | None]]
]
"""`summary.summarize` with its client and model, or the tests' double."""


def int_of(value: object) -> int:
    """An id stored in the data of an item; 0 (no row has it) if it is not an integer."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def command_of(text: str) -> str | None:
    """The command the whole message is, or `None`."""
    word = text.strip()
    if " " in word or "\n" in word:
        return None
    name = word.split("@", 1)[0]
    return name if name in (MEMORY_COMMAND, UNDO_COMMAND) else None


def _snippet(text: str, length: int = 60) -> str:
    line = " ".join(text.split()) or "(nada)"
    return line if len(line) <= length else line[: length - 1] + "…"


def change_lines(rows: list[ChangeRow], timezone: ZoneInfo) -> str:
    """The list of changes that [📖 Ver] sends: date, file, operation, before and after."""
    if not rows:
        return NO_CHANGES
    lines = []
    for row in rows:
        moment = datetime.fromisoformat(row.created_at).astimezone(timezone)
        lines.append(
            f"{moment:%d/%m %H:%M} · {row.file} · {OPERATIONS.get(row.operation, row.operation)}"
            f"\n  Antes: {_snippet(row.before, 200)}\n  Después: {_snippet(row.after, 200)}"
        )
    return "\n\n".join(lines)


def limits_line(config: LiveConfig) -> str:
    limits = config.limits
    quiet = limits.quiet_hours
    return (
        f"{limits.max_messages_per_day} mensajes al día, silencio de"
        f" {quiet.start:%H:%M} a {quiet.end:%H:%M}, {limits.max_deletions_per_day} borrados"
        " al día (se cambia a mano o a más discreto conversando)"
    )


def pages(items: list[Item], header: str) -> list[InteractiveMessage]:
    """The items in messages of `MAX_ITEMS` at most, numbered `(1/2)` when there is more than
    one; the titles are cut to fit (what an item does lives in its data)."""
    chunks = [items[start : start + MAX_ITEMS] for start in range(0, len(items), MAX_ITEMS)]
    total = len(chunks)
    return [
        fit_titles(
            InteractiveMessage(
                header=header if total == 1 else f"{header} ({number}/{total})",
                items=tuple(chunk),
            )
        )
        for number, chunk in enumerate(chunks, start=1)
    ]


class Commands:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        clock: Clock,
        timezone: ZoneInfo,
        config_dir: Path,
        config: LiveConfig,
        todoist: TodoistTools,
        summarize: Summarizer,
        summary_model: str,
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._timezone = timezone
        self._config_dir = config_dir
        self._config = config
        self._todoist = todoist
        self._summarize = summarize
        self._summary_model = summary_model

    @property
    def handlers(self) -> dict[str, TapHandler]:
        return {VIEW_KIND: self.view, UNDO_LIST_KIND: self.undo}

    async def reply(self, command: str) -> str | Reply:
        now = self._clock.now().astimezone(self._timezone)
        conversations.note_message(self._conn, now.date(), now)
        logger.info("command %s", command.removeprefix("/"))
        if command == MEMORY_COMMAND:
            return await self.memory()
        return self.undo_list()

    # --- /memoria ---------------------------------------------------------------------------

    def _read(self, name: str) -> str | None:
        try:
            return changes.read_text(self._config_dir, name)
        except ChangeRefused:
            return None

    async def memory(self) -> Reply:
        texts = {name: self._read(name) for name, _ in SECTIONS[:4]}
        lines, spent = await self._summarize(texts)
        if spent is not None:
            day = self._clock.now().astimezone(self._timezone).date()
            usage.record(self._conn, day, self._summary_model, spent)
        items = []
        for name, label in SECTIONS:
            line = limits_line(self._config) if name == "limites.yaml" else (lines or {}).get(name)
            title = f"📖 {label}: {line}" if line else f"📖 {label}"
            items.append(Item(title=title, options=(VIEW,), data={"section": name}))
        count = len(changes.recent(self._conn, LAST_CHANGES))
        items.append(
            Item(
                title=f"📖 Últimos cambios: {count}" if count else "📖 Últimos cambios: ninguno",
                options=(VIEW,),
                data={"section": CHANGES_SECTION},
            )
        )
        message = fit_titles(
            InteractiveMessage(
                body=None if lines is not None else NO_SUMMARY,
                header=MEMORY_HEADER,
                items=tuple(items),
            )
        )
        return Reply(None, Outgoing(VIEW_KIND, message, expires_at(self._clock, self._timezone)))

    async def view(self, item: Item, option: str, approval_id: int) -> HandlerOutcome:
        section = str((item.data or {}).get("section", ""))
        if option != "view":
            return Failed("invalid_option")
        if section == CHANGES_SECTION:
            rows = changes.recent(self._conn, LAST_CHANGES)
            return Show(change_lines(rows, self._timezone))
        if section not in dict(SECTIONS):
            return Failed("unknown_section")
        text = self._read(section)
        logger.info("memory view of %s", section)
        return Show(f"{section}:\n\n{text}" if text else f"{section}: aún no existe.")

    # --- /deshacer --------------------------------------------------------------------------

    def undo_list(self) -> str | Reply:
        items = []
        for row in changes.recent(self._conn, LAST_CHANGES):
            moment = datetime.fromisoformat(row.created_at).astimezone(self._timezone)
            fragment = row.after if row.operation in ("add", "replace") else row.before
            title = (
                f"📝 {moment:%d/%m} {row.file} · {OPERATIONS.get(row.operation, row.operation)}:"
                f" {_snippet(fragment)}"
            )
            items.append(Item(title=title, options=(UNDO,), data={"change": row.id}))
        deleted = restore.offered(self._conn, self._clock)
        for row in deleted[:MAX_DELETIONS_SHOWN]:
            moment = datetime.fromisoformat(row.deleted_at).astimezone(self._timezone)
            items.append(
                Item(
                    title=f"🗑️ {moment:%d/%m} {_snippet(row.content)}",
                    options=(UNDO,),
                    data={"deleted": row.id},
                )
            )
        if not items:
            return NOTHING_TO_UNDO
        messages = pages(items, UNDO_HEADER)
        expires = expires_at(self._clock, self._timezone)
        outgoing = [Outgoing(UNDO_LIST_KIND, message, expires) for message in messages]
        hidden = len(deleted) - MAX_DELETIONS_SHOWN
        text = f"Y {hidden} borrados más antiguos, que no enseño." if hidden > 0 else None
        logger.info("undo list: %d item(s), %d message(s)", len(items), len(messages))
        return Reply(text, outgoing[0], more=tuple(outgoing[1:]))

    async def undo(self, item: Item, option: str, approval_id: int) -> HandlerOutcome:
        data = item.data or {}
        if option != "undo":
            return Failed("invalid_option")
        if "deleted" in data:
            return await restore.restore(
                self._conn, self._clock, self._todoist, int_of(data["deleted"])
            )
        change = changes.get(self._conn, int_of(data.get("change")))
        if change is None:
            return Failed("unknown_change")
        outcome = changes.undo(self._conn, self._clock, self._config_dir, change.id)
        if outcome == "unknown":
            return Failed("unknown_change")
        if outcome == "undone" and change.file in YAML_SCHEMAS:
            self._config.reload()
        return UNDO_RESULTS[outcome]
