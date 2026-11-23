"""The report of the heartbeat's evaluation (decision 1.9-e) and its comparison with the last one.

Two files in `<data>/evals/`: `latido-<AAAAMMDD-HHMM>.json`, which the next run compares with, and
the same name in `.md`, to read. They hold every pass with its reason and its messages, to judge
the tone: the data is invented. The screen gets the summary.

The comparison is scenario by scenario: only the scenarios with the same id and the same
fingerprint in both reports count, the figures of both are recomputed over them, and a figure
that gets worse by more than 5 points is flagged (the regression of 13.3). New, removed and
changed scenarios are listed apart, so adding one never breaks the comparison of the others.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol

from jimic.evals.heartbeat import Outcome, PassResult, ScenarioResult, Score, is_correct, score
from jimic.evals.scenarios import Scenario
from jimic.memory.usage import TokenUsage, cost

MIN_ACCURACY = Fraction(90, 100)
MAX_FALSE_SPEAK = Fraction(5, 100)
REGRESSION = Fraction(5, 100)
"""More than this drop (5 points) is a regression; exactly 5 is not."""
VERSION = 1
PREFIX = "latido"
"""The name of the reports of the heartbeat; the plan's are `plan-` (task 1.14)."""


def _name(prefix: str) -> re.Pattern[str]:
    return re.compile(rf"{re.escape(prefix)}-([0-9]{{8}}-[0-9]{{4}})(?:-([0-9]+))?\.json")


class UnreadableReport(Exception):
    """A previous report that cannot be read as one."""


@dataclass(frozen=True)
class Spend:
    usd: Decimal
    calls: int
    """Calls that the API answered (the only ones with usage)."""
    usage: TokenUsage

    @property
    def per_call(self) -> Decimal | None:
        return self.usd / self.calls if self.calls else None


def spend(model: str, results: Sequence[ScenarioResult]) -> Spend:
    usages = [result.usage for item in results for result in item.passes if result.usage]
    return Spend(
        usd=sum((cost(model, usage) for usage in usages), Decimal(0)),
        calls=len(usages),
        usage=TokenUsage(
            input_tokens=sum(usage.input_tokens for usage in usages),
            output_tokens=sum(usage.output_tokens for usage in usages),
            cache_read_tokens=sum(usage.cache_read_tokens for usage in usages),
            cache_write_5m_tokens=sum(usage.cache_write_5m_tokens for usage in usages),
            cache_write_1h_tokens=sum(usage.cache_write_1h_tokens for usage in usages),
        ),
    )


@dataclass(frozen=True)
class Comparison:
    previous: str
    """The name of the previous report."""
    before: Score | None
    """`None` when no scenario is common to both: nothing to compare."""
    after: Score | None
    worse: tuple[str, ...]
    """`accuracy` and `false_speak`, if they got worse by more than 5 points."""
    common: tuple[str, ...]
    """The ids with the same fingerprint in both: the only ones compared."""
    new: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]
    """The same id with another fingerprint: left out of the comparison."""
    unreadable: bool = False


def worsened(before: Score, after: Score) -> tuple[str, ...]:
    """Each figure in its bad direction: accuracy that drops, «speaks when it should not» that
    rises. Fractions, so exactly 5 points is never flagged by rounding."""
    worse = []
    if before.accuracy is not None and after.accuracy is not None:
        if before.accuracy - after.accuracy > REGRESSION:
            worse.append("accuracy")
    if before.false_speak is not None and after.false_speak is not None:
        if after.false_speak - before.false_speak > REGRESSION:
            worse.append("false_speak")
    return tuple(worse)


class Fingerprinted(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def fingerprint(self) -> str: ...


@dataclass(frozen=True)
class Partition[T: Fingerprinted]:
    """The scenarios of two reports, by id and fingerprint (decision 1.9-e)."""

    common: list[T]
    """The current ones with the same id and fingerprint in the previous report."""
    new: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]


