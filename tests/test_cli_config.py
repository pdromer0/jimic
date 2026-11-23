"""`jimic config check` e `init`: salida, códigos y ningún contenido de los ficheros en pantalla."""

import os
from pathlib import Path

import pytest

import jimic.cli
from jimic.memory.personal import template
from jimic.paths import resolve_paths

HABIT = """\
habits:
  - id: lectura
    name: "Lectura"
    priority: medium
    duration: 20m
    moment: evening
    todoist_task: "Leer"
"""

KEY_TASK = """\
  - id: insistir_lectura
    type: key_task
    purpose: "Que no se me pase"
    habit: {habit}
    grace: 45m
    max_per_day: 3
    default_time: "21:00"
    priority: medium

habits:"""


def intentions(*, habit: str = "lectura", extra_habit: str = "") -> str:
    """The template with one habit (plus `extra_habit`) and a key task pointing at `habit`."""
    text = template("intenciones.yaml").replace("habits: []", HABIT.rstrip() + extra_habit, 1)
    return text.replace("\nhabits:", "\n" + KEY_TASK.format(habit=habit), 1)


ROUTINE = "## Horario base\n## Comidas\n## Jornada\n## Fines de semana\n"

SCENARIOS = {
    "zx81": (
        "intenciones.yaml",
        template("intenciones.yaml").replace(
            'default_time: "08:00"', 'default_time: "valor-inventado-zx81"', 1
        ),
        "intentions[0].default_time: hora no válida",
    ),
    "zx82": (
        "limites.yaml",
        template("limites.yaml") + "clave-inventada-zx82: 1\n",
        "clave desconocida (no se muestra)",
    ),
    "zx83": (
        "intenciones.yaml",
        intentions(habit="habito_inventado_zx83"),
        "intentions[2].habit: no apunta a ningún hábito",
    ),
    "zx84": ("limites.yaml", "a: !!int zx84\n", "no se puede leer como YAML (ValueError)"),
    "zx85": ("limites.yaml", "a: *zx85\n", "no se puede leer como YAML (ComposerError)"),
    "zx86": (
        "intenciones.yaml",
        intentions(extra_habit="\n" + HABIT.replace("habits:\n", "").rstrip()).replace(
            "id: lectura", "id: habito_zx86"
        ),
        "habits[1].id: id repetido",
    ),
    "zx87": ("objetivos.md", "## Salud\n- objetivo-inventado-zx87\n", "objetivo sin"),
    "zx88": (
        "preferencias.md",
        "## Iconos\n| Tarea | Icono |\n| --- | --- |\n| a | b | zx88 |\n",
        "no tiene dos columnas",
    ),
    "zx89": (
        "rutina.md",
        ROUTINE + "## Horario base\ntexto-inventado-zx89\n",
        "la sección «Horario base» está repetida",
    ),
    "zx91": (
        "limites.yaml",
        "zx91: 1\nzx91: 2\n",
        "no se puede leer como YAML (DuplicateKeyError)",
    ),
    "zx92": (
        "limites.yaml",
        template("limites.yaml").replace("  log_idea: free", "  herramienta_zx92: free"),
        "tool_levels: valor no permitido",
    ),
}


@pytest.fixture
def config_dir() -> Path:
    return resolve_paths(os.environ).config_dir


def run(argv: list[str], capsys) -> tuple[int, str, str]:
    code = jimic.cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_init_y_despues_check_sale_con_0(config_dir, capsys):
    code, out, err = run(["config", "init"], capsys)
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        f"Ficheros personales en {config_dir}:",
        "- intenciones.yaml: creado",
        "- limites.yaml: creado",
        "- rutina.md, objetivos.md, preferencias.md:"
        " los crea la puesta en marcha (/puesta_en_marcha)",
    ]
    code, out, err = run(["config", "check"], capsys)
    assert (code, err) == (0, "")
    assert out.splitlines()[1:] == [
        "- intenciones.yaml: correcto",
        "- limites.yaml: correcto",
        "- rutina.md: aún no existe: lo crea la puesta en marcha (/puesta_en_marcha)",
        "- objetivos.md: aún no existe: lo crea la puesta en marcha (/puesta_en_marcha)",
        "- preferencias.md: aún no existe: lo crea la puesta en marcha (/puesta_en_marcha)",
    ]


