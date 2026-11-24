"""The asymmetric rule (decision 11): talking, JimiC only gets more discreet.

The old and the new `Limits`, both valid, are compared field by field. A change is accepted only
if some value moves and every value that moves goes the discreet way:

| Field | More discreet |
| --- | --- |
| `max_messages_per_day`, `max_insist_per_task`, `max_deletions_per_day` | Down |
| `quiet_hours` | The new window holds the old one |
| `silent_during_events` | Only from `false` to `true` |
| `group_window`, `quiet_while_chatting` | Up |
| `budget.*` (the four) | Down |
| `tool_levels` | Only from `free` to `blocked` |
| `sensitive_categories` | Any change is refused (it comes in hito 4) |
| `intenciones.yaml`: the `spacing` of each key task (its `grace` without it) | Up |
| `intenciones.yaml`: the `days` of the habit of each key task (without them, all seven) | Fewer |

In `intenciones.yaml` only `spacing` and the `days` of a key task's habit are protected (decisions
149 and F2-a): what is compared is what the code applies, the gap (`KeyTask.gap`), so lowering
`grace` of a key task without `spacing` also lowers it and is refused too, and the days on which
it insists, which must stay a subset (removing days is insisting less; adding them, insisting
more). The days are those of the habit each version of the key task points to. A key task that is
new, or removed, has nothing to compare; the days of a habit without a key task are free.

A window is the set of minutes `[from, to)` of the day, crossing midnight when `from > to`;
`from == to` is the whole day (decision 80). A tool missing from `tool_levels` counts as
`blocked`, as the guard reads it.
"""

from dataclasses import dataclass
from datetime import time

from jimic.memory.schemas import (
    TOOL_NAMES,
    WEEKDAYS,
    Intentions,
    KeyTask,
    Limits,
    Window,
    is_free,
)

MINUTES_PER_DAY = 24 * 60


def _minute(moment: time) -> int:
    return moment.hour * 60 + moment.minute


def minutes(window: Window) -> frozenset[int]:
    """The minutes of the day inside `window`."""
    start, end = _minute(window.start), _minute(window.end)
    if start == end:
        return frozenset(range(MINUTES_PER_DAY))
    if start < end:
        return frozenset(range(start, end))
    return frozenset(range(start, MINUTES_PER_DAY)) | frozenset(range(end))


def holds(new: Window, old: Window) -> bool:
    """Whether the new window keeps quiet at least every minute the old one did."""
    return minutes(new) >= minutes(old)


@dataclass(frozen=True)
class Verdict:
    changed: tuple[str, ...]
    """The fields whose value moves, as `budget.daily_cap_usd` or `tool_levels.log_idea`."""
    relaxed: tuple[str, ...]
    """Those that move the wrong way."""

    @property
    def accepted(self) -> bool:
        return bool(self.changed) and not self.relaxed


def _moves(old: Limits, new: Limits) -> list[tuple[str, bool, bool]]:
    """`(field, moves, discreet)` for every field but `quiet_hours` and `tool_levels`."""
    a, b = old.budget, new.budget
    return [
        (name, before != after, discreet)
        for name, before, after, discreet in (
            (
                "max_messages_per_day",
                old.max_messages_per_day,
                new.max_messages_per_day,
                new.max_messages_per_day <= old.max_messages_per_day,
            ),
            (
                "max_insist_per_task",
                old.max_insist_per_task,
                new.max_insist_per_task,
                new.max_insist_per_task <= old.max_insist_per_task,
            ),
            (
                "max_deletions_per_day",
                old.max_deletions_per_day,
                new.max_deletions_per_day,
                new.max_deletions_per_day <= old.max_deletions_per_day,
            ),
            (
                "silent_during_events",
                old.silent_during_events,
                new.silent_during_events,
                new.silent_during_events or not old.silent_during_events,
            ),
            (
                "group_window",
                old.group_window,
                new.group_window,
                new.group_window >= old.group_window,
            ),
            (
                "quiet_while_chatting",
                old.quiet_while_chatting,
                new.quiet_while_chatting,
                new.quiet_while_chatting >= old.quiet_while_chatting,
            ),
            (
                "budget.monthly_target_usd",
                a.monthly_target_usd,
                b.monthly_target_usd,
                b.monthly_target_usd <= a.monthly_target_usd,
            ),
            ("budget.warn_at", a.warn_at, b.warn_at, b.warn_at <= a.warn_at),
            (
                "budget.daily_cap_usd",
                a.daily_cap_usd,
                b.daily_cap_usd,
                b.daily_cap_usd <= a.daily_cap_usd,
            ),
            (
                "budget.max_session_usd",
                a.max_session_usd,
                b.max_session_usd,
                b.max_session_usd <= a.max_session_usd,
            ),
            (
                "sensitive_categories",
                dict(old.sensitive_categories),
                dict(new.sensitive_categories),
                False,
            ),
        )
    ]


def judge(old: Limits, new: Limits) -> Verdict:
    """What moves from `old` to `new`, and what of it relaxes a limit."""
    changed: list[str] = []
    relaxed: list[str] = []
    for name, moves, discreet in _moves(old, new):
        if moves:
            changed.append(name)
            if not discreet:
                relaxed.append(name)
    if minutes(old.quiet_hours) != minutes(new.quiet_hours):
        changed.append("quiet_hours")
        if not holds(new.quiet_hours, old.quiet_hours):
            relaxed.append("quiet_hours")
    for tool in TOOL_NAMES:
        was, now = is_free(old, tool), is_free(new, tool)
        if was != now:
            changed.append(f"tool_levels.{tool}")
            if now:
                relaxed.append(f"tool_levels.{tool}")
    return Verdict(tuple(changed), tuple(relaxed))


def relaxed_spacing(old: Intentions, new: Intentions) -> tuple[str, ...]:
    """The key tasks, by id, whose gap between notices goes down: `<id>.spacing`."""
    before = {i.id: i.gap for i in old.intentions if isinstance(i, KeyTask)}
    return tuple(
        f"{i.id}.spacing"
        for i in new.intentions
        if isinstance(i, KeyTask) and i.id in before and i.gap < before[i.id]
    )


def _key_days(intentions: Intentions) -> dict[str, frozenset[str]]:
    """The days on which each key task may insist: those of its habit, all seven without them."""
    habits = {habit.id: habit for habit in intentions.habits}
    days = {}
    for intention in intentions.intentions:
        if isinstance(intention, KeyTask) and intention.habit in habits:
            chosen = habits[intention.habit].days
            days[intention.id] = frozenset(WEEKDAYS if chosen is None else chosen)
    return days


def relaxed_days(old: Intentions, new: Intentions) -> tuple[str, ...]:
    """The key tasks, by id, that would insist on a day they did not: `<id>.days`."""
    before = _key_days(old)
    return tuple(
        f"{intent}.days"
        for intent, days in _key_days(new).items()
        if intent in before and not days <= before[intent]
    )
