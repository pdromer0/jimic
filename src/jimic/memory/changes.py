"""Memory changes: check them, write them after Pedro's button, keep them in `changes`, undo them.

What a change may touch:

- the five personal files (`rutina.md`, `objetivos.md`, `preferencias.md`, `intenciones.yaml`,
  `limites.yaml`), which must exist (`/puesta_en_marcha` creates the Markdown ones, task 1.13);
- `ideas.md`, only with `add` (an idea), created 0600 if it is missing.

A symbolic link is refused: writing through it would write outside the config root.

`check` applies the change to the text as it is **now** and validates the result: the schema of
task 1.3 for a YAML file (loaded with `ruamel.yaml`), the Markdown checks of task 1.3, and for
`limites.yaml`, and for the `spacing` of the key tasks of `intenciones.yaml`, the asymmetric rule
against the current values (`discreet.py`). The text written
is the patched text, never a dump: outside the fragment the file stays byte for byte.

`save` checks again and writes atomically (0600), with a row in `changes`: the exact patch, the
`sha256` of the whole file before and after, the operation, the approval and Pedro's message.
`undo` restores the file only if it is still exactly as the change left it, and records the
reversal as one more change. Everything is synchronous: no `await` between reading the file and
writing it.

Refusals carry a code and a text: the text for the agent (`for_agent`, it never goes to a log)
and a short result for the button (`result`). Logs say codes, file names and ids.
"""

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jimic.clock import Clock, to_db
from jimic.files import write_private
from jimic.memory.database import transaction
from jimic.memory.discreet import judge, relaxed_days, relaxed_spacing
from jimic.memory.edits import EditError, Operation, Patch, patch_for
from jimic.memory.personal import (
    INTENTIONS_FILE,
    LIMITS_FILE,
    MARKDOWN_CHECKS,
    YAML_SCHEMAS,
    render_problems,
    validate_yaml,
)
from jimic.memory.problems import Problem
from jimic.memory.schemas import Intentions, Limits
from jimic.memory.yaml_io import YamlSyntaxError, parse_yaml

logger = logging.getLogger(__name__)

IDEAS_FILE = "ideas.md"
MEMORY_FILES: tuple[str, ...] = (*MARKDOWN_CHECKS, *YAML_SCHEMAS)


def stem(name: str) -> str:
    """`limites.yaml` → `limites`: what the system notices keep (no dots there)."""
    return name.split(".", 1)[0]


FILE_OF_STEM = {stem(name): name for name in MEMORY_FILES}
"""Back from a stem to the name of the file."""
UNDO_ORIGIN = "[Deshacer]"
"""The origin of a reversal: Pedro's button, not a message."""
BOM = "\ufeff"

RESULTS = {
    "changed": "ha cambiado: no lo guardo",
    "invalid": "ya no vale: no lo guardo",
    "relaxes": "ya no vale: no lo guardo",
    "missing": "el fichero no existe",
    "symlink": "es un enlace: edítalo a mano",
    "unreadable": "no puedo leerlo: edítalo a mano",
    "no_value": "ya no vale: no lo guardo",
}


@dataclass(frozen=True)
class Proposed:
    """A change as the agent proposed it, or an idea (`ideas.md`, `add`)."""

    file: str
    operation: Operation
    before: str | None
    after: str | None


