"""The heartbeat's evaluation: the decision of Haiku on each scenario, graded (decision 1.9-a).

Only the decision is evaluated: the state goes through `fixed_block` and `build_request` to the
real API, as in the heartbeat, and the answer through `judge`. The two passes are code with their
own tests, so they stay out. A failed call is a failed pass with its code, never retried here
(the client keeps the retries of the SDK, like the service).

Grading (1.9-b): a pass is right when what it decides (`speak` and the set of intentions after
`judge`, in any order) is one of the acceptable sets; an `invalid_decision` or an error of the API
is wrong. «Speaks when it should not» (1.9-c) counts, over the passes of the scenarios whose only
acceptable answer is silence, those that speak.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from fractions import Fraction

import anthropic

from jimic.evals.scenarios import Scenario, Suite, build_state
from jimic.heartbeat.decide import CreateMessage, api_error_code, build_request, fixed_block, judge
from jimic.memory.usage import TokenUsage

CONCURRENCY = 4
"""Calls at a time: 117 in a row take minutes, and 4 stay far from the limits of `jimic-dev`."""
INVALID = "invalid_decision:"


@dataclass(frozen=True)
class PassResult:
    """What one call decided. The reason and the messages may quote the (invented) day: the
    `repr` hides them, and only the report shows them."""

    speak: bool
    intents: frozenset[str]
    reason: str = field(repr=False)
    messages: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    """`(intent, text)` of each pick kept by `judge`."""
    usage: TokenUsage | None = None
    api_error: str | None = None

    @property
    def invalid(self) -> bool:
        return self.reason.startswith(INVALID)


@dataclass(frozen=True)
class Outcome:
    """The grade of the passes of one scenario: what the report keeps and compares."""

    id: str
    fingerprint: str
    silent_only: bool
    correct: tuple[bool, ...]
    speak: tuple[bool, ...]


@dataclass(frozen=True)
class ScenarioResult:
    scenario: Scenario
    passes: tuple[PassResult, ...]

    @property
    def outcome(self) -> Outcome:
        return Outcome(
            id=self.scenario.id,
            fingerprint=self.scenario.fingerprint,
            silent_only=self.scenario.silent_only,
            correct=tuple(is_correct(self.scenario.expected, result) for result in self.passes),
            speak=tuple(result.speak for result in self.passes),
        )


def is_correct(expected: Sequence[frozenset[str]], result: PassResult) -> bool:
    """Right when what it decided is one of the acceptable sets (1.9-b)."""
    if result.api_error is not None or result.invalid:
        return False
    chosen = result.intents if result.speak else frozenset()
    return chosen in expected


@dataclass(frozen=True)
class Score:
    """The two figures of 13.3, as counts: comparing them never depends on rounding."""

    correct: int
    passes: int
    spoke_when_silent: int
    silent_passes: int

    @property
    def accuracy(self) -> Fraction | None:
        return Fraction(self.correct, self.passes) if self.passes else None

    @property
    def false_speak(self) -> Fraction | None:
        """«Speaks when it should not»; `None` without scenarios whose only answer is silence."""
        return Fraction(self.spoke_when_silent, self.silent_passes) if self.silent_passes else None


def score(outcomes: Sequence[Outcome]) -> Score:
    silent = [outcome for outcome in outcomes if outcome.silent_only]
    return Score(
        correct=sum(sum(outcome.correct) for outcome in outcomes),
        passes=sum(len(outcome.correct) for outcome in outcomes),
        spoke_when_silent=sum(sum(outcome.speak) for outcome in silent),
        silent_passes=sum(len(outcome.speak) for outcome in silent),
    )


async def run_pass(
    create: CreateMessage, model: str, suite: Suite, scenario: Scenario
) -> PassResult:
    """One call of the model on `scenario`, as the heartbeat makes it.

    Any error of the call or of reading its answer is a failed pass with its code
    (`unexpected:<Type>` if it is not one of the API's): one odd answer never loses the passes
    already paid for.
    """
    state = build_state(suite, scenario)
    request = build_request(model, fixed_block(suite.intentions, state.now), state)
    try:
        message = await create(**request)
        decision = judge(message, state)
    except anthropic.APIError as error:
        return PassResult(False, frozenset(), "", api_error=api_error_code(error))
    except Exception as error:  # noqa: BLE001 - see the docstring: a pass, never the run
        return PassResult(False, frozenset(), "", api_error=f"unexpected:{type(error).__name__}")
    intents = (
        frozenset(decision.intent.split(",")) if decision.speak and decision.intent else frozenset()
    )
    return PassResult(
        speak=decision.speak,
        intents=intents,
        reason=decision.reason,
        messages=tuple((item.intent, item.text) for item in decision.proposals),
        usage=decision.usage,
    )


async def run_scenarios(
    create: CreateMessage,
    model: str,
    suite: Suite,
    scenarios: Sequence[Scenario],
    passes: int,
    concurrency: int = CONCURRENCY,
) -> list[ScenarioResult]:
    """`passes` calls for each scenario that is not pending, at most `concurrency` at a time.
    The results keep the order of `scenarios`."""
    gate = asyncio.Semaphore(concurrency)
    runnable = [scenario for scenario in scenarios if scenario.pending is None]

    async def one(scenario: Scenario) -> PassResult:
        async with gate:
            return await run_pass(create, model, suite, scenario)

    results = await asyncio.gather(*(one(scenario) for scenario in runnable for _ in range(passes)))
    return [
        ScenarioResult(scenario, tuple(results[index * passes : (index + 1) * passes]))
        for index, scenario in enumerate(runnable)
    ]