def test_check_sin_ficheros_sale_con_1(capsys):
    code, out, _ = run(["config", "check"], capsys)
    assert code == jimic.cli.CHECK_FAILED
    assert "- limites.yaml: no existe: créalo con `jimic config init`" in out.splitlines()


def test_la_raiz_la_elige_jimic_entorno(monkeypatch, fake_home, capsys):
    monkeypatch.setenv("JIMIC_ENTORNO", "dev")
    assert run(["config", "init"], capsys)[0] == 0
    assert (fake_home / ".config" / "jimic-dev" / "limites.yaml").is_file()
    assert not (fake_home / ".config" / "jimic").exists()


def test_un_entorno_invalido_sale_con_el_error_de_arranque(monkeypatch, capsys):
    monkeypatch.setenv("JIMIC_ENTORNO", "pruebas")
    code, out, err = run(["config", "check"], capsys)
    assert (code, out) == (jimic.cli.SETUP_ERROR, "")
    assert err.startswith("jimic: JIMIC_ENTORNO")


def test_init_sin_permiso_sale_con_el_error_de_arranque(fake_home, capsys):
    (fake_home / ".config").mkdir(mode=0o500)
    try:
        code, out, err = run(["config", "init"], capsys)
    finally:
        (fake_home / ".config").chmod(0o700)
    assert (code, out) == (jimic.cli.SETUP_ERROR, "")
    assert err == "jimic: no se pueden crear los ficheros (PermissionError)\n"


@pytest.mark.parametrize("marker", SCENARIOS)
def test_ningun_valor_inventado_sale_en_jimic_config_check(marker, config_dir, capsys):
    """Pilla `input_value`, `str(exc)` del cargador y una clave de Pedro nombrada en la ruta.

    Cada caso comprueba antes que su valor está en el fichero y que el motivo esperado sale:
    sin eso, el test pasaría también si el problema no se detectara.
    """
    name, text, reason = SCENARIOS[marker]
    run(["config", "init"], capsys)
    for markdown in ("rutina.md", "objetivos.md", "preferencias.md"):
        (config_dir / markdown).write_text(
            ROUTINE if markdown == "rutina.md" else template_free(markdown), encoding="utf-8"
        )
    (config_dir / name).write_text(text, encoding="utf-8")
    assert marker in (config_dir / name).read_text(encoding="utf-8")
    code, out, err = run(["config", "check"], capsys)
    assert code == jimic.cli.CHECK_FAILED
    assert reason in out
    assert marker not in (out + err).lower()


def template_free(name: str) -> str:
    """A valid Markdown file for the scenarios that break another one."""
    if name == "objetivos.md":
        return "## Salud\n- [declarado] Dormir más\n"
    return "## Iconos\n| Tarea | Icono |\n| --- | --- |\n| Leer | 📚 |\n"


def test_check_dice_un_spacing_que_no_es_mayor_que_0(config_dir, capsys):
    """Decisión 149: `spacing` es opcional, y si está, mayor que 0; `config check` lo dice."""
    run(["config", "init"], capsys)
    for markdown in ("rutina.md", "objetivos.md", "preferencias.md"):
        (config_dir / markdown).write_text(
            ROUTINE if markdown == "rutina.md" else template_free(markdown), encoding="utf-8"
        )
    with_spacing = intentions().replace("    grace: 45m\n", "    grace: 45m\n    spacing: 1h\n")
    (config_dir / "intenciones.yaml").write_text(with_spacing, encoding="utf-8")
    assert run(["config", "check"], capsys)[0] == 0
    zero = with_spacing.replace("spacing: 1h", "spacing: 0m")
    (config_dir / "intenciones.yaml").write_text(zero, encoding="utf-8")
    code, out, _ = run(["config", "check"], capsys)
    assert code == jimic.cli.CHECK_FAILED
    assert "intentions[2].spacing: la duración debe ser mayor que 0" in out
