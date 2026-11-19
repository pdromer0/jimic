"""Ficheros personales: `check` da fichero, línea y motivo; `init` crea los YAML sin pisar."""

import os
import stat
from datetime import time
from pathlib import Path

import pytest
from ruamel.yaml.comments import CommentedMap

from jimic.memory.personal import (
    PersonalFileError,
    check,
    has_errors,
    init,
    render_check,
    save_yaml_file,
    template,
)
from jimic.memory.problems import Problem
from jimic.memory.schemas import Intentions, cross_check
from jimic.memory.yaml_io import dump_yaml, parse_yaml, quoted_hour

YAML_FILES = ("intenciones.yaml", "limites.yaml")
MARKDOWN_FILES = ("rutina.md", "objetivos.md", "preferencias.md")

INVALID_INTENTIONS = """\
# Intenciones inventadas
habits:
  - id: gimnasio
    name: "Gimnasio"
    priority: high
    duration: 75m
    moment: midday
    todoist_task: "Gimnasio"
  - id: lectura
    name: "Lectura"
    duration: 20m
    moment: evening
    todoist_task: "Leer"
intentions:
  - id: plan_del_dia
    type: daily_plan
    purpose: "Plan"
    window: {from: "07:30", to: 7h}
    default_time: 25:00
    priority: high
"""
"""Hábito sin prioridad en la línea 9, `7h` en la 18 y `25:00` en la 19 (contadas a mano)."""

UNKNOWN_HABIT = """\
habits:
  - id: lectura
    name: "Lectura"
    priority: medium
    duration: 20m
    moment: evening
    todoist_task: "Leer"
intentions:
  - id: insistir
    type: key_task
    purpose: "Que no se me pase"
    habit: habito_inventado
    grace: 45m
    max_per_day: 3
    default_time: "13:30"
    priority: high
"""
"""`habit:` en la línea 12."""


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """An empty config root, apart from the fake home that `config_dir` also holds."""
    return tmp_path / "config"


def write(directory: Path, name: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def report_of(config_dir: Path, name: str):
    return next(report for report in check(config_dir) if report.name == name)


def uncomment(block: str) -> str:
    """Remove the `# ` of the commented example lines, as the template tells Pedro to."""
    return "\n".join(line[2:] for line in block.splitlines() if line.startswith("#   "))


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_init_crea_los_dos_yaml_privados_y_no_los_markdown(tmp_path):
    """También las carpetas intermedias que faltan; la última, privada."""
    config_dir = tmp_path / "config" / "jimic"
    assert init(config_dir) == [("intenciones.yaml", True), ("limites.yaml", True)]
    assert sorted(p.name for p in config_dir.iterdir()) == sorted(YAML_FILES)
    assert mode(config_dir) == 0o700
    for name in YAML_FILES:
        assert mode(config_dir / name) == 0o600
        assert (config_dir / name).read_text(encoding="utf-8") == template(name)


def test_init_da_0600_aunque_la_umask_sea_permisiva(config_dir):
    previous = os.umask(0o000)
    try:
        init(config_dir)
    finally:
        os.umask(previous)
    assert {mode(config_dir / name) for name in YAML_FILES} == {0o600}


def test_init_nunca_sobrescribe_un_fichero_que_ya_existe(config_dir):
    """Pilla abrir con truncado: el fichero de Pedro se quedaría con la plantilla."""
    mine = write(config_dir, "limites.yaml", "# mis límites, inventados\n")
    os.utime(mine, (1_000_000_000, 1_000_000_000))
    assert init(config_dir) == [("intenciones.yaml", True), ("limites.yaml", False)]
    assert mine.read_text(encoding="utf-8") == "# mis límites, inventados\n"
    assert mine.stat().st_mtime == 1_000_000_000
    assert init(config_dir) == [("intenciones.yaml", False), ("limites.yaml", False)]


def test_las_plantillas_generadas_pasan_la_comprobacion(config_dir):
    init(config_dir)
    reports = check(config_dir)
    assert [(r.name, r.status) for r in reports] == [
        ("intenciones.yaml", "ok"),
        ("limites.yaml", "ok"),
        *((name, "pending") for name in MARKDOWN_FILES),
    ]
    assert not has_errors(reports)


def test_los_ejemplos_comentados_de_la_plantilla_son_validos():
    """Si el esquema cambia y el ejemplo no, Pedro copiaría un ejemplo que no vale."""
    head, tail = template("intenciones.yaml").split("habits: []", 1)
    habit_block, key_task_block = tail.split("# Ejemplo inventado de tarea clave")
    text = f"{head.rstrip()}\n{uncomment(key_task_block)}\nhabits:\n{uncomment(habit_block)}\n"
    model = Intentions.model_validate(parse_yaml(text))
    assert [h.id for h in model.habits] == ["lectura"]
    assert [i.id for i in model.intentions][-1] == "insistir_lectura"
    assert cross_check(model) == []


def test_sin_ficheros_faltan_los_yaml_y_los_markdown_quedan_pendientes(config_dir):
    reports = check(config_dir / "no-existe")
    assert [r.status for r in reports] == ["missing", "missing", "pending", "pending", "pending"]
    assert has_errors(reports)


def test_una_hora_invalida_o_un_habito_sin_prioridad_dan_fichero_y_linea(config_dir):
    """El criterio del issue (con el cambio de la revisión del chat): `7h` y `25:00`."""
    init(config_dir)
    write(config_dir, "intenciones.yaml", INVALID_INTENTIONS)
    report = report_of(config_dir, "intenciones.yaml")
    assert report.status == "invalid"
    assert report.problems == (
        Problem(9, "habits[1].priority: falta"),
        Problem(18, "intentions[0].window.to: hora no válida (H:MM o HH:MM, de 00:00 a 23:59)"),
        Problem(19, "intentions[0].default_time: hora no válida (H:MM o HH:MM, de 00:00 a 23:59)"),
    )
    hour = "hora no válida (H:MM o HH:MM, de 00:00 a 23:59)"
    assert render_check(config_dir, check(config_dir)).splitlines()[2:5] == [
        "  intenciones.yaml:9: habits[1].priority: falta",
        f"  intenciones.yaml:18: intentions[0].window.to: {hour}",
        f"  intenciones.yaml:19: intentions[0].default_time: {hour}",
    ]


def test_una_key_task_con_un_habito_que_no_existe_da_su_linea(config_dir):
    write(config_dir, "intenciones.yaml", UNKNOWN_HABIT)
    assert report_of(config_dir, "intenciones.yaml").problems == (
        Problem(12, "intentions[0].habit: no apunta a ningún hábito"),
    )


def test_las_claves_de_pedro_no_salen_en_la_ruta(config_dir):
    """Una clave desconocida y una herramienta que no existe: se señala dónde, no cuál."""
    text = template("limites.yaml").replace(
        "  log_idea: free", "  log_idea: free\n  borrar_todo: free\n  extra_inventada: 1"
    )
    write(config_dir, "limites.yaml", text)
    assert report_of(config_dir, "limites.yaml").problems == (
        Problem(22, "tool_levels: valor no permitido"),
        Problem(23, "tool_levels: valor no permitido"),
        Problem(23, "tool_levels: valor no permitido"),
    )
    write(config_dir, "limites.yaml", template("limites.yaml") + "purpose: 1\n")
    assert report_of(config_dir, "limites.yaml").problems == (
        Problem(23, "clave desconocida (no se muestra)"),
    )


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "el fichero está vacío"),
        ("# solo comentarios\n", "el fichero está vacío"),
        ("- una lista\n", "debe ser un mapa de claves"),
        ("un texto\n", "debe ser un mapa de claves"),
        ("a: [1, 2\nb: 3\n", "no se puede leer como YAML (ParserError), cerca de la línea 2"),
        ("a: !!int x\n", "no se puede leer como YAML (ValueError)"),
    ],
)
def test_un_yaml_vacio_o_que_no_es_un_mapa_se_explica(config_dir, text, reason):
    write(config_dir, "limites.yaml", text)
    assert [p.reason for p in report_of(config_dir, "limites.yaml").problems] == [reason]


