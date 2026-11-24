"""The report of the evaluation of the plan (decision 1.14-f) and its comparison with the last.

Two files in `<data>/evals/`, `plan-<AAAAMMDD-HHMM>.json` and `.md`, as the heartbeat's (1.9-e):
every pass with what failed and the plan as the code painted it (invented data), to read its tone.
Each block (the plan, the close and the memory, task 1.16) gets its grade and the two of decision
131: the passes right on their hard rules and those right on their checks of judgement, each with
its threshold of 13.3 (100 %).

The comparison is scenario by scenario, only on the ids with the same fingerprint, and flags a
grade that drops more than 5 points (the regression of 13.3), on the grade of every check. It is
made with the last **complete** report (`complete`, version 2: every scenario of the file ran),
as the heartbeat's (decision 148), or with the one `--comparar-con` names. The pieces are those
of `jimic.evals.report`: the names of the files, the reports and the partition.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from jimic.evals.plan import GradedRun, PlanOutcome, PlanResult, PlanScore, plan_score
from jimic.evals.report import (
    REGRESSION,
    UnreadableReport,
    chosen_report,
    dollars,
    latest_complete,
    partition,
    percent,
)

PREFIX = "plan"
THRESHOLD = Fraction(1)
"""13.3 of the design: 100 % of the automatic checks; the hard rules and the checks of
judgement, each with this threshold (decision 131)."""
VERSION = 2
"""2: `complete`, the blocks and the two grades (task 1.16). Version 1 is never complete."""
BLOCK_NAMES = {"plan": "Plan", "cierre": "Cierre", "memoria": "Memoria"}


@dataclass(frozen=True)
class PlanComparison:
    previous: str
    before: PlanScore | None
    after: PlanScore | None
    worse: bool
    common: tuple[str, ...]
    new: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]
    unreadable: bool = False
    skipped: tuple[str, ...] = ()
    """Newer reports that could not be read, so could not say they were complete."""


def compare(name: str, current: Sequence[PlanOutcome], previous: Sequence[PlanOutcome]):
    parts = partition(current, previous)
    if not parts.common:
        return PlanComparison(name, None, None, False, (), parts.new, parts.removed, parts.changed)
    before_by_id = {outcome.id: outcome for outcome in previous}
    before = plan_score([before_by_id[outcome.id] for outcome in parts.common])
    after = plan_score(parts.common)
    worse = (
        before.grade is not None
        and after.grade is not None
        and before.grade - after.grade > REGRESSION
    )
    ids = tuple(outcome.id for outcome in parts.common)
    return PlanComparison(name, before, after, worse, ids, parts.new, parts.removed, parts.changed)


def read_outcomes(document: object) -> list[PlanOutcome]:
    try:
        assert isinstance(document, dict)
        return [
            PlanOutcome(
                id=str(item["id"]),
                fingerprint=str(item["fingerprint"]),
                ok=tuple(bool(one["ok"]) for one in item["passes"]),
                hard=tuple(bool(one.get("ok_hard", one["ok"])) for one in item["passes"]),
                criterion=tuple(bool(one.get("ok_criterion", one["ok"])) for one in item["passes"]),
                block=str(item.get("block", "plan")),
            )
            for item in document["scenarios"]
        ]
    except (AssertionError, KeyError, TypeError, AttributeError) as error:
        raise UnreadableReport(type(error).__name__) from None


def compare_with_previous(folder: Path, current: Sequence[PlanOutcome]) -> PlanComparison | None:
    """With the last complete report; `None` when there is none and nothing was skipped."""
    latest = latest_complete(folder, PREFIX)
    skipped = latest.skipped
    if latest.path is None:
        if not skipped:
            return None
        return PlanComparison("", None, None, False, (), (), (), (), skipped=skipped)
    name = latest.path.name
    try:
        previous = read_outcomes(latest.document)
    except UnreadableReport:
        return PlanComparison(
            name, None, None, False, (), (), (), (), unreadable=True, skipped=skipped
        )
    return replace(compare(name, current, previous), skipped=skipped)


def chosen(folder: Path, name: str) -> tuple[str, list[PlanOutcome]]:
    """The report of `--comparar-con`; `EvalError` before spending if it cannot be read."""
    return chosen_report(folder, name, read_outcomes)


def spend(results: Sequence[PlanResult]) -> tuple[Decimal, int]:
    """The SDK's estimate of what the passes spent, and how many runs of the agent."""
    passes = [one for item in results for one in item.passes]
    return sum((one.usd for one in passes), Decimal(0)), sum(one.runs for one in passes)