def partition[T: Fingerprinted](current: Sequence[T], previous: Sequence[T]) -> Partition[T]:
    before_by_id = {outcome.id: outcome for outcome in previous}
    now_ids = {outcome.id for outcome in current}
    return Partition(
        common=[
            outcome
            for outcome in current
            if outcome.id in before_by_id
            and before_by_id[outcome.id].fingerprint == outcome.fingerprint
        ],
        new=tuple(outcome.id for outcome in current if outcome.id not in before_by_id),
        removed=tuple(outcome.id for outcome in previous if outcome.id not in now_ids),
        changed=tuple(
            outcome.id
            for outcome in current
            if outcome.id in before_by_id
            and before_by_id[outcome.id].fingerprint != outcome.fingerprint
        ),
    )


def compare(name: str, current: Sequence[Outcome], previous: Sequence[Outcome]) -> Comparison:
    before_by_id = {outcome.id: outcome for outcome in previous}
    parts = partition(current, previous)
    common, new, removed, changed = parts.common, parts.new, parts.removed, parts.changed
    if not common:
        return Comparison(name, None, None, (), (), new, removed, changed)
    before = score([before_by_id[outcome.id] for outcome in common])
    after = score(common)
    ids = tuple(outcome.id for outcome in common)
    return Comparison(name, before, after, worsened(before, after), ids, new, removed, changed)


def read_outcomes(document: object) -> list[Outcome]:
    """The outcomes kept in a report's JSON."""
    try:
        assert isinstance(document, dict)
        return [
            Outcome(
                id=str(item["id"]),
                fingerprint=str(item["fingerprint"]),
                silent_only=bool(item["silent_only"]),
                correct=tuple(bool(one["correct"]) for one in item["passes"]),
                speak=tuple(bool(one["speak"]) for one in item["passes"]),
                errors=tuple(one["api_error"] is not None for one in item["passes"]),
            )
            for item in document["scenarios"]
        ]
    except (AssertionError, KeyError, TypeError) as error:
        raise UnreadableReport(type(error).__name__) from None


def previous_report(folder: Path, prefix: str = PREFIX) -> Path | None:
    """The most recent report of `folder` named `<prefix>-…`, by the time in its name."""
    found = []
    pattern = _name(prefix)
    for path in folder.glob(f"{prefix}-*.json"):
        match = pattern.fullmatch(path.name)
        if match is not None:
            found.append(((match[1], int(match[2] or 1)), path))
    return max(found)[1] if found else None


def compare_with_previous(folder: Path, current: Sequence[Outcome]) -> Comparison | None:
    """`None` when there is no previous report."""
    path = previous_report(folder)
    if path is None:
        return None
    try:
        previous = read_outcomes(json.loads(path.read_text(encoding="utf-8")))
    except OSError, ValueError, UnreadableReport:
        return Comparison(path.name, None, None, (), (), (), (), (), unreadable=True)
    return compare(path.name, current, previous)


def report_paths(folder: Path, started: datetime, prefix: str = PREFIX) -> tuple[Path, Path]:
    """The two files of a run; a second run in the same minute gets `-2`, and so on."""
    stem = f"{prefix}-{started:%Y%m%d-%H%M}"
    number = 1
    while True:
        name = stem if number == 1 else f"{stem}-{number}"
        paths = folder / f"{name}.json", folder / f"{name}.md"
        if not any(path.exists() for path in paths):
            return paths
        number += 1


# --- Text ------------------------------------------------------------------------------------


def percent(value: Fraction) -> str:
    return f"{float(value * 100):.1f}".replace(".", ",") + " %"


def dollars(value: Decimal) -> str:
    return f"{value:.5f}".replace(".", ",") + " $"


def _intents(intents: frozenset[str]) -> str:
    return "{" + ", ".join(sorted(intents)) + "}" if intents else "callar"


def describe(result: PassResult) -> str:
    """What a pass decided, in ids and codes only."""
    if result.api_error is not None:
        return f"error de la API ({result.api_error})"
    if result.invalid:
        return result.reason.split(" ", 1)[0]
    return _intents(result.intents) if result.speak else "callar"


def _expected(scenario: Scenario) -> str:
    return " o ".join(_intents(alternative) for alternative in scenario.expected)


