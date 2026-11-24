"""El plan confirmado (CU-01 paso 6, tarea 1.16): al enviarse, `day_plans` y `plan_items` con las
tareas de «Tu día»; después, lo que crean, mueven o borran sus botones.

El agente es un doble; Todoist, el doble detrás del lector y el escritor de verdad. Todo inventado.
"""

from datetime import date

import pytest
from execution_data import todoist_task
from heartbeat_data import madrid
from plan_data import PlanWorld, plan_agent, proposing

from jimic.channel.bot import SendError
from jimic.channel.message import InteractiveMessage
from jimic.memory import day_plans
from jimic.memory.database import open_database

MONDAY = date(2030, 1, 7)
LUNCH = todoist_task("t-comida", "Comida", due="2030-01-07T14:00:00")
CALL = todoist_task("t-llamar", "Llamar al seguro", due="2030-01-07")
EARLY = todoist_task("t-temprana", "Meditación", due="2030-01-07T07:10:00")
OVERDUE = todoist_task("t-ayer", "Estiramientos", due="2030-01-06T21:00:00")
TOMORROW = todoist_task("t-manana", "Dentista", due="2030-01-08T09:00:00")
GYM = {
    "action": "create",
    "title": "Gimnasio",
    "due": "2030-01-07T12:30",
    "duration_minutes": 75,
    "icon": "🏋️",
    "reason": "toca empuje",
}


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def plan_rows(conn) -> list[tuple]:
    return [(i.task_id, i.status, i.scheduled_at) for i in day_plans.items(conn, MONDAY)]


def sent_plan(w: PlanWorld) -> tuple[int, InteractiveMessage]:
    """The plan the runner delivered, saved as the channel does."""
    ((_, plan),) = w.sender.delivered
    assert isinstance(plan, InteractiveMessage)
    approval_id, _ = w.show([], message=plan)
    return approval_id, plan


def test_al_enviarse_el_plan_guarda_las_tareas_de_hoy_de_tu_dia(conn, tmp_path):
    """Pilla un plan que no guarda sus tareas, que guarda una atrasada o de mañana (no están en
    «Tu día»), o que guarda los títulos."""
    w = PlanWorld(conn, tmp_path, proposing(items=[GYM]), tasks=[LUNCH, CALL, OVERDUE, TOMORROW])
    w.handover()
    assert plan_rows(conn) == [
        ("t-llamar", "pending", None),
        ("t-comida", "pending", madrid("2030-01-07T14:00")),
    ]
    (plan,) = conn.execute("SELECT plan FROM day_plans").fetchone()
    assert plan == '{"code": "agent", "proposals": 1}'


def test_el_plan_minimo_tambien_se_guarda(conn, tmp_path):
    """Pilla un plan mínimo (el agente no pudo) que no deja la fila del día."""
    w = PlanWorld(conn, tmp_path, plan_agent(), tasks=[LUNCH])
    w.handover()
    assert plan_rows(conn) == [("t-comida", "pending", madrid("2030-01-07T14:00"))]
    (plan,) = conn.execute("SELECT plan FROM day_plans").fetchone()
    assert '"code": "minimal:no_proposal"' in plan


def test_en_un_plan_tardio_lo_pasado_no_entra(conn, tmp_path):
    """Pilla un plan tardío que guarda las tareas de antes de su hora, que no salen en «Tu día»
    (H1-7: se ven en el cierre)."""
    w = PlanWorld(conn, tmp_path, proposing(items=[]), tasks=[EARLY, LUNCH], now="2030-01-07T10:15")
    w.handover()
    assert [row[0] for row in plan_rows(conn)] == ["t-comida"]


def test_si_telegram_falla_no_hay_plan_guardado(conn, tmp_path):
    """Pilla un plan que se guarda antes de salir: el cierre lo daría por enviado."""
    w = PlanWorld(conn, tmp_path, proposing(items=[GYM]), tasks=[LUNCH])
    w.sender.fail = "timeout"
    with pytest.raises(SendError):
        w.handover()
    assert not day_plans.has_plan(conn, MONDAY)


def test_lo_que_crea_el_plan_con_su_boton_entra_en_el_plan(conn, tmp_path):
    """Pilla un [✅] del plan que crea la tarea pero no la apunta en el plan del día."""
    w = PlanWorld(conn, tmp_path, proposing(items=[GYM]), tasks=[LUNCH])
    w.handover()
    approval_id, _ = sent_plan(w)
    w.tap(approval_id, 1, "ok")
    created = [i for i in day_plans.items(conn, MONDAY) if i.task_id != "t-comida"]
    assert [(i.status, i.scheduled_at) for i in created] == [
        ("pending", madrid("2030-01-07T12:30"))
    ]
    assert w.api.find(created[0].task_id) is not None


def test_mover_a_hoy_una_atrasada_la_mete_y_moverla_fuera_la_deja_movida(conn, tmp_path):
    """Pilla un movimiento del plan que no cambia el plan: la atrasada traída a hoy entra
    pendiente a su hora nueva, y la de hoy pasada a mañana queda `moved`."""
    w = PlanWorld(conn, tmp_path, plan_agent(), tasks=[LUNCH, OVERDUE])
    entries = [
        w.move_entry("t-ayer", "2030-01-07T21:30") | {"plan_day": "2030-01-07"},
        w.move_entry("t-comida", "2030-01-08T14:00") | {"plan_day": "2030-01-07"},
    ]
    day_plans.record_plan(conn, MONDAY, madrid("2030-01-07T07:45"), [("t-comida", None)], {})
    approval_id, _ = w.show(entries)
    w.tap(approval_id, None, "all")
    assert plan_rows(conn) == [
        ("t-comida", "moved", None),
        ("t-ayer", "pending", madrid("2030-01-07T21:30")),
    ]


def test_borrar_con_el_boton_del_plan_la_saca_del_plan(conn, tmp_path):
    """Pilla un borrado del plan que deja la tarea en el plan: la cifra la contaría sin hacer."""
    w = PlanWorld(conn, tmp_path, plan_agent(), tasks=[LUNCH, CALL])
    day_plans.record_plan(
        conn, MONDAY, madrid("2030-01-07T07:45"), [("t-comida", None), ("t-llamar", None)], {}
    )
    approval_id, _ = w.show([w.delete_entry("t-llamar") | {"plan_day": "2030-01-07"}])
    w.tap(approval_id, 1, "del")
    assert [row[0] for row in plan_rows(conn)] == ["t-comida"]


def test_lo_que_crea_una_conversacion_no_entra_en_el_plan(conn, tmp_path):
    """Pilla un plan que se queda con todo lo que JimiC crea: solo cuentan sus botones."""
    w = PlanWorld(conn, tmp_path, plan_agent(), tasks=[LUNCH])
    day_plans.record_plan(conn, MONDAY, madrid("2030-01-07T07:45"), [("t-comida", None)], {})
    approval_id, _ = w.show([w.create_entry("Tarea nueva", "2030-01-07T18:00")])
    w.tap(approval_id, 1, "ok")
    assert [row[0] for row in plan_rows(conn)] == ["t-comida"]
