"""Structure checks for the three Markdown personal files; the content is never judged.

- `rutina.md`: the four fixed `##` sections, once each (any other section is free).
- `objetivos.md`: every top-level list item (`-`, `*`, `+`, `1.`) starts with a tag; nested
  items and plain lines (`Meta:`, `Prioridad:`) are free, and the area sections are not checked.
- `preferencias.md`: a `## Iconos` section with a table whose rows start with `|` and have
  exactly two non-empty cells; the separator row (`| --- | --- |`) is not a row.

Headings match exactly (level 2, same case); lines inside fenced code blocks are skipped.
Problems carry a line and a fixed reason, never the text of the line.
"""

import re
from collections.abc import Iterator

from jimic.memory.problems import Problem

ROUTINE_SECTIONS = ("Horario base", "Comidas", "Jornada", "Fines de semana")
OBJECTIVE_TAGS = ("[medible]", "[declarado]", "[enlazado]")
ICONS_SECTION = "Iconos"

HEADING = re.compile(r" {0,3}(#{1,6})[ \t]+(.*?)[ \t]*")
# A backtick fence cannot have backticks after it (that is inline code); a tilde fence can.
FENCE = re.compile(r" {0,3}(`{3,}(?=[^`]*$)|~{3,})(.*)")
THEMATIC_BREAK = re.compile(r" {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*")
TOP_LEVEL_ITEM = re.compile(r"(?:[-*+]|[0-9]{1,9}[.)])(?:[ \t]+(.*))?")
TABLE_ROW = re.compile(r" {0,3}\|.*")
SEPARATOR_ROW = re.compile(r" {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*")
UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


def _lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield `(line number, line)` outside fenced code blocks."""
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
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
            sections.append((number, match[2]))
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


def check_preferences(text: str) -> list[Problem]:
    starts = [number for number, title in _sections(text) if title == ICONS_SECTION]
    if not starts:
        return [Problem(None, f"falta la sección «{ICONS_SECTION}»")]
    problems = [Problem(n, f"la sección «{ICONS_SECTION}» está repetida") for n in starts[1:]]
    rows = []
    for number, line in _lines(text):
        if number <= starts[0]:
            continue
        heading = HEADING.fullmatch(line)
        if heading and len(heading[1]) <= 2:
            break
        if TABLE_ROW.fullmatch(line) and not SEPARATOR_ROW.fullmatch(line):
            rows.append((number, line))
    if not rows:
        problems.append(Problem(starts[0], f"la sección «{ICONS_SECTION}» no tiene tabla"))
    for number, line in rows:
        cells = UNESCAPED_PIPE.split(line.strip().removeprefix("|").removesuffix("|"))
        if len(cells) != 2:
            problems.append(Problem(number, "la fila de iconos no tiene dos columnas"))
        elif not all(cell.strip() for cell in cells):
            problems.append(Problem(number, "la fila de iconos tiene una celda vacía"))
    return problems