def _figures(total: Score) -> list[str]:
    lines = []
    if total.accuracy is not None:
        mark = "✅" if total.accuracy >= MIN_ACCURACY else "❌"
        lines.append(
            f"Acierto: {percent(total.accuracy)} ({total.correct}/{total.passes})"
            f" {mark} (umbral ≥ 90 %)"
        )
    if total.false_speak is not None:
        mark = "✅" if total.false_speak <= MAX_FALSE_SPEAK else "❌"
        lines.append(
            f"Habla cuando no debía: {percent(total.false_speak)}"
            f" ({total.spoke_when_silent}/{total.silent_passes}) {mark} (umbral ≤ 5 %)"
        )
    else:
        lines.append("Habla cuando no debía: sin pasadas de callar que contar en esta ejecución")
    if total.errors:
        lines.append(
            f"Pasadas con error: {total.errors} (fallo en el acierto;"
            " fuera de «habla cuando no debía»)"
        )
    return lines


def _comparison_lines(comparison: Comparison | None) -> list[str]:
    if comparison is None:
        return ["Comparación: es el primer informe."]
    if comparison.unreadable:
        return [f"Comparación: {comparison.previous} no se puede leer; sin comparación."]
    lines = [f"Comparación con {comparison.previous}:"]
    before, after = comparison.before, comparison.after
    if before is None or after is None:
        lines.append("  sin informe anterior comparable (ningún escenario en común)")
    else:
        lines.append(f"  sobre {len(comparison.common)} escenarios comunes:")
        if before.accuracy is not None and after.accuracy is not None:
            lines.append(f"    acierto: {percent(before.accuracy)} → {percent(after.accuracy)}")
        if before.false_speak is not None and after.false_speak is not None:
            lines.append(
                f"    habla cuando no debía: {percent(before.false_speak)}"
                f" → {percent(after.false_speak)}"
            )
        names = {"accuracy": "el acierto baja", "false_speak": "«habla cuando no debía» sube"}
        for metric in comparison.worse:
            lines.append(f"  ⚠️ Regresión: {names[metric]} más de 5 puntos")
    for label, ids in (
        ("nuevos", comparison.new),
        ("quitados", comparison.removed),
        ("cambiados", comparison.changed),
    ):
        if ids:
            lines.append(f"  {label}: {', '.join(ids)}")
    return lines


def summary(
    model: str,
    passes: int,
    results: Sequence[ScenarioResult],
    skipped: Sequence[Scenario],
    paid: Spend,
    comparison: Comparison | None,
) -> str:
    """What the screen shows: the figures against 13.3, the spend and what fails."""
    total = score([item.outcome for item in results])
    lines = [
        f"Evaluación del latido · {model} · {passes} pasadas · {len(results)} escenarios",
        *_figures(total),
    ]
    if skipped:
        listed = ", ".join(f"{item.id} (pendiente: {item.pending})" for item in skipped)
        lines.append(f"Saltados: {len(skipped)}: {listed}")
    per_call = f" ({dollars(paid.per_call)}/llamada)" if paid.per_call is not None else ""
    usage = paid.usage
    lines.append(
        f"Coste: {dollars(paid.usd)} en {paid.calls} llamadas{per_call} · tokens:"
        f" {usage.input_tokens} de entrada, {usage.cache_read_tokens} de caché leída,"
        f" {usage.cache_write_tokens} de caché escrita, {usage.output_tokens} de salida"
    )
    failing = [item for item in results if not all(item.outcome.correct)]
    if failing:
        lines.append("Fallan:")
        for item in failing:
            outcome = item.outcome
            wrong = [
                describe(result)
                for result, right in zip(item.passes, outcome.correct, strict=True)
                if not right
            ]
            lines.append(
                f"  {item.scenario.id} ({sum(outcome.correct)}/{len(outcome.correct)} bien):"
                f" {'; '.join(wrong)} · esperado: {_expected(item.scenario)}"
            )
    lines.extend(_comparison_lines(comparison))
    return "\n".join(lines)


# --- Files -----------------------------------------------------------------------------------


def _usage_json(usage: TokenUsage | None) -> dict[str, int] | None:
    if usage is None:
        return None
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_5m_tokens": usage.cache_write_5m_tokens,
        "cache_write_1h_tokens": usage.cache_write_1h_tokens,
    }


