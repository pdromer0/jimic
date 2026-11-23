"""Lo que toca ahora (decisión 1.15b-a): para cada intención, `due_now` y su `why`, del código.

Los valores de las horas son los del plan, calculados a mano: plan 07:30–09:30 (hora por defecto
08:00), aviso a las 19:00 con su tramo de 15 minutos y gimnasio a las 12:00 con 45 de margen.
"""

import json
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from heartbeat_data import (
    CLOSE,
    GYM,
    HABIT,
    MADRID,
    PLAN,
    PLANTS,
    add_notice,
    day_state,
    intentions,
    madrid,
    outgoing,
)

from jimic.heartbeat.decide import fixed_block
from jimic.heartbeat.due import due_now
from jimic.heartbeat.key_tasks import KeyTaskStatus
from jimic.heartbeat.limits import WaitingNotices
from jimic.memory.database import open_database
from jimic.memory.schemas import DailyPlan, DayClose, KeyTask, Notice

THURSDAY = date(2030, 1, 10)
PLAN_I = DailyPlan.model_validate(PLAN)
CLOSE_I = DayClose.model_validate({**CLOSE, "window": {"from": "21:00", "to": "22:30"}})
PLANTS_I = Notice.model_validate(PLANTS)
GYM_I = KeyTask.model_validate(GYM)


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.sqlite3")
    yield connection
    connection.close()


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2030, 1, 10, hour, minute, second, tzinfo=MADRID)


def due(intention: Any, now: datetime, **changes: Any) -> tuple[bool, str]:
    arguments: dict[str, Any] = {
        "left_today": 1,
        "waiting": False,
        "key": None,
        "messages_left": 5,
        "close_fits": True,
    } | changes
    result = due_now(intention, now, **arguments)
    return result.due, result.why


def gym(**changes: Any) -> KeyTaskStatus:
    """El gimnasio de hoy a las 12:00: se puede insistir desde las 12:45."""
    fields: dict[str, Any] = {
        "intent": "insistir_gimnasio",
        "task": None,
        "done_by": None,
        "rescheduled": False,
        "not_today": False,
        "paused_until": None,
        "insist_from": at(12, 45),
        "tone_level": 1,
        "sent_today": 0,
    } | changes
    return KeyTaskStatus(**fields)


# --- Plan y cierre: su ventana, no su hora por defecto -----------------------------------------


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (at(7, 29), (False, "before_window")),
        (at(7, 29, 59), (False, "before_window")),
        (at(7, 30), (True, "in_window")),
        (at(9, 30), (True, "in_window")),
        (at(9, 30, 40), (True, "in_window")),
        (at(9, 31), (True, "late")),
        (at(11, 15), (True, "late")),
    ],
)
def test_el_plan_toca_en_su_ventana_y_despues_si_no_ha_salido(moment, expected):
    assert due(PLAN_I, moment) == expected


def test_el_plan_a_las_7_45_toca_dentro_de_la_ventana():
    """Versión incorrecta 1: el plan no espera a su hora por defecto (08:00), que es la del plan
    B. Era el fallo de l03 y l35."""
    assert due(PLAN_I, at(7, 45)) == (True, "in_window")


def test_antes_de_la_ventana_el_plan_no_toca():
    """Lo que sigue verde con la versión incorrecta 1: antes de las 7:30 y de las 8:00."""
    assert due(PLAN_I, at(7, 0)) == (False, "before_window")


def test_la_hora_por_defecto_no_adelanta_ni_retrasa_el_plan():
    """Con la hora por defecto fuera de la ventana, la ventana manda igual."""
    early = DailyPlan.model_validate({**PLAN, "default_time": "06:00"})
    late = DailyPlan.model_validate({**PLAN, "default_time": "10:30"})
    for intention in (early, late):
        assert due(intention, at(7, 0)) == (False, "before_window")
        assert due(intention, at(9, 0)) == (True, "in_window")
        assert due(intention, at(9, 45)) == (True, "late")


def test_el_plan_ya_atendido_hoy_no_toca():
    assert due(PLAN_I, at(9), left_today=0) == (False, "sent_today")


def test_una_ventana_que_cruza_la_medianoche_nunca_es_antes():
    crossing = DayClose.model_validate({**CLOSE, "window": {"from": "23:00", "to": "01:00"}})
    assert due(crossing, at(23, 30)) == (True, "in_window")
    assert due(crossing, at(0, 30)) == (True, "in_window")
    assert due(crossing, at(12)) == (True, "late")


def test_sin_ventana_toca_todo_el_dia_y_es_tardio_tras_su_hora():
    """Como `late_after`: sin ventana, tardío después de la hora por defecto."""
    bare = DayClose.model_validate(CLOSE)
    assert due(bare, at(8)) == (True, "in_window")
    assert due(bare, at(22, 1)) == (True, "late")


# --- Avisos: desde su hora, aunque se les pasara ------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (at(18, 59), (False, "before_time")),
        (at(19, 0), (True, "on_time")),
        (at(19, 14), (True, "on_time")),
        (at(19, 15), (True, "late")),
    ],
)
def test_un_aviso_toca_desde_su_hora(moment, expected):
    assert due(PLANTS_I, moment) == expected


