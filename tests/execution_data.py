"""Doubles and invented data for what the code executes (task 1.11): nothing here is real.

Todoist is `FakeTodoistApi` behind the real reader and writer, so the rules run against what the
library returns. `ExecutionWorld` holds the database, a clock the test moves, the executor and
helpers to show a proposal as the channel would (saved, with its message id).
"""

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_data import FakeAgent
from conftest import VALID_ENV, FixedClock
from heartbeat_data import MADRID, calendar_with, event, limits, madrid, sources
from todoist_data import FakeTodoistApi, raw_task

from jimic.agent.conversation import Conversation
from jimic.agent.proposals import TaskSnapshot, expires_at
from jimic.channel import store
from jimic.channel.bot import Incoming, Reply
from jimic.channel.message import InteractiveMessage, Tap
from jimic.channel.taps import TapResult, process_tap
from jimic.clock import to_db
from jimic.config import Settings
from jimic.execution.assistant import Assistant
from jimic.execution.execute import Executor
from jimic.execution.plan_message import PLAN_KIND, plan_message
from jimic.heartbeat.sources import Sources
from jimic.paths import Paths
from jimic.tools.todoist import ApiTodoistReader, ApiTodoistWriter, TodoistTools

NOW = "2030-01-07T10:15"
PROJECT = "proyecto-inventado"


def todoist_task(task_id: str, title: str = "Tarea inventada", **kwargs: Any) -> dict[str, Any]:
    """A raw task of today in the invented project, unless `kwargs` say otherwise."""
    options: dict[str, Any] = {"project_id": PROJECT, "due": "2030-01-07"} | kwargs
    return raw_task(task_id, title, **options)


class YieldingApi(FakeTodoistApi):
    """Gives the loop a turn on every read: two executions really interleave."""

    async def get_task(self, task_id: str):
        await asyncio.sleep(0)
        return await super().get_task(task_id)


class ExecutionWorld:
    def __init__(
        self,
        conn: sqlite3.Connection,
        tasks: list[dict[str, Any]] | None = None,
        **limit_changes: Any,
    ):
        self.conn = conn
        self.clock = FixedClock(madrid(NOW))
        self.api = YieldingApi(tasks if tasks is not None else [])
        self.todoist = TodoistTools(
            ApiTodoistReader(self.api, self.clock, MADRID),
            ApiTodoistWriter(self.api, MADRID),
            self.api.close,
        )
        self.limits = limits(**limit_changes)
        self.executor = Executor(
            conn=conn, clock=self.clock, timezone=MADRID, limits=self.limits, todoist=self.todoist
        )
        self._next_message = 500

    # --- Proposals as the agent leaves them ---

    def snapshot(self, task_id: str) -> dict[str, Any]:
        task = asyncio.run(self.todoist.reader.get_task(task_id))
        assert task is not None
        return TaskSnapshot.of(task).model_dump()

    def delete_entry(self, task_id: str, reason: str = "duplicada") -> dict[str, Any]:
        return {
            "action": "delete",
            "task_id": task_id,
            "reason": reason,
            "snapshot": self.snapshot(task_id),
        }

    def move_entry(self, task_id: str, due: str) -> dict[str, Any]:
        return {
            "action": "move",
            "task_id": task_id,
            "due": due,
            "reason": "r",
            "snapshot": self.snapshot(task_id),
        }

    @staticmethod
    def create_entry(
        title: str = "Tarea nueva", due: str = "2030-01-07T18:00", **extra: Any
    ) -> dict[str, Any]:
        return {"action": "create", "title": title, "due": due, "reason": "r"} | extra

    def show(
        self,
        entries: list[dict[str, Any]],
        kind: str = PLAN_KIND,
        message: InteractiveMessage | None = None,
    ) -> tuple[int, int]:
        """Save a proposal as the channel does once Telegram took it; `(approval, message id)`."""
        if message is None:
            message = plan_message(entries, self.clock.now().date(), MADRID)
        approval_id = store.create(
            self.conn, self.clock, kind, message, expires_at(self.clock, MADRID)
        )
        self._next_message += 1
        store.set_message_id(self.conn, approval_id, self._next_message)
        return approval_id, self._next_message

    def tap(self, approval_id: int, item: int | None, option: str) -> TapResult:
        return asyncio.run(
            process_tap(
                self.conn, self.clock, self.executor.handlers, Tap(approval_id, item, option)
            )
        )

    def message(self, approval_id: int) -> InteractiveMessage:
        approval = store.load(self.conn, approval_id)
        assert approval is not None
        return approval.message

    def status(self, approval_id: int) -> str:
        approval = store.load(self.conn, approval_id)
        assert approval is not None
        return approval.status

    def photos(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT todoist_task_id, content, description, due, duration, project_id, labels,"
            " priority, reason, approval_id FROM deleted_tasks ORDER BY id"
        ).fetchall()

    def writes(self) -> list[str]:
        return [
            name
            for name, _ in self.api.calls
            if name in ("add_task", "update_task", "delete_task", "complete_task")
        ]

    def add_photo(self, deleted_at: datetime) -> None:
        """A deletion already done at `deleted_at` (aware), for the cap."""
        self.conn.execute(
            "INSERT INTO deleted_tasks"
            " (deleted_at, todoist_task_id, content, project_id, priority, reason)"
            " VALUES (?, 'antigua', 'x', 'p', 1, 'r')",
            (to_db(deleted_at),),
        )


class AssistantWorld(ExecutionWorld):
    """The assistant with a fake agent, the sources of the heartbeat and Todoist over the fake."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        tmp_path: Path,
        agent: FakeAgent,
        tasks: list[dict[str, Any]] | None = None,
        calendar_title: str | None = None,
        **limit_changes: Any,
    ):
        super().__init__(conn, tasks, **limit_changes)
        self.agent = agent
        self.paths = Paths("prod", tmp_path / "config", tmp_path / "data")
        self.paths.config_dir.mkdir()
        self.paths.data_dir.mkdir()
        base = sources(
            calendar_with(
                event(madrid("2030-01-07T12:00"), title=calendar_title or "Reunión inventada")
            )
        )
        self.sources = Sources(todoist=self.todoist, calendar=base.calendar, apex=base.apex)
        self.conversation = Conversation(
            settings=Settings.model_validate(VALID_ENV),
            paths=self.paths,
            limits=self.limits,
            clock=self.clock,
            conn=conn,
            sources=self.sources,
            run_agent=agent,
        )
        self.assistant = Assistant(
            conversation=self.conversation,
            executor=self.executor,
            conn=conn,
            clock=self.clock,
            timezone=MADRID,
        )

    def say(
        self, text: str, reply_to: int | None = None, last_sent: int | None = None
    ) -> str | Reply:
        return asyncio.run(
            self.assistant.reply(Incoming(text, reply_to=reply_to, last_sent=last_sent))
        )
