"""What the Telegram channel calls with Pedro's text (task 1.11).

1. A written «sí» that confirms a deletion (`confirm.py`) is executed by the code, through the
   same path as its button, and never reaches the agent.
2. Anything else goes to the conversation. If the run proposed something, the answer goes as one
   message with the exact list under it (decision 1.11-a): with its buttons, or, when every
   condition of decision 1.11-b holds, executed at once with its [Deshacer]. If doing it at once
   fails, the proposal goes with its buttons and the failure as a note.

Everything an approval offers expires at the end of the local day (RF-07, 1.11-c).
"""

import logging
import sqlite3
from zoneinfo import ZoneInfo

from jimic.agent.conversation import Conversation
from jimic.agent.proposals import expires_at
from jimic.channel import store
from jimic.channel.bot import Incoming, Outgoing, Reply
from jimic.channel.message import Tap
from jimic.channel.taps import Failed, failure_note, process_tap
from jimic.clock import Clock
from jimic.execution import confirm
from jimic.execution.execute import Executor
from jimic.execution.immediate import runs_now
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
    ) -> None:
        self._conversation = conversation
        self._executor = executor
        self._conn = conn
        self._clock = clock
        self._timezone = timezone

    async def reply(self, incoming: Incoming) -> str | Reply:
        found = confirm.target(self._conn, self._clock.now(), incoming)
        if found is not None:
            return await self._confirm(found)
        turn = await self._conversation.respond(incoming.text)
        run = turn.run
        if run is None or not run.items:
            return turn.text
        today = self._clock.now().astimezone(self._timezone).date()
        expires = expires_at(self._clock, self._timezone)
        message = plan_message(run.items, today, self._timezone)
        if runs_now(run):
            done = await self._executor.now(run.items[0])
            if not isinstance(done, Failed):
                logger.info("proposal executed at once")
                undo = undo_message(done.title, done.undo)
                fallback = f"✅ Hecho: {done.title}"
                return Reply(turn.text, Outgoing(UNDO_KIND, undo, expires, if_unsent=fallback))
            logger.info("proposal at once failed (%s)", done.code)
            item = message.items[0].model_copy(update={"note": failure_note(done.code)})
            message = message.model_copy(update={"items": (item,)})
        return Reply(turn.text, Outgoing(PLAN_KIND, message, expires))

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