def test_a_su_hora_el_aviso_toca():
    """Lo que sigue verde con la versión incorrecta 2."""
    assert due(PLANTS_I, at(19, 5)) == (True, "on_time")


def test_el_aviso_que_no_salio_sigue_tocando():
    """Versión incorrecta 2: el riego de las 19:00 que un evento tapó, a las 21:00 (l25)."""
    assert due(PLANTS_I, at(21)) == (True, "late")


def test_el_aviso_ya_enviado_o_en_la_cola_no_toca():
    assert due(PLANTS_I, at(19, 15), left_today=0) == (False, "sent_today")
    assert due(PLANTS_I, at(19, 15), left_today=0, waiting=True) == (False, "waiting")


# --- Tareas clave -------------------------------------------------------------------------------


def test_la_tarea_clave_toca_pasado_su_margen_y_no_antes():
    assert due(GYM_I, at(12, 44), key=gym()) == (False, "too_early")
    assert due(GYM_I, at(12, 45), key=gym()) == (True, "can_insist")


@pytest.mark.parametrize(
    ("changes", "why"),
    [
        ({"done_by": "todoist"}, "done"),
        ({"rescheduled": True}, "rescheduled"),
        ({"not_today": True}, "not_today"),
        ({"paused_until": at(14, 45)}, "paused"),
    ],
)
def test_una_tarea_clave_hecha_parada_o_en_pausa_no_toca(changes, why):
    """Versión incorrecta 5: `due_now` mira el `refusal` de la 1.15."""
    assert due(GYM_I, at(13, 30), key=gym(**changes)) == (False, why)


def test_hecha_y_con_el_tope_gana_hecha():
    assert due(GYM_I, at(15), key=gym(done_by="button"), left_today=0) == (False, "done")


def test_con_el_tope_de_avisos_la_tarea_clave_no_toca():
    assert due(GYM_I, at(15), key=gym(sent_today=3), left_today=0) == (False, "insist_cap")


# --- Cupo (decisión 78) ---------------------------------------------------------------------------


def test_sin_mensajes_libres_los_demas_no_caben():
    """Lo que sigue verde con la versión incorrecta 3."""
    assert due(PLANTS_I, at(21), messages_left=0) == (False, "no_room")
    assert due(PLAN_I, at(9), messages_left=0) == (False, "no_room")
    assert due(GYM_I, at(13), key=gym(), messages_left=0) == (False, "no_room")


def test_el_cierre_usa_su_hueco():
    """Versión incorrecta 3: sin mensajes libres, el cierre toca con el hueco que se le guarda."""
    assert due(CLOSE_I, at(21, 30), messages_left=0, close_fits=True) == (True, "in_window")


def test_sin_hueco_tampoco_el_cierre():
    assert due(CLOSE_I, at(21, 30), messages_left=0, close_fits=False) == (False, "no_room")


def test_lo_que_aun_no_toca_dice_su_motivo_y_no_el_del_cupo():
    assert due(PLANTS_I, at(18), messages_left=0) == (False, "before_time")


# --- En el estado del día ----------------------------------------------------------------------


def test_el_estado_calcula_el_cupo_con_el_hueco_del_cierre(conn):
    """Tope 8 con el cierre pendiente: con 7 mensajes, el riego no cabe y el cierre sí."""
    for number in range(7):
        moment = madrid("2030-01-10T08:00") + timedelta(hours=number)
        add_notice(conn, "otra", moment, number + 1)
    close = {**CLOSE, "window": {"from": "21:00", "to": "22:30"}}
    state = day_state(conn, madrid("2030-01-10T21:30"), intentions(close, PLANTS))
    whys = {item.id: (item.due_now, item.why) for item in state.intentions}
    assert whys == {"cierre_del_dia": (True, "in_window"), "regar": (False, "no_room")}
    assert state.due == frozenset({"cierre_del_dia"})


def test_el_estado_marca_lo_que_espera(conn):
    queue = WaitingNotices()
    queue.add(outgoing("regar"), THURSDAY)
    state = day_state(conn, madrid("2030-01-10T19:15"), intentions(PLANTS), queue=queue)
    (regar,) = state.intentions
    assert (regar.due_now, regar.why) == (False, "waiting")


def test_el_modelo_no_recibe_horas_que_comparar(conn):
    """Ni ventanas ni horas por defecto en el bloque fijo o en el estado, ni `insist_from` en la
    tarea clave: recibe `due_now` y `why` de cada intención."""
    its = intentions(PLAN, GYM, PLANTS, habits=[HABIT])
    moment = madrid("2030-01-10T12:50")
    block = fixed_block(its, moment)
    document = json.loads(day_state(conn, moment, its).to_json())
    for text in (block, json.dumps(document)):
        for field in ('"window"', '"default_time"', '"insist_from"'):
            assert field not in text
    assert {item["id"]: (item["due_now"], item["why"]) for item in document["intentions"]} == {
        "plan_del_dia": (True, "late"),
        "insistir_gimnasio": (False, "too_early"),
        "regar": (False, "before_time"),
    }
