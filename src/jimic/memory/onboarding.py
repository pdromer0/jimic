"""The onboarding (CU-12, task 1.13): the skeletons of the three Markdown files and the progress.

- `create_skeletons` writes the Markdown files that are missing from their templates, with the
  structure of 7.2 of the design and nothing of Pedro's: private (0600) and never over a file
  that exists (`O_CREAT | O_EXCL`, as `jimic config init`). Each one passes `jimic config check`.
- `progress` reads the files **as they are now** and says which parts are done, what is missing
  and where to go on. There is no state apart: a [Deshacer] or an edit by hand counts at once.

What «finished» means (`Progress.finished`, every piece):

1. `jimic config check` is `ok` for the five personal files.
2. Each of the four fixed sections of `rutina.md` has a line with content (not blank, not a
   comment) before the next heading of level 1 or 2.
3. Each of the nine areas of `objetivos.md` (`## <area>`, exact title) has a line `Prioridad:`
   with `alta`, `media`, `baja` or `ahora no`; unless it is «ahora no», a line `Meta:` that is
   not empty; and «Gestión del tiempo», unless it is «ahora no», a top-level `[medible]` item.
   Training needs nothing more: its measurable goals are read from APEX (decision 74).
4. `habits` of `intenciones.yaml` has at least one habit.
5. `preferencias.md` has a list item outside `## Iconos` (the tone) and a row of icons besides the
   header.

The quiet hours and how much to insist live in `limites.yaml` and do not count: the defaults may
be what Pedro wants, and the file keeps no trace of having been asked.

Nothing here logs or returns the text of the files, except the goals of the summary, which go
to Pedro.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from jimic.memory.markdown_checks import (
    ICONS_SECTION,
    ROUTINE_SECTIONS,
    SEPARATOR_ROW,
    TABLE_ROW,
    TOP_LEVEL_ITEM,
    Section,
    outline,
)
from jimic.memory.personal import (
    INTENTIONS_FILE,
    MARKDOWN_CHECKS,
    check,
    create_private,
    template,
)
from jimic.memory.schemas import Intentions
from jimic.memory.yaml_io import YamlSyntaxError, parse_yaml

ROUTINE_FILE = "rutina.md"
OBJECTIVES_FILE = "objetivos.md"
PREFERENCES_FILE = "preferencias.md"

TIME_AREA = "Gestión del tiempo"
AREAS = (
    TIME_AREA,
    "Entrenamiento",
    "Hábitos",
    "Nutrición",
    "Salud",
    "Trabajo y proyectos personales",
    "Empleo",
    "Ocio",
    "Social",
)
"""The nine areas (1.4 of the design), the two measured first: the order of the interview."""
NOT_NOW = "ahora no"
PRIORITIES = ("alta", "media", "baja", NOT_NOW)
"""Also the order of the summary."""

GOAL_LINE = re.compile(r"[ \t]*Meta:(.*)")
PRIORITY_LINE = re.compile(r"[ \t]*Prioridad:(.*)")


def create_skeletons(config_dir: Path) -> list[tuple[str, bool]]:
    """Create the Markdown files that are missing; return `(name, created)` for each."""
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return [(name, create_private(config_dir / name, template(name))) for name in MARKDOWN_CHECKS]


@dataclass(frozen=True)
class Area:
    name: str
    priority: str | None
    """One of `PRIORITIES`, or `None` if the line is missing or says something else."""
    goal: str | None
    """The text after `Meta:`, without spaces around; `None` if missing or empty."""
    measurable: int
    """Top-level `[medible]` items."""

    @property
    def done(self) -> bool:
        if self.priority is None:
            return False
        if self.priority == NOT_NOW:
            return True
        if not self.goal:
            return False
        return self.name != TIME_AREA or self.measurable > 0


@dataclass(frozen=True)
class Progress:
    started: bool
    """`objetivos.md` exists: the skeletons are there (or Pedro wrote the file by hand)."""
    invalid: tuple[str, ...]
    """The personal files that `jimic config check` does not give as `ok` (a missing Markdown
    file included)."""
    routine_missing: tuple[str, ...]
    """The fixed sections of `rutina.md` without content, in their order."""
    areas: tuple[Area, ...]
    habits: int
    tone: bool
    icons: int
    """Rows of the icons table besides the header."""

    @property
    def missing_areas(self) -> tuple[str, ...]:
        return tuple(area.name for area in self.areas if not area.done)

    @property
    def finished(self) -> bool:
        return (
            not self.invalid
            and not self.routine_missing
            and not self.missing_areas
            and self.habits > 0
            and self.tone
            and self.icons > 0
        )

    @property
    def in_progress(self) -> bool:
        return self.started and not self.finished

    def done_parts(self) -> list[str]:
        """What is saved, in the order of the interview; names, never content."""
        parts = []
        if not self.routine_missing:
            parts.append("la rutina")
        parts += [f"el área «{area.name}»" for area in self.areas if area.done]
        if self.habits:
            parts.append(f"los hábitos ({self.habits})")
        if self.tone:
            parts.append("el tono de las preferencias")
        if self.icons:
            parts.append(f"los iconos ({self.icons})")
        return parts

    def missing_parts(self) -> list[str]:
        """What is missing, in the order of the interview: the first one is where to go on."""
        parts = [f"arreglar {name}, que no pasa `jimic config check`" for name in self.invalid]
        if self.routine_missing:
            parts.append(f"la rutina: {', '.join(self.routine_missing)}")
        parts += [f"el área «{name}» de los objetivos" for name in self.missing_areas]
        if not self.habits:
            parts.append("los hábitos (la sección habits de intenciones.yaml)")
        if not self.tone:
            parts.append("el tono (preferencias.md)")
        if not self.icons:
            parts.append("la tabla de iconos (preferencias.md)")
        return parts

    def summary(self) -> str:
        """The goals by priority (part 5 of the interview), from `objetivos.md`."""
        lines = ["Tus metas por prioridad:"]
        for priority in PRIORITIES:
            areas = [area for area in self.areas if area.priority == priority]
            if not areas:
                continue
            if priority == NOT_NOW:
                lines.append(f"Ahora no: {', '.join(area.name for area in areas)}.")
                continue
            goals = "; ".join(
                f"{area.name}, {area.goal}" if area.goal else area.name for area in areas
            )
            lines.append(f"{priority.capitalize()}: {goals}.")
        return "\n".join(lines)


def _read(path: Path) -> str:
    """The text of a file, or `""` if it is missing or cannot be read: its parts count as
    missing, and `jimic config check` says what is wrong."""
    if path.is_symlink():
        return ""
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError, UnicodeDecodeError:
        return ""


def _first(sections: list[Section], title: str) -> Section | None:
    return next((section for section in sections if section.title == title), None)


def _has_content(section: Section | None) -> bool:
    return section is not None and any(line.strip() for _, line in section.body)


def _value(pattern: re.Pattern[str], section: Section) -> str | None:
    for _, line in section.body:
        match = pattern.fullmatch(line)
        if match:
            return match[1].strip() or None
    return None


def _priority(section: Section) -> str | None:
    value = _value(PRIORITY_LINE, section)
    if value is None:
        return None
    normalized = " ".join(value.casefold().rstrip(".").split())
    return normalized if normalized in PRIORITIES else None


def _measurable(section: Section) -> int:
    count = 0
    for _, line in section.body:
        match = TOP_LEVEL_ITEM.fullmatch(line)
        if match and (match[1] or "").startswith("[medible]"):
            count += 1
    return count


def _area(sections: list[Section], name: str) -> Area:
    section = _first(sections, name)
    if section is None:
        return Area(name, None, None, 0)
    return Area(name, _priority(section), _value(GOAL_LINE, section), _measurable(section))


def _habits(config_dir: Path) -> int:
    try:
        doc = parse_yaml(_read(config_dir / INTENTIONS_FILE))
        return len(Intentions.model_validate(doc).habits)
    except YamlSyntaxError, ValidationError:
        return 0


def _preferences(text: str) -> tuple[bool, int]:
    """Whether there is a list item outside `## Iconos`, and the rows of icons besides the
    header (the first row of the table)."""
    sections = outline(text)
    icons = _first(sections, ICONS_SECTION)
    tone = any(
        (match := TOP_LEVEL_ITEM.fullmatch(line)) and (match[1] or "").strip()
        for section in sections
        if section is not icons
        for _, line in section.body
    )
    if icons is None:
        return tone, 0
    rows = [
        line
        for _, line in icons.body
        if TABLE_ROW.fullmatch(line) and not SEPARATOR_ROW.fullmatch(line)
    ]
    return tone, max(len(rows) - 1, 0)


def progress(config_dir: Path) -> Progress:
    """The onboarding as the files say it is now; nothing is created or changed."""
    invalid = tuple(report.name for report in check(config_dir) if report.status != "ok")
    routine = outline(_read(config_dir / ROUTINE_FILE))
    objectives = outline(_read(config_dir / OBJECTIVES_FILE))
    tone, icons = _preferences(_read(config_dir / PREFERENCES_FILE))
    return Progress(
        started=(config_dir / OBJECTIVES_FILE).exists(),
        invalid=invalid,
        routine_missing=tuple(
            name for name in ROUTINE_SECTIONS if not _has_content(_first(routine, name))
        ),
        areas=tuple(_area(objectives, name) for name in AREAS),
        habits=_habits(config_dir),
        tone=tone,
        icons=icons,
    )


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


MIN_GOAL_WORDS = 2
"""A description of one word («Fuerza») is too short to tell a copy from a coincidence."""


def _count(goal: str, text: str) -> int:
    """Whole-word occurrences of `goal` in `text`, both normalized."""
    return len(re.findall(rf"(?<!\w){re.escape(goal)}(?!\w)", text))


def copied_goal(before: str | None, after: str, goals: list[str]) -> bool:
    """Whether the change from `before` to `after` adds one of `goals` (the descriptions of the
    goals of APEX in progress): whole words, ignoring case and spaces. A goal that was already in
    `before` (an edit by hand) does not count again. Only the description: a target like
    «100 kg» is too short to tell a copy from a coincidence."""
    old, new = _normalized(before or ""), _normalized(after)
    for goal in map(_normalized, goals):
        if len(goal.split()) >= MIN_GOAL_WORDS and _count(goal, new) > _count(goal, old):
            return True
    return False
