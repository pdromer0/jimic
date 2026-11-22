"""The conversation with Pedro: one session of the Agent SDK per local day (decision 1.10-a).

`Conversation.reply` takes what Pedro wrote and returns the answer. It notes the moment at once
(decision 1.10-c, also while the message waits for its turn), then runs **one turn at a time**:
a lock keeps the order in which the messages arrived. Each run resumes today's session, or
starts one if there is none; tomorrow is another one.

Before a run, if today's spend has reached `daily_cap_usd`, the SDK is not called and Pedro gets
a fixed sentence. After a run, its cost (the SDK's estimate) goes to `model_usage` and the
budget of the month is checked. A run that fails (the API, the subprocess, the caps of turns or
of spend) gives a fixed sentence and a code in the log, never a trace.

Logs never carry what Pedro wrote, the answer, the arguments of the tools or the key.
"""

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKError, ResultError, ResultMessage, query
from claude_agent_sdk.types import Message

from jimic.agent.options import build_options
from jimic.agent.tools import ToolContext, build_server
from jimic.clock import Clock
from jimic.config import Settings
from jimic.heartbeat.decide import RETRYABLE_STATUS
from jimic.heartbeat.sources import Sources
from jimic.memory import conversations, system_notices, usage
from jimic.memory.schemas import Limits
from jimic.memory.usage import TokenUsage
from jimic.paths import Paths

logger = logging.getLogger(__name__)

DAILY_CAP_TEXT = "Hoy he llegado a mi tope; mañana seguimos, los avisos siguen."
API_DOWN_TEXT = (
    "Ahora mismo no consigo hablar con el modelo. Vuelve a escribirme en un rato; los avisos"
    " siguen saliendo."
)
CAPPED_TEXT = (
    "Esto se me ha hecho largo y he parado para no gastar de más. ¿Me lo pides más concreto?"
)
FAILED_TEXT = "Se me ha atascado algo por dentro y no he podido contestarte. Prueba en un rato."
WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
STUCK = ("agent:process:", "agent:error:")
"""The failures after which today's session is forgotten when it was resumed."""

type RunAgent = Callable[[str, ClaudeAgentOptions], AsyncIterator[Message]]
"""`claude_agent_sdk.query`, or the tests' double."""


