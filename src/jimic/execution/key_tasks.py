"""What each button of a key task does (decisions 1.15-c to 1.15-g), by the code, without model.

- [🏃 Voy ahora]: a `going` mark; the insistence pauses as long as the habit lasts. «¡Vamos! 💪».
  Its row goes and [Ya entrené] (or [Hecho]) and [Hoy no] stay, for later; so with [Más tarde].
- [⏰ Más tarde]: «¿A qué hora? ⏰». The tap is remembered (`EditContext`): Pedro's next message
  goes to the agent with a line of the code that names the task and its id, so the agent can
  propose the move without reading Todoist (decision 1.15-d). Meanwhile the task stays at its hour.
- [✅ Ya entrené] / [✅ Hecho]: completes today's task in Todoist (read afresh: only if it is still
  open and of today) and writes a `done` mark. «¡Hecho! 💪 Una menos», with [↩️ Deshacer].
- [⏭ Hoy no]: a `not_today` mark, no reason (decision 1.15-f). «Vale, hoy nada de …», with
  [↩️ Deshacer].

[↩️ Deshacer] (`key_undo`) takes the mark away and, if JimiC closed the task, opens it again: a
normal one is reopened; a recurring one (which completing sent to its next day, and which reopening
does not bring back: contract of 23-nov-2026) is moved back to the due it had, if it is still where
JimiC left it. If Todoist changed meanwhile, only the mark goes. Everything expires at the end of
the local day. Marks are on the local day.
"""

import logging
import sqlite3
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from jimic.agent.proposals import due_value, same_due
from jimic.channel.message import InteractiveMessage, Item, Option
from jimic.channel.taps import Answer, Failed, Followup, HandlerOutcome, TapHandler
from jimic.clock import Clock, local_day_bounds
from jimic.execution.execute import UNDONE
from jimic.execution.plan_message import UNDO_EXPIRED
from jimic.execution.rules import due_day
from jimic.heartbeat.key_message import KEY_KIND
from jimic.memory import key_marks
from jimic.tools.failures import ToolFailure
from jimic.tools.todoist import TASK_NOT_FOUND, Due, TodoistTask, TodoistTools

logger = logging.getLogger(__name__)

KEY_UNDO_KIND = "key_undo"
UNDO = Option(key="undo", label="↩️ Deshacer")

GOING = "🏃 a por ello"
GO_TEXT = "¡Vamos! 💪"
LATER = "⏰ ¿a qué hora?"
LATER_TEXT = "¿A qué hora? ⏰"
DONE = "✅ hecho"
DONE_TEXT = "¡Hecho! 💪 Una menos"
SKIPPED = "⏭ hoy no"
SKIP_TEXT = "Vale, hoy nada de {habit}. 🛋️"

MARK_ONLY = "↩️ deshecho (Todoist, sin tocar)"
"""[Deshacer] of a done whose task changed or is gone: the mark goes, Todoist is left alone."""

LATER_LINE = "Pedro tocó ⏰ Más tarde en: {label}; su mensaje dice a qué hora moverla"
"""The line of the code before Pedro's next message (decision 1.15-d)."""


def later_label(data: dict[str, Any], timezone: ZoneInfo) -> str:
    """`«Gimnasio» (hoy 12:00, tarea t-1)`, or without a task of today, say so."""
    habit = data.get("habit", "?")
    task_id = data.get("task_id")
    if not task_id:
        return f"«{habit}» (hoy no tiene tarea en Todoist)"
    due = due_value(data.get("task_due"))
    when = "hoy"
    if isinstance(due, datetime):
        when += f" {due.astimezone(timezone):%H:%M}"
    return f"«{habit}» ({when}, tarea {task_id})"


def still_open(item: Item) -> Item:
    """After [Voy ahora] or [Más tarde]: the item stays, with [Ya entrené] (or [Hecho]) and
    [Hoy no] to tap when it is done or when Pedro gives up for today."""
    return item.model_copy(
        update={"options": tuple(o for o in item.options if o.key in ("done", "skip"))}
    )


def key_undo_message(body: str, item: Item, data: dict[str, Any]) -> InteractiveMessage:
    return InteractiveMessage(
        body=body,
        items=(Item(title=item.title, options=(UNDO,), data=data),),
        style="key",
        expired_text=UNDO_EXPIRED,
    )


