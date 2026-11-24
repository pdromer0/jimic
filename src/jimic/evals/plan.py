"""The evaluation of the plan of the day: the real agent on each scenario, graded (1.14-f).

A pass is a whole morning of an invented Pedro in a temporary folder: his personal files, a
SQLite of its own, the folder of the agent as `CLAUDE_CONFIG_DIR`, fixed sources and a clock that
moves to each message. The messages of Pedro before the plan go through the conversation, the
plan through `DailyPlanRunner.build` (the same code as the service, without sending it) and the
messages after it through the conversation again: one session of the Agent SDK for the whole
pass, with the prompt, the tools, the guard and the options of 9.2.

A pass is right when the plan is the agent's (not the minimal one) and **every** check of the
scenario holds; the grade is the share of right passes, and the threshold is 100 % (13.3 of the
design). Each pass also gets the two grades of decision 131: right on its hard rules (`dura`) and
right on its checks of judgement (`criterio`); a pass whose plan is not the agent's fails both.
What failed is a list of check names, never the text of the model; the report keeps the painted
plan of each pass (invented) to read its tone.

The same program runs the close (task 1.16: `DayCloseRunner.build` with the real agent writing
the note; the pass needs the note to be the agent's) and the memory (CU-13: Pedro's messages
through the conversation, the last one graded; its run must end well).
"""

import asyncio
import logging
import sqlite3
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
from jimic.close.runner import DayCloseRunner
from jimic.config import Settings
from jimic.evals.plan_checks import Check, CheckError, CheckInput, run_check
from jimic.evals.plan_scenarios import (
    AnyScenario,
    CloseScenario,
    MemoryScenario,
    PlanScenario,
    PlanSuite,
    fixed_sources,
)
from jimic.files import write_private
from jimic.heartbeat.key_tasks import DoneBy, KeyTaskStatus, todays_task
from jimic.memory import day_plans
from jimic.memory.database import open_database
from jimic.memory.live import LiveConfig
from jimic.memory.schemas import DailyPlan, KeyTask
from jimic.paths import Paths
from jimic.plan.runner import DailyPlanRunner
from jimic.tools.todoist import TaskList

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
    """The checks of one run, what they said and their kind (`dura`, `criterio`)."""

    results: tuple[tuple[str, bool], ...]
    items: tuple[dict[str, Any], ...] = field(default=(), repr=False)
    kinds: tuple[str, ...] = ()
    """The kind of each result; a result without one is a hard rule (a run that broke)."""

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(name for name, ok in self.results if not ok)

    def holds(self, kind: str) -> bool:
        """Every check of `kind` holds."""
        kinds = self.kinds or ("dura",) * len(self.results)
        return all(ok for (_, ok), its in zip(self.results, kinds, strict=False) if its == kind)


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

    def _holds(self, kind: str) -> bool:
        graded = [g for g in (self.plan, self.after) if g is not None]
        return self.code == AGENT and bool(graded) and all(g.holds(kind) for g in graded)

    @property
    def ok_hard(self) -> bool:
        """Right on its hard rules (decision 131)."""
        return self._holds("dura")

    @property
    def ok_criterion(self) -> bool:
        """Right on its checks of judgement (decision 131)."""
        return self._holds("criterio")


@dataclass(frozen=True)
class PlanOutcome:
    """The grade of the passes of one scenario: what the report keeps and compares."""

    id: str
    fingerprint: str
    ok: tuple[bool, ...]
    hard: tuple[bool, ...] = ()
    criterion: tuple[bool, ...] = ()
    block: str = "plan"


@dataclass(frozen=True)
class PlanResult:
    scenario: AnyScenario
    passes: tuple[PlanPass, ...]

    @property
    def outcome(self) -> PlanOutcome:
        return PlanOutcome(
            self.scenario.id,
            self.scenario.fingerprint,
            tuple(p.ok for p in self.passes),
            tuple(p.ok_hard for p in self.passes),
            tuple(p.ok_criterion for p in self.passes),
            self.scenario.block,
        )


