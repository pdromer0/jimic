"""What each button of the close of the day does (CU-14, task 1.16), by the code, without model.

- [✅ Hecha]: completes the task in Todoist if it is still open and of the day of the close (read
  afresh; until the close expires, also after midnight). If Pedro already completed it, it counts
  all the same. The task of a key task also gets its `done` mark (if not, the heartbeat would take
  a recurring one for a jump). «✅ hecha», and a message with its [↩️ Deshacer].
- [🗣️ La haré]: **never moves nor marks** the task (H1-8); only notes `will_do` in the plan.
- [➡️ Mañana]: moves it to the same time tomorrow or to the next free moment (`close/day.py`),
  reading tomorrow's calendar and Todoist afresh at each tap; if either cannot be read, nothing
  moves and the item stays to try again. Out of today, the task of a key task is «hoy no»
  (decision 145). Its [↩️ Deshacer] (Pedro's review of the plan, decision 97: every move the code
  makes can be undone) takes it back where it was, if it is still where JimiC left it, and takes
  away the «hoy no» that move wrote.
- [➡️ Todo lo pendiente a mañana]: [Mañana] on everything still pending; what was declared or
  marked is resolved, so it is never touched. One [↩️ Deshacer] for all (`Followup.join`).
- [1]…[5]: «¿Cómo ha ido el día?», once, in `day_ratings`.

Everything acts on the day of the close (in each item's data), not on the clock's day, and
expires with the close, «la mañana siguiente». The plan's tasks get their state in `plan_items`.
"""

import logging
import sqlite3
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from jimic.agent.proposals import due_value, same_due
from jimic.channel.message import Item, Option
from jimic.channel.taps import Answer, Declined, Failed, Followup, HandlerOutcome, TapHandler
from jimic.clock import Clock
from jimic.close.day import morning_after, tomorrow_due
from jimic.close.message import CLOSE_KIND
from jimic.execution.execute import GONE, UNDONE
from jimic.execution.key_tasks import where_jimic_left_it
from jimic.execution.plan_message import undo_message, when
from jimic.execution.rules import due_day
from jimic.memory import day_plans, key_marks
from jimic.memory.live import LiveConfig
from jimic.tools.calendar import CalendarReader
from jimic.tools.failures import ToolFailure
from jimic.tools.todoist import TASK_NOT_FOUND, Due, TodoistTask, TodoistTools

logger = logging.getLogger(__name__)

CLOSE_UNDO_KIND = "close_undo"
UNDO = Option(key="undo", label="↩️ Deshacer")

DONE = "✅ hecha"
ALREADY_DONE = "✅ ya estaba hecha"
WILL_DO = "🗣️ la harás"
CHANGED = "ha cambiado: no la toco"
RATED = "⭐ {rating}"
ALREADY_RATED = "ya valorado"
UNDONE_SOME = "↩️ deshecho ({done} de {total})"
MARK_ONLY = "↩️ deshecho (Todoist, sin tocar)"


def _day(data: dict[str, Any]) -> date:
    return date.fromisoformat(str(data["day"]))


def tomorrow_text(due: Due, day: date, timezone: ZoneInfo) -> str:
    """`➡️ mañana 14:30`: `when` counted from the day of the close."""
    return f"➡️ {when(due, day, timezone)}"


