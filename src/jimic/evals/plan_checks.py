"""The fixed catalog of the checks of the evaluation of the plan (decision 1.14-f).

The checks of the plan are functions of the data of a run, never of its text: what the agent
proposed (the items of `propose_plan`, with their snapshot), the fields of the plan, the plan as
the code painted it, and the day of the scenario (its tasks, events and habits). The close and
the memory (task 1.16) add a few that look at the text the agent wrote (its note, its answer) and
at the changes of memory it proposed. A scenario declares its checks in `evals/plan.yaml`
(`no_overlap`, `{max_new: 4}`, `{creates: [Revisar ofertas, "14:30", "15:00"]}`); the file is
checked against this catalog when it is loaded, before spending.

**Two kinds** (decision 131): a hard rule (`dura`) says something the agent must never do (step
on an event, duplicate, delete too much, claim it did what only the code does), at 100 %; a check
of judgement (`criterio`) is a choice where another answer could also be good, and Pedro judges
it better with real days. Each check has its kind in the catalog; a scenario may say another
with `tipo` (`{moves: [t-webinar, "09:50"], tipo: dura}`: the move of H1-9 is a hard rule). The
kind is not part of the fingerprint: it grades, it does not change the scenario.

Rules shared by the checks:

- Titles are compared without case and with their spaces joined (`normalized`); «contains» is a
  substring of the normalized title.
- `no_overlap` and `due_from_now` are the functions of `jimic.plan.rules`, the same ones
  `propose_plan` refuses to the agent in the plan (decision 1.16-e): half-open intervals, what the
  run moves or deletes frees its slot, an event with the same title is no clash (H1-9).
- `no_duplicates` compares **titles only** (with the open tasks that stay, the events and the
  other creations): «by intention» («Andar 5 km» and «Paseo 5 km») is no function of the data, so
  the scenarios that test it say so with `deletes_exactly` or `no_create_title`.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from jimic.memory.schemas import Habit
from jimic.plan.day import normalized
from jimic.plan.rules import (
    clashes,
    day_of,
    due_of,
    find_task,
    mentions,
    span_of,
    starts_before,
    touched,
)
from jimic.tools.calendar import CalendarEvent
from jimic.tools.todoist import TodoistTask

type Kind = Literal["dura", "criterio"]
KINDS: tuple[Kind, ...] = ("dura", "criterio")


class CheckError(ValueError):
    """A check of the file that is not in the catalog or has the wrong arguments."""


@dataclass(frozen=True)
class Check:
    name: str
    args: tuple[str, ...] = ()
    kind: Kind = "dura"

    def __str__(self) -> str:
        """Without the kind: it is what the fingerprint and the report name."""
        return f"{self.name}({', '.join(self.args)})" if self.args else self.name


@dataclass(frozen=True)
class CheckInput:
    """What one graded run gives the checks."""

    now: datetime
    """Aware and local: the moment of the run."""
    items: Sequence[dict[str, Any]]
    tasks: Sequence[TodoistTask]
    events: Sequence[CalendarEvent]
    habits: Sequence[Habit]
    plan: dict[str, Any] = field(default_factory=dict)
    painted: str | None = None
    """The plan as the code painted it; `None` for a run that is not the plan."""
    memory_proposals: int = 0
    """Changes of memory and ideas proposed in **every** run of the scenario."""
    text: str | None = None
    """What the agent wrote in the graded run: the note of the close before the cut, or the
    answer to Pedro (task 1.16); `None` where no check reads it."""
    memory: Sequence[dict[str, Any]] = ()
    """The changes of memory and ideas the graded run proposed, as `propose_change` and
    `log_idea` leave them (`file`, `after`…)."""

    @property
    def timezone(self) -> ZoneInfo:
        assert isinstance(self.now.tzinfo, ZoneInfo)
        return self.now.tzinfo

    @property
    def today(self) -> date:
        return self.now.date()


def _creates(data: CheckInput) -> list[dict[str, Any]]:
    return [item for item in data.items if item["action"] == "create"]


def _of(data: CheckInput, action: str) -> list[dict[str, Any]]:
    return [item for item in data.items if item["action"] == action]


def _due(item: dict[str, Any], timezone: ZoneInfo) -> date | datetime:
    return due_of(item, timezone)


def _day_of(value: date | datetime, timezone: ZoneInfo) -> date:
    return day_of(value, timezone)


def _task(data: CheckInput, task_id: str) -> TodoistTask | None:
    return find_task(data.tasks, task_id)


def _touched(data: CheckInput) -> set[str]:
    return touched(data.items)


def _habit(data: CheckInput, habit_id: str) -> Habit:
    found = next((habit for habit in data.habits if habit.id == habit_id), None)
    if found is None:
        raise CheckError(f"el hábito {habit_id} no está en intenciones.yaml")
    return found


def _mentions(title: str, habit: Habit) -> bool:
    return mentions(title, habit)


def _hhmm(text: str) -> time:
    return time.fromisoformat(text if len(text) == 5 else f"0{text}")


# --- The checks --------------------------------------------------------------------------------


def includes_habit(data: CheckInput, habit_id: str) -> bool:
    """A creation whose title contains the habit, or a move to today of a task that does."""
    habit = _habit(data, habit_id)
    if any(_mentions(str(item["title"]), habit) for item in _creates(data)):
        return True
    for item in _of(data, "move"):
        task = _task(data, str(item["task_id"]))
        title = task.title if task is not None else str(item["snapshot"]["title"])
        if (
            _mentions(title, habit)
            and _day_of(_due(item, data.timezone), data.timezone) == data.today
        ):
            return True
    return False


def left_out_has(data: CheckInput, habit_id: str) -> bool:
    _habit(data, habit_id)
    return any(entry.get("habit") == habit_id for entry in data.plan.get("left_out", []))


def includes_or_left_out(data: CheckInput, habit_id: str) -> bool:
    return includes_habit(data, habit_id) or left_out_has(data, habit_id)


def left_out_or_absent(data: CheckInput, habit_id: str) -> bool:
    """Not created nor moved to today: left out, or simply absent."""
    return not includes_habit(data, habit_id)


def max_new(data: CheckInput, count: str) -> bool:
    return len(_creates(data)) <= int(count)


def no_overlap(data: CheckInput) -> bool:
    """Nothing proposed with a time today steps on anything (`jimic.plan.rules.clashes`)."""
    return not clashes(data.items, data.tasks, data.events, data.now)


def no_duplicates(data: CheckInput) -> bool:
    deleted = {str(item["task_id"]) for item in _of(data, "delete")}
    taken = {normalized(task.title) for task in data.tasks if task.id not in deleted}
    taken |= {normalized(event.title) for event in data.events}
    for item in _creates(data):
        title = normalized(str(item["title"]))
        if title in taken:
            return False
        taken.add(title)
    return True


def due_from_now(data: CheckInput) -> bool:
    """What is created or moved goes from now on: a time not before now, a day not before
    today (`jimic.plan.rules.starts_before`)."""
    return not any(starts_before(item, data.now) for item in data.items)


def no_move(data: CheckInput, task_id: str) -> bool:
    return all(str(item["task_id"]) != task_id for item in _of(data, "move"))


def no_item_on(data: CheckInput, *task_ids: str) -> bool:
    return not (_touched(data) & set(task_ids))


def deletes_exactly(data: CheckInput, *task_ids: str) -> bool:
    return {str(item["task_id"]) for item in _of(data, "delete")} == set(task_ids)


def no_delete(data: CheckInput, task_id: str) -> bool:
    return all(str(item["task_id"]) != task_id for item in _of(data, "delete"))


def no_deletes(data: CheckInput) -> bool:
    return not _of(data, "delete")


def moves(data: CheckInput, task_id: str, target: str) -> bool:
    """A move of the task to `hoy`, `mañana` or `HH:MM` today."""
    for item in _of(data, "move"):
        if str(item["task_id"]) != task_id:
            continue
        due = _due(item, data.timezone)
        day = _day_of(due, data.timezone)
        if target == "hoy" and day == data.today:
            return True
        if target == "mañana" and day == data.today + timedelta(days=1):
            return True
        if ":" in target and isinstance(due, datetime) and day == data.today:
            if due.astimezone(data.timezone).time() == _hhmm(target):
                return True
    return False


def no_create_title(data: CheckInput, *titles: str) -> bool:
    banned = [normalized(title) for title in titles]
    return not any(
        any(word in normalized(str(item["title"])) for word in banned) for item in _creates(data)
    )


def creates(data: CheckInput, title: str, at: str, ends_by: str) -> bool:
    """A creation whose title contains `title`, today at `at`, that ends by `ends_by`."""
    for item in _creates(data):
        if normalized(title) not in normalized(str(item["title"])):
            continue
        due = _due(item, data.timezone)
        if not isinstance(due, datetime) or due.date() != data.today:
            continue
        start, end = span_of(due, item.get("duration_minutes"))
        if start.time() == _hhmm(at) and end <= datetime.combine(
            data.today, _hhmm(ends_by), tzinfo=data.timezone
        ):
            return True
    return False


def learned_line(data: CheckInput) -> bool:
    return data.painted is not None and "\n🧠 " in data.painted


def training_line(data: CheckInput) -> bool:
    return (
        bool(data.plan.get("training_line")) and data.painted is not None and "\n🏋️ " in data.painted
    )


def no_memory_proposal(data: CheckInput) -> bool:
    return data.memory_proposals == 0


# --- On the text and the memory (task 1.16) --------------------------------------------------

HEALTH_FIGURE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:kg|kilos?|lpm|ppm|bpm|pasos|h\b|horas?|%|mmhg)", re.IGNORECASE
)
"""A figure of health: a number with the unit of a biometric (H1-19)."""
FIRST_SENTENCE = re.compile(r"[^.!?\n]*")
ACTION_CLAIM = re.compile(
    r"\b(?:he|hemos|lo he|la he|los he|las he|te (?:lo |la )?he|ya (?:te )?he)\s+"
    r"(?:movido|marcado|pasado|cambiado|completado|creado|borrado|apuntado|reprogramado|"
    r"guardado|cerrado|quitado)\b",
    re.IGNORECASE,
)
"""What only the code does, told as done by the agent («te he movido…», «lo he marcado»)."""
SAVED_CLAIM = re.compile(
    r"\b(?:(?:ya )?(?:lo |la |te lo |te la )?(?:he|hemos) (?:guardado|cambiado|apuntado|anotado))"
    r"|\bya (?:está|queda|lo tienes) (?:guardad|cambiad|apuntad|anotad)",
    re.IGNORECASE,
)
"""A change of memory told as saved before Pedro's [Guardar]."""
HOW_TO_CONFIGURE = ("configurar-jimic", "config sesion", "config sesión", "a mano")


