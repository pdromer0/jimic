"""The conversation with Pedro: one session of the Agent SDK per local day (decision 1.10-a).

`Conversation.respond` takes what Pedro wrote and returns the answer with what the run proposed
and whether it read external content (`Turn`); `reply` returns only the text. Each run carries
Pedro's text as the origin of what it proposes for the memory (decision 1.12-a) and gets the
current text of his five files in its system prompt (1.12-c): a file cut there registers the
system notice `memory_clipped`. While the onboarding is in progress, its script and where it
stands go after them (task 1.13). It notes the
moment at once
(decision 1.10-c, also while the message waits for its turn), then runs **one turn at a time**:
a lock keeps the order in which the messages arrived. Each run resumes today's session, or
starts one if there is none; tomorrow is another one.

Before a run, if today's spend has reached `daily_cap_usd`, the SDK is not called and Pedro gets
a fixed sentence. After a run, its cost (its tokens priced; the SDK's `total_cost_usd` is the whole
session's, so it is not counted) goes to `model_usage` and the
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
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKError, ResultError, ResultMessage, query
from claude_agent_sdk.types import Message

from jimic.agent.guard import guard_hooks
from jimic.agent.memory_context import memory_block, stem
from jimic.agent.onboarding_context import onboarding_block
from jimic.agent.options import build_options
from jimic.agent.tools import RunMode, RunState, ToolContext, build_server
from jimic.clock import Clock
from jimic.config import Settings
from jimic.heartbeat.decide import status_kind
from jimic.heartbeat.sources import Sources
from jimic.memory import conversations, system_notices, usage
from jimic.memory.live import LiveConfig
from jimic.memory.onboarding import progress
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
DAILY_CAP = "agent:daily_cap"
CODE_MARK = "(JimiC)"
"""What goes after the time of a message the code writes, not Pedro (task 1.14)."""
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
    """The SDK's `total_cost_usd`: the total of the whole session, so it is never counted (the
    cost of a run comes from its `usage`). Kept for the logs and the contract."""
    usage: TokenUsage | None = None
    api_status: int | None = None
    ran: bool = False
    """The engine answered something before failing: it may have spent, unknown how much."""


def usage_of(data: dict[str, Any] | None) -> TokenUsage:
    """The tokens of a result, as the SDK reports them. Cache writes without their split by TTL
    count as 5 minutes, the SDK's default (the heartbeat asks for 1 h, hence its opposite rule)."""
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


def with_onboarding(memory: str, config_dir: Path) -> str:
    """The memory block and, while the onboarding is in progress, its block (task 1.13)."""
    block = onboarding_block(progress(config_dir))
    return f"{memory}\n\n{block}" if block else memory


@dataclass(frozen=True)
class Turn:
    """What a turn gives: the text for Pedro and, if the run ended well, its state (what it
    proposed and whether it read external content). A run that fails proposes nothing."""

    text: str
    run: RunState | None = None
    code: str = "ok"
    """How the run ended: `ok`, `agent:daily_cap` (the SDK was not called) or the code of its
    failure (`agent:api:503`, `agent:max_turns`...)."""


