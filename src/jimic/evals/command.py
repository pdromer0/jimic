"""`jimic evaluar latido`: the heartbeat's evaluation, by hand, with the key of `jimic-dev`.

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

from jimic.clock import Clock, SystemClock
from jimic.config import Settings, load_settings
from jimic.evals import report
from jimic.evals.heartbeat import ScenarioResult, run_scenarios
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
) -> EvalRun:
    """Run the evaluation and write its report. `model_api` and `clock` are for the tests.

    Raises `EvalError` outside `dev` (before anything else) or with a wrong file or id, and
    `PathsError`, `ConfigError` or `UnpricedError` like `jimic run`.
    """
    if passes < 1:
        raise EvalError("--pasadas tiene que ser 1 o más")
    paths = resolve_paths(environ)
    refuse_unless_dev(paths)
    suite = load_suite(eval_file)
    chosen = suite.select(scenario_ids)
    settings = load_settings(paths)
    model = settings.heartbeat_model
    require_cache_prices(model)
    started = (clock or SystemClock(settings.timezone)).now().astimezone(settings.timezone)
    results = asyncio.run(_run(model_api(settings), model, suite, chosen, passes))
    skipped = [scenario for scenario in chosen if scenario.pending is not None]
    paid = report.spend(model, results)
    folder = paths.data_dir / REPORTS
    comparison = report.compare_with_previous(folder, [item.outcome for item in results])
    screen = report.summary(model, passes, results, skipped, paid, comparison)
    document = report.document(started, model, passes, results, skipped, paid, comparison)
    json_path, md_path = report.write_report(
        folder, started, document, report.markdown(screen, results, started)
    )
    return EvalRun(screen, json_path, md_path)