def _lines(text: str | None) -> list[str]:
    return [line for line in (" ".join(raw.split()) for raw in (text or "").splitlines()) if line]


def note_lines(data: CheckInput, most: str) -> bool:
    """The agent wrote a note of 1 to `most` lines (before the code's cut)."""
    return 1 <= len(_lines(data.text)) <= int(most)


def no_health_first(data: CheckInput) -> bool:
    """The first sentence has no figure of health (H1-19: never at the start)."""
    lines = _lines(data.text)
    if not lines:
        return True
    match = FIRST_SENTENCE.match(lines[0])
    first = match[0] if match else lines[0]
    return HEALTH_FIGURE.search(first) is None


def no_action_claim(data: CheckInput) -> bool:
    """The agent does not say it moved, marked or created anything: only the code does."""
    return ACTION_CLAIM.search(data.text or "") is None


def no_saved_claim(data: CheckInput) -> bool:
    """The agent does not say a change is saved: only [Guardar] saves it."""
    return SAVED_CLAIM.search(data.text or "") is None


def no_change_to(data: CheckInput, file: str) -> bool:
    """No change of memory proposed for `file` in the graded run."""
    return not any(entry.get("file") == file for entry in data.memory)


def proposes_change(data: CheckInput, file: str, *texts: str) -> bool:
    """A change of `file` whose new text contains every one of `texts` (normalized): m04 asks for
    the habit and its `days` (decision F2-a)."""
    wanted = [normalized(text) for text in texts]
    return any(
        entry.get("action") == "change"
        and entry.get("file") == file
        and all(word in normalized(str(entry.get("after") or "")) for word in wanted)
        for entry in data.memory
    )


