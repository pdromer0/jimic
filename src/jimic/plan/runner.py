"""The plan of the day, end to end (task 1.14): the hook `on_handover` of the heartbeat.

When the heartbeat hands a `daily_plan` over (decision 94), `DailyPlanRunner.handover` runs the
agent **in today's conversation** with a fixed note of the code (decision 1.14-a: no origin, not
«Pedro conversa», waits for the lock), reads Todoist, the calendar and APEX itself, paints the
plan (`plan/message.py`) and sends it as one message, which counts in the quota (a row in
`notices`). If the agent cannot (its run fails, `daily_cap_usd`, or it proposes no plan), the
minimal plan goes instead (decision 1.14-e). A `day_close` still only goes to the log (task 1.16).

**Retries (Pedro's review of the plan).** If sending fails, the hook raises and the plan is not
attended, so a later heartbeat may hand it over again. At most one retry with the agent per day:
the next try sends the minimal plan, without the agent, and after that the tries only log and
count as attended. A failure of Telegram that lasts never runs the agent on every heartbeat. The
count lives in the service (a restart starts it again).

Logs say codes and counts, never what the plan says.
"""

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from jimic.agent.conversation import Conversation
from jimic.agent.proposals import expires_at
from jimic.agent.tools import RunState
from jimic.channel.message import InteractiveMessage, MessageTooLong
from jimic.clock import Clock
from jimic.execution.plan_message import PLAN_KIND
from jimic.heartbeat.beat import log_handover
from jimic.heartbeat.sources import Sources
from jimic.heartbeat.state import DayState
from jimic.memory.changes import read_text
from jimic.memory.live import LiveConfig
from jimic.memory.markdown_checks import icon_rows
from jimic.memory.notices import record_sent
from jimic.memory.onboarding import PREFERENCES_FILE
from jimic.memory.schemas import DailyPlan
from jimic.plan import minimal
from jimic.plan.day import day_view, hour, normalized
from jimic.plan.message import (
    DEFAULT_GREETING,
    PlanContent,
    icon_table,
    late_after,
    learned_line,
    paint,
)

logger = logging.getLogger(__name__)

PLAN_NOTE = (
    "Toca proponer el plan del día: lee el calendario, las tareas y APEX y llama una vez a"
    " propose_plan con lo que propones y los campos del plan. El mensaje lo pinta el código."
)
LATE_NOTE = " Es un plan tardío: la ventana acababa a las {end}; solo lo que queda del día."
MAX_AGENT_TRIES = 2
"""The first try and one retry with the agent, per day."""
MAX_TRIES = 3
"""After the tries with the agent, one with the minimal plan; then only the log."""

type Plan = InteractiveMessage | str


def read_icons(config_dir: Path) -> dict[str, str]:
    """The table of `## Iconos` of `preferencias.md`, keyed by `normalized` task; empty if the
    file cannot be read (the default icons)."""
    try:
        text = read_text(config_dir, PREFERENCES_FILE)
    except Exception as error:  # a file that cannot be read leaves the default icons
        logger.warning("icons unreadable (%s)", type(error).__name__)
        return {}
    return icon_table(icon_rows(text)) if text else {}


class PlanSender(Protocol):
    async def deliver(self, kind: str, content: Plan, expires_at: datetime) -> int:
        """Send it; return the id of its (last) message. Raises `SendError`."""
        ...


@dataclass(frozen=True)
class BuiltPlan:
    plan: Plan
    code: str
    """`agent`, or `minimal:<why>` (`no_agent`, `no_proposal`, the code of the run)."""
    run: RunState | None = None
    """The run of the agent, when the plan is its own."""


def is_late(intention: DailyPlan, now: datetime) -> bool:
    """After the window of the plan (H1-7), as `late_after` says."""
    window = intention.window
    span = None if window is None else (window.start, window.end)
    return late_after(span, intention.default_time, now)


def plan_note(intention: DailyPlan, late: bool) -> str:
    if not late:
        return PLAN_NOTE
    end = intention.window.end if intention.window is not None else intention.default_time
    return PLAN_NOTE + LATE_NOTE.format(end=hour(end))