def _comparison_lines(comparison: PlanComparison | None) -> list[str]:
    if comparison is None:
        return ["Comparación: no hay ningún informe completo anterior."]
    skipped = (
        [f"  saltados (no se pueden leer): {', '.join(comparison.skipped)}"]
        if comparison.skipped
        else []
    )
    if not comparison.previous:
        return ["Comparación: no hay ningún informe completo anterior que se pueda leer.", *skipped]
    if comparison.unreadable:
        return [f"Comparación: {comparison.previous} no se puede leer; sin comparación.", *skipped]
    lines = [f"Comparación con {comparison.previous}:", *skipped]
    before, after = comparison.before, comparison.after
    if before is None or after is None or before.grade is None or after.grade is None:
        lines.append("  sin informe anterior comparable (ningún escenario en común)")
    else:
        lines.append(
            f"  sobre {len(comparison.common)} escenarios comunes:"
            f" {percent(before.grade)} → {percent(after.grade)}"
        )
        if comparison.worse:
            lines.append("  ⚠️ Regresión: la nota baja más de 5 puntos")
    for label, ids in (
        ("nuevos", comparison.new),
        ("quitados", comparison.removed),
        ("cambiados", comparison.changed),
    ):
        if ids:
            lines.append(f"  {label}: {', '.join(ids)}")
    return lines


def _mark(grade: Fraction) -> str:
    return "✅" if grade >= THRESHOLD else "❌"


def _kinds_line(label: str, total: PlanScore) -> str | None:
    """`Plan: duras 100,0 % (39/39) ✅ · criterio 97,4 % (38/39) ❌`."""
    hard, criterion = total.hard_grade, total.criterion_grade
    if hard is None or criterion is None:
        return None
    return (
        f"{label}: duras {percent(hard)} ({total.hard}/{total.passes}) {_mark(hard)}"
        f" · criterio {percent(criterion)} ({total.criterion}/{total.passes}) {_mark(criterion)}"
    )


def block_lines(results: Sequence[PlanResult]) -> list[str]:
    """The grade of each block that ran, with its two kinds (decision 131)."""
    lines = []
    for block, label in BLOCK_NAMES.items():
        outcomes = [item.outcome for item in results if item.outcome.block == block]
        if not outcomes:
            continue
        score = plan_score(outcomes)
        assert score.grade is not None
        line = _kinds_line(f"{label} ({len(outcomes)})", score)
        lines.append(f"{line} · todas {percent(score.grade)} {_mark(score.grade)}")
    return lines


def summary(
    model: str, passes: int, results: Sequence[PlanResult], comparison: PlanComparison | None
) -> str:
    total = plan_score([item.outcome for item in results])
    lines = [f"Evaluación del plan · {model} · {passes} pasadas · {len(results)} escenarios"]
    if total.grade is not None:
        lines.append(
            f"Nota: {percent(total.grade)} ({total.right}/{total.passes} pasadas cumplen todas"
            f" sus comprobaciones) {_mark(total.grade)} (umbral 100 %)"
        )
        lines.extend(block_lines(results))
    usd, runs = spend(results)
    per_run = f" ({dollars(usd / runs)}/ejecución)" if runs else ""
    lines.append(f"Coste: {dollars(usd)} en {runs} ejecuciones del agente{per_run}")
    failing = [item for item in results if not all(item.outcome.ok)]
    if failing:
        lines.append("Fallan:")
        for item in failing:
            reasons = [", ".join(one.failed) for one in item.passes if not one.ok]
            lines.append(
                f"  {item.scenario.id} ({sum(item.outcome.ok)}/{len(item.passes)} bien):"
                f" {' | '.join(reasons)}"
            )
    lines.extend(_comparison_lines(comparison))
    return "\n".join(lines)