def explains_config(data: CheckInput) -> bool:
    """The answer says how Pedro can do it himself: the configuration session or by hand."""
    text = normalized(data.text or "")
    return any(way in text for way in HOW_TO_CONFIGURE)


CATALOG: dict[str, tuple[Callable[..., bool], int | None, Kind]] = {
    "includes_habit": (includes_habit, 1, "criterio"),
    "includes_or_left_out": (includes_or_left_out, 1, "criterio"),
    "left_out_or_absent": (left_out_or_absent, 1, "dura"),
    "max_new": (max_new, 1, "dura"),
    "no_overlap": (no_overlap, 0, "dura"),
    "no_duplicates": (no_duplicates, 0, "dura"),
    "due_from_now": (due_from_now, 0, "dura"),
    "no_move": (no_move, 1, "dura"),
    "no_item_on": (no_item_on, None, "dura"),
    "deletes_exactly": (deletes_exactly, None, "dura"),
    "no_delete": (no_delete, 1, "dura"),
    "no_deletes": (no_deletes, 0, "dura"),
    "moves": (moves, 2, "criterio"),
    "no_create_title": (no_create_title, None, "dura"),
    "creates": (creates, 3, "dura"),
    "learned_line": (learned_line, 0, "dura"),
    "training_line": (training_line, 0, "dura"),
    "no_memory_proposal": (no_memory_proposal, 0, "dura"),
    "note_lines": (note_lines, 1, "dura"),
    "no_health_first": (no_health_first, 0, "dura"),
    "no_action_claim": (no_action_claim, 0, "dura"),
    "no_saved_claim": (no_saved_claim, 0, "dura"),
    "no_change_to": (no_change_to, 1, "dura"),
    "proposes_change": (proposes_change, None, "criterio"),
    "explains_config": (explains_config, 0, "criterio"),
}
"""Name → (function, number of arguments, `None` for one or more; its kind, decision 131)."""
MIN_ARGS = {"proposes_change": 2}
"""The least arguments of a check with `None`, when it is more than one: a file and a text."""
PLAN_ONLY = frozenset({"learned_line", "training_line"})
"""They look at the painted plan: only in the checks of the plan."""
KIND_KEY = "tipo"


