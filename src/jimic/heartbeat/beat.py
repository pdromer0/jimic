"""The heartbeat as the service runs it, every 15 minutes (6.3 of the design; task 1.8).

`Heartbeat.beat` runs one cycle and does what the cycle does not: it sends the verdicts that say
`send` (a grouped one is one message), writes one row in `decisions` per heartbeat (also when
the first pass stops it, or when it finds the previous one still running), one row in `notices`
per intention sent (only proactive messages), counts the spend and sends the system notices.

The system notices (decision 1.8-c) are fixed texts of the code, outside the daily cap: they do
not go to `notices`. At most one per kind and day (the budget one per month), and never in the
quiet hours: one detected in them waits for the first heartbeat out of them, that same day.

What the model wrote (its reason, its message) goes to `decisions` and to Telegram, never to a
log. Logs say codes, ids and counts.
"""

import logging
import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from jimic.channel.bot import SendError
from jimic.clock import Clock, to_db
from jimic.heartbeat.cycle import CycleResult, Decision, run_cycle
from jimic.heartbeat.limits import Verdict, WaitingNotices, in_window
from jimic.heartbeat.sources import Sources
from jimic.heartbeat.state import DayState
from jimic.memory import system_notices, usage
from jimic.memory.schemas import Intentions, Limits

logger = logging.getLogger(__name__)

INTERVAL = timedelta(minutes=15)
SOURCE_NAMES = {"calendar": "el calendario", "tasks": "las tareas de Todoist", "apex": "APEX"}
HINTS = {"calendar:auth": "Para volver a darle permiso: jimic google autorizar"}
"""The command to run when a source needs Pedro (pending of task 1.5)."""
API_DOWN_TEXT = (
    "⚠️ No consigo hablar con el modelo: la API de Anthropic no responde. Mientras tanto, tus"
    " avisos salen a su hora por defecto con un texto sencillo."
)


class Sender(Protocol):
    async def send_text(self, text: str) -> int: ...


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
            code += f" send_failed:{failures[index]}"
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
    ) -> None:
        self._clock = clock
        self._timezone = timezone
        self._limits = limits
        self._intentions = intentions
        self._sources = sources
        self._conn = conn
        self._decider = decider
        self._sender = sender
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
        if decision.api_down:
            system_notices.detect(self._conn, "api_down", day, now)
        if result.state is not None:
            down = [
                f"{name}:{source['unavailable_reason']}"
                for name, source in result.state.sources().items()
                if not source["available"]
            ]
            if down:
                system_notices.detect(self._conn, "source_down", day, now, ",".join(down))

    async def _send(self, verdicts: tuple[Verdict, ...]) -> dict[int, str]:
        """Send each `send` verdict as one message; return the failures by verdict index."""
        failures: dict[int, str] = {}
        for index, verdict in enumerate(verdicts):
            if verdict.action != "send":
                continue
            try:
                message_id = await self._sender.send_text(
                    "\n\n".join(item.text for item in verdict.items)
                )
            except SendError as error:
                failures[index] = str(error)
                continue
            sent_at = to_db(self._clock.now())
            self._conn.executemany(
                "INSERT INTO notices (sent_at, intent, telegram_message_id) VALUES (?, ?, ?)",
                [(sent_at, intent, message_id) for intent in verdict.intents],
            )
            logger.info("heartbeat sent %s", ",".join(verdict.intents))
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
        budget = self._limits.budget
        spent = usage.spent_in_month(self._conn, day)
        return (
            f"💸 Llevas {spent:.2f} $ gastados este mes, el {budget.warn_at:.0%} del objetivo de"
            f" {budget.monthly_target_usd:.2f} $."
        )

    def _text_of(self, notice: system_notices.PendingNotice, day: date) -> str:
        if notice.kind == "api_down":
            return API_DOWN_TEXT
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
