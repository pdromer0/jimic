"""The five personal files in the config root: check them, create the YAML ones, save changes.

- `jimic config check` (`check`): each file is correct, invalid (with its problems), missing
  (a YAML file: create it with `jimic config init`) or pending (a Markdown file: the onboarding
  creates it, CU-12). Problems name the file, a line and a reason built from schema names,
  never from the content: Pydantic's `msg` and `input` and the loader's `str(exc)` are dropped.
- `jimic config init` (`init`): writes the two YAML templates that are missing, private, and
  never overwrites a file.
- `save_yaml_file`: validates a changed document and writes it atomically (task 1.12 uses it).
- `load_personal`: what `jimic run` loads; the two YAML files must be correct, and the Markdown
  ones may be missing until the onboarding (task 1.13).
"""

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from jimic.files import write_private
from jimic.memory.markdown_checks import check_objectives, check_preferences, check_routine
from jimic.memory.problems import Problem
from jimic.memory.schemas import Intentions, Limits, cross_check, field_names
from jimic.memory.yaml_io import YamlSyntaxError, dump_yaml, line_of, parse_yaml

INTENTIONS_FILE = "intenciones.yaml"
LIMITS_FILE = "limites.yaml"

YAML_SCHEMAS: dict[str, type[BaseModel]] = {INTENTIONS_FILE: Intentions, LIMITS_FILE: Limits}
MARKDOWN_CHECKS: dict[str, Callable[[str], list[Problem]]] = {
    "rutina.md": check_routine,
    "objetivos.md": check_objectives,
    "preferencias.md": check_preferences,
}

FIELD_NAMES = field_names()

REASONS = {
    "missing": "falta",
    "extra_forbidden": "clave desconocida (no se muestra)",
    "hour_format": "hora no válida (H:MM o HH:MM, de 00:00 a 23:59)",
    "duration_format": "duración no válida (como 30m o 2h)",
    "duration_not_positive": "la duración debe ser mayor que 0",
    "string_pattern_mismatch": "id no válido (solo a-z, 0-9 y _)",
    "string_too_short": "está vacío",
    "too_short": "no puede estar vacía",
    "too_long": "debe estar vacío por ahora",
    "string_type": "debe ser texto",
    "int_type": "debe ser un número entero",
    "float_type": "debe ser un número",
    "bool_type": "debe ser true o false",
    "finite_number": "debe ser un número finito",
    "greater_than_equal": "fuera de rango (demasiado pequeño)",
    "less_than_equal": "fuera de rango (demasiado grande)",
    "literal_error": "valor no permitido",
    "union_tag_invalid": "type desconocido",
    "union_tag_not_found": "falta type",
    "model_type": "debe ser un mapa de claves",
    "dict_type": "debe ser un mapa de claves",
    "list_type": "debe ser una lista",
    "duplicate_id": "id repetido",
    "unknown_habit": "no apunta a ningún hábito",
    "repeated_days": "días repetidos",
}

Status = Literal["ok", "invalid", "missing", "pending"]


@dataclass(frozen=True)
class FileReport:
    name: str
    status: Status
    problems: tuple[Problem, ...] = ()


class PersonalFilesInvalid(Exception):
    """`jimic run` cannot start: the message is the report of `jimic config check`."""


class PersonalFileError(Exception):
    """A document does not fit its schema; the message lists the problems without values."""

    def __init__(self, name: str, problems: Sequence[Problem]) -> None:
        super().__init__(render_problems(name, problems))
        self.problems = tuple(problems)


def _path(loc: Sequence[str | int], error_type: str) -> str:
    """The location with schema names and indexes only: keys written by Pedro are dropped."""
    if error_type == "extra_forbidden":
        loc = loc[:-1]  # the unknown key itself, even if it looks like a schema name
    path = ""
    for segment in loc:
        if isinstance(segment, int):
            path += f"[{segment}]"
        elif segment in FIELD_NAMES:
            path += f".{segment}" if path else segment
    return path


def _problem(doc: object, loc: Sequence[str | int], error_type: str) -> Problem:
    path = _path(loc, error_type)
    reason = REASONS.get(error_type, f"no es válido ({error_type})")
    return Problem(line_of(doc, loc), f"{path}: {reason}" if path else reason)


def validate_yaml(name: str, doc: object) -> list[Problem]:
    """Return the problems of a loaded document against the schema of file `name`.

    Raises `ValueError` if `name` is not one of the two YAML personal files.
    """
    if name not in YAML_SCHEMAS:
        raise ValueError(f"{name} no es un fichero YAML personal")
    if doc is None:
        return [Problem(None, "el fichero está vacío")]
    try:
        model = YAML_SCHEMAS[name].model_validate(doc)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False, include_context=False)
        problems = [_problem(doc, error["loc"], error["type"]) for error in errors]
        return sorted(problems, key=lambda problem: problem.line or 0)
    if isinstance(model, Intentions):
        return [_problem(doc, loc, error_type) for loc, error_type in cross_check(model)]
    return []


