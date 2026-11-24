"""Los botones del cierre (tarea 1.16): [Hecha], [La haré], [Mañana], la fila global, la
valoración y sus [Deshacer], sin modelo. Todoist es el doble detrás del lector y el escritor de
verdad; el calendario, uno fijo por día. Datos del e08 inventado (`close_data.py`).
"""

from typing import Any

import pytest
from close_data import FRIDAY, PLAN, THURSDAY, CloseWorld, at, raw_e08
from heartbeat_data import calendar_with, event
from todoist_data import raw_task

from jimic.channel.message import render
from jimic.memory import day_plans, key_marks
from jimic.memory.database import open_database

PLAN_IDS = frozenset(task.id for task in PLAN)


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def with_plan(conn) -> None:
    day_plans.record_plan(
        conn,
        THURSDAY,
        at("2030-01-10T07:45"),
        [(task_id, None) for task_id in sorted(PLAN_IDS)],
        {},
    )


def raw(w: CloseWorld, task_id: str) -> dict[str, Any]:
    found = w.api.find(task_id)
    assert found is not None
    return found


def undo(w: CloseWorld, result) -> int:
    """Show the follow-up with [Deshacer] as the channel would, and tap it."""
    (followup,) = result.messages
    approval_id = w.show(followup.message, followup.kind)
    w.tap(approval_id, 1, "undo")
    return approval_id


# --- El e08 entero ---------------------------------------------------------------------------


def test_el_e08_lo_declarado_ni_se_mueve_ni_se_marca_y_lo_pendiente_pasa_a_manana(conn):
    """Las notas: «Proyecto personal» y «Cena» → [La haré]; «Paseo 5 km» → [Mañana]; después,
    [Todo lo pendiente a mañana]. Valores a mano: el paseo a las 19:00 y las ofertas a las 14:30
    del viernes, sin pisar el gimnasio (13:00–14:15). Pilla la versión incorrecta 6 (la global
    mueve lo declarado)."""
    w = CloseWorld(conn)
    with_plan(conn)
    approval = w.show(w.paint(plan=PLAN_IDS))
    w.tap(approval, 2, "will")
    w.tap(approval, 4, "will")
    w.tap(approval, 3, "tmrw")
    result = w.tap(approval, None, "all")
    assert w.touched("t-proyecto") == [] and w.touched("t-cena") == []
    assert w.due("t-proyecto") == "2030-01-10T17:15:00"
    assert w.due("t-cena") == "2030-01-10T21:30:00"
    assert w.due("t-paseo") == "2030-01-11T19:00:00"
    assert w.due("t-ofertas") == "2030-01-11T14:30:00"
    assert w.due("t-gimnasio-viernes") == "2030-01-11T13:00:00"
    statuses = {i.task_id: i.status for i in day_plans.items(conn, THURSDAY)}
    assert statuses == {
        "t-meditacion": "pending",  # las hechas las anota el cierre, no los toques
        "t-comida": "pending",
        "t-trabajo": "pending",
        "t-ofertas": "moved",
        "t-proyecto": "will_do",
        "t-paseo": "moved",
        "t-cena": "will_do",
    }
    assert len(result.messages) == 1  # un [Deshacer] para la global
    text = render(w.message(approval), approval).text
    assert "2️⃣ ▫️ Proyecto personal (17:15) — 🗣️ la harás" in text
    assert "1️⃣ ▫️ Revisar ofertas (14:30) — ➡️ mañana 14:30" in text


def test_la_hare_nunca_mueve_ni_marca(conn):
    """Versión incorrecta 5: [La haré] marca la tarea. Pilla cualquier escritura en Todoist."""
    w = CloseWorld(conn)
    approval = w.show(w.paint())
    result = w.tap(approval, 2, "will")
    assert result.code == "resolved"
    assert w.touched("t-proyecto") == []
    assert raw(w, "t-proyecto")["checked"] is False


def test_hecha_la_marca_en_todoist_y_sube_la_cifra(conn):
    """Pilla un [Hecha] que no completa, o una cifra provisional que no sube."""
    w = CloseWorld(conn)
    with_plan(conn)
    approval = w.show(w.paint(plan=PLAN_IDS))
    assert "📈 Por ahora: 3 de 7 del plan" in render(w.message(approval), approval).text
    result = w.tap(approval, 4, "done")
    assert w.touched("t-cena") == ["complete_task"]
    assert "📈 Por ahora: 4 de 7 del plan" in render(w.message(approval), approval).text
    (item,) = [i for i in day_plans.items(conn, THURSDAY) if i.task_id == "t-cena"]
    assert (item.status, item.marked_at) == ("done", at("2030-01-10T21:30"))
    assert [f.message.items[0].options[0].key for f in result.messages] == ["undo"]


