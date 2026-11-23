"""What the Telegram channel calls with Pedro's text (task 1.11).

0. `/memoria` and `/deshacer` are answered by the code (`commands.py`), never by the agent.
1. A written «sí» that confirms a deletion (`confirm.py`) is executed by the code, through the
   same path as its button, and never reaches the agent.
2. Anything else goes to the conversation. If the run proposed something, the answer goes as one
   message with the exact list under it (decision 1.11-a): with its buttons, or, when every
   condition of decision 1.11-b holds, executed at once with its [Deshacer]. If doing it at once
   fails, the proposal goes with its buttons and the failure as a note.
3. What the run proposed for the memory (task 1.12) goes as «Para tu memoria» with [Guardar] or
   [📝 Apuntar]: under the answer, or after the plan as a second message. If the run read
   external content after proposing, nothing is offered and the code says so. If the asymmetric
   rule refused a change of `limites.yaml`, the code says which key Pedro may change by hand
   (decision 1.13-a): there is no special mode for the onboarding.

The onboarding (CU-12, task 1.13), with a `config_dir`:

- `/puesta_en_marcha` (or `/puesta-en-marcha`, which Telegram does not link) creates the Markdown
  files that are missing and goes to the conversation as one more message of the day, with its
  session, the origin rule and the wait of the heartbeat. It uses up the suggestion of the day.
  Once the onboarding is finished, the code answers it alone.
- While it is not finished, the first answer of the agent of each local day that ends well
  carries one more line, `SUGGESTION` (decision 1.13-b). It is marked in SQLite, so a restart
  does not repeat it. It never goes with `/memoria`, `/deshacer`, a written «sí» or a failed
  run, nor in what the heartbeat sends, and it is no message of its own: it costs no quota.

Everything an approval offers expires at the end of the local day (RF-07, 1.11-c).
"""

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jimic.agent.conversation import Conversation, Turn
from jimic.agent.proposals import expires_at
from jimic.agent.tools import ManualChange, RunState
from jimic.channel import store
from jimic.channel.bot import Incoming, Outgoing, Reply
from jimic.channel.message import Tap
from jimic.channel.taps import Failed, failure_note, process_tap
from jimic.clock import Clock
from jimic.execution import confirm
from jimic.execution.commands import Commands, command_of
from jimic.execution.execute import Executor
from jimic.execution.immediate import runs_now
from jimic.execution.memory_message import DROPPED_TEXT, MEMORY_KIND, memory_message
from jimic.execution.plan_message import (
    PLAN_KIND,
    UNDO_KIND,
    plan_message,
    undo_message,
)
from jimic.memory import conversations
from jimic.memory.onboarding import create_skeletons, progress
from jimic.memory.personal import LIMITS_FILE

logger = logging.getLogger(__name__)

NOTHING_PENDING = "Eso ya estaba resuelto."
ONBOARDING_COMMANDS = ("/puesta_en_marcha", "/puesta-en-marcha")
SUGGESTION = "Cuando quieras, hacemos la puesta en marcha: /puesta_en_marcha"
ALREADY_DONE = (
    "La puesta en marcha ya está hecha ✅. Si quieres cambiar algo, dímelo; lo que tengo, en"
    " /memoria."
)
NO_SKELETONS = (
    "No he podido crear los ficheros de la puesta en marcha. Mira `jimic config check` y prueba"
    " otra vez."
)
MANUAL_TEXT = (
    "Eso no te lo puedo guardar: en limites.yaml solo puedo hacerme más discreto. Si lo quieres,"
    " cámbialo tú a mano en limites.yaml, sin reiniciar nada: {changes}. Más adelante, la sesión"
    " de configuración te ayudará con estos cambios."
)
MAX_MANUAL_LINE = 200
"""Longer than this, the fragment the agent proposed is not quoted: only its keys."""


def manual_note(manual: list[ManualChange]) -> str | None:
    """The text for what the asymmetric rule refused (1.13-a): the key and the line Pedro would
    write, never the file."""
    parts = []
    for change in manual:
        keys = ", ".join(change.keys)
        line = (change.after or "").strip()
        if line and len(line) <= MAX_MANUAL_LINE and "\n" not in line:
            part = f"{keys}, que quedaría «{line}»"
        else:
            part = keys
        if part not in parts:
            parts.append(part)
    return MANUAL_TEXT.format(changes="; ".join(parts)) if parts else None


