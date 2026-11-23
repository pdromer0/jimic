"""The evaluation of the plan of the day: the real agent on each scenario, graded (1.14-f).

A pass is a whole morning of an invented Pedro in a temporary folder: his personal files, a
SQLite of its own, the folder of the agent as `CLAUDE_CONFIG_DIR`, fixed sources and a clock that
moves to each message. The messages of Pedro before the plan go through the conversation, the
plan through `DailyPlanRunner.build` (the same code as the service, without sending it) and the
messages after it through the conversation again: one session of the Agent SDK for the whole
pass, with the prompt, the tools, the guard and the options of 9.2.

A pass is right when the plan is the agent's (not the minimal one) and **every** check of the
scenario holds; the grade is the share of right passes, and the threshold is 100 % (13.3 of the
design). What failed is a list of check names, never the text of the model; the report keeps the
painted plan of each pass (invented) to read its tone.
"""

import asyncio
import logging
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from jimic.agent.conversation import Conversation, RunAgent, sdk_query
from jimic.agent.tools import RunState
from jimic.channel.message import InteractiveMessage, render
from jimic.clock import to_db
from jimic.config import Settings
from jimic.evals.plan_checks import Check, CheckError, CheckInput, run_check
from jimic.evals.plan_scenarios import PlanScenario, PlanSuite, fixed_sources
from jimic.files import write_private
from jimic.memory.database import open_database
from jimic.memory.live import LiveConfig
from jimic.memory.schemas import DailyPlan
from jimic.paths import Paths
from jimic.plan.runner import DailyPlanRunner

logger = logging.getLogger(__name__)

CONCURRENCY = 3
"""Passes at a time: each one starts the engine of the agent as a subprocess."""
AGENT = "agent"


class MovingClock:
    """The clock of a pass: the test of each message moves it."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self) -> datetime:
        return self.moment


@dataclass(frozen=True)
class GradedRun:
    """The checks of one run and what they said."""

    results: tuple[tuple[str, bool], ...]
    items: tuple[dict[str, Any], ...] = field(default=(), repr=False)

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(name for name, ok in self.results if not ok)


@dataclass(frozen=True)
class PlanPass:
    code: str
    """`agent`, `minimal:<why>` (the agent could not) or `error:<Type>` (the pass broke)."""
    plan: GradedRun | None = None
    after: GradedRun | None = None
    painted: str = field(default="", repr=False)
    """The plan as the code painted it (invented)."""
    usd: Decimal = Decimal(0)
    runs: int = 0
    """Runs of the agent in this pass."""

    @property
    def failed(self) -> tuple[str, ...]:
        """What went wrong: the code, if the plan is not the agent's, and the checks."""
        failed: list[str] = [] if self.code == AGENT else [self.code]
        for graded, suffix in ((self.plan, ""), (self.after, " (después)")):
            if graded is not None:
                failed += [f"{name}{suffix}" for name in graded.failed]
        return tuple(failed)

    @property
    def ok(self) -> bool:
        return self.code == AGENT and not self.failed


@dataclass(frozen=True)
class PlanOutcome:
    """The grade of the passes of one scenario: what the report keeps and compares."""

    id: str
    fingerprint: str
    ok: tuple[bool, ...]


@dataclass(frozen=True)
class PlanResult:
    scenario: PlanScenario
    passes: tuple[PlanPass, ...]

    @property
    def outcome(self) -> PlanOutcome:
        return PlanOutcome(
            self.scenario.id, self.scenario.fingerprint, tuple(p.ok for p in self.passes)
        )


@dataclass(frozen=True)
class PlanScore:
    right: int
    passes: int

    @property
    def grade(self) -> Fraction | None:
        return Fraction(self.right, self.passes) if self.passes else None


def plan_score(outcomes: Sequence[PlanOutcome]) -> PlanScore:
    return PlanScore(
        right=sum(sum(outcome.ok) for outcome in outcomes),
        passes=sum(len(outcome.ok) for outcome in outcomes),
    )


def grade(checks: Sequence[Check], data: CheckInput) -> GradedRun:
    results = []
    for check in checks:
        try:
            ok = run_check(check, data)
        except CheckError, KeyError, ValueError:
            ok = False  # a check that cannot read what the agent proposed is a failure
        results.append((str(check), ok))
    return GradedRun(tuple(results), tuple(data.items))


def painted_text(plan: InteractiveMessage | str) -> str:
    return plan if isinstance(plan, str) else render(plan, 1).text


def _write_world(scenario: PlanScenario, suite: PlanSuite, config_dir: Path) -> None:
    config_dir.mkdir(mode=0o700)
    for name, text in scenario.files.items():
        write_private(config_dir / name, text)
    write_private(config_dir / "limites.yaml", suite.limits_text)


def _memory(runs: Sequence[RunState | None]) -> int:
    return sum(len(run.memory) for run in runs if run is not None)