@dataclass(frozen=True)
class PlanScore:
    right: int
    passes: int
    hard: int = 0
    """Passes right on their hard rules."""
    criterion: int = 0
    """Passes right on their checks of judgement."""

    @property
    def grade(self) -> Fraction | None:
        return Fraction(self.right, self.passes) if self.passes else None

    @property
    def hard_grade(self) -> Fraction | None:
        return Fraction(self.hard, self.passes) if self.passes else None

    @property
    def criterion_grade(self) -> Fraction | None:
        return Fraction(self.criterion, self.passes) if self.passes else None


def plan_score(outcomes: Sequence[PlanOutcome]) -> PlanScore:
    return PlanScore(
        right=sum(sum(outcome.ok) for outcome in outcomes),
        passes=sum(len(outcome.ok) for outcome in outcomes),
        hard=sum(sum(outcome.hard) for outcome in outcomes),
        criterion=sum(sum(outcome.criterion) for outcome in outcomes),
    )


def grade(checks: Sequence[Check], data: CheckInput) -> GradedRun:
    results = []
    for check in checks:
        try:
            ok = run_check(check, data)
        except CheckError, KeyError, ValueError:
            ok = False  # a check that cannot read what the agent proposed is a failure
        results.append((str(check), ok))
    return GradedRun(tuple(results), tuple(data.items), tuple(c.kind for c in checks))


def painted_text(plan: InteractiveMessage | str) -> str:
    return plan if isinstance(plan, str) else render(plan, 1).text


def _write_world(scenario: AnyScenario, suite: PlanSuite, config_dir: Path) -> None:
    config_dir.mkdir(mode=0o700)
    for name, text in scenario.files.items():
        write_private(config_dir / name, text)
    write_private(config_dir / "limites.yaml", suite.limits_text)


def _memory(runs: Sequence[RunState | None]) -> int:
    return sum(len(run.memory) for run in runs if run is not None)


async def run_pass(
    settings: Settings, suite: PlanSuite, scenario: AnyScenario, run_agent: RunAgent = sdk_query
) -> PlanPass:
    """One pass of `scenario`. Never raises for a failure of the agent or of the pass."""
    with tempfile.TemporaryDirectory(prefix="jimic-plan-") as folder:
        try:
            if isinstance(scenario, CloseScenario):
                return await _run_close(settings, suite, scenario, Path(folder), run_agent)
            if isinstance(scenario, MemoryScenario):
                return await _run_memory(settings, suite, scenario, Path(folder), run_agent)
            return await _run_pass(settings, suite, scenario, Path(folder), run_agent)
        except Exception as error:  # noqa: BLE001 - a broken pass is a failed pass, never the run
            logger.warning("plan pass %s failed (%s)", scenario.id, type(error).__name__)
            return PlanPass(code=f"error:{type(error).__name__}")


@dataclass
class _World:
    paths: Paths
    config: LiveConfig
    clock: MovingClock
    conn: sqlite3.Connection
    conversation: Conversation


def _open_world(
    settings: Settings,
    suite: PlanSuite,
    scenario: AnyScenario,
    folder: Path,
    run_agent: RunAgent,
) -> _World:
    paths = Paths("dev", folder / "config", folder / "data")
    _write_world(scenario, suite, paths.config_dir)
    paths.data_dir.mkdir(mode=0o700)
    config = LiveConfig.load(paths.config_dir)
    clock = MovingClock(scenario.now)
    conn = open_database(paths.database)
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
    return _World(paths, config, clock, conn, conversation)


def _spent(conn: sqlite3.Connection) -> tuple[Decimal, int]:
    (spent,) = conn.execute(
        "SELECT COALESCE(SUM(CAST(usd AS REAL)), 0) FROM model_usage"
    ).fetchone()
    count = conn.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0]
    return Decimal(str(spent)), count


def key_statuses(scenario: CloseScenario) -> list[KeyTaskStatus]:
    """The state of the key tasks the scenario declares, as the heartbeat would hand it."""
    intentions = scenario.intentions
    habits = {habit.id: habit for habit in intentions.habits}
    by_id = {i.id: i for i in intentions.intentions if isinstance(i, KeyTask)}
    statuses = []
    for key in scenario.keys:
        intention = by_id[key.intent]
        habit = habits.get(intention.habit)
        tasks = TaskList(available=True, tasks=list(scenario.tasks))
        task = todays_task(tasks, habit, scenario.now.date())
        done_by: DoneBy | None = None
        if key.state == "done_button":
            done_by = "button"
        elif key.state == "done_apex":
            done_by = "apex"
        statuses.append(
            KeyTaskStatus(
                intent=key.intent,
                task=task,
                done_by=done_by,
                rescheduled=False,
                not_today=key.state == "not_today",
                paused_until=None,
                insist_from=scenario.now,
                tone_level=1,
                sent_today=0,
            )
        )
    return statuses


