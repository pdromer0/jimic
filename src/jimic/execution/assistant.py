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
   external content after proposing, nothing is offered and the code says so.

Everything an approval offers expires at the end of the local day (RF-07, 1.11-c).
"""

import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from jimic.agent.conversation import Conversation
from jimic.agent.proposals import expires_at
from jimic.agent.tools import RunState
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

logger = logging.getLogger(__name__)

NOTHING_PENDING = "Eso ya estaba resuelto."


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
    ) -> None:
        self._commands = commands
        self._conversation = conversation
        self._executor = executor
        self._conn = conn
        self._clock = clock
        self._timezone = timezone

    async def reply(self, incoming: Incoming) -> str | Reply:
        command = command_of(incoming.text)
        if command is not None and self._commands is not None:
            return await self._commands.reply(command)
        found = confirm.target(self._conn, self._clock.now(), incoming)
        if found is not None:
            return await self._confirm(found)
        turn = await self._conversation.respond(incoming.text)
        run = turn.run
        if run is None:
            return turn.text
        text = f"{turn.text}\n\n{DROPPED_TEXT}" if run.memory_dropped else turn.text
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