async def run_pass(
    settings: Settings, suite: PlanSuite, scenario: PlanScenario, run_agent: RunAgent = sdk_query
) -> PlanPass:
    """One pass of `scenario`. Never raises for a failure of the agent or of the pass."""
    with tempfile.TemporaryDirectory(prefix="jimic-plan-") as folder:
        try:
            return await _run_pass(settings, suite, scenario, Path(folder), run_agent)
        except Exception as error:  # noqa: BLE001 - a broken pass is a failed pass, never the run
            logger.warning("plan pass %s failed (%s)", scenario.id, type(error).__name__)
            return PlanPass(code=f"error:{type(error).__name__}")


async def _run_pass(
    settings: Settings,
    suite: PlanSuite,
    scenario: PlanScenario,
    folder: Path,
    run_agent: RunAgent,
) -> PlanPass:
    paths = Paths("dev", folder / "config", folder / "data")
    _write_world(scenario, suite, paths.config_dir)
    paths.data_dir.mkdir(mode=0o700)
    config = LiveConfig.load(paths.config_dir)
    clock = MovingClock(scenario.now)
    conn = open_database(paths.database)
    try:
        for change in scenario.changes:
            moment = change.at.replace(tzinfo=scenario.now.tzinfo)
            conn.execute(
                "INSERT INTO changes (created_at, file, before, after, source_message,"
                " operation) VALUES (?, ?, NULL, ?, 'inventado', 'add')",
                (to_db(moment), change.file, change.after),
            )
        conversation = Conversation(
            settings=settings,
            paths=paths,
            limits=config.limits,
            clock=clock,
            conn=conn,
            sources=fixed_sources(scenario, clock),
            run_agent=run_agent,
            config=config,
        )
        runner = DailyPlanRunner(
            conversation=conversation,
            sources=fixed_sources(scenario, clock),
            config=config,
            conn=conn,
            clock=clock,
            timezone=suite.timezone,
            config_dir=paths.config_dir,
        )
        intention = next(
            item for item in config.intentions.intentions if isinstance(item, DailyPlan)
        )
        runs: list[RunState | None] = []
        for message in scenario.before:
            clock.moment = scenario.at(message.at)
            runs.append((await conversation.respond(message.text)).run)
        clock.moment = scenario.now
        built = await runner.build(intention)
        runs.append(built.run)
        last: RunState | None = None
        for message in scenario.after:
            clock.moment = scenario.at(message.at)
            last = (await conversation.respond(message.text)).run
            runs.append(last)
        (spent,) = conn.execute(
            "SELECT COALESCE(SUM(CAST(usd AS REAL)), 0) FROM model_usage"
        ).fetchone()
        usd = Decimal(str(spent))
        count = conn.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0]
    finally:
        conn.close()
    habits = config.intentions.habits
    painted = painted_text(built.plan)
    memory = _memory(runs)
    # The agent's items only if its plan is the one painted: with the minimal plan the pass
    # fails by its code, and its checks must not score what Pedro never saw.
    own = built.run if built.code == AGENT else None
    plan_input = CheckInput(
        now=scenario.now,
        items=own.items if own is not None else [],
        tasks=scenario.tasks,
        events=scenario.events,
        habits=habits,
        plan=own.plan if own is not None else {},
        painted=painted,
        memory_proposals=memory,
    )
    graded_after = None
    if scenario.checks_after:
        moment = scenario.at(scenario.after[-1].at)
        after_input = CheckInput(
            now=moment,
            items=last.items if last is not None else [],
            tasks=scenario.tasks,
            events=scenario.events,
            habits=habits,
            memory_proposals=memory,
        )
        graded_after = grade(scenario.checks_after, after_input)
        if last is None:
            graded_after = GradedRun((("la ejecución de después falló", False),))
    return PlanPass(
        code=built.code,
        plan=grade(scenario.checks, plan_input),
        after=graded_after,
        painted=painted,
        usd=usd,
        runs=count,
    )


async def run_plan_scenarios(
    settings: Settings,
    suite: PlanSuite,
    scenarios: Sequence[PlanScenario],
    passes: int,
    run_agent: RunAgent = sdk_query,
    concurrency: int = CONCURRENCY,
    on_pass: Callable[[PlanScenario, PlanPass], None] | None = None,
) -> list[PlanResult]:
    """`passes` passes of each scenario, at most `concurrency` at a time; in file order."""
    gate = asyncio.Semaphore(concurrency)

    async def one(scenario: PlanScenario) -> PlanPass:
        async with gate:
            result = await run_pass(settings, suite, scenario, run_agent)
        if on_pass is not None:
            on_pass(scenario, result)
        return result

    results = await asyncio.gather(*(one(s) for s in scenarios for _ in range(passes)))
    return [
        PlanResult(scenario, tuple(results[index * passes : (index + 1) * passes]))
        for index, scenario in enumerate(scenarios)
    ]