def test_hecha_y_su_deshacer_la_reabren(conn):
    w = CloseWorld(conn)
    with_plan(conn)
    approval = w.show(w.paint(plan=PLAN_IDS))
    undo(w, w.tap(approval, 4, "done"))
    assert raw(w, "t-cena")["checked"] is False
    (item,) = [i for i in day_plans.items(conn, THURSDAY) if i.task_id == "t-cena"]
    assert item.status == "pending"


def test_hecha_de_una_ya_completada_en_la_app_cuenta_sin_completarla_otra_vez(conn):
    """Pilla un [Hecha] que completa dos veces, o que no anota cuándo la marcó Pedro."""
    w = CloseWorld(conn)
    with_plan(conn)
    approval = w.show(w.paint(plan=PLAN_IDS))
    raw(w, "t-cena").update(checked=True, completed_at="2030-01-10T20:00:00.000000Z")
    result = w.tap(approval, 4, "done")
    assert w.touched("t-cena") == [] and result.messages == ()
    (item,) = [i for i in day_plans.items(conn, THURSDAY) if i.task_id == "t-cena"]
    assert (item.status, item.marked_at) == ("done", at("2030-01-10T21:00"))


def test_hecha_de_una_tarea_clave_la_marca_hecha_y_deshacer_quita_la_marca(conn):
    """Pilla una recurrente de tarea clave cerrada desde el cierre que el latido tomaría por un
    salto (sin marca `done`), o un [Deshacer] que deja la marca."""
    tasks = raw_e08()
    tasks.append(
        raw_task(
            "t-gimnasio",
            "Gimnasio",
            project_id="proyecto-inventado",
            due="2030-01-10T20:00:00",
            recurring=True,
        )
    )
    w = CloseWorld(conn, tasks)
    approval = w.show(w.paint(keys={"gimnasio": "insistir_gimnasio"}))
    message = w.message(approval)
    number = next(n for n, i in enumerate(message.items, 1) if "Gimnasio" in i.title)
    result = w.tap(approval, number, "done")
    assert key_marks.of_day(conn, THURSDAY).of("insistir_gimnasio").done
    assert w.due("t-gimnasio") == "2030-01-11T20:00:00"  # recurrente: pasa a mañana
    undo(w, result)
    assert not key_marks.of_day(conn, THURSDAY).of("insistir_gimnasio").done
    assert w.due("t-gimnasio") == "2030-01-10T20:00:00"


# --- [Mañana] y su [Deshacer] ----------------------------------------------------------------


def test_manana_no_pisa_el_gimnasio_de_las_13_00(conn):
    """Valor a mano: 13:30 (30 min) → 14:15. Versión incorrecta 7 ([Mañana] no mira mañana)."""
    tasks = [
        *raw_e08(),
        raw_task(
            "t-llamar",
            "Llamar al seguro",
            project_id="proyecto-inventado",
            due="2030-01-10T13:30:00",
            duration=30,
        ),
    ]
    w = CloseWorld(conn, tasks)
    approval = w.show(w.paint())
    w.tap(approval, 1, "tmrw")
    assert w.due("t-llamar") == "2030-01-11T14:15:00"


def test_manana_mira_el_calendario_de_manana(conn):
    w = CloseWorld(conn)
    w.calendar.days[FRIDAY] = calendar_with(event(at("2030-01-11T19:00"), minutes=90))
    approval = w.show(w.paint())
    w.tap(approval, 3, "tmrw")
    assert w.due("t-paseo") == "2030-01-11T20:30:00"
    assert w.calendar.reads == [FRIDAY]


def test_manana_y_su_deshacer_devuelven_la_tarea_a_hoy(conn):
    """Ajuste de Pedro (decisión 97): todo movimiento del código se puede deshacer."""
    w = CloseWorld(conn)
    with_plan(conn)
    approval = w.show(w.paint(plan=PLAN_IDS))
    result = w.tap(approval, 3, "tmrw")
    assert w.due("t-paseo") == "2030-01-11T19:00:00"
    undo(w, result)
    assert w.due("t-paseo") == "2030-01-10T19:00:00"
    (item,) = [i for i in day_plans.items(conn, THURSDAY) if i.task_id == "t-paseo"]
    assert item.status == "pending"


def test_con_una_tarea_clave_la_insistencia_puede_volver_tras_deshacer(conn):
    """Ajuste de Pedro. Versión incorrecta 15: el [Deshacer] no quita el `not_today` que puso
    el movimiento (decisión 145)."""
    w = CloseWorld(conn)
    key_marks.note_seen(conn, THURSDAY, "ofertas", "t-ofertas", at("2030-01-10T14:15"))
    approval = w.show(w.paint(keys={"revisar ofertas": "ofertas"}))
    result = w.tap(approval, 1, "tmrw")
    assert key_marks.of_day(conn, THURSDAY).of("ofertas").not_today
    undo(w, result)
    assert not key_marks.of_day(conn, THURSDAY).of("ofertas").not_today
    assert w.due("t-ofertas") == "2030-01-10T14:30:00"


