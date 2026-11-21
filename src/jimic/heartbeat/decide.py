"""The decision of the heartbeat with Haiku (decisions 9, 13 and 25; task 1.8).

One direct call to the API per heartbeat that passes the first pass: a fixed block (the
instructions, today's active intentions and the tone) with `cache_control`, and the state of the
day at the end, in the user message. The answer is structured: `messages.create` with the
schema of `HeartbeatDecision` in `output_config.format`, validated here, so a call that answers
something invalid still has its usage counted (`messages.parse` would raise and lose it).

- An answer that does not fit (an intention that is not active today, speaking without a
  message, a message too long, or no valid JSON) is `invalid_decision:<why>`: nobody speaks
  and it is not retried.
- The plan and the close (decision 1.8-b) are decided and recorded, but nothing goes out until
  tasks 1.14 and 1.16 bring them.
- If the API fails (RF-04), each `notice` and `key_task` whose default time falls in this
  heartbeat's slot and has not gone out today goes out with a fixed template of the code.

What the model writes (`reason`, `message`) never reaches a log: logs say codes and ids.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from importlib.resources import files
from typing import Any

import anthropic
from anthropic.types import Message
from pydantic import BaseModel, ConfigDict, ValidationError

from jimic.channel.message import telegram_length
from jimic.config import Settings
from jimic.heartbeat.cycle import Decision
from jimic.heartbeat.limits import Outgoing, applies_on
from jimic.heartbeat.state import DayState
from jimic.memory.schemas import DailyPlan, DayClose, Intention, Intentions, KeyTask, Notice
from jimic.memory.usage import TokenUsage

logger = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com"
"""Explicit: otherwise the SDK takes `ANTHROPIC_BASE_URL` from the environment, and JimiC
only reads its `.env`."""
TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 2
MAX_TOKENS = 2048
"""Room for the adaptive thinking of the model as well as the answer."""
EFFORT = "low"
"""The lowest the API offers."""
CACHE_TTL = "1h"
"""Heartbeats are 15 minutes apart: a 5-minute cache would expire before every call."""
MAX_MESSAGE = 400
"""Telegram length (UTF-16) of one message: seven grouped (the most the cap lets wait) fit."""
SLOT = timedelta(minutes=15)
"""A heartbeat's slot for the default times: `default_time <= now < default_time + SLOT`."""
PENDING_TASK = {"daily_plan": "1.14", "day_close": "1.16"}
"""The intentions decided but not sent yet (decision 1.8-b), and the task that brings them."""
INTENTIONS_MARK = "<<INTENCIONES>>"

type CreateMessage = Callable[..., Awaitable[Message]]
"""`AsyncAnthropic().messages.create`, or the tests' double."""


# The schema the model answers with (decision 1.8-a: text only, no buttons). No docstring: it
# would go to the model as the schema's description.
class HeartbeatDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speak: bool
    intent: str | None
    reason: str
    message: str | None


def prompt_template() -> str:
    return files("jimic.heartbeat").joinpath("prompt.md").read_text(encoding="utf-8")


def _describe(intention: Intention, habits: dict[str, str]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": intention.id,
        "type": intention.type,
        "purpose": intention.purpose,
        "default_time": intention.default_time.strftime("%H:%M"),
        "priority": intention.priority,
    }
    if isinstance(intention, DailyPlan | DayClose) and intention.window is not None:
        entry["window"] = {
            "from": intention.window.start.strftime("%H:%M"),
            "to": intention.window.end.strftime("%H:%M"),
        }
    if isinstance(intention, KeyTask):
        entry["habit"] = habits.get(intention.habit, intention.habit)
    if isinstance(intention, Notice):
        entry["days"] = intention.days
    return entry


def fixed_block(intentions: Intentions, now: datetime) -> str:
    """The cached part: the same all day unless `intenciones.yaml` changes."""
    habits = {habit.id: habit.name for habit in intentions.habits}
    lines = [
        json.dumps(_describe(intention, habits), ensure_ascii=False, separators=(",", ":"))
        for intention in intentions.intentions
        if applies_on(intention, now.date())
    ]
    return prompt_template().replace(INTENTIONS_MARK, "\n".join(lines) or "(ninguna)")


