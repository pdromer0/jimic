"""The code that executes, after Pedro's button or message (H1-1): never the model.

`Executor.handlers` are the tap handlers of the two kinds of approval:

- `plan`: a proposal of the agent. `ok` creates or moves, `del` deletes (with every rule of
  `deletion.py`), `no` and `keep` only say so.
- `undo`: what was done at once (decision 1.11-b), with its [Deshacer] until the end of the day.
  Undoing a creation deletes what JimiC created; undoing a move puts back the date it had. Both
  check first that the task is still as JimiC left it: if Pedro changed it, nothing is undone.

`Executor.now` runs the one item of a proposal at once and gives what its [Deshacer] needs.
Every write goes through the writer of `build_todoist`, with the guard of `dev`. A failure of
Todoist is `Failed` with its code: the item stays pending and Pedro can try again.
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from jimic.agent.proposals import TaskSnapshot, due_value
from jimic.channel.message import Item
from jimic.channel.taps import Failed, HandlerOutcome, TapHandler
from jimic.clock import Clock
from jimic.execution import deletion
from jimic.execution.plan_message import PLAN_KIND, UNDO_KIND, item_title, proposed_due
from jimic.memory.live import LiveConfig
from jimic.memory.schemas import Limits
from jimic.tools.failures import ToolFailure
from jimic.tools.todoist import TASK_NOT_FOUND, TodoistTask, TodoistTools

logger = logging.getLogger(__name__)

CREATED = "✓ creada"
MOVED = "✓ movida"
DISCARDED = "⏭ descartada"
STAYS = "🆗 se queda"
UNDONE = "↩️ deshecho"
CHANGED = "ha cambiado: no lo deshago"
GONE = deletion.GONE


@dataclass(frozen=True)
class DoneNow:
    """What `Executor.now` did: the line to show and what its [Deshacer] needs."""

    title: str
    undo: dict[str, Any]


class Executor:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        clock: Clock,
        timezone: ZoneInfo,
        limits: Limits,
        todoist: TodoistTools,
        config: LiveConfig | None = None,
    ) -> None:
        """`config` is the live configuration; without it, `limits` is a fixed one."""
        self._conn = conn
        self._clock = clock
        self._timezone = timezone
        self._config = config
        self._limits = limits
        self._todoist = todoist

    @property
    def _active_limits(self) -> Limits:
        """The limits now: the live ones, or the fixed ones."""
        return self._config.limits if self._config is not None else self._limits

    @property
    def handlers(self) -> dict[str, TapHandler]:
        return {PLAN_KIND: self.plan, UNDO_KIND: self.undo}

    def _today(self) -> date:
        return self._clock.now().astimezone(self._timezone).date()

    # --- A proposal -------------------------------------------------------------------------

    async def plan(self, item: Item, option: str, approval_id: int) -> HandlerOutcome:
        data = item.data or {}
        action = data.get("action")
        if option == "no":
            return DISCARDED if action == "create" else STAYS
        if option == "keep":
            return STAYS
        if action == "delete" and option == "del":
            return await deletion.delete(
                conn=self._conn,
                clock=self._clock,
                timezone=self._timezone,
                limits=self._active_limits,
                todoist=self._todoist,
                item=item,
                approval_id=approval_id,
            )
        if option != "ok" or action not in ("create", "move"):
            logger.warning("plan: option %s does not fit the item", option)
            return Failed("invalid_option")
        try:
            if action == "create":
                await self._create(data)
                return CREATED
            await self._move(data)
            return MOVED
        except ToolFailure as error:
            if error.code == TASK_NOT_FOUND:
                return GONE
            return Failed(error.code)

    async def _create(self, data: dict[str, Any]) -> TodoistTask:
        """Create in the inbox (in `dev`, the guard sends it to the test project)."""
        created = await self._todoist.writer.create(
            str(data["title"]),
            None,
            proposed_due(str(data["due"]), self._timezone),
            data.get("duration_minutes"),
        )
        logger.info("task created")
        return created

    async def _move(self, data: dict[str, Any]) -> None:
        await self._todoist.writer.move(
            str(data["task_id"]), proposed_due(str(data["due"]), self._timezone)
        )
        logger.info("task moved")

    # --- At once ----------------------------------------------------------------------------

    async def now(self, entry: dict[str, Any]) -> DoneNow | Failed:
        """Run a create or a move at once (decision 1.11-b)."""
        title = item_title(entry, self._today(), self._timezone)
        try:
            if entry["action"] == "create":
                created = await self._create(entry)
                return DoneNow(
                    title,
                    {
                        "action": "create",
                        "task_id": created.id,
                        "snapshot": TaskSnapshot.of(created).model_dump(),
                    },
                )
            await self._move(entry)
        except ToolFailure as error:
            return Failed(error.code)
        before = TaskSnapshot.model_validate(entry["snapshot"])
        after = proposed_due(str(entry["due"]), self._timezone)
        return DoneNow(
            title,
            {
                "action": "move",
                "task_id": entry["task_id"],
                "before": before.due,
                # As JimiC left it: the [Deshacer] checks that nothing changed since.
                "snapshot": before.model_copy(update={"due": after.isoformat()}).model_dump(),
            },
        )

    # --- [Deshacer] -------------------------------------------------------------------------

    async def undo(self, item: Item, option: str, approval_id: int) -> HandlerOutcome:
        data = item.data or {}
        if option != "undo":
            return Failed("invalid_option")
        task_id = str(data["task_id"])
        try:
            current = await self._todoist.reader.get_task(task_id)
        except ToolFailure as error:
            return Failed(error.code)
        if current is None:
            return GONE
        left = TaskSnapshot.model_validate(data["snapshot"])
        if not left.matches(TaskSnapshot.of(current)):
            logger.info("undo refused (changed)")
            return CHANGED
        try:
            if data["action"] == "create":
                if current.is_recurring or current.has_subtasks:
                    return CHANGED
                await self._todoist.writer.delete(task_id)
            else:
                before = data.get("before")
                await self._todoist.writer.move(
                    task_id, due_value(None if before is None else str(before))
                )
        except ToolFailure as error:
            if error.code == TASK_NOT_FOUND:
                return GONE
            return Failed(error.code)
        logger.info("undone (%s)", data["action"])
        return UNDONE