def _comparison_json(comparison: Comparison | None) -> dict[str, Any] | None:
    if comparison is None:
        return None

    def figures(total: Score | None) -> dict[str, int] | None:
        if total is None:
            return None
        return {
            "correct": total.correct,
            "passes": total.passes,
            "spoke_when_silent": total.spoke_when_silent,
            "silent_passes": total.silent_passes,
            "errors": total.errors,
        }

    return {
        "previous": comparison.previous,
        "unreadable": comparison.unreadable,
        "before": figures(comparison.before),
        "after": figures(comparison.after),
        "worse": list(comparison.worse),
        "common": list(comparison.common),
        "new": list(comparison.new),
        "removed": list(comparison.removed),
        "changed": list(comparison.changed),
    }


def document(
    started: datetime,
    model: str,
    passes: int,
    results: Sequence[ScenarioResult],
    skipped: Sequence[Scenario],
    paid: Spend,
    comparison: Comparison | None,
) -> dict[str, Any]:
    """The JSON report: everything, every pass with its reason and messages (invented data)."""
    total = score([item.outcome for item in results])
    return {
        "version": VERSION,
        "started": started.isoformat(timespec="seconds"),
        "model": model,
        "passes": passes,
        "summary": {
            "correct": total.correct,
            "passes": total.passes,
            "spoke_when_silent": total.spoke_when_silent,
            "silent_passes": total.silent_passes,
            "errors": total.errors,
            "skipped": [{"id": item.id, "pending": item.pending} for item in skipped],
            "usd": str(paid.usd),
            "calls": paid.calls,
            "usage": _usage_json(paid.usage),
        },
        "comparison": _comparison_json(comparison),
        "scenarios": [
            {
                "id": item.scenario.id,
                "title": item.scenario.title,
                "fingerprint": item.scenario.fingerprint,
                "silent_only": item.scenario.silent_only,
                "expected": [sorted(alternative) for alternative in item.scenario.expected],
                "why": item.scenario.why,
                "passes": [
                    {
                        "correct": is_correct(item.scenario.expected, result),
                        "speak": result.speak,
                        "intents": sorted(result.intents),
                        "reason": result.reason,
                        "messages": [
                            {"intent": intent, "text": text} for intent, text in result.messages
                        ],
                        "usage": _usage_json(result.usage),
                        "usd": str(cost(model, result.usage)) if result.usage else None,
                        "api_error": result.api_error,
                    }
                    for result in item.passes
                ],
            }
            for item in results
        ],
    }


def _quoted(text: str) -> str:
    return "«" + " ".join(text.split()) + "»"


def markdown(screen: str, results: Sequence[ScenarioResult], started: datetime) -> str:
    """The report to read: the summary, then each scenario with its passes."""
    lines = [
        f"# Evaluación del latido · {started:%Y-%m-%d %H:%M}",
        "",
        "Datos inventados (`evals/latido.yaml`).",
        "",
        "```text",
        screen,
        "```",
        "",
        "## Escenarios",
    ]
    for item in results:
        outcome = item.outcome
        mark = "✅" if all(outcome.correct) else "❌"
        lines += [
            "",
            f"### {item.scenario.id} · {item.scenario.title} · "
            f"{sum(outcome.correct)}/{len(outcome.correct)} {mark}",
            "",
            f"Esperado: {_expected(item.scenario)}",
        ]
        if item.scenario.why:
            lines.append(f"Por qué: {item.scenario.why}")
        lines.append("")
        for number, (result, right) in enumerate(
            zip(item.passes, outcome.correct, strict=True), start=1
        ):
            lines.append(f"- Pasada {number} {'✅' if right else '❌'}: {describe(result)}")
            if result.reason:
                lines.append(f"  - Motivo: {_quoted(result.reason)}")
            for intent, text in result.messages:
                lines.append(
                    f"  - {intent}: {_quoted(text) if text else '(lo redacta otra pieza)'}"
                )
    return "\n".join(lines) + "\n"


def write_report(
    folder: Path, started: datetime, report: dict[str, Any], text: str
) -> tuple[Path, Path]:
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    json_path, md_path = report_paths(folder, started)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(text, encoding="utf-8")
    return json_path, md_path
