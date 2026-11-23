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


# --- The onboarding (task 1.13) ---------------------------------------------------------------

AREA_NAMES = (
    "Gestión del tiempo",
    "Entrenamiento",
    "Hábitos",
    "Nutrición",
    "Salud",
    "Trabajo y proyectos personales",
    "Empleo",
    "Ocio",
    "Social",
)
"""Written here, not imported: the tests compare with the code's list."""
AREA_TEXT = {
    "Gestión del tiempo": (
        "Meta: llegar a todo sin correr (inventado)\nPrioridad: alta\n\n"
        "- [medible] Cerrar 5 tareas al día, según Todoist\n"
    ),
    "Entrenamiento": "Meta: ganar fuerza (inventado)\nPrioridad: alta\n",
    "Hábitos": "Meta: leer cada noche\nPrioridad: media\n\n- [declarado] Leer 20 minutos\n",
    "Ocio": "Prioridad: ahora no\n",
    "Social": "Prioridad: Ahora no.\n",
}
"""The areas not here get a plain goal with priority «baja»."""
HABIT = (
    "habits:\n"
    "  - id: lectura\n"
    '    name: "Lectura"\n'
    "    priority: medium\n"
    "    duration: 20m\n"
    "    moment: evening\n"
    '    todoist_task: "Leer"\n'
)
"""An invented habit, for the first line `habits: []` of the template."""


def area_text(name: str) -> str:
    return AREA_TEXT.get(name, f"Meta: algo de {name.lower()} (inventado)\nPrioridad: baja\n")


def objectives(areas: dict[str, str]) -> str:
    """`objetivos.md` with one section per area, in the order given."""
    return "# Objetivos\n" + "".join(f"\n## {name}\n\n{text}" for name, text in areas.items())


def finished_objectives() -> str:
    return objectives({name: area_text(name) for name in AREA_NAMES})


def intentions_with_habit() -> str:
    text = template("intenciones.yaml")
    assert text.count("\nhabits: []") == 1
    return text.replace("\nhabits: []", "\n" + HABIT.rstrip("\n"), 1)


def write_finished(config_dir: Path) -> Path:
    """The five files with everything the onboarding asks for."""
    write_personal(config_dir)
    (config_dir / "objetivos.md").write_text(finished_objectives(), encoding="utf-8")
    (config_dir / "intenciones.yaml").write_text(intentions_with_habit(), encoding="utf-8")
    return config_dir