def document(
    started: datetime,
    model: str,
    passes: int,
    results: Sequence[PlanResult],
    comparison: PlanComparison | None,
    complete: bool = False,
) -> dict[str, Any]:
    """The JSON of the report. `complete`: every scenario of the file was chosen."""
    total = plan_score([item.outcome for item in results])
    usd, runs = spend(results)
    return {
        "version": VERSION,
        "complete": complete,
        "started": started.isoformat(timespec="minutes"),
        "model": model,
        "passes": passes,
        "grade": {
            "right": total.right,
            "passes": total.passes,
            "hard": total.hard,
            "criterion": total.criterion,
        },
        "spend": {"usd": str(usd), "runs": runs},
        "comparison": None
        if comparison is None
        else {
            "previous": comparison.previous,
            "unreadable": comparison.unreadable,
            "skipped": list(comparison.skipped),
            "common": list(comparison.common),
            "worse": comparison.worse,
            "new": list(comparison.new),
            "removed": list(comparison.removed),
            "changed": list(comparison.changed),
        },
        "scenarios": [
            {
                "id": item.scenario.id,
                "block": item.scenario.block,
                "title": item.scenario.title,
                "origin": item.scenario.origin,
                "fingerprint": item.scenario.fingerprint,
                "passes": [
                    {
                        "ok": one.ok,
                        "ok_hard": one.ok_hard,
                        "ok_criterion": one.ok_criterion,
                        "code": one.code,
                        "failed": list(one.failed),
                        "checks": [] if one.plan is None else _checks_json(one.plan),
                        "checks_after": [] if one.after is None else _checks_json(one.after),
                        "items": [] if one.plan is None else list(one.plan.items),
                        "items_after": [] if one.after is None else list(one.after.items),
                        "painted": one.painted,
                        "usd": str(one.usd),
                        "runs": one.runs,
                    }
                    for one in item.passes
                ],
            }
            for item in results
        ],
    }


def _checks_json(graded: GradedRun) -> list[list[Any]]:
    """`[name, ok, kind]` for each check."""
    kinds = graded.kinds or ("dura",) * len(graded.results)
    return [[name, ok, kind] for (name, ok), kind in zip(graded.results, kinds, strict=False)]


def markdown(screen: str, results: Sequence[PlanResult], started: datetime) -> str:
    lines = [
        f"# Evaluación del plan · {started:%Y-%m-%d %H:%M}",
        "",
        "Datos inventados (`evals/plan.yaml`).",
        "",
        "```text",
        screen,
        "```",
        "",
        "## Escenarios",
    ]
    for item in results:
        scenario = item.scenario
        mark = "✅" if all(item.outcome.ok) else "❌"
        origin = f" · {scenario.origin}" if scenario.origin else ""
        lines += [
            "",
            f"### {scenario.id}{origin} · {BLOCK_NAMES[scenario.block]} · {scenario.title} ·"
            f" {sum(item.outcome.ok)}/{len(item.passes)} {mark}",
            "",
            f"Comprobaciones: {', '.join(str(check) for check in scenario.checks)}",
        ]
        if scenario.checks_after:
            after = ", ".join(str(check) for check in scenario.checks_after)
            lines.append(f"Después: {after}")
        if scenario.why:
            lines.append(f"Por qué: {scenario.why}")
        for number, one in enumerate(item.passes, start=1):
            verdict = "✅" if one.ok else f"❌ {', '.join(one.failed)}"
            lines += ["", f"Pasada {number} · {verdict}", "", "```text", one.painted, "```"]
    return "\n".join(lines) + "\n"
