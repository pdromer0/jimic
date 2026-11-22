"""Esquemas de `intenciones.yaml` y `limites.yaml`: estrictos, con horas y duraciones propias."""

from datetime import time, timedelta
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from jimic.memory.schemas import (
    TOOL_NAMES,
    DailyPlan,
    DayClose,
    Intentions,
    Limits,
    cross_check,
)


def habit(**changes: Any) -> dict[str, Any]:
    return {
        "id": "lectura",
        "name": "Lectura inventada",
        "priority": "medium",
        "duration": "20m",
        "moment": "evening",
        "todoist_task": "Leer un rato",
    } | changes


def key_task(**changes: Any) -> dict[str, Any]:
    return {
        "id": "insistir_lectura",
        "type": "key_task",
        "purpose": "Que no se me pase",
        "habit": "lectura",
        "grace": "45m",
        "max_per_day": 3,
        "default_time": "21:00",
        "priority": "high",
    } | changes


def notice(**changes: Any) -> dict[str, Any]:
    return {
        "id": "aviso",
        "type": "notice",
        "purpose": "Un aviso inventado",
        "days": ["mon", "fri"],
        "default_time": "13:30",
        "priority": "low",
    } | changes


def limits(**changes: Any) -> dict[str, Any]:
    return {
        "max_messages_per_day": 8,
        "quiet_hours": {"from": "23:00", "to": "07:00"},
        "silent_during_events": True,
        "group_window": "30m",
        "quiet_while_chatting": "10m",
        "max_insist_per_task": 3,
        "max_deletions_per_day": 5,
        "budget": {
            "monthly_target_usd": 30,
            "warn_at": 0.8,
            "daily_cap_usd": 3,
            "max_session_usd": 0.5,
        },
        "tool_levels": {name: "free" for name in TOOL_NAMES},
        "sensitive_categories": {},
    } | changes


def errors(model: type[BaseModel], data: dict[str, Any]) -> list[tuple[tuple[str | int, ...], str]]:
    """Return `(loc, type)` of each error, failing if the data validates."""
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(data)
    return [(tuple(e["loc"]), e["type"]) for e in excinfo.value.errors(include_input=False)]


def intentions_with(*, intentions=(), habits=()) -> dict[str, Any]:
    return {"intentions": list(intentions), "habits": list(habits)}


@pytest.mark.parametrize(
    ("text", "expected"),
    [("7:00", time(7)), ("07:00", time(7)), ("00:00", time(0)), ("23:59", time(23, 59))],
)
def test_una_hora_valida_se_lee_con_o_sin_cero_inicial(text, expected):
    """Pilla rechazar `7:00`, que YAML 1.2 lee como texto sin comillas (1.3-a)."""
    model = Intentions.model_validate(intentions_with(intentions=[notice(default_time=text)]))
    assert model.intentions[0].default_time == expected


@pytest.mark.parametrize(
    "value", ["24:00", "25:00", "7h", "7:0", "007:00", "7:60", " 7:00", "7:00\n", 420, ""]
)
def test_una_hora_con_formato_invalido_es_un_error(value):
    """Pilla aceptar 24–29, un número (`7:00` con YAML 1.1 llega como 420) o un salto final."""
    data = intentions_with(intentions=[notice(default_time=value)])
    assert errors(Intentions, data) == [
        (("intentions", 0, "notice", "default_time"), "hour_format")
    ]


def test_una_hora_con_digitos_no_ascii_es_un_error():
    """Pilla `\\d` en lugar de `[0-9]`: `\\d` acepta los dígitos arábigos."""
    data = intentions_with(intentions=[notice(default_time="٧:٠٠")])
    assert errors(Intentions, data) == [
        (("intentions", 0, "notice", "default_time"), "hour_format")
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("75m", timedelta(minutes=75)),
        ("2h", timedelta(hours=2)),
        ("0m", timedelta(0)),
        ("99999999m", timedelta(days=69444, hours=10, minutes=39)),
    ],
)
def test_una_duracion_valida_se_lee_en_minutos_u_horas(text, expected):
    model = Limits.model_validate(limits(group_window=text))
    assert model.group_window == expected