def sdk_query(prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
    return query(prompt=prompt, options=options)


def stamp(moment: datetime) -> str:
    """The local time JimiC puts before each message: `[lun 07/01 10:15]`."""
    return f"[{WEEKDAYS[moment.weekday()]} {moment:%d/%m %H:%M}]"


@dataclass(frozen=True)
class Outcome:
    """How a run ended: `code` is `ok` or a code for the log; `text` is the answer, if any."""

    code: str
    text: str | None = None
    session_id: str | None = None
    usd: Decimal | None = None
    usage: TokenUsage | None = None
    api_status: int | None = None
    ran: bool = False
    """The engine answered something before failing: it may have spent, unknown how much."""


def usage_of(data: dict[str, Any] | None) -> TokenUsage:
    """The tokens of a result, as the SDK reports them (cache writes split by TTL if it can)."""
    data = data or {}
    split = data.get("cache_creation") or {}
    five, hour = split.get("ephemeral_5m_input_tokens"), split.get("ephemeral_1h_input_tokens")
    if five is None and hour is None:
        five, hour = data.get("cache_creation_input_tokens") or 0, 0
    return TokenUsage(
        input_tokens=data.get("input_tokens") or 0,
        output_tokens=data.get("output_tokens") or 0,
        cache_read_tokens=data.get("cache_read_input_tokens") or 0,
        cache_write_5m_tokens=five or 0,
        cache_write_1h_tokens=hour or 0,
    )


def _classify(
    subtype: str | None,
    is_error: bool,
    api_status: int | None,
    terminal_reason: str | None,
) -> str:
    if subtype == "error_max_turns":
        return "agent:max_turns"
    if subtype == "error_max_budget_usd":
        return "agent:max_budget"
    if api_status is not None or terminal_reason == "api_error":
        return f"agent:api:{api_status if api_status is not None else 'unknown'}"
    if is_error:
        return f"agent:error:{subtype or 'unknown'}"
    return "ok"


def outcome_of_result(result: ResultMessage) -> Outcome:
    code = _classify(
        result.subtype, result.is_error, result.api_error_status, result.terminal_reason
    )
    text = (result.result or "").strip() or None
    if code == "ok" and text is None:
        code = "agent:no_result"
    usd = None if result.total_cost_usd is None else Decimal(str(result.total_cost_usd))
    return Outcome(
        code, text, result.session_id, usd, usage_of(result.usage), result.api_error_status
    )


def outcome_of_error(error: ResultError) -> Outcome:
    code = _classify(error.subtype, True, error.api_error_status, error.terminal_reason)
    cost = error.data.get("total_cost_usd")
    usd = Decimal(str(cost)) if isinstance(cost, int | float) else None
    return Outcome(
        code, None, error.session_id, usd, usage_of(error.data.get("usage")), error.api_error_status
    )


async def run_once(run_agent: RunAgent, prompt: str, options: ClaudeAgentOptions) -> Outcome:
    """Run the agent and say how it ended. Never raises for a failure of the SDK."""
    result: ResultMessage | None = None
    ran = False
    try:
        async for message in run_agent(prompt, options):
            ran = True
            if isinstance(message, ResultMessage):
                result = message
    except ResultError as error:
        # The engine reported a failed result and exited; the result, if it came, says more.
        return outcome_of_result(result) if result is not None else outcome_of_error(error)
    except ClaudeSDKError as error:
        if result is not None:
            return outcome_of_result(result)
        return Outcome(f"agent:process:{type(error).__name__}", ran=ran)
    if result is None:
        return Outcome("agent:no_result", ran=ran)
    return outcome_of_result(result)


def api_notice_detail(status: int | None) -> str:
    """`rejected:` for a 4xx that will not go away by waiting, `down:` otherwise (as the
    heartbeat says it, decision 1.8-c)."""
    if status is None:
        return "down:agent_unknown"
    rejected = 400 <= status < 500 and status not in RETRYABLE_STATUS
    return f"{'rejected' if rejected else 'down'}:agent_http_{status}"


class Conversation:
    """One per service. `reply` is what the Telegram channel calls with Pedro's text."""

    def __init__(
        self,
        *,
        settings: Settings,
        paths: Paths,
        limits: Limits,
        clock: Clock,
        conn: sqlite3.Connection,
        sources: Sources,
        run_agent: RunAgent = sdk_query,
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._limits = limits
        self._clock = clock
        self._conn = conn
        self._sources = sources
        self._run_agent = run_agent
        self._lock = asyncio.Lock()

    def _now(self) -> datetime:
        return self._clock.now().astimezone(self._settings.timezone)

    async def reply(self, text: str) -> str:
        """The answer to `text`, which Pedro wrote just now."""
        received = self._now()
        conversations.note_message(self._conn, received.date(), received)
        async with self._lock:
            try:
                return await self._turn(text, received)
            except Exception as error:
                logger.error("agent turn failed (%s)", type(error).__name__)
                return FAILED_TEXT

    async def _turn(self, text: str, received: datetime) -> str:
        day = received.date()
        if usage.spent_on(self._conn, day) >= self._limits.budget.daily_cap_usd:
            logger.info("agent: daily_cap")
            system_notices.check_budget(self._conn, self._limits.budget, self._now())
            return DAILY_CAP_TEXT
        agent_dir = self._paths.agent_dir
        agent_dir.mkdir(mode=0o700, exist_ok=True)
        session = conversations.session_of(self._conn, day)
        context = ToolContext.of(
            self._sources,
            self._conn,
            self._clock,
            self._settings.timezone,
            self._paths.config_dir,
            day,
        )
        options = build_options(
            self._settings, self._limits, agent_dir, build_server(context), session
        )
        outcome = await run_once(self._run_agent, f"{stamp(received)} {text}", options)
        self._bookkeeping(day, outcome, resumed=session is not None)
        return self._answer(outcome)

    def _bookkeeping(self, day: date, outcome: Outcome, resumed: bool) -> None:
        now = self._now()
        if outcome.usage is not None or outcome.usd is not None or outcome.ran:
            # Without the SDK's estimate (or without a result at all, once the engine answered),
            # the most a run may spend: the discreet count.
            usd = outcome.usd
            if usd is None:
                usd = Decimal(str(self._limits.budget.max_session_usd))
            usage.record_cost(
                self._conn,
                day,
                self._settings.agent_model,
                outcome.usage or TokenUsage(0, 0),
                usd,
            )
            system_notices.check_budget(self._conn, self._limits.budget, now)
        if resumed and outcome.code.startswith(STUCK):
            # A session that fails when resumed (not the API, not a cap) must not block the rest
            # of the day: the next message starts another one.
            conversations.save_session(self._conn, day, None)
        elif outcome.session_id:
            conversations.save_session(self._conn, day, outcome.session_id)
        if outcome.code.startswith("agent:api:"):
            detail = api_notice_detail(outcome.api_status)
            system_notices.detect(self._conn, "api_down", day, now, detail)

    @staticmethod
    def _answer(outcome: Outcome) -> str:
        if outcome.code == "ok":
            assert outcome.text is not None
            logger.info("agent: ok")
            return outcome.text
        logger.warning("agent: %s", outcome.code)
        if outcome.code.startswith("agent:api:"):
            return API_DOWN_TEXT
        if outcome.code in ("agent:max_turns", "agent:max_budget"):
            return CAPPED_TEXT
        return FAILED_TEXT