class Assistant:
    def __init__(
        self,
        *,
        conversation: Conversation,
        executor: Executor,
        conn: sqlite3.Connection,
        clock: Clock,
        timezone: ZoneInfo,
        commands: Commands | None = None,
        config_dir: Path | None = None,
    ) -> None:
        """Without `config_dir` there is no onboarding: neither its command nor its
        suggestion."""
        self._commands = commands
        self._config_dir = config_dir
        self._conversation = conversation
        self._executor = executor
        self._conn = conn
        self._clock = clock
        self._timezone = timezone

    async def reply(self, incoming: Incoming) -> str | Reply:
        command = command_of(incoming.text)
        if command is not None and self._commands is not None:
            return await self._commands.reply(command)
        if self._config_dir is not None and command_of(incoming.text, ONBOARDING_COMMANDS):
            return await self._onboarding(incoming.text, self._config_dir)
        found = confirm.target(self._conn, self._clock.now(), incoming)
        if found is not None:
            return await self._confirm(found)
        return await self._converse(incoming.text, suggest=True)

    def _today(self) -> date:
        return self._clock.now().astimezone(self._timezone).date()

    async def _onboarding(self, text: str, config_dir: Path) -> str | Reply:
        """`/puesta_en_marcha`: the skeletons, then the conversation (task 1.13)."""
        day = self._today()
        if progress(config_dir).finished:
            conversations.note_message(self._conn, day, self._clock.now())
            logger.info("onboarding: already finished")
            return ALREADY_DONE
        try:
            created = create_skeletons(config_dir)
        except OSError as error:
            conversations.note_message(self._conn, day, self._clock.now())
            logger.error("onboarding: skeletons failed (%s)", type(error).__name__)
            return NO_SKELETONS
        logger.info("onboarding: %d skeleton(s) created", sum(new for _, new in created))
        reply = await self._converse(text, suggest=False)
        # Pedro launched it: no suggestion today (the row exists, the turn noted the message).
        conversations.claim_suggestion(self._conn, day)
        return reply

    def _suggestion(self, day: date) -> bool:
        """Whether this answer carries the suggestion (1.13-b): the onboarding is not finished
        and it was not suggested on `day` yet; claiming it marks it."""
        if self._config_dir is None or progress(self._config_dir).finished:
            return False
        if not conversations.claim_suggestion(self._conn, day):
            return False
        logger.info("onboarding suggested")
        return True

    async def _converse(self, message: str, suggest: bool) -> str | Reply:
        turn = await self._conversation.respond(message)
        return await self._reply(turn, suggest)

    async def _reply(self, turn: Turn, suggest: bool) -> str | Reply:
        """The answer of a turn, with what its run proposed under it."""
        day = self._today()
        run = turn.run
        if run is None:
            return turn.text
        notes = [turn.text]
        if run.memory_dropped:
            notes.append(DROPPED_TEXT)
        # If the agent then offered a change of limites.yaml that holds, that is the answer.
        offered = any(entry.get("file") == LIMITS_FILE for entry in run.memory)
        manual = None if offered else manual_note(run.manual)
        if manual is not None:
            logger.info("manual change explained: %d", len(run.manual))
            notes.append(manual)
        if suggest and self._suggestion(day):
            notes.append(SUGGESTION)
        text = "\n\n".join(notes)
        expires = expires_at(self._clock, self._timezone)
        memory = self._memory(run, expires)
        if not run.items:
            return Reply(text, memory) if memory is not None else text
        plan = await self._plan(run, expires)
        return Reply(text, plan, more=() if memory is None else (memory,))

    def _memory(self, run: RunState, expires: datetime) -> Outgoing | None:
        """What the run proposed for the memory, with its buttons (task 1.12); the tools already
        checked that it fits."""
        if not run.memory:
            return None
        logger.info("memory proposal: %d item(s)", len(run.memory))
        return Outgoing(MEMORY_KIND, memory_message(run.memory), expires)

    async def _plan(self, run: RunState, expires: datetime) -> Outgoing:
        today = self._clock.now().astimezone(self._timezone).date()
        message = plan_message(run.items, today, self._timezone)
        if runs_now(run):
            done = await self._executor.now(run.items[0])
            if not isinstance(done, Failed):
                logger.info("proposal executed at once")
                undo = undo_message(done.title, done.undo)
                fallback = f"✅ Hecho: {done.title}"
                return Outgoing(UNDO_KIND, undo, expires, if_unsent=fallback)
            logger.info("proposal at once failed (%s)", done.code)
            item = message.items[0].model_copy(update={"note": failure_note(done.code)})
            message = message.model_copy(update={"items": (item,)})
        return Outgoing(PLAN_KIND, message, expires)

    async def _confirm(self, found: confirm.Target) -> Reply:
        """The «sí»: a tap of `del` on that item, by the code; it counts as Pedro writing."""
        now = self._clock.now().astimezone(self._timezone)
        conversations.note_message(self._conn, now.date(), now)
        tap = Tap(found.approval_id, found.item, "del")
        result = await process_tap(self._conn, self._clock, self._executor.handlers, tap)
        logger.info("written yes: %s", result.code)
        approval = store.load(self._conn, found.approval_id)
        text = NOTHING_PENDING
        if approval is not None:
            item = approval.message.items[found.item - 1]
            status = item.result or item.note
            if status is not None and result.code not in ("in_progress", "nothing_to_do"):
                text = f"{item.title} — {status}"
        return Reply(text, refresh=(found.approval_id,))
