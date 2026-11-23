"""Doubles and invented data for the plan of the day (task 1.14): nothing here is real.

`PlanWorld` is the world of the assistant (a fake agent, Todoist over its fake, the fixed calendar
and APEX of the heartbeat) with the runner of the plan and a sender that records what it gets.
"""

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_data import FakeAgent, ToolCall, result_message
from execution_data import AssistantWorld
from heartbeat_data import CLOSE, HABIT, MADRID, PLAN, day_state, intentions, madrid

from jimic.channel.bot import SendError
from jimic.channel.message import InteractiveMessage
from jimic.memory.live import LiveConfig
from jimic.plan.runner import BuiltPlan, DailyPlanRunner

MAX_DELIVERIES = 10
FREE_TEXT = "Texto-libre-del-agente-5521"
OFFERS_HABIT = HABIT | {
    "id": "ofertas",
    "name": "Revisar ofertas",
    "duration": "30m",
    "moment": "afternoon",
    "todoist_task": "Revisar ofertas",
}
STRETCH_HABIT = HABIT | {
    "id": "estiramientos",
    "name": "Estiramientos",
    "priority": "medium",
    "duration": "15m",
    "moment": "evening",
    "todoist_task": "Estiramientos",
}


class SpyPlanSender:
    """Stands in for the channel: records what it delivers; `fail` makes Telegram refuse."""

    def __init__(self) -> None:
        self.delivered: list[tuple[str, InteractiveMessage | str]] = []
        self.tries = 0
        self.fail: str | None = None

    async def deliver(
        self, kind: str, content: InteractiveMessage | str, expires_at: datetime
    ) -> int:
        self.tries += 1
        if self.tries > MAX_DELIVERIES:
            raise AssertionError("the plan was delivered in a loop")
        if self.fail is not None:
            raise SendError(self.fail)
        self.delivered.append((kind, content))
        return 900 + len(self.delivered)


def plan_agent(*steps: Any, text: str = FREE_TEXT) -> FakeAgent:
    """An agent that reads the calendar and the tasks, then does `steps` and answers `text`."""
    reads = [ToolCall("read_calendar", {}), ToolCall("read_tasks", {})]
    return FakeAgent([*reads, *steps, result_message(text)])


def proposing(**args: Any) -> FakeAgent:
    return plan_agent(ToolCall("propose_plan", args))


class PlanWorld(AssistantWorld):
    def __init__(
        self,
        conn: sqlite3.Connection,
        tmp_path: Path,
        agent: FakeAgent,
        tasks: list[dict[str, Any]] | None = None,
        habits: list[dict[str, Any]] | None = None,
        now: str = "2030-01-07T07:45",
        plans: tuple[dict[str, Any], ...] = (PLAN,),
        **kwargs: Any,
    ):
        super().__init__(conn, tmp_path, agent, tasks if tasks is not None else [], **kwargs)
        self.clock.moment = madrid(now)
        chosen = habits if habits is not None else [HABIT]
        self.config = LiveConfig.fixed(intentions(*plans, CLOSE, habits=chosen), self.limits)
        self.sender = SpyPlanSender()
        self.runner = self.make_runner(self.sender)

    def make_runner(self, sender: SpyPlanSender | None) -> DailyPlanRunner:
        return DailyPlanRunner(
            conversation=self.conversation,
            sources=self.sources,
            config=self.config,
            conn=self.conn,
            clock=self.clock,
            timezone=MADRID,
            config_dir=self.paths.config_dir,
            sender=sender,
        )

    def handover(self, intent: str = "plan_del_dia") -> None:
        state = day_state(self.conn, self.clock.now(), self.config.intentions)
        asyncio.run(self.runner.handover(intent, state))

    def build(self, use_agent: bool = True) -> BuiltPlan:
        intention = self.runner.intention("plan_del_dia")
        assert intention is not None
        return asyncio.run(self.runner.build(intention, use_agent))

    def notices(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT intent, telegram_message_id FROM notices ORDER BY id"
        ).fetchall()