class KeyTaskActions:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        clock: Clock,
        timezone: ZoneInfo,
        todoist: TodoistTools,
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._timezone = timezone
        self._todoist = todoist

    @property
    def handlers(self) -> dict[str, TapHandler]:
        return {KEY_KIND: self.tap, KEY_UNDO_KIND: self.undo}

    def _now(self) -> datetime:
        return self._clock.now().astimezone(self._timezone)

    def _followup(self, body: str, item: Item, data: dict[str, Any]) -> Followup:
        expires = local_day_bounds(self._now().date(), self._timezone)[1]
        return Followup(KEY_UNDO_KIND, key_undo_message(body, item, data), expires, body)

    # --- The four buttons -----------------------------------------------------------------------

    async def tap(self, item: Item, option: str, approval_id: int) -> HandlerOutcome:
        data = item.data or {}
        intent = str(data.get("intent", ""))
        now = self._now()
        if option == "go":
            key_marks.record(self._conn, now.date(), intent, "going", now)
            logger.info("key task: going")
            return Answer(GOING, GO_TEXT, then=still_open(item))
        if option == "later":
            logger.info("key task: later")
            return Answer(LATER, LATER_TEXT, then=still_open(item))
        if option == "skip":
            mark = key_marks.record(self._conn, now.date(), intent, "not_today", now)
            logger.info("key task: not today")
            text = SKIP_TEXT.format(habit=str(data.get("habit", "")).lower())
            return Answer(SKIPPED, self._followup(text, item, {"action": "skip", "mark": mark}))
        if option == "done":
            return await self._done(item, data, intent, now)
        logger.warning("key task: option %s does not fit", option)
        return Failed("invalid_option")

    async def _done(
        self, item: Item, data: dict[str, Any], intent: str, now: datetime
    ) -> HandlerOutcome:
        """Complete today's task if it is still open and of today, then the mark."""
        undo: dict[str, Any] = {"action": "done"}
        task_id = data.get("task_id")
        if task_id:
            try:
                closed = await self._close(str(task_id), now.date())
            except ToolFailure as error:
                return Failed(error.code)
            if closed is not None:
                undo |= {"task_id": str(task_id)} | closed
        undo["mark"] = key_marks.record(
            self._conn, now.date(), intent, "done", now, undo.get("task_id")
        )
        logger.info("key task: done (%s)", "closed" if "task_id" in undo else "mark only")
        return Answer(DONE, self._followup(DONE_TEXT, item, undo))

    async def _close(self, task_id: str, today: date) -> dict[str, Any] | None:
        """Complete the task if it is open and of today; what its [Deshacer] needs, or `None`
        if there was nothing to close (done in the app, moved or gone)."""
        current = await self._todoist.reader.get_task(task_id)
        if current is None or current.completed or current.due is None:
            return None
        if due_day(current.due) != today:
            return None
        await self._todoist.writer.complete(task_id)
        if not current.is_recurring:
            return {"recurring": False}
        # Closed already: a failed read now must not fail the tap (no mark, no [Deshacer]).
        try:
            after = await self._todoist.reader.get_task(task_id)
        except ToolFailure as error:
            logger.info("key task: next due unknown (%s)", error.code)
            after = None
        return {
            "recurring": True,
            "before": current.due.isoformat(),
            "after": None if after is None or after.due is None else after.due.isoformat(),
        }

    # --- [↩️ Deshacer] --------------------------------------------------------------------------

    async def undo(self, item: Item, option: str, approval_id: int) -> HandlerOutcome:
        """Take the mark away and, if JimiC closed the task and it is still as it left it, open it
        again. If Todoist changed (or the task is gone), only the mark goes: Pedro asked to undo
        the done."""
        data = item.data or {}
        if option != "undo":
            return Failed("invalid_option")
        untouched = False
        if data.get("action") == "done" and data.get("task_id"):
            try:
                untouched = not await self._reopen(data)
            except ToolFailure as error:
                if error.code != TASK_NOT_FOUND:
                    return Failed(error.code)  # nothing undone: it can be tried again
                untouched = True
        key_marks.remove(self._conn, int(str(data["mark"])))
        logger.info("key task: undone (%s%s)", data.get("action"), ", mark only" * untouched)
        return MARK_ONLY if untouched else UNDONE

    async def _reopen(self, data: dict[str, Any]) -> bool:
        """Open again what JimiC closed; `False` if the task changed or is gone (left alone)."""
        task_id = str(data["task_id"])
        current = await self._todoist.reader.get_task(task_id)
        if current is None:
            return False
        if not data.get("recurring"):
            if current.completed:
                await self._todoist.writer.reopen(task_id)
            return True
        before = due_value(data.get("before"))
        if not _where_jimic_left_it(current, data.get("after"), before):
            logger.info("key task: Todoist changed, left alone")
            return False
        await self._todoist.writer.move(task_id, before)
        now = self._now()
        key_marks.note_moved(self._conn, now.date(), task_id, now)
        return True


def _where_jimic_left_it(task: TodoistTask, after: object, before: Due | None) -> bool:
    """The task is still where completing it left it; if that was not read, still on a day
    after the one it had."""
    current = None if task.due is None else task.due.isoformat()
    if after is not None:
        return same_due(current, str(after))
    return task.due is not None and before is not None and due_day(task.due) > due_day(before)