class ChangeRefused(Exception):
    """The change cannot be written; `code` for the log, `for_agent` and `result` for people."""

    def __init__(self, code: str, for_agent: str, relaxed: tuple[str, ...] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.for_agent = for_agent
        self.relaxed = relaxed
        """With `relaxes`: the keys of `limites.yaml` that the change would relax."""

    @property
    def result(self) -> str:
        return RESULTS.get(self.code, "no lo guardo")


@dataclass(frozen=True)
class Checked:
    file: str
    operation: Operation
    text: str
    """The file as it is now (`""` for an `ideas.md` that does not exist yet)."""
    patch: Patch
    new_text: str


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(config_dir: Path, name: str) -> str | None:
    """The exact text of a file (BOM and line breaks kept), or `None` if it does not exist.
    Raises `ChangeRefused` for a symbolic link or a file that is not UTF-8."""
    path = config_dir / name
    if path.is_symlink():
        raise ChangeRefused("symlink", f"{name} es un enlace simbólico: Pedro lo edita a mano.")
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise ChangeRefused(
            "unreadable", f"No puedo leer {name} ({type(error).__name__}): Pedro lo edita a mano."
        ) from None


def _yaml_doc(name: str, text: str) -> object:
    try:
        return parse_yaml(text.removeprefix(BOM))
    except YamlSyntaxError as error:
        raise ChangeRefused(
            "invalid", f"Con ese cambio {name} no se puede leer como YAML ({error.kind})."
        ) from None


def _invalid(name: str, problems: list[Problem]) -> ChangeRefused:
    return ChangeRefused(
        "invalid",
        f"Con ese cambio {name} no es válido:\n{render_problems(name, problems)}",
    )


def _limits(text: str) -> Limits | None:
    """The limits of the file as it is now, or `None` if it does not load or validate (it is
    the current file that is wrong, not the change)."""
    try:
        doc = parse_yaml(text.removeprefix(BOM))
    except YamlSyntaxError:
        return None
    if validate_yaml(LIMITS_FILE, doc):
        return None
    return Limits.model_validate(doc)


def _intentions(text: str) -> Intentions | None:
    """The intentions of the file as it is now, or `None` if it does not validate: a change may
    be the one that fixes it, so then there is no `spacing` to compare with."""
    try:
        doc = parse_yaml(text.removeprefix(BOM))
    except YamlSyntaxError:
        return None
    if validate_yaml(INTENTIONS_FILE, doc):
        return None
    return Intentions.model_validate(doc)


def validate(name: str, old_text: str, new_text: str) -> None:
    """Raise `ChangeRefused` unless `new_text` is a valid `name` that `old_text` may become
    conversing (the asymmetric rule for `limites.yaml`)."""
    if name == IDEAS_FILE:
        return
    if name in MARKDOWN_CHECKS:
        problems = MARKDOWN_CHECKS[name](new_text.removeprefix(BOM))
        if problems:
            raise _invalid(name, problems)
        return
    old = _limits(old_text) if name == LIMITS_FILE else None
    if name == LIMITS_FILE and old is None:
        # The rule compares with the values of now: without them, nothing can be judged.
        raise ChangeRefused(
            "invalid", "limites.yaml no es válido ahora mismo: Pedro tiene que arreglarlo a mano."
        )
    problems = validate_yaml(name, _yaml_doc(name, new_text))
    if problems:
        raise _invalid(name, problems)
    if name == INTENTIONS_FILE:
        before = _intentions(old_text)
        if before is None:
            return
        after = Intentions.model_validate(_yaml_doc(name, new_text))
        spacing = relaxed_spacing(before, after)
        if spacing:
            raise ChangeRefused(
                "relaxes",
                f"Eso baja el espacio entre avisos ({', '.join(spacing)}) en intenciones.yaml:"
                " conversando solo puedo hacer a JimiC más discreto. Si Pedro lo quiere, lo edita"
                " él a mano.",
                spacing,
            )
        days = relaxed_days(before, after)
        if days:
            raise ChangeRefused(
                "relaxes",
                f"Eso añade días en que insiste una tarea clave ({', '.join(days)}) en"
                " intenciones.yaml: conversando solo puedo hacer a JimiC más discreto (quitar"
                " días sí). Si Pedro lo quiere, lo edita él a mano o con /configurar-jimic.",
                days,
            )
        return
    if old is None:
        return
    new = Limits.model_validate(_yaml_doc(name, new_text))
    verdict = judge(old, new)
    if verdict.relaxed:
        raise ChangeRefused(
            "relaxes",
            f"Eso relaja {', '.join(verdict.relaxed)} en limites.yaml, la zona protegida:"
            " conversando solo puedo hacer a JimiC más discreto. Si Pedro lo quiere, lo edita él"
            " a mano en limites.yaml.",
            verdict.relaxed,
        )
    if not verdict.changed:
        raise ChangeRefused(
            "no_value",
            "En limites.yaml solo se cambian valores, y solo para hacer a JimiC más discreto: ese"
            " cambio no mueve ninguno.",
        )


def check(config_dir: Path, proposed: Proposed) -> Checked:
    """The change applied to the file as it is now, validated. Raises `ChangeRefused`."""
    name = proposed.file
    if name not in (*MEMORY_FILES, IDEAS_FILE):
        raise ChangeRefused("unknown_file", f"{name} no es un fichero de memoria.")
    if name == IDEAS_FILE and proposed.operation != "add":
        raise ChangeRefused("ideas_add_only", "En ideas.md solo se añaden ideas.")
    text = read_text(config_dir, name)
    if text is None:
        if name != IDEAS_FILE:
            raise ChangeRefused(
                "missing",
                f"{name} aún no existe: lo crea la puesta en marcha, que Pedro lanza con"
                " /puesta_en_marcha.",
            )
        text = ""
    try:
        patch = patch_for(text, proposed.operation, proposed.before, proposed.after)
    except EditError as error:
        if error.code == "repeated":
            raise ChangeRefused(
                "changed", f"El «antes» aparece más de una vez en {name}: hazlo más largo."
            ) from None
        raise ChangeRefused(
            "changed", f"El «antes» no está en {name} tal cual: cópialo exacto del fichero."
        ) from None
    new_text = patch.apply(text)
    validate(name, text, new_text)
    return Checked(name, proposed.operation, text, patch, new_text)


Kind = Literal["add", "replace", "remove", "undo"]


def _record(
    conn: sqlite3.Connection,
    clock: Clock,
    name: str,
    operation: Kind,
    patch: Patch,
    text: str,
    new_text: str,
    origin: str,
    approval_id: int | None,
    revert_of: int | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO changes (created_at, file, before, after, source_message, operation,"
        " position, file_before_sha, file_after_sha, revert_of, approval_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            to_db(clock.now()),
            name,
            patch.removed,
            patch.inserted,
            origin,
            operation,
            patch.position,
            sha(text),
            sha(new_text),
            revert_of,
            approval_id,
        ),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def save(
    conn: sqlite3.Connection,
    clock: Clock,
    config_dir: Path,
    proposed: Proposed,
    origin: str,
    approval_id: int | None,
) -> int:
    """Check against the file as it is now, write it and record the change; return its id.
    Raises `ChangeRefused` without writing anything."""
    checked = check(config_dir, proposed)
    # The row first and the file inside the same transaction: a write that fails leaves no row.
    with transaction(conn):
        change_id = _record(
            conn,
            clock,
            checked.file,
            checked.operation,
            checked.patch,
            checked.text,
            checked.new_text,
            origin,
            approval_id,
        )
        write_private(config_dir / checked.file, checked.new_text)
    logger.info("change %d saved (%s, %s)", change_id, checked.file, checked.operation)
    return change_id


@dataclass(frozen=True)
class ChangeRow:
    id: int
    created_at: str
    file: str
    operation: str
    before: str
    after: str
    reverted: bool


def _row(row: tuple) -> ChangeRow:
    return ChangeRow(row[0], row[1], row[2], row[3] or "", row[4] or "", row[5] or "", row[6])


ROW = "id, created_at, file, operation, before, after, reverted_at IS NOT NULL"


def recent(conn: sqlite3.Connection, limit: int = 10) -> list[ChangeRow]:
    """The last `limit` changes not reverted, the newest first (reversals included)."""
    rows = conn.execute(
        f"SELECT {ROW} FROM changes WHERE reverted_at IS NULL AND operation IS NOT NULL"
        " ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row(row) for row in rows]


def get(conn: sqlite3.Connection, change_id: int) -> ChangeRow | None:
    row = conn.execute(f"SELECT {ROW} FROM changes WHERE id = ?", (change_id,)).fetchone()
    return None if row is None else _row(row)


type UndoOutcome = Literal["undone", "already", "changed", "invalid", "unknown"]


def undo(conn: sqlite3.Connection, clock: Clock, config_dir: Path, change_id: int) -> UndoOutcome:
    """Put the file back as it was before change `change_id`, if it is still exactly as that
    change left it, and record the reversal. A change of `limites.yaml` may be undone even if
    that relaxes a limit (it gives back what Pedro had by hand), but the schema must hold."""
    row = conn.execute(
        "SELECT file, before, after, position, file_before_sha, file_after_sha, reverted_at"
        " FROM changes WHERE id = ? AND operation IS NOT NULL",
        (change_id,),
    ).fetchone()
    if row is None:
        return "unknown"
    name, before, after, position, before_sha, after_sha, reverted_at = row
    if reverted_at is not None:
        return "already"
    try:
        text = read_text(config_dir, name)
    except ChangeRefused:
        return "changed"
    if text is None:
        text = ""
        if name != IDEAS_FILE:
            return "changed"
    if sha(text) != after_sha:
        logger.info("undo of change %d refused (changed)", change_id)
        return "changed"
    reversal = Patch(position, after, before)
    try:
        restored = reversal.apply(text)
    except EditError:
        return "changed"
    if sha(restored) != before_sha:
        return "changed"
    if name in YAML_SCHEMAS:
        try:
            if validate_yaml(name, _yaml_doc(name, restored)):
                return "invalid"
        except ChangeRefused:
            return "invalid"
    with transaction(conn):
        new_id = _record(
            conn, clock, name, "undo", reversal, text, restored, UNDO_ORIGIN, None, change_id
        )
        conn.execute(
            "UPDATE changes SET reverted_at = ? WHERE id = ?", (to_db(clock.now()), change_id)
        )
        write_private(config_dir / name, restored)
    logger.info("change %d undone by change %d (%s)", change_id, new_id, name)
    return "undone"