class CloseActions:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        clock: Clock,
        timezone: ZoneInfo,
        todoist: TodoistTools,
        calendar: CalendarReader,
        config: LiveConfig,
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._timezone = timezone
        self._todoist = todoist
        self._calendar = calendar
        self._config = config

    @property
    def handlers(self) -> dict[str, TapHandler]:
        return {CLOSE_KIND: self.tap, CLOSE_UNDO_KIND: self.undo}

    def _now(self) -> datetime:
        return self._clock.now().astimezone(self._timezone)

    def _followup(self, title: str, day: date, data: dict[str, Any], join=None) -> Followup:
        expires = morning_after(day, self._config.limits.quiet_hours, self._timezone)
        return Followup(CLOSE_UNDO_KIND, undo_message(title, data), expires, title, join)

    # --- The buttons -----------------------------------------------------------------------------

    async def tap(self, item: Item, option: str, approval_id: int) -> HandlerOutcome:
        data = item.data or {}
        if option.startswith("r") and item.question:
            return self._rate(data, int(option[1:]))
        day = _day(data)
        task_id = str(data["task_id"])
        if option == "will":
            day_plans.set_status(self._conn, day, task_id, "will_do")
            logger.info("close: will do")
            return WILL_DO
        try:
            current = await self._todoist.reader.get_task(task_id)
        except ToolFailure as error:
            return Failed(error.code)
        if current is None:
            return Declined(GONE, "will")
        if option == "done":
            return await self._done(item, data, day, current)
        if option == "tmrw":
            return await self._tomorrow(item, data, day, current)
        logger.warning("close: option %s does not fit", option)
        return Failed("invalid_option")

    def _rate(self, data: dict[str, Any], rating: int) -> HandlerOutcome:
        if not 1 <= rating <= 5:
            return Failed("invalid_option")
        if not day_plans.rate(self._conn, _day(data), rating, self._clock.now()):
            return ALREADY_RATED
        logger.info("close: rated")
        return RATED.format(rating=rating)

    async def _done(
        self, item: Item, data: dict[str, Any], day: date, current: TodoistTask
    ) -> HandlerOutcome:
        now = self._now()
        if current.completed:
            marked = current.completed_at or now
            day_plans.set_status(self._conn, day, current.id, "done", marked)
            return ALREADY_DONE
        if self._jumped(current, day):
            # A recurring task completed in the app jumps to its next day and never shows as
            # completed (contract of 23-nov-2026): Pedro says it is done, so it counts.
            intent = data.get("intent")
            if intent is not None:
                key_marks.record(self._conn, day, str(intent), "done", now, current.id)
            day_plans.set_status(self._conn, day, current.id, "done", now)
            logger.info("close: done (recurring, already in the app)")
            return ALREADY_DONE
        if current.due is None or due_day(current.due) != day:
            return Declined(CHANGED, "will")
        try:
            await self._todoist.writer.complete(current.id)
        except ToolFailure as error:
            return Failed(error.code)
        undo: dict[str, Any] = {"action": "done", "day": day.isoformat(), "task_id": current.id}
        if current.is_recurring:
            # Closed already: a failed read must not fail the tap.
            try:
                after = await self._todoist.reader.get_task(current.id)
            except ToolFailure as error:
                logger.info("close: next due unknown (%s)", error.code)
                after = None
            undo |= {
                "recurring": True,
                "before": current.due.isoformat(),
                "after": None if after is None or after.due is None else after.due.isoformat(),
            }
        intent = data.get("intent")
        if intent is not None:
            undo["mark"] = key_marks.record(self._conn, day, str(intent), "done", now, current.id)
        day_plans.set_status(self._conn, day, current.id, "done", now)
        logger.info("close: done")
        return Answer(DONE, self._followup(f"✅ {item.title}", day, undo))

    def _jumped(self, task: TodoistTask, day: date) -> bool:
        """A recurring task now on a later day that JimiC did not move: completed in the app."""
        return (
            task.is_recurring
            and task.due is not None
            and due_day(task.due) > day
            and task.id not in key_marks.of_day(self._conn, day).moved
        )

    async def _tomorrow(
        self, item: Item, data: dict[str, Any], day: date, current: TodoistTask
    ) -> HandlerOutcome:
        if current.completed:
            return Declined(ALREADY_DONE, "will")
        if current.due is None or due_day(current.due) != day:
            return Declined(CHANGED, "will")
        tomorrow = day + timedelta(days=1)
        calendar = await self._calendar.read_day(tomorrow)
        if not calendar.available:
            return Failed(f"calendar:{calendar.unavailable_reason}")
        tasks = await self._todoist.reader.read_day(tomorrow)
        if not tasks.available:
            return Failed(str(tasks.unavailable_reason))
        due = tomorrow_due(current, day, calendar, tasks)
        try:
            await self._todoist.writer.move(current.id, due)
        except ToolFailure as error:
            return Failed(error.code)
        now = self._now()
        if key_marks.note_jimic_move(self._conn, day, current.id, now, due_day(due)):
            logger.info("close: key task moved off today: not today")
        day_plans.set_status(self._conn, day, current.id, "moved")
        move = {"task_id": current.id, "before": current.due.isoformat(), "after": due.isoformat()}
        undo = {"action": "moves", "day": day.isoformat(), "moves": [move]}
        result = tomorrow_text(due, day, self._timezone)
        logger.info("close: moved to tomorrow")
        return Answer(result, self._followup(f"{result}: {item.title}", day, undo, self._join))

    def _join(self, followups: Sequence[Followup]) -> Followup:
        """The [Deshacer] of several moves of one tap, in one message."""
        if len(followups) == 1:
            return followups[0]
        moves: list[Any] = []
        for followup in followups:
            found = (followup.message.items[0].data or {}).get("moves")
            moves += found if isinstance(found, list) else []
        data = (followups[0].message.items[0].data or {}) | {"moves": moves}
        title = f"➡️ {len(moves)} tareas a mañana"
        return self._followup(title, _day(data), data)

    # --- [↩️ Deshacer] ------------------------------------------------------------------------

    async def undo(self, item: Item, option: str, approval_id: int) -> HandlerOutcome:
        data = item.data or {}
        if option != "undo":
            return Failed("invalid_option")
        if data.get("action") == "done":
            return await self._undo_done(data)
        return await self._undo_moves(data)

    async def _undo_done(self, data: dict[str, Any]) -> HandlerOutcome:
        day, task_id = _day(data), str(data["task_id"])
        try:
            current = await self._todoist.reader.get_task(task_id)
            untouched = current is None or not await self._reopen(current, data)
        except ToolFailure as error:
            if error.code != TASK_NOT_FOUND:
                return Failed(error.code)
            untouched = True
        if data.get("mark") is not None:
            key_marks.remove(self._conn, int(str(data["mark"])))
        day_plans.set_status(self._conn, day, task_id, "pending")
        logger.info("close: done undone%s", ", mark only" * untouched)
        return MARK_ONLY if untouched else UNDONE

    async def _reopen(self, current: TodoistTask, data: dict[str, Any]) -> bool:
        if not data.get("recurring"):
            if current.completed:
                await self._todoist.writer.reopen(current.id)
            return True
        before = due_value(data.get("before"))
        if not where_jimic_left_it(current, data.get("after"), before):
            return False
        await self._todoist.writer.move(current.id, before)
        now = self._now()
        key_marks.note_moved(self._conn, _day(data), current.id, now)
        return True

    async def _undo_moves(self, data: dict[str, Any]) -> HandlerOutcome:
        day = _day(data)
        moves = data.get("moves") or []
        assert isinstance(moves, list)
        done = 0
        for move in moves:
            assert isinstance(move, dict)
            task_id = str(move["task_id"])
            try:
                current = await self._todoist.reader.get_task(task_id)
                if current is None or not same_due(
                    None if current.due is None else current.due.isoformat(), str(move["after"])
                ):
                    logger.info("close: move undo refused (changed)")
                    continue
                before = due_value(str(move["before"]))
                await self._todoist.writer.move(task_id, before)
            except ToolFailure as error:
                if len(moves) == 1:
                    return Failed(error.code)
                logger.info("close: move undo failed (%s)", error.code)
                continue
            now = self._now()
            key_marks.note_jimic_move(
                self._conn, day, task_id, now, None if before is None else due_day(before)
            )
            key_marks.moved_back(self._conn, day, task_id)
            day_plans.set_status(self._conn, day, task_id, "pending")
            done += 1
        logger.info("close: moves undone (%d of %d)", done, len(moves))
        if done == len(moves):
            return UNDONE
        if done == 0:
            return CHANGED
        return UNDONE_SOME.format(done=done, total=len(moves))
