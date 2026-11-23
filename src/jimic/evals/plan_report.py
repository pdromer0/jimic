"""The report of the evaluation of the plan (decision 1.14-f) and its comparison with the last.

Two files in `<data>/evals/`, `plan-<AAAAMMDD-HHMM>.json` and `.md`, as the heartbeat's (1.9-e):
every pass with what failed and the plan as the code painted it (invented data), to read its tone.
The comparison is scenario by scenario, only on the ids with the same fingerprint, and flags a
grade that drops more than 5 points (the regression of 13.3). The pieces are those of
`jimic.evals.report`: the names of the files, the previous report and the partition.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from jimic.evals.plan import PlanOutcome, PlanResult, PlanScore, plan_score
from jimic.evals.report import (
    REGRESSION,
    UnreadableReport,
    dollars,
    partition,
    percent,
    previous_report,
)

PREFIX = "plan"
THRESHOLD = Fraction(1)
"""13.3 of the design: 100 % of the automatic checks."""
VERSION = 1


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
            )
            for item in document["scenarios"]
        ]
    except (AssertionError, KeyError, TypeError) as error:
        raise UnreadableReport(type(error).__name__) from None


def compare_with_previous(folder: Path, current: Sequence[PlanOutcome]) -> PlanComparison | None:
    path = previous_report(folder, PREFIX)
    if path is None:
        return None
    try:
        previous = read_outcomes(json.loads(path.read_text(encoding="utf-8")))
    except OSError, ValueError, UnreadableReport:
        return PlanComparison(path.name, None, None, False, (), (), (), (), unreadable=True)
    return compare(path.name, current, previous)


def spend(results: Sequence[PlanResult]) -> tuple[Decimal, int]:
    """The SDK's estimate of what the passes spent, and how many runs of the agent."""
    passes = [one for item in results for one in item.passes]
    return sum((one.usd for one in passes), Decimal(0)), sum(one.runs for one in passes)


def _comparison_lines(comparison: PlanComparison | None) -> list[str]:
    if comparison is None:
        return ["Comparación: es el primer informe."]
    if comparison.unreadable:
        return [f"Comparación: {comparison.previous} no se puede leer; sin comparación."]
    lines = [f"Comparación con {comparison.previous}:"]
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


def summary(
    model: str, passes: int, results: Sequence[PlanResult], comparison: PlanComparison | None
) -> str:
    total = plan_score([item.outcome for item in results])
    lines = [f"Evaluación del plan · {model} · {passes} pasadas · {len(results)} escenarios"]
    if total.grade is not None:
        mark = "✅" if total.grade >= THRESHOLD else "❌"
        lines.append(
            f"Nota: {percent(total.grade)} ({total.right}/{total.passes} pasadas cumplen todas"
            f" sus comprobaciones) {mark} (umbral 100 %)"
        )
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
) -> dict[str, Any]:
    total = plan_score([item.outcome for item in results])
    usd, runs = spend(results)
    return {
        "version": VERSION,
        "started": started.isoformat(timespec="minutes"),
        "model": model,
        "passes": passes,
        "grade": {"right": total.right, "passes": total.passes},
        "spend": {"usd": str(usd), "runs": runs},
        "comparison": None
        if comparison is None
        else {
            "previous": comparison.previous,
            "unreadable": comparison.unreadable,
            "common": list(comparison.common),
            "worse": comparison.worse,
            "new": list(comparison.new),
            "removed": list(comparison.removed),
            "changed": list(comparison.changed),
        },
        "scenarios": [
            {
                "id": item.scenario.id,
                "title": item.scenario.title,
                "origin": item.scenario.origin,
                "fingerprint": item.scenario.fingerprint,
                "passes": [
                    {
                        "ok": one.ok,
                        "code": one.code,
                        "failed": list(one.failed),
                        "checks": [] if one.plan is None else [list(r) for r in one.plan.results],
                        "checks_after": []
                        if one.after is None
                        else [list(r) for r in one.after.results],
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
            f"### {scenario.id}{origin} · {scenario.title} ·"
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
