"""The heartbeat as the service runs it, every 15 minutes (6.3 of the design; task 1.8).

`Heartbeat.beat` runs one cycle and does what the cycle does not: it sends the verdicts that say
`send` (a grouped one is one message), hands the plan and the close over to the agent through an
injected hook (`on_handover`, which writes a row in `handovers`), writes one row in `decisions`
per heartbeat (also when the first pass stops it, or when it finds the previous one still
running), one row in `notices` per intention sent (only proactive messages), counts the spend and
sends the system notices.

The system notices (decision 1.8-c) are fixed texts of the code, outside the daily cap: they do
not go to `notices`. At most one per kind and day (the budget one per month), and never in the
quiet hours: one detected in them waits for the first heartbeat out of them, that same day.

What the model wrote (its reason, its message) goes to `decisions` and to Telegram, never to a
log. Logs say codes, ids and counts.
"""

import logging
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from jimic.channel.bot import SendError
from jimic.clock import Clock, to_db
from jimic.heartbeat.cycle import CycleResult, Decision, run_cycle
from jimic.heartbeat.limits import SEPARATOR, Verdict, WaitingNotices, in_window
from jimic.heartbeat.sources import Sources
from jimic.heartbeat.state import DayState
from jimic.memory import system_notices, usage
from jimic.memory.notices import record_handover
from jimic.memory.schemas import Intentions, Limits

logger = logging.getLogger(__name__)

INTERVAL = timedelta(minutes=15)
SOURCE_NAMES = {"calendar": "el calendario", "tasks": "las tareas de Todoist", "apex": "APEX"}
HINTS = {"calendar:auth": "Para volver a darle permiso: jimic google autorizar"}
"""The command to run when a source needs Pedro (pending of task 1.5)."""
MEANWHILE = "Mientras tanto, tus avisos salen a su hora por defecto con un texto sencillo."
API_DOWN_TEXT = f"⚠️ No consigo hablar con el modelo: la API de Anthropic no responde. {MEANWHILE}"
API_REJECTED_TEXT = (
    "⚠️ La API de Anthropic rechaza las peticiones ({code}): revisa CLAVE_ANTHROPIC y"
    f" MODELO_LATIDO en el .env. {MEANWHILE}"
)


class Sender(Protocol):
    async def send_text(self, text: str) -> int: ...


type OnHandover = Callable[[str, DayState], Awaitable[None]]
"""Called with the id of a plan or a close and the state of the day when the heartbeat hands it
over to the agent. Tasks 1.14 and 1.16 fill it in; if it raises, the intention is not attended."""

HANDOVER_TASK = {"daily_plan": "1.14", "day_close": "1.16"}


async def log_handover(intent: str, state: DayState) -> None:
    """The hook of task 1.10: only a log line, with the id and the task that will bring it."""
    kind = next((item.type for item in state.intentions if item.id == intent), "?")
    logger.info("handover %s (pending %s)", intent, HANDOVER_TASK.get(kind, "?"))


class Decider(Protocol):
    """A `decide` for `run_cycle` that also says which model it calls, for the spend."""

    @property
    def model(self) -> str: ...

    async def __call__(self, state: DayState, /) -> Decision: ...


def next_beat(now: datetime) -> datetime:
    """The first quarter of an hour (:00, :15, :30, :45) at or after `now`, in its zone.

    Counted in UTC: the zone's offsets are whole hours, and a wall-clock sum would land in
    the gap of a change of time.
    """
    utc = now.astimezone(UTC)
    floor = utc.replace(minute=utc.minute - utc.minute % 15, second=0, microsecond=0)
    first = floor if floor == utc else floor + INTERVAL
    return first.astimezone(now.tzinfo)


def _verdict_codes(verdicts: tuple[Verdict, ...], failures: dict[int, str]) -> str:
    codes = []
    for index, verdict in enumerate(verdicts):
        code = f"{verdict.action} {verdict.reason}"
        if index in failures:
            code += f" {failures[index]}"
        codes.append(code)
    return f" [{'; '.join(codes)}]" if codes else ""