def test_un_fichero_que_no_es_utf8_o_no_se_puede_leer_da_solo_el_tipo(config_dir):
    config_dir.mkdir()
    (config_dir / "rutina.md").write_bytes(b"## Horario base \xff\n")
    assert report_of(config_dir, "rutina.md").problems == (
        Problem(None, "no se puede leer (UnicodeDecodeError)"),
    )
    (config_dir / "objetivos.md").mkdir()
    assert report_of(config_dir, "objetivos.md").problems == (
        Problem(None, "no se puede leer (IsADirectoryError)"),
    )


def test_los_markdown_presentes_se_comprueban(config_dir):
    write(config_dir, "objetivos.md", "## Salud\n- sin etiqueta\n")
    report = report_of(config_dir, "objetivos.md")
    assert report.status == "invalid"
    assert report.problems == (Problem(2, "objetivo sin [medible], [declarado] ni [enlazado]"),)


@pytest.mark.parametrize("name", YAML_FILES)
def test_cambiar_un_valor_de_una_plantilla_solo_cambia_su_linea(config_dir, name):
    """Ida y vuelta sobre las plantillas: comentarios, orden y comillas, intactos."""
    init(config_dir)
    path = config_dir / name
    doc = parse_yaml(path.read_text(encoding="utf-8"))
    assert isinstance(doc, CommentedMap)
    if name == "limites.yaml":
        doc["max_messages_per_day"] = 6
        old, new = "max_messages_per_day: 8 ", "max_messages_per_day: 6 "
    else:
        doc["intentions"][1]["default_time"] = quoted_hour(time(21, 30))
        old, new = 'default_time: "22:00"', 'default_time: "21:30"'
    save_yaml_file(path, doc)
    original = template(name).splitlines()
    written = path.read_text(encoding="utf-8").splitlines()
    changed = [i for i, (a, b) in enumerate(zip(original, written, strict=True)) if a != b]
    assert len(changed) == 1
    assert written[changed[0]] == original[changed[0]].replace(old, new)
    assert mode(path) == 0o600


def test_guardar_un_documento_invalido_no_escribe_nada(config_dir):
    init(config_dir)
    path = config_dir / "limites.yaml"
    doc = parse_yaml(path.read_text(encoding="utf-8"))
    assert isinstance(doc, CommentedMap)
    doc["max_deletions_per_day"] = "valor-inventado-zx90"
    with pytest.raises(PersonalFileError) as excinfo:
        save_yaml_file(path, doc)
    assert excinfo.value.problems == (
        Problem(9, "max_deletions_per_day: debe ser un número entero"),
    )
    assert "zx90" not in str(excinfo.value)
    assert path.read_text(encoding="utf-8") == template("limites.yaml")
    assert sorted(p.name for p in config_dir.iterdir()) == sorted(YAML_FILES)
    assert dump_yaml(doc) != template("limites.yaml")
