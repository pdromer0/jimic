"""Structure checks for the three Markdown personal files; the content is never judged.

- `rutina.md`: the four fixed `##` sections, once each (any other section is free).
- `objetivos.md`: every top-level list item (`-`, `*`, `+`, `1.`) starts with a tag; nested
  items and plain lines (`Meta:`, `Prioridad:`) are free, and the area sections are not checked.
- `preferencias.md`: a `## Iconos` section with a table whose rows start with `|` and have
  exactly two non-empty cells; the separator row (`| --- | --- |`) is not a row.

Headings match exactly (level 2, same case); lines inside fenced code blocks and HTML
comment blocks are skipped.
Problems carry a line and a fixed reason, never the text of the line.

`outline` gives the same view to the onboarding (task 1.13), which reads what each section holds.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass

from jimic.memory.problems import Problem

ROUTINE_SECTIONS = ("Horario base", "Comidas", "Jornada", "Fines de semana")
OBJECTIVE_TAGS = ("[medible]", "[declarado]", "[enlazado]")
ICONS_SECTION = "Iconos"

# The closing sequence (`## Comidas ##`) is not part of the title; `## C#` keeps its `#`.
HEADING = re.compile(r" {0,3}(#{1,6})(?:[ \t]+(.*?))??(?:[ \t]+#+)?[ \t]*")
# A backtick fence cannot have backticks after it (that is inline code); a tilde fence can.
FENCE = re.compile(r" {0,3}(`{3,}(?=[^`]*$)|~{3,})(.*)")
THEMATIC_BREAK = re.compile(r" {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*")
TOP_LEVEL_ITEM = re.compile(r"(?:[-*+]|[0-9]{1,9}[.)])(?:[ \t]+(.*))?")
TABLE_ROW = re.compile(r" {0,3}\|.*")
SEPARATOR_ROW = re.compile(r" {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*")
UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


def _lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield `(line number, line)` outside fenced code blocks and HTML comment blocks.

    A comment block starts at a line that begins with `<!--` and ends at the first `-->`.
    """
    fence: str | None = None
    in_comment = False
    for number, line in enumerate(text.splitlines(), start=1):
        if in_comment:
            in_comment = "-->" not in line
            continue
        if fence is None and line.lstrip().startswith("<!--"):
            in_comment = "-->" not in line.lstrip()[4:]
            continue
        match = FENCE.fullmatch(line)
        if fence is None and match:
            fence = match[1]
        elif fence is not None:
            # Only a fence of the same character, at least as long and with nothing after it,
            # closes the block.
            if (
                match
                and match[1][0] == fence[0]
                and len(match[1]) >= len(fence)
                and not match[2].strip()
            ):
                fence = None
        else:
            yield number, line


def _sections(text: str) -> list[tuple[int, str]]:
    """Return `(line, title)` of each level-2 heading."""
    sections = []
    for number, line in _lines(text):
        match = HEADING.fullmatch(line)
        if match and len(match[1]) == 2:
            sections.append((number, match[2] or ""))
    return sections


@dataclass(frozen=True)
class Section:
    """A part of a Markdown file: a level-2 section, or what goes before the first one (or
    after a level-1 heading), with `title` `None`. `body` holds its lines outside code blocks
    and comments, up to the next heading of level 1 or 2, without that heading."""

    title: str | None
    line: int | None
    body: tuple[tuple[int, str], ...]


def outline(text: str) -> list[Section]:
    """The sections of `text`, in order; the first is always the one without a title."""
    sections: list[Section] = []
    title: str | None = None
    start: int | None = None
    body: list[tuple[int, str]] = []
    for number, line in _lines(text):
        match = HEADING.fullmatch(line)
        if match and len(match[1]) <= 2:
            sections.append(Section(title, start, tuple(body)))
            title = (match[2] or "") if len(match[1]) == 2 else None
            start, body = number, []
        else:
            body.append((number, line))
    sections.append(Section(title, start, tuple(body)))
    return sections


def check_routine(text: str) -> list[Problem]:
    problems = []
    sections = _sections(text)
    for name in ROUTINE_SECTIONS:
        lines = [number for number, title in sections if title == name]
        if not lines:
            problems.append(Problem(None, f"falta la sección «{name}»"))
        for repeated in lines[1:]:
            problems.append(Problem(repeated, f"la sección «{name}» está repetida"))
    return problems


def check_objectives(text: str) -> list[Problem]:
    problems = []
    for number, line in _lines(text):
        if THEMATIC_BREAK.fullmatch(line):
            continue
        match = TOP_LEVEL_ITEM.fullmatch(line)
        if match and not (match[1] or "").startswith(OBJECTIVE_TAGS):
            problems.append(Problem(number, "objetivo sin [medible], [declarado] ni [enlazado]"))
    return problems


def _table_lines(text: str, start: int) -> list[tuple[int, str]]:
    """The lines of tables of the section that starts at line `start`, separators included."""
    lines = []
    for number, line in _lines(text):
        if number <= start:
            continue
        heading = HEADING.fullmatch(line)
        if heading and len(heading[1]) <= 2:
            break
        if TABLE_ROW.fullmatch(line):
            lines.append((number, line))
    return lines


def check_preferences(text: str) -> list[Problem]:
    starts = [number for number, title in _sections(text) if title == ICONS_SECTION]
    if not starts:
        return [Problem(None, f"falta la sección «{ICONS_SECTION}»")]
    problems = [Problem(n, f"la sección «{ICONS_SECTION}» está repetida") for n in starts[1:]]
    rows = [
        (number, line)
        for number, line in _table_lines(text, starts[0])
        if not SEPARATOR_ROW.fullmatch(line)
    ]
    if not rows:
        problems.append(Problem(starts[0], f"la sección «{ICONS_SECTION}» no tiene tabla"))
    for number, line in rows:
        cells = UNESCAPED_PIPE.split(line.strip().removeprefix("|").removesuffix("|"))
        if len(cells) != 2:
            problems.append(Problem(number, "la fila de iconos no tiene dos columnas"))
        elif not all(cell.strip() for cell in cells):
            problems.append(Problem(number, "la fila de iconos tiene una celda vacía"))
    return problems


def icon_rows(text: str) -> list[tuple[str, str]]:
    """The rows of the table of `## Iconos` as `(task, icon)`, stripped (task 1.14): what the
    plan of the day paints «Tu día» with. The header (a row followed by a separator) and the rows
    without two non-empty cells are skipped; only the first section counts."""
    starts = [number for number, title in _sections(text) if title == ICONS_SECTION]
    if not starts:
        return []
    lines = _table_lines(text, starts[0])
    rows = []
    for index, (_, line) in enumerate(lines):
        if SEPARATOR_ROW.fullmatch(line):
            continue
        if index + 1 < len(lines) and SEPARATOR_ROW.fullmatch(lines[index + 1][1]):
            continue  # the header
        cells = [
            cell.strip()
            for cell in UNESCAPED_PIPE.split(line.strip().removeprefix("|").removesuffix("|"))
        ]
        if len(cells) == 2 and all(cells):
            rows.append((cells[0], cells[1]))
    return rows