class Heartbeat:
    """One per service: it keeps the queue of the group window as long as the service lives."""

    def __init__(
        self,
        *,
        clock: Clock,
        timezone: ZoneInfo,
        limits: Limits,
        intentions: Intentions,
        sources: Sources,
        conn: sqlite3.Connection,
        decider: Decider,
        sender: Sender,
        on_handover: OnHandover = log_handover,
    ) -> None:
        self._clock = clock
        self._timezone = timezone
        self._limits = limits
        self._intentions = intentions
        self._sources = sources
        self._conn = conn
        self._decider = decider
        self._sender = sender
        self._on_handover = on_handover
        self.queue = WaitingNotices()
        self._running = False

    def _now(self) -> datetime:
        return self._clock.now().astimezone(self._timezone)

    async def beat(self) -> None:
        """One heartbeat. Never raises: a failure is a row `error:<Type>` and a log line."""
        now = self._now()
        if self._running:
            logger.warning("heartbeat skipped (overlap)")
            self._record(now, False, None, "overlap")
            return
        self._running = True
        try:
            await self._beat(now)
        except Exception as error:
            # Only the type: a message can quote content.
            logger.error("heartbeat failed (%s)", type(error).__name__)
            self._record(now, False, None, f"error:{type(error).__name__}")
        finally:
            self._running = False

    async def _beat(self, now: datetime) -> None:
        result = await run_cycle(
            clock=self._clock,
            timezone=self._timezone,
            limits=self._limits,
            intentions=self._intentions,
            sources=self._sources,
            conn=self._conn,
            queue=self.queue,
            decide=self._decider,
        )
        if result.stopped is not None:
            self._record(now, False, None, result.stopped)
        else:
            await self._after_decision(now, result)
        # The row of this heartbeat is written: what follows must never write a second one.
        try:
            await self._send_system_notices()
        except Exception as error:
            logger.error("heartbeat system notices failed (%s)", type(error).__name__)

    async def _after_decision(self, now: datetime, result: CycleResult) -> None:
        """Send first (the second pass already took what waited from the queue), then the row
        of the heartbeat, then the bookkeeping, which can fail without losing either."""
        decision = result.decision
        assert decision is not None  # the cycle decides whenever the first pass goes on
        failures = await self._send(result.verdicts)
        assert result.state is not None  # as the decision
        failures |= await self._hand_over(result.verdicts, result.state)
        reason = decision.reason + _verdict_codes(result.verdicts, failures)
        self._record(now, decision.speak, decision.intent, reason)
        try:
            self._bookkeeping(now, decision, result)
        except Exception as error:
            logger.error("heartbeat bookkeeping failed (%s)", type(error).__name__)

    def _bookkeeping(self, now: datetime, decision: Decision, result: CycleResult) -> None:
        day = now.date()
        if decision.usage is not None:
            usage.record(self._conn, day, self._decider.model, decision.usage)
            self._check_budget(now)
        if decision.api_error is not None:
            system_notices.detect(self._conn, "api_down", day, now, decision.api_error)
        if result.state is not None:
            down = [
                f"{name}:{source['unavailable_reason']}"
                for name, source in result.state.sources().items()
                if not source["available"]
            ]
            if down:
                system_notices.detect(self._conn, "source_down", day, now, ",".join(down))

    async def _send(self, verdicts: tuple[Verdict, ...]) -> dict[int, str]:
        """Send each `send` verdict as one message; return the failures by verdict index, as
        codes (`send_failed:<code>`)."""
        failures: dict[int, str] = {}
        for index, verdict in enumerate(verdicts):
            if verdict.action != "send":
                continue
            try:
                message_id = await self._sender.send_text(
                    SEPARATOR.join(item.text for item in verdict.items)
                )
            except SendError as error:
                failures[index] = f"send_failed:{error}"
                continue
            sent_at = to_db(self._clock.now())
            self._conn.executemany(
                "INSERT INTO notices (sent_at, intent, telegram_message_id) VALUES (?, ?, ?)",
                [(sent_at, intent, message_id) for intent in verdict.intents],
            )
            logger.info("heartbeat sent %s", ",".join(verdict.intents))
        return failures

    async def _hand_over(self, verdicts: tuple[Verdict, ...], state: DayState) -> dict[int, str]:
        """Call the hook for each `handover` verdict and mark it attended; return the failures
        by verdict index. Nothing is sent and nothing goes to `notices` (decision 1.8-b)."""
        failures: dict[int, str] = {}
        for index, verdict in enumerate(verdicts):
            if verdict.action != "handover":
                continue
            for intent in verdict.intents:
                try:
                    await self._on_handover(intent, state)
                except Exception as error:
                    logger.error("handover failed (%s)", type(error).__name__)
                    failures[index] = f"handover_failed:{type(error).__name__}"
                    continue
                record_handover(self._conn, intent, self._clock.now())
        return failures

    def _record(self, now: datetime, speak: bool, intent: str | None, reason: str) -> None:
        self._conn.execute(
            "INSERT INTO decisions (created_at, speak, intent, reason) VALUES (?, ?, ?, ?)",
            (to_db(now), int(speak), intent, reason),
        )

    # --- System notices ------------------------------------------------------------------------

    def _check_budget(self, now: datetime) -> None:
        budget = self._limits.budget
        spent = usage.spent_in_month(self._conn, now.date())
        if spent > 0 and spent >= budget.warn_at * budget.monthly_target_usd:
            system_notices.detect(self._conn, "budget", now.date(), now)

    def _budget_text(self, day: date) -> str:
        """The spend of the month as it is now: it may be past the warning when it goes out."""
        target = self._limits.budget.monthly_target_usd
        spent = usage.spent_in_month(self._conn, day)
        share = f", el {spent / target:.0%} del objetivo de {target:.2f} $" if target > 0 else ""
        return f"💸 Llevas {spent:.2f} $ gastados este mes{share}."

    def _text_of(self, notice: system_notices.PendingNotice, day: date) -> str:
        if notice.kind == "api_down":
            kind, _, code = notice.detail.partition(":")
            return API_REJECTED_TEXT.format(code=code) if kind == "rejected" else API_DOWN_TEXT
        if notice.kind == "budget":
            return self._budget_text(day)
        parts = [entry.split(":", 1)[0] for entry in notice.detail.split(",")]
        names = [SOURCE_NAMES.get(part, part) for part in parts]
        lines = [f"⚠️ Ahora mismo no puedo leer {' ni '.join(names)}."]
        lines += [hint for code, hint in HINTS.items() if code in notice.detail.split(",")]
        return "\n".join(lines)

    async def _send_system_notices(self) -> None:
        """Send what waits, unless the clock is in the quiet hours now (it may have moved since
        the heartbeat began)."""
        now = self._now()
        if in_window(now.time(), self._limits.quiet_hours):
            return
        for notice in system_notices.pending(self._conn, now.date()):
            try:
                await self._sender.send_text(self._text_of(notice, now.date()))
            except SendError:
                continue  # it stays waiting for the next heartbeat
            system_notices.mark_sent(self._conn, notice.id, self._clock.now())
            logger.info("system notice sent (%s)", notice.kind)
