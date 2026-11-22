"""Invented personal files for the tests of task 1.12: never Pedro's.

`write_personal(config_dir)` writes the five files: the two YAML templates of `jimic config
init` and three invented Markdown files that pass the checks of task 1.3.
"""

import hashlib
from pathlib import Path

from jimic.memory.personal import template

ROUTINE = (
    "# Rutina\n\n"
    "## Horario base\n- Me levanto a las 7:00 (inventado)\n\n"
    "## Comidas\n- Como a las 14:00\n\n"
    "## Jornada\n- Trabajo de 9:00 a 17:00\n\n"
    "## Fines de semana\n- Libres\n"
)
OBJECTIVES = (
    "# Objetivos\n\n## Salud\nMeta: estar en forma\nPrioridad: alta\n\n"
    "- [medible] Entrenar 3 veces por semana\n"
)
PREFERENCES = (
    "# Preferencias\n\n- Mensajes cortos\n\n## Iconos\n\n"
    "| Tarea | Icono |\n| --- | --- |\n| Gimnasio | 🏋️ |\n"
)
MARKDOWN = {"rutina.md": ROUTINE, "objetivos.md": OBJECTIVES, "preferencias.md": PREFERENCES}
QUIET_LINE = 'quiet_hours: {from: "23:00", to: "07:00"}'
DELETIONS_LINE = "max_deletions_per_day: 5"
MESSAGES_LINE = "max_messages_per_day: 8"


def write_personal(config_dir: Path) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in ("intenciones.yaml", "limites.yaml"):
        (config_dir / name).write_text(template(name), encoding="utf-8")
    for name, text in MARKDOWN.items():
        (config_dir / name).write_text(text, encoding="utf-8")
    return config_dir


def file_sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def shas(config_dir: Path) -> dict[str, str | None]:
    """The fingerprint of every memory file and of `ideas.md`."""
    names = (*MARKDOWN, "intenciones.yaml", "limites.yaml", "ideas.md")
    return {name: file_sha(config_dir / name) for name in names}