@pytest.mark.parametrize(
    "value", ["1h30m", "75", "75 m", "-5m", "1" + "0" * 30 + "m", "1" * 5000 + "m", 30]
)
def test_una_duracion_invalida_o_enorme_es_un_error_y_no_un_traceback(value):
    """Pilla el `OverflowError` de `timedelta` y el `ValueError` de `int` con 5000 cifras."""
    assert errors(Limits, limits(group_window=value)) == [(("group_window",), "duration_format")]


def test_un_habito_sin_prioridad_es_un_error():
    data = intentions_with(habits=[habit(), {k: v for k, v in habit().items() if k != "priority"}])
    assert errors(Intentions, data) == [(("habits", 1, "priority"), "missing")]


def test_los_campos_del_habito_tienen_su_formato():
    """Pilla un id con mayúsculas o guiones, un texto vacío y una duración de cero."""
    data = intentions_with(
        habits=[habit(id="Lectura-1", name="", todoist_task="", duration="0m", moment="noche")]
    )
    assert errors(Intentions, data) == [
        (("habits", 0, "id"), "string_pattern_mismatch"),
        (("habits", 0, "name"), "string_too_short"),
        (("habits", 0, "duration"), "duration_not_positive"),
        (("habits", 0, "moment"), "literal_error"),
        (("habits", 0, "todoist_task"), "string_too_short"),
    ]


def test_un_id_con_salto_de_linea_final_es_un_error():
    """Pilla un patrón con `$` de Python, que acepta `"lectura\\n"` (llega de un bloque `|`)."""
    data = intentions_with(habits=[habit(id="lectura\n")])
    assert errors(Intentions, data) == [(("habits", 0, "id"), "string_pattern_mismatch")]


def test_las_intenciones_se_distinguen_por_su_tipo():
    """Pilla un `type` desconocido aceptado y `task:`, que la 1.3-c sustituye por `habit:`."""
    data = intentions_with(
        intentions=[notice(type="recordatorio"), key_task(task="Leer")], habits=[habit()]
    )
    assert errors(Intentions, data) == [
        (("intentions", 0), "union_tag_invalid"),
        (("intentions", 1, "key_task", "task"), "extra_forbidden"),
    ]


def test_las_intenciones_de_serie_son_validas():
    """El plan y el cierre del día, con ventana o sin ella."""
    plan = {
        "id": "plan_del_dia",
        "type": "daily_plan",
        "purpose": "Proponer el plan",
        "window": {"from": "07:30", "to": "9:30"},
        "default_time": "08:00",
        "priority": "high",
    }
    close = {
        "id": "cierre_del_dia",
        "type": "day_close",
        "purpose": "Cerrar el día",
        "default_time": "22:00",
        "priority": "high",
        "enabled": False,
    }
    model = Intentions.model_validate(intentions_with(intentions=[plan, close]))
    first, second = model.intentions
    assert isinstance(first, DailyPlan) and isinstance(second, DayClose)
    assert first.window is not None
    assert (first.window.start, first.window.end) == (time(7, 30), time(9, 30))
    assert second.enabled is False


@pytest.mark.parametrize("value", ["8", True, 8.0])
def test_un_tope_que_no_es_un_entero_es_un_error(value):
    """Pilla el modo laxo de Pydantic, que convierte `"8"` en 8 y `true` en 1."""
    assert errors(Limits, limits(max_deletions_per_day=value)) == [
        (("max_deletions_per_day",), "int_type")
    ]


def test_los_demas_tipos_tampoco_se_convierten():
    data = limits(silent_during_events="yes", budget=limits()["budget"] | {"daily_cap_usd": "3"})
    assert errors(Limits, data) == [
        (("silent_during_events",), "bool_type"),
        (("budget", "daily_cap_usd"), "float_type"),
    ]