def build_request(model: str, system: str, state: DayState) -> dict[str, Any]:
    """The arguments of `messages.create`: the fixed block first, the state last."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral", "ttl": CACHE_TTL},
            }
        ],
        "messages": [{"role": "user", "content": state.to_json()}],
        "output_config": {
            "effort": EFFORT,
            "format": {
                "type": "json_schema",
                "schema": anthropic.transform_schema(HeartbeatDecision),
            },
        },
    }


def usage_of(message: Message) -> TokenUsage:
    """The usage of a call. Cache writes without their split by TTL are the TTL we ask for."""
    usage = message.usage
    written = usage.cache_creation_input_tokens or 0
    if usage.cache_creation is not None:
        five, hour = (
            usage.cache_creation.ephemeral_5m_input_tokens,
            usage.cache_creation.ephemeral_1h_input_tokens,
        )
    else:
        five, hour = (0, written) if CACHE_TTL == "1h" else (written, 0)
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
        cache_write_5m_tokens=five,
        cache_write_1h_tokens=hour,
    )


def _answer_text(message: Message) -> str:
    return "".join(block.text for block in message.content if block.type == "text")


def _invalid(why: str, usage: TokenUsage) -> Decision:
    logger.info("decision: invalid_decision:%s", why)
    return Decision(False, None, f"invalid_decision:{why}", usage=usage)


def judge(message: Message, state: DayState) -> Decision:
    """What an answer of the model decides, after checking it against today's intentions."""
    usage = usage_of(message)
    try:
        answer = HeartbeatDecision.model_validate_json(_answer_text(message))
    except ValidationError:
        return _invalid("unparsed", usage)
    if not answer.speak:
        logger.info("decision: silent")
        return Decision(False, None, answer.reason, usage=usage)
    active = {intention.id: intention.type for intention in state.intentions}
    if answer.intent not in active:
        return _invalid("unknown_intent", usage)
    pending = PENDING_TASK.get(active[answer.intent])
    if pending is not None:
        logger.info("decision: speak %s (pending %s)", answer.intent, pending)
        reason = f"{answer.reason} (pendiente: {pending})"
        return Decision(True, answer.intent, reason, usage=usage)
    text = (answer.message or "").strip()
    if not text:
        return _invalid("no_message", usage)
    if telegram_length(text) > MAX_MESSAGE:
        return _invalid("too_long", usage)
    logger.info("decision: speak %s", answer.intent)
    return Decision(
        True, answer.intent, answer.reason, (Outgoing(intent=answer.intent, text=text),), usage
    )


def template_text(intention: Intention, habits: dict[str, str]) -> str:
    if isinstance(intention, KeyTask):
        return f"⏰ Toca {habits.get(intention.habit, intention.habit)}"
    return f"⏰ {intention.purpose}"


def templates(intentions: Intentions, state: DayState) -> tuple[Outgoing, ...]:
    """Plan B (RF-04): the `notice` and `key_task` of today whose default time falls in this
    heartbeat's slot and that have not gone out today. The plan and the close have none."""
    now = state.now
    sent = {entry.intent for entry in state.sent_today}
    habits = {habit.id: habit.name for habit in intentions.habits}
    chosen = []
    for intention in intentions.intentions:
        if not isinstance(intention, Notice | KeyTask) or not applies_on(intention, now.date()):
            continue
        start = datetime.combine(now.date(), intention.default_time, tzinfo=now.tzinfo)
        if start <= now < start + SLOT and intention.id not in sent:
            chosen.append(Outgoing(intent=intention.id, text=template_text(intention, habits)))
    return tuple(chosen)


class HaikuDecider:
    """The `decide` of `run_cycle`: one call to the model, never retried by us."""

    def __init__(self, create: CreateMessage, model: str, intentions: Intentions) -> None:
        self._create = create
        self._model = model
        self._intentions = intentions

    @property
    def model(self) -> str:
        return self._model

    async def __call__(self, state: DayState) -> Decision:
        request = build_request(self._model, fixed_block(self._intentions, state.now), state)
        try:
            message = await self._create(**request)
        except anthropic.APIError as error:
            logger.warning("decision: api_down (%s)", type(error).__name__)
            return self.plan_b(state)
        return judge(message, state)

    def plan_b(self, state: DayState) -> Decision:
        chosen = templates(self._intentions, state)
        intents = ",".join(item.intent for item in chosen) or None
        return Decision(bool(chosen), intents, "api_down", chosen, api_down=True)


def build_client(settings: Settings) -> anthropic.AsyncAnthropic:
    """The client with the key of the `.env`, passed explicitly (never the environment's).

    The SDK and its HTTP client log at WARNING only: their DEBUG has the request.
    """
    for name in ("anthropic", "httpx2", "httpcore2"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        base_url=API_URL,
        timeout=TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
    )
