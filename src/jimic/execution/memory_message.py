"""The message of what the agent proposed for Pedro's memory, and what its buttons do (task 1.12).

One message «Para tu memoria», one item per change or idea of the run (`MAX_MEMORY_ITEMS`):

- a change: `📝 <file> · añadir|cambiar|quitar`, then `Antes:` and `Después:` exact (`(nada)`
  where there is none), with `[💾 Guardar] [✏️ Cambiar] [✖️ No]`;
- an idea: `💡 <text>`, with `[📝 Apuntar] [✖️ No]`.

There is no global row: each change is saved with its own button. Titles are never cut
(`fit_titles` is not used): a proposal that does not fit whole in its worst state is refused to
the agent, which is the size cap of the notes. Everything is escaped when rendered.

The handlers (`MemoryHandlers`):

- [Guardar] checks again against the file as it is now and writes it (`changes.save`); if the
  «antes» is gone or the result is no longer valid, nothing is written and the approval ends
  `rejected` (`Declined`). After a YAML file, the live configuration reloads.
  The [Guardar] that finishes the onboarding (task 1.13) also sends `FINISHED_TEXT` and the
  goals by priority, from `objetivos.md`.
- [Cambiar] closes the proposal and asks Pedro how he wants it: the agent proposes again.
- [📝 Apuntar] adds the line to `ideas.md` (created 0600 if missing), as one more change.
- [No] only says so.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any

from jimic.channel.message import InteractiveMessage, Item, Option, ensure_fits
from jimic.channel.taps import Declined, Failed, HandlerOutcome, Show
from jimic.clock import Clock
from jimic.memory import changes
from jimic.memory.changes import ChangeRefused, Proposed
from jimic.memory.live import LiveConfig
from jimic.memory.onboarding import progress
from jimic.memory.personal import YAML_SCHEMAS

logger = logging.getLogger(__name__)

MEMORY_KIND = "memory"
HEADER = "Para tu memoria"
MAX_MEMORY_ITEMS = 3
"""Changes and ideas of one run: one message of Pedro rarely asks for more."""
NOTHING = "(nada)"
OPERATIONS = {"add": "añadir", "replace": "cambiar", "remove": "quitar", "undo": "deshacer"}
"""How an operation is named to Pedro (`undo` only in `/deshacer`)."""

SAVE = Option(key="save", label="💾 Guardar")
EDIT = Option(key="edit", label="✏️ Cambiar", approves=False)
NO = Option(key="no", label="✖️ No", approves=False)
NOTE = Option(key="note", label="📝 Apuntar")

SAVED = "💾 guardado"
NOTED = "📝 apuntada"
ASK_HOW = "✏️ dime cómo lo quieres"
DISCARDED = "✖️ descartado"
DROPPED_TEXT = (
    "No te ofrezco guardar nada de esto: en este mismo mensaje he leído tu calendario o tus"
    " tareas. Si lo quieres, pídemelo otra vez en otro mensaje."
)
"""What the code adds when a run read after proposing (decision 1.12-a)."""
FINISHED_TEXT = (
    "✅ Puesta en marcha terminada: los cinco ficheros pasan `jimic config check`. Ya no te la"
    " recuerdo; lo que quieras cambiar, me lo dices cuando sea."
)
"""After the [Guardar] that finishes the onboarding (task 1.13), with the goals by priority."""


def change_title(entry: dict[str, Any]) -> str:
    operation = OPERATIONS[str(entry["operation"])]
    before = entry.get("before") or NOTHING
    after = entry.get("after") or NOTHING
    return f"📝 {entry['file']} · {operation}\nAntes: {before}\nDespués: {after}"


def memory_item(entry: dict[str, Any]) -> Item:
    if entry["action"] == "idea":
        return Item(title=f"💡 {entry['text']}", options=(NOTE, NO), data=entry)
    return Item(title=change_title(entry), options=(SAVE, EDIT, NO), data=entry)


def memory_message(entries: list[dict[str, Any]]) -> InteractiveMessage:
    """The message of `entries`; raises `MessageTooLong` if it cannot fit whole."""
    message = InteractiveMessage(header=HEADER, items=tuple(memory_item(e) for e in entries))
    ensure_fits(message)
    return message


def proposed_of(entry: dict[str, Any]) -> Proposed:
    if entry["action"] == "idea":
        return Proposed(changes.IDEAS_FILE, "add", None, str(entry["line"]))
    return Proposed(
        str(entry["file"]),
        entry["operation"],
        entry.get("before"),
        entry.get("after"),
    )


class MemoryHandlers:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        clock: Clock,
        config_dir: Path,
        config: LiveConfig,
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._config_dir = config_dir
        self._config = config

    async def memory(self, item: Item, option: str, approval_id: int) -> HandlerOutcome:
        entry = item.data or {}
        action = entry.get("action")
        if option == "no":
            return DISCARDED
        if option == "edit" and action == "change":
            return ASK_HOW
        if (option, action) not in (("save", "change"), ("note", "idea")):
            logger.warning("memory: option %s does not fit the item", option)
            return Failed("invalid_option")
        proposed = proposed_of(entry)
        was_finished = progress(self._config_dir).finished
        try:
            changes.save(
                self._conn,
                self._clock,
                self._config_dir,
                proposed,
                str(entry.get("origin", "")),
                approval_id,
            )
        except ChangeRefused as refused:
            logger.info("memory: not saved (%s)", refused.code)
            return Declined(refused.result, NO.key)
        if proposed.file in YAML_SCHEMAS:
            self._config.reload()
        if action == "idea":
            return NOTED
        if not was_finished:
            state = progress(self._config_dir)
            if state.finished:
                logger.info("onboarding finished")
                return Show(f"{FINISHED_TEXT}\n\n{state.summary()}", result=SAVED)
        return SAVED