def test_un_entero_vale_como_importe_y_un_tope_enorme_no_revienta():
    model = Limits.model_validate(limits(max_messages_per_day=10**30))
    assert model.budget.monthly_target_usd == 30.0
    assert model.max_messages_per_day == 10**30
    assert model.max_deletions_per_day == 5


@pytest.mark.parametrize(
    ("budget", "expected"),
    [
        ({"warn_at": -0.01}, ("warn_at", "greater_than_equal")),
        ({"warn_at": 1.01}, ("warn_at", "less_than_equal")),
        ({"warn_at": float("nan")}, ("warn_at", "finite_number")),
        ({"monthly_target_usd": float("inf")}, ("monthly_target_usd", "finite_number")),
        ({"max_session_usd": -1}, ("max_session_usd", "greater_than_equal")),
    ],
)
def test_el_presupuesto_tiene_sus_rangos(budget, expected):
    data = limits(budget=limits()["budget"] | budget)
    assert errors(Limits, data) == [(("budget", expected[0]), expected[1])]


@pytest.mark.parametrize("warn_at", [0, 1, 0.0, 1.0])
def test_el_aviso_vale_en_los_bordes(warn_at):
    Limits.model_validate(limits(budget=limits()["budget"] | {"warn_at": warn_at}))


def test_los_topes_negativos_y_las_horas_de_silencio_invalidas_son_errores():
    data = limits(max_insist_per_task=-1, quiet_hours={"from": "23:00", "to": "7h"})
    assert errors(Limits, data) == [
        (("quiet_hours", "to"), "hour_format"),
        (("max_insist_per_task",), "greater_than_equal"),
    ]


def test_tool_levels_solo_admite_las_seis_herramientas_y_dos_niveles():
    """Pilla una herramienta con efecto que se cuela y un nivel «con botón»."""
    data = limits(tool_levels={"delete_task": "free", "read_tasks": "button"})
    assert errors(Limits, data) == [
        (("tool_levels", "delete_task", "[key]"), "literal_error"),
        (("tool_levels", "read_tasks"), "literal_error"),
    ]


def test_las_categorias_sensibles_esperan_al_hito_4():
    assert errors(Limits, limits(sensitive_categories={"salud": "x"})) == [
        (("sensitive_categories",), "too_long")
    ]


def test_limites_no_lleva_chat_id_ni_le_falta_ninguna_clave():
    """Pilla `allowed_chat_id` aceptado (1.3-d) y un valor por defecto silencioso."""
    data = {k: v for k, v in limits(allowed_chat_id="123").items() if k != "max_deletions_per_day"}
    assert errors(Limits, data) == [
        (("max_deletions_per_day",), "missing"),
        (("allowed_chat_id",), "extra_forbidden"),
    ]


def test_una_key_task_que_apunta_a_un_habito_que_no_existe_se_rechaza():
    """Pilla el `habit:` sin comprobar; un id de intención no cuenta como hábito."""
    data = intentions_with(
        intentions=[key_task(), key_task(id="otra", habit="insistir_lectura")], habits=[habit()]
    )
    assert cross_check(Intentions.model_validate(data)) == [
        (("intentions", 1, "habit"), "unknown_habit")
    ]


def test_los_ids_repetidos_se_rechazan_en_la_segunda_aparicion():
    """Pilla dos hábitos con el mismo id; un hábito y una intención sí pueden compartirlo."""
    data = intentions_with(
        intentions=[key_task(id="lectura"), notice(), notice(days=["mon", "mon"])],
        habits=[habit(), habit(name="Otra"), habit(id="otro")],
    )
    assert cross_check(Intentions.model_validate(data)) == [
        (("habits", 1, "id"), "duplicate_id"),
        (("intentions", 2, "id"), "duplicate_id"),
        (("intentions", 2, "days"), "repeated_days"),
    ]


def test_un_fichero_correcto_no_tiene_problemas_cruzados():
    data = intentions_with(intentions=[key_task(), notice()], habits=[habit()])
    assert cross_check(Intentions.model_validate(data)) == []