def test_un_hoy_no_de_pedro_sigue_tras_deshacer_el_movimiento(conn):
    """Pilla un [Deshacer] que quita cualquier «hoy no» (el de Pedro no lleva tarea)."""
    w = CloseWorld(conn)
    key_marks.note_seen(conn, THURSDAY, "ofertas", "t-ofertas", at("2030-01-10T14:15"))
    key_marks.record(conn, THURSDAY, "ofertas", "not_today", at("2030-01-10T15:00"))
    approval = w.show(w.paint(keys={"revisar ofertas": "ofertas"}))
    undo(w, w.tap(approval, 1, "tmrw"))
    assert key_marks.of_day(conn, THURSDAY).of("ofertas").not_today


def test_el_deshacer_de_la_global_devuelve_todas(conn):
    w = CloseWorld(conn)
    approval = w.show(w.paint())
    result = w.tap(approval, None, "all")
    assert {w.due(t) for t in ("t-ofertas", "t-proyecto", "t-paseo", "t-cena")} == {
        "2030-01-11T14:30:00",
        "2030-01-11T17:15:00",
        "2030-01-11T19:00:00",
        "2030-01-11T21:30:00",
    }
    (followup,) = result.messages
    assert followup.message.items[0].title == "➡️ 4 tareas a mañana"
    undo(w, result)
    assert w.due("t-cena") == "2030-01-10T21:30:00"
    assert w.due("t-ofertas") == "2030-01-10T14:30:00"


def test_el_deshacer_no_toca_lo_que_pedro_cambio_despues(conn):
    w = CloseWorld(conn)
    approval = w.show(w.paint())
    result = w.tap(approval, 3, "tmrw")
    raw(w, "t-paseo")["due"]["date"] = "2030-01-12T10:00:00"
    undo(w, result)
    assert w.due("t-paseo") == "2030-01-12T10:00:00"


def test_con_el_calendario_de_manana_caido_no_se_mueve_y_se_puede_reintentar(conn):
    w = CloseWorld(conn)
    w.calendar.down = "timeout"
    approval = w.show(w.paint())
    result = w.tap(approval, 3, "tmrw")
    assert result.code == "handler_failed"
    assert w.touched("t-paseo") == []
    w.calendar.down = None
    w.tap(approval, 3, "tmrw")
    assert w.due("t-paseo") == "2030-01-11T19:00:00"


def test_tras_medianoche_los_botones_actuan_sobre_el_dia_del_cierre(conn):
    """Pilla el día del reloj en vez del del cierre: a las 00:30 la cena es «de ayer»."""
    w = CloseWorld(conn)
    approval = w.show(w.paint())
    w.clock.moment = at("2030-01-11T00:30")
    w.tap(approval, 4, "done")
    assert w.touched("t-cena") == ["complete_task"]


def test_a_la_manana_siguiente_los_botones_ya_no_valen(conn):
    """Valor a mano: caduca a las 07:00 del viernes (`2030-01-11T06:00Z`)."""
    w = CloseWorld(conn)
    approval = w.show(w.paint())
    w.clock.moment = at("2030-01-11T07:00")
    assert w.tap(approval, 4, "done").code == "expired"
    assert w.touched("t-cena") == []


# --- La valoración ---------------------------------------------------------------------------


def test_la_valoracion_se_guarda_una_vez(conn):
    """Pilla una valoración que no se guarda o que se puede volver a tocar."""
    w = CloseWorld(conn)
    approval = w.show(w.paint())
    rating = len(w.message(approval).items)
    w.tap(approval, rating, "r4")
    assert w.tap(approval, rating, "r2").code == "nothing_to_do"
    assert conn.execute("SELECT day, rating FROM day_ratings").fetchall() == [("2030-01-10", 4)]
    assert render(w.message(approval), approval).text.endswith("¿Cómo ha ido el día? — ⭐ 4")


def test_la_valoracion_vale_hasta_la_manana_siguiente(conn):
    """Versión incorrecta 10: el cierre caduca al acabar el día. A las 06:45 aún vale."""
    w = CloseWorld(conn)
    approval = w.show(w.paint())
    w.clock.moment = at("2030-01-11T06:45")
    w.tap(approval, len(w.message(approval).items), "r3")
    assert conn.execute("SELECT rating FROM day_ratings").fetchall() == [(3,)]