def _yaml_problems(name: str, text: str) -> list[Problem]:
    try:
        doc = parse_yaml(text)
    except YamlSyntaxError as exc:
        near = f", cerca de la línea {exc.line}" if exc.line is not None else ""
        return [Problem(None, f"no se puede leer como YAML ({exc.kind}){near}")]
    return validate_yaml(name, doc)


def _check_file(config_dir: Path, name: str) -> FileReport:
    path = config_dir / name
    if not path.exists():
        return FileReport(name, "missing" if name in YAML_SCHEMAS else "pending")
    try:
        # `utf-8-sig` drops the BOM some editors write, which would hide the first heading.
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        # Only the type: a decode error quotes the bytes it could not read.
        return FileReport(
            name, "invalid", (Problem(None, f"no se puede leer ({type(exc).__name__})"),)
        )
    if name in YAML_SCHEMAS:
        problems = _yaml_problems(name, text)
    else:
        problems = MARKDOWN_CHECKS[name](text)
    return FileReport(name, "invalid" if problems else "ok", tuple(problems))


def check(config_dir: Path) -> list[FileReport]:
    """Check the five personal files in `config_dir`; nothing is created or changed."""
    return [_check_file(config_dir, name) for name in (*YAML_SCHEMAS, *MARKDOWN_CHECKS)]


def has_errors(reports: Sequence[FileReport]) -> bool:
    """A missing Markdown file is not an error: the onboarding creates it."""
    return any(report.status in ("invalid", "missing") for report in reports)


STATUS_TEXT: dict[Status, str] = {
    "ok": "correcto",
    "invalid": "no es válido",
    "missing": "no existe: créalo con `jimic config init`",
    "pending": "aún no existe: lo crea la puesta en marcha",
}


def render_problems(name: str, problems: Sequence[Problem]) -> str:
    return "\n".join(
        f"  {name}:{p.line}: {p.reason}" if p.line is not None else f"  {name}: {p.reason}"
        for p in problems
    )


def render_check(config_dir: Path, reports: Sequence[FileReport]) -> str:
    lines = [f"Ficheros personales en {config_dir}:"]
    for report in reports:
        lines.append(f"- {report.name}: {STATUS_TEXT[report.status]}")
        if report.problems:
            lines.append(render_problems(report.name, report.problems))
    return "\n".join(lines)


def _create_private(path: Path, text: str) -> bool:
    """Create `path` (0600) with `text`; return `False`, untouched, if it already exists."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
    except BaseException:
        # Only this call created it: a half-written file would block every later `init`.
        path.unlink(missing_ok=True)
        raise
    return True


def template(name: str) -> str:
    return files("jimic.memory").joinpath("templates", name).read_text(encoding="utf-8")


def init(config_dir: Path) -> list[tuple[str, bool]]:
    """Create the missing YAML files from their templates; return `(name, created)` for each.

    The Markdown files are left to the onboarding (CU-12), which starts when `objetivos.md`
    does not exist.
    """
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return [(name, _create_private(config_dir / name, template(name))) for name in YAML_SCHEMAS]


def render_init(config_dir: Path, results: Sequence[tuple[str, bool]]) -> str:
    lines = [f"Ficheros personales en {config_dir}:"]
    for name, created in results:
        lines.append(f"- {name}: {'creado' if created else 'ya existe, no se toca'}")
    markdown = ", ".join(MARKDOWN_CHECKS)
    lines.append(f"- {markdown}: los crea la puesta en marcha")
    return "\n".join(lines)


def save_yaml_file(path: Path, doc: object) -> None:
    """Validate `doc` against the schema of `path` and replace the file atomically (0600).

    Raises `PersonalFileError` without writing anything if the document does not fit, and
    `ValueError` if `path` is not named like one of the two YAML personal files.
    """
    problems = validate_yaml(path.name, doc)
    if problems:
        raise PersonalFileError(path.name, problems)
    write_private(path, dump_yaml(doc))


def load_personal(config_dir: Path) -> tuple[Intentions, Limits]:
    """Load and validate `intenciones.yaml` and `limites.yaml`.

    Raises `PersonalFilesInvalid` with the whole report of `check` if either is missing or
    invalid. The Markdown files do not stop the service.
    """
    reports = check(config_dir)
    if any(r.status != "ok" for r in reports if r.name in YAML_SCHEMAS):
        raise PersonalFilesInvalid(render_check(config_dir, reports))
    intentions = Intentions.model_validate(_read_yaml(config_dir / INTENTIONS_FILE))
    limits = Limits.model_validate(_read_yaml(config_dir / LIMITS_FILE))
    return intentions, limits


def _read_yaml(path: Path) -> object:
    return parse_yaml(path.read_text(encoding="utf-8-sig"))