class DailyPlanRunner:
    def __init__(
        self,
        *,
        conversation: Conversation,
        sources: Sources,
        config: LiveConfig,
        conn: sqlite3.Connection,
        clock: Clock,
        timezone: ZoneInfo,
        config_dir: Path,
        sender: PlanSender | None = None,
    ) -> None:
        """Without `sender` it only builds (the evaluation)."""
        self._conversation = conversation
        self._sources = sources
        self._config = config
        self._conn = conn
        self._clock = clock
        self._timezone = timezone
        self._config_dir = config_dir
        self._sender = sender
        self._tries: dict[tuple[date, str], int] = {}
        """Tries of each plan intention today; only today's are kept."""

    def _now(self) -> datetime:
        return self._clock.now().astimezone(self._timezone)

    def _plan_intents(self) -> list[str]:
        return [i.id for i in self._config.intentions.intentions if isinstance(i, DailyPlan)]

    def intention(self, intent: str) -> DailyPlan | None:
        found = next((i for i in self._config.intentions.intentions if i.id == intent), None)
        return found if isinstance(found, DailyPlan) else None

    async def handover(self, intent: str, state: DayState) -> None:
        """The hook of the heartbeat. Raises `SendError` if the plan could not go out."""
        intention = self.intention(intent)
        if intention is None:
            await log_handover(intent, state)
            return
        if self._sender is None:
            raise RuntimeError("the plan has nowhere to go")
        day = self._now().date()
        key = (day, intent)
        tries = self._tries.get(key, 0)
        if tries >= MAX_TRIES:
            logger.warning("plan: given up today after %d tries", tries)
            return
        self._tries = {k: v for k, v in self._tries.items() if k[0] == day} | {key: tries + 1}
        built = await self.build(intention, use_agent=tries < MAX_AGENT_TRIES)
        logger.info("plan: %s (try %d)", built.code, tries + 1)
        message_id = await self._sender.deliver(
            PLAN_KIND, built.plan, expires_at(self._clock, self._timezone)
        )
        record_sent(self._conn, intent, self._clock.now(), message_id)

    async def _read(self, day: date):
        sources = self._sources
        return await asyncio.gather(
            sources.calendar.read_day(day),
            sources.todoist.reader.read_today(),
            asyncio.to_thread(sources.apex.read_today),
        )

    def _icons(self) -> dict[str, str]:
        return read_icons(self._config_dir)

    async def build(self, intention: DailyPlan, use_agent: bool = True) -> BuiltPlan:
        """The plan for now: the agent's, or the minimal one. Never raises for the agent."""
        now = self._now()
        late = is_late(intention, now)
        turn = None
        if use_agent:
            turn = await self._conversation.run_note(plan_note(intention, late), "plan")
        run = turn.run if turn is not None and turn.code == "ok" else None
        now = self._now()
        calendar, tasks, apex = await self._read(now.date())
        view = day_view(calendar, tasks, now, late)
        table = self._icons()
        learned = learned_line(self._conn, now, self._plan_intents())
        if run is not None and run.proposed_plan:
            content = PlanContent(
                greeting=run.plan.get("greeting") or DEFAULT_GREETING,
                entries=run.items,
                left_out=[entry["line"] for entry in run.plan.get("left_out", [])],
                training=run.plan.get("training_line"),
                learned=learned,
                day_icons={
                    normalized(entry["title"]): entry["icon"]
                    for entry in run.plan.get("day_icons", [])
                },
            )
            try:
                return BuiltPlan(paint(content, view, late, now, table), "agent", run)
            except MessageTooLong:
                why = "too_long"
        elif not use_agent:
            why = "no_agent"
        elif turn is not None and turn.code != "ok":
            why = turn.code
        else:
            why = "no_proposal"
        content = PlanContent(
            greeting=minimal.GREETING,
            entries=minimal.minimal_entries(self._config.intentions.habits, tasks, now.date()),
            training=minimal.training_line(apex),
            learned=learned,
        )
        return BuiltPlan(paint(content, view, late, now, table), f"minimal:{why}", run)
