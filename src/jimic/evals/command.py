"""`jimic evaluar latido` and `jimic evaluar plan`, by hand, with the key of `jimic-dev`.

`run` is the heartbeat's (task 1.9) and `run_plan` the plan's (task 1.14, below).

Only with `ENTORNO=dev` (decision 76): it refuses **before** reading the `.env`, like
`jimic telegram demo`. It reads `evals/latido.yaml` from the folder it is launched from, calls
the model of the heartbeat (`MODELO_LATIDO`) `passes` times per scenario, and writes the report in
`<data>/evals/`. It touches neither the database of the copy (the spend does not go to
`model_usage`: an evaluation is not use of the service) nor Todoist, Calendar or Telegram.
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from jimic.agent.conversation import RunAgent, sdk_query
from jimic.clock import Clock, SystemClock
from jimic.config import Settings, load_settings
from jimic.evals import plan_report, report
from jimic.evals.heartbeat import ScenarioResult, run_scenarios
from jimic.evals.plan import run_plan_scenarios
from jimic.evals.plan_scenarios import PLAN_FILE, load_plan_suite
from jimic.evals.scenarios import EVALS_FILE, EvalError, Scenario, Suite, load_suite
from jimic.heartbeat.decide import ModelApi, build_model_api
from jimic.memory.usage import require_cache_prices
from jimic.paths import Paths, resolve_paths

DEFAULT_PASSES = 3
REPORTS = "evals"
"""The folder of the reports, inside the data root of the copy."""


@dataclass(frozen=True)
class EvalRun:
    screen: str
    json_path: Path
    md_path: Path

    @property
    def text(self) -> str:
        """What `jimic evaluar latido` prints: the summary and where the report is."""
        return f"{self.screen}\nInforme: {self.json_path} y {self.md_path.name}"


def refuse_unless_dev(paths: Paths) -> None:
    if paths.environment != "dev":
        raise EvalError(
            f"la evaluación solo corre con ENTORNO=dev, y esta copia es {paths.environment}:"
            " lánzala con JIMIC_ENTORNO=dev"
        )


async def _run(
    api: ModelApi, model: str, suite: Suite, scenarios: Sequence[Scenario], passes: int
) -> list[ScenarioResult]:
    try:
        return await run_scenarios(api.create, model, suite, scenarios, passes)
    finally:
        await api.close()


def run(
    environ: Mapping[str, str],
    scenario_ids: Sequence[str] | None = None,
    passes: int = DEFAULT_PASSES,
    *,
    eval_file: Path = EVALS_FILE,
    model_api: Callable[[Settings], ModelApi] = build_model_api,
    clock: Clock | None = None,
    compare_to: str | None = None,
) -> EvalRun:
    """Run the evaluation and write its report. `compare_to` is the report of `--comparar-con`
    (without it, the last complete one); `model_api` and `clock` are for the tests.

    Raises `EvalError` outside `dev` (before anything else), with a wrong file or id or a report
    of `--comparar-con` that cannot be read, and `PathsError`, `ConfigError` or `UnpricedError`
    like `jimic run`. All before spending.
    """
    paths = resolve_paths(environ)
    refuse_unless_dev(paths)
    if passes < 1:
        raise EvalError("--pasadas tiene que ser 1 o más")
    suite = load_suite(eval_file)
    chosen = suite.select(scenario_ids)
    settings = load_settings(paths)
    model = settings.heartbeat_model
    require_cache_prices(model)
    folder = paths.data_dir / REPORTS
    reference = report.chosen_report(folder, compare_to) if compare_to is not None else None
    complete = {scenario.id for scenario in chosen} == {scenario.id for scenario in suite.scenarios}
    started = (clock or SystemClock(settings.timezone)).now().astimezone(settings.timezone)
    results = asyncio.run(_run(model_api(settings), model, suite, chosen, passes))
    skipped = [scenario for scenario in chosen if scenario.pending is not None]
    paid = report.spend(model, results)
    outcomes = [item.outcome for item in results]
    if reference is not None:
        name, previous = reference
        comparison = report.compare(name, outcomes, previous)
    else:
        comparison = report.compare_with_previous(folder, outcomes)
    screen = report.summary(model, passes, results, skipped, paid, comparison)
    document = report.document(started, model, passes, results, skipped, paid, comparison, complete)
    json_path, md_path = report.write_report(
        folder, started, document, report.markdown(screen, results, started)
    )
    return EvalRun(screen, json_path, md_path)


# --- `jimic evaluar plan` (decision 1.14-f) ------------------------------------------------------


def run_plan(
    environ: Mapping[str, str],
    scenario_ids: Sequence[str] | None = None,
    passes: int = DEFAULT_PASSES,
    *,
    eval_file: Path = PLAN_FILE,
    run_agent: RunAgent = sdk_query,
    clock: Clock | None = None,
) -> EvalRun:
    """The evaluation of the plan with the real agent (`MODELO_AGENTE`), and its report.

    Only with `ENTORNO=dev`, refused before reading the `.env`. Each pass runs the agent in a
    temporary folder with invented files and fixed sources: it touches neither the database of
    the copy nor Todoist, Calendar or Telegram. `run_agent` and `clock` are for the tests."""
    paths = resolve_paths(environ)
    refuse_unless_dev(paths)
    if passes < 1:
        raise EvalError("--pasadas tiene que ser 1 o más")
    suite = load_plan_suite(eval_file)
    chosen = suite.select(scenario_ids)
    settings = load_settings(paths)
    started = (clock or SystemClock(settings.timezone)).now().astimezone(settings.timezone)
    results = asyncio.run(run_plan_scenarios(settings, suite, chosen, passes, run_agent))
    folder = paths.data_dir / REPORTS
    comparison = plan_report.compare_with_previous(folder, [item.outcome for item in results])
    model = settings.agent_model
    screen = plan_report.summary(model, passes, results, comparison)
    document = plan_report.document(started, model, passes, results, comparison)
    json_path, md_path = report.write_report(
        folder,
        started,
        document,
        plan_report.markdown(screen, results, started),
        plan_report.PREFIX,
    )
    return EvalRun(screen, json_path, md_path)