async def _run_close(
    settings: Settings,
    suite: PlanSuite,
    scenario: CloseScenario,
    folder: Path,
    run_agent: RunAgent,
) -> PlanPass:
    world = _open_world(settings, suite, scenario, folder, run_agent)
    conn = world.conn
    try:
        day = scenario.now.date()
        scheduled = {
            task.id: task.due
            for task in (*scenario.tasks, *scenario.completed)
            if isinstance(task.due, datetime)
        }
        if scenario.plan:
            plan_tasks = [(task_id, scheduled.get(task_id)) for task_id in scenario.plan]
            day_plans.record_plan(conn, day, scenario.now, plan_tasks, {"code": "eval"})
        runner = DayCloseRunner(
            conversation=world.conversation,
            sources=fixed_sources(scenario, world.clock),
            config=world.config,
            conn=conn,
            clock=world.clock,
            timezone=suite.timezone,
            config_dir=world.paths.config_dir,
        )
        built = await runner.build(key_statuses(scenario))
        usd, count = _spent(conn)
    finally:
        conn.close()
    painted = "" if built.message is None else render(built.message, 1).text
    data = CheckInput(
        now=scenario.now,
        items=[],
        tasks=scenario.tasks,
        events=scenario.events,
        habits=scenario.intentions.habits,
        painted=painted,
        text=built.agent_text,
    )
    return PlanPass(
        code=AGENT if built.code == "agent" else built.code,
        plan=grade(scenario.checks, data),
        painted=painted,
        usd=usd,
        runs=count,
    )


async def _run_memory(
    settings: Settings,
    suite: PlanSuite,
    scenario: MemoryScenario,
    folder: Path,
    run_agent: RunAgent,
) -> PlanPass:
    world = _open_world(settings, suite, scenario, folder, run_agent)
    conn = world.conn
    try:
        turns = []
        for message in scenario.messages:
            world.clock.moment = scenario.at(message.at)
            turns.append(await world.conversation.respond(message.text))
        usd, count = _spent(conn)
    finally:
        conn.close()
    last = turns[-1]
    data = CheckInput(
        now=world.clock.moment,
        items=last.run.items if last.run is not None else [],
        tasks=scenario.tasks,
        events=scenario.events,
        habits=scenario.intentions.habits,
        memory_proposals=_memory([turn.run for turn in turns]),
        text=last.text,
        memory=last.run.memory if last.run is not None else [],
    )
    return PlanPass(
        code=AGENT if last.code == "ok" else last.code,
        plan=grade(scenario.checks, data),
        painted=last.text,
        usd=usd,
        runs=count,
    )


async def _run_pass(
    settings: Settings,
    suite: PlanSuite,
    scenario: PlanScenario,
    folder: Path,
    run_agent: RunAgent,
) -> PlanPass:
    world = _open_world(settings, suite, scenario, folder, run_agent)
    paths, config, clock, conn = world.paths, world.config, world.clock, world.conn
    conversation = world.conversation
    try:
        for change in scenario.changes:
            moment = change.at.replace(tzinfo=scenario.now.tzinfo)
            conn.execute(
                "INSERT INTO changes (created_at, file, before, after, source_message,"
                " operation) VALUES (?, ?, NULL, ?, 'inventado', 'add')",
                (to_db(moment), change.file, change.after),
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
        usd, count = _spent(conn)
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
    scenarios: Sequence[AnyScenario],
    passes: int,
    run_agent: RunAgent = sdk_query,
    concurrency: int = CONCURRENCY,
    on_pass: Callable[[AnyScenario, PlanPass], None] | None = None,
) -> list[PlanResult]:
    """`passes` passes of each scenario, at most `concurrency` at a time; in file order."""
    gate = asyncio.Semaphore(concurrency)

    async def one(scenario: AnyScenario) -> PlanPass:
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