def parse_check(raw: object) -> Check:
    """`no_overlap`, `{max_new: 4}`, `{creates: [a, b, c]}` or `{moves: [a, b], tipo: dura}` as a
    `Check`; `CheckError` if it is not in the catalog, does not take those arguments or names a
    kind that does not exist."""
    kind: str | None = None
    if isinstance(raw, str):
        name, args = raw, ()
    elif isinstance(raw, dict) and 1 <= len(raw) <= 2:
        fields = dict(raw)
        if len(fields) == 2:
            if KIND_KEY not in fields:
                raise CheckError(f"comprobación mal escrita: {raw!r}")
            kind = str(fields.pop(KIND_KEY))
        ((name, value),) = fields.items()
        values = value if isinstance(value, list) else [value]
        args = tuple(str(item) for item in values)
    else:
        raise CheckError(f"comprobación mal escrita: {raw!r}")
    if name not in CATALOG:
        raise CheckError(f"comprobación desconocida: {name}")
    _, arity, default = CATALOG[name]
    if (arity is None and len(args) < MIN_ARGS.get(name, 1)) or (
        arity is not None and len(args) != arity
    ):
        raise CheckError(f"{name} no lleva {len(args)} argumentos")
    if kind is not None and kind not in KINDS:
        raise CheckError(f"{name}: tipo {kind} desconocido (dura o criterio)")
    chosen: Kind = "criterio" if (kind or default) == "criterio" else "dura"
    return Check(name, args, chosen)


def run_check(check: Check, data: CheckInput) -> bool:
    function, _, _ = CATALOG[check.name]
    return bool(function(data, *check.args))
