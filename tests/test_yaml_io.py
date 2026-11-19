"""YAML de ida y vuelta: horas como texto, líneas de cada valor y errores sin contenido."""

from datetime import time

import pytest
from ruamel.yaml.comments import CommentedMap

from jimic.memory.yaml_io import YamlSyntaxError, dump_yaml, line_of, parse_yaml, quoted_hour

INTENTIONS = """\
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
    purpose: "valor-inventado-zx81"
    window: {from: "07:30", to: "9:30"}
    default_time: 25:00
    priority: high
  - id: insistir
    type: key_task
    purpose: "Que no se me pase"
    habit: habito_inventado_zx83
    grace: 45m
    max_per_day: 3
    default_time: "13:30"
    priority: high
"""
"""Las líneas esperadas (9, 18, 19 y 24) se contaron a mano buscando cada marcador (plan, §4)."""

COMMENTED = """\
# limites.yaml inventado
max_messages_per_day: 8        # tope
quiet_hours: {from: "23:00", to: "07:00"}   # silencio
group_window: 30m              # agrupa
budget:                        # presupuesto
  # antes del objetivo
  monthly_target_usd: 30       # objetivo
  warn_at: 0.8                 # aviso
days: [mon, tue]               # días
# comentario final
"""


def load_map(text: str) -> CommentedMap:
    """Parse `text`, failing unless its root is a mapping."""
    doc = parse_yaml(text)
    assert isinstance(doc, CommentedMap)
    return doc


@pytest.mark.parametrize(
    ("text", "expected"), [("a: 7:00", "7:00"), ('a: "7:00"', "7:00"), ("a: 07:00", "07:00")]
)
def test_una_hora_llega_como_texto_con_o_sin_comillas(text, expected):
    """El hecho volátil de 1.3-a: con YAML 1.2, `7:00` sin comillas no es un número."""
    value = load_map(text)["a"]
    assert isinstance(value, str) and value == expected


def test_con_yaml_1_1_forzado_la_hora_llega_como_numero():
    """Por eso el esquema rechaza un número donde va una hora."""
    assert parse_yaml("%YAML 1.1\n---\na: 7:00\n") == {"a": 420}


@pytest.mark.parametrize(
    ("loc", "line"),
    [
        (("habits", 1, "priority"), 9),
        (("intentions", 0, "daily_plan", "window", "to"), 18),
        (("intentions", 0, "daily_plan", "default_time"), 19),
        (("intentions", 1, "habit"), 24),
        (("intentions", 7), 14),
        ((), 2),
    ],
)
def test_cada_ubicacion_da_su_linea(loc, line):
    """Pilla líneas contadas desde 0 y una clave que falta sin la línea de su padre."""
    assert line_of(parse_yaml(INTENTIONS), loc) == line


def test_sin_un_mapa_no_hay_linea():
    assert line_of(parse_yaml("# solo un comentario\n"), ("a",)) is None
    assert line_of(parse_yaml("texto\n"), ("a",)) is None


@pytest.mark.parametrize(
    ("text", "kind", "line"),
    [
        ("a: 1\nb: !!int zx84\n", "ValueError", None),
        ("a: 1\nb: !!float zx84\n", "ValueError", None),
        ("a: 1\nb: *zx84\n", "ComposerError", 2),
        ("a: 1\nb: zx84\x01\n", "ReaderError", None),
        ("a: 1\nb: !!timestamp zx84\n", "ConstructorError", 2),
        ("a: 1\nzx84: 1\nzx84: 2\n", "DuplicateKeyError", 3),
        ("a: 1\nb: [zx84, 2\nc: 3\nd: 4\n", "ParserError", 3),
    ],
)
def test_un_yaml_que_no_se_carga_da_solo_el_tipo_y_una_linea_cercana(text, kind, line):
    """Pilla capturar solo `YAMLError` (`!!int` lanza `ValueError`) y guardar `str(exc)`."""
    with pytest.raises(YamlSyntaxError) as excinfo:
        parse_yaml(text)
    error = excinfo.value
    assert (error.kind, error.line) == (kind, line)
    assert "zx84" not in str(error)
    assert error.__cause__ is None and error.__suppress_context__


def test_sin_cambios_se_escribe_igual():
    assert dump_yaml(parse_yaml(COMMENTED)) == COMMENTED


def test_cambiar_un_valor_solo_cambia_su_linea():
    """Pilla perder comentarios, comillas, orden o la lista en línea al escribir."""
    doc = load_map(COMMENTED)
    doc["budget"]["warn_at"] = 0.7
    doc["days"].append("fri")
    expected = COMMENTED.replace("warn_at: 0.8 ", "warn_at: 0.7 ").replace(
        "days: [mon, tue]               ", "days: [mon, tue, fri]          "
    )
    assert dump_yaml(doc) == expected


def test_una_hora_se_escribe_entre_comillas_aunque_antes_no_las_tuviera():
    doc = load_map("group_window: 30m   # agrupa\nhora: 7:00   # sin comillas\n")
    doc["hora"] = quoted_hour(time(7, 5))
    assert dump_yaml(doc) == 'group_window: 30m   # agrupa\nhora: "07:05" # sin comillas\n'