def api_notice_detail(status: int | None) -> str:
    """`rejected:` or `down:`, with the same rule as the heartbeat (`status_kind`)."""
    if status is None:
        return "down:agent_unknown"
    return f"{status_kind(status)}:agent_http_{status}"


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
        config: LiveConfig | None = None,
    ) -> None:
        """`config` is the live configuration; without it, `limits` is a fixed one."""
        self._settings = settings
        self._paths = paths
        self._config = config
        self._limits = limits
        self._clock = clock
        self._conn = conn
        self._sources = sources
        self._run_agent = run_agent
        self._lock = asyncio.Lock()

    @property
    def _active_limits(self) -> Limits:
        """The limits now: the live ones, or the fixed ones."""
        return self._config.limits if self._config is not None else self._limits

    def _now(self) -> datetime:
        return self._clock.now().astimezone(self._settings.timezone)

    async def reply(self, text: str) -> str:
        """The answer to `text`, which Pedro wrote just now."""
        return (await self.respond(text)).text

    async def respond(self, text: str, code_line: str | None = None) -> Turn:
        """The turn for `text`, which Pedro wrote just now. `code_line` goes before it, as a
        line of the code (`[lun 07/01 10:15] (JimiC) …`): what he tapped (decision 1.14-h). It
        is not his: the origin is `text` alone."""
        received = self._now()
        conversations.note_message(self._conn, received.date(), received)
        async with self._lock:
            try:
                return await self._turn(text, received, code_line)
            except Exception as error:
                logger.error("agent turn failed (%s)", type(error).__name__)
                return Turn(FAILED_TEXT, code=f"error:{type(error).__name__}")

    def _capped(self, day: date) -> bool:
        """Today's spend has reached `daily_cap_usd`: the SDK is not called."""
        if usage.spent_on(self._conn, day) < self._active_limits.budget.daily_cap_usd:
            return False
        logger.info("agent: daily_cap")
        system_notices.check_budget(self._conn, self._active_limits.budget, self._now())
        return True

    async def _turn(self, text: str, received: datetime, code_line: str | None) -> Turn:
        day = received.date()
        if self._capped(day):
            return Turn(DAILY_CAP_TEXT, code=DAILY_CAP)
        prompt = f"{stamp(received)} {text}"
        if code_line is not None:
            prompt = f"{stamp(received)} {CODE_MARK} {code_line}\n{prompt}"
        return await self._execute(prompt, RunState(origin=text), day)

    async def run_note(
        self, note: str, mode: RunMode, unless_written_after: datetime | None = None
    ) -> Turn | None:
        """A run without a message of Pedro (task 1.14): the plan the heartbeat hands over, or
        the follow-up after his taps (1.13-c). It resumes today's session with `note`, written
        by the code as `[jue 10/01 07:45] (JimiC) …`, waits for the lock like any message and
        spends from the same budget, but it is not «Pedro conversa» (no `note_message`) and it
        has no origin: no memory proposals, and nothing runs at once.

        With `unless_written_after`, it does not run (`None`) if Pedro wrote after that moment:
        his message already moved the conversation on. Checked inside the lock."""
        async with self._lock:
            try:
                if unless_written_after is not None:
                    last = conversations.last_message_at(self._conn)
                    if last is not None and last > unless_written_after:
                        logger.info("agent: %s skipped (pedro_wrote)", mode)
                        return None
                now = self._now()
                day = now.date()
                if self._capped(day):
                    return Turn(DAILY_CAP_TEXT, code=DAILY_CAP)
                conversations.ensure_day(self._conn, day)
                run = RunState(origin=None, mode=mode)
                return await self._execute(f"{stamp(now)} {CODE_MARK} {note}", run, day)
            except Exception as error:
                logger.error("agent %s failed (%s)", mode, type(error).__name__)
                return Turn(FAILED_TEXT, code=f"error:{type(error).__name__}")

    async def _execute(self, prompt: str, run: RunState, day: date) -> Turn:
        """One run of the agent in the session of `day` (its row exists), with `run` as its
        state; the caller holds the lock."""
        agent_dir = self._paths.agent_dir
        agent_dir.mkdir(mode=0o700, exist_ok=True)
        session = conversations.session_of(self._conn, day)
        limits = self._active_limits
        memory = memory_block(self._paths.config_dir)
        if memory.clipped:
            detail = ",".join(stem(name) for name in memory.clipped)
            system_notices.detect(self._conn, "memory_clipped", day, self._now(), detail)
        context = ToolContext.of(
            self._sources,
            self._conn,
            self._clock,
            self._settings.timezone,
            self._paths.config_dir,
            day,
            run,
        )
        options = build_options(
            self._settings,
            limits,
            agent_dir,
            build_server(context),
            session,
            guard_hooks(limits, self._conn, day, run),
            with_onboarding(memory.text, self._paths.config_dir),
        )
        outcome = await run_once(self._run_agent, prompt, options)
        self._bookkeeping(day, outcome, resumed=session is not None)
        if outcome.code != "ok":
            return Turn(self._answer(outcome), code=outcome.code)
        run.close()
        return Turn(self._answer(outcome), run)

    def _bookkeeping(self, day: date, outcome: Outcome, resumed: bool) -> None:
        now = self._now()
        if outcome.usage is not None or outcome.ran:
            # The dollars of the run come from its tokens, as for Haiku: the SDK's
            # `total_cost_usd` is the total of the whole (resumed) session, not of this run.
            # Without tokens (or without a result at all, once the engine answered), the most a
            # run may spend: the discreet count.
            tokens = outcome.usage or TokenUsage(0, 0)
            usd = Decimal(str(self._active_limits.budget.max_session_usd))
            if tokens == TokenUsage(0, 0) and outcome.code.startswith("agent:api:"):
                # The API refused the run and billed nothing: its row says so (decision 133).
                usd = Decimal(0)
            elif tokens != TokenUsage(0, 0):
                try:
                    usd = usage.cost(self._settings.agent_model, tokens)
                except usage.UnpricedError:
                    # Never lose the count (nor the answer) over a missing price.
                    logger.warning(
                        "agent: model %s has no price, counting the cap of the run",
                        self._settings.agent_model,
                    )
            usage.record_cost(self._conn, day, self._settings.agent_model, tokens, usd)
            system_notices.check_budget(self._conn, self._active_limits.budget, now)
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
