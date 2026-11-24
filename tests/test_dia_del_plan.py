"""El plan de cada día en SQLite (tarea 1.16): sus tareas, sus estados, la cifra y la valoración.

Todo inventado: ids de tareas falsos y un día de enero de 2030 en Madrid.
"""

from datetime import date, timedelta

import pytest
from heartbeat_data import madrid

from jimic.memory import day_plans
from jimic.memory.database import open_database

DAY = date(2030, 1, 10)
SENT = madrid("2030-01-10T07:45")


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "datos" / "jimic.sqlite3")
    yield connection
    connection.close()


def with_plan(conn, *tasks):
    day_plans.record_plan(conn, DAY, SENT, tasks, {"code": "agent", "proposals": 0})


def test_el_plan_guarda_sus_tareas_pendientes_con_su_hora(conn):
    """Pilla un plan que no guarda sus tareas, o que pierde la hora de una con hora."""
    with_plan(conn, ("t-comida", madrid("2030-01-10T14:00")), ("t-llamar", None))
    assert day_plans.has_plan(conn, DAY)
    assert [(i.task_id, i.status, i.scheduled_at) for i in day_plans.items(conn, DAY)] == [
        ("t-comida", "pending", madrid("2030-01-10T14:00")),
        ("t-llamar", "pending", None),
    ]


def test_el_plan_guarda_codigos_y_cuentas_sin_titulos(conn):
    """Pilla un `plan` que guarda algo más que lo que se le da (por ejemplo, un título)."""
    with_plan(conn, ("t-1", None))
    (plan,) = conn.execute("SELECT plan FROM day_plans").fetchone()
    assert plan == '{"code": "agent", "proposals": 0}'


def test_un_segundo_plan_el_mismo_dia_anade_lo_que_falta_sin_repetir(conn):
    """Pilla un segundo envío que falla por la clave del día, o que repite una tarea."""
    with_plan(conn, ("t-1", None))
    day_plans.set_status(conn, DAY, "t-1", "will_do")
    day_plans.record_plan(conn, DAY, SENT + timedelta(hours=1), [("t-1", None), ("t-2", None)], {})
    assert [(i.task_id, i.status) for i in day_plans.items(conn, DAY)] == [
        ("t-1", "will_do"),
        ("t-2", "pending"),
    ]
    (confirmed,) = conn.execute("SELECT confirmed_at FROM day_plans").fetchone()
    assert confirmed == "2030-01-10T06:45:00+00:00"


def test_sin_plan_ese_dia_no_se_anade_ninguna_tarea(conn):
    """Pilla `add_item` sin comprobar el plan: la clave ajena lo rechazaría con un error."""
    assert not day_plans.add_item(conn, DAY, "t-1", None)
    assert day_plans.items(conn, DAY) == []


def test_una_tarea_que_los_botones_traen_a_hoy_vuelve_a_pendiente_con_su_hora_nueva(conn):
    """Pilla `add_item` que deja el estado `moved` o la hora vieja de una tarea que ya estaba."""
    with_plan(conn, ("t-1", madrid("2030-01-10T10:00")))
    day_plans.set_status(conn, DAY, "t-1", "moved")
    assert day_plans.add_item(conn, DAY, "t-1", madrid("2030-01-10T18:00"))
    (item,) = day_plans.items(conn, DAY)
    assert (item.status, item.scheduled_at) == ("pending", madrid("2030-01-10T18:00"))


def test_hecha_antes_de_su_hora_se_anota_como_temprana(conn):
    """Pilla `marked_early` al revés o sin mirar la hora (Comida 14:00 marcada a las 13:20)."""
    with_plan(
        conn,
        ("t-comida", madrid("2030-01-10T14:00")),
        ("t-meditacion", madrid("2030-01-10T07:10")),
        ("t-llamar", None),
    )
    day_plans.set_status(conn, DAY, "t-comida", "done", madrid("2030-01-10T13:20"))
    day_plans.set_status(conn, DAY, "t-meditacion", "done", madrid("2030-01-10T09:30"))
    day_plans.set_status(conn, DAY, "t-llamar", "done", madrid("2030-01-10T08:00"))
    early = {i.task_id: (i.marked_early, i.marked_at) for i in day_plans.items(conn, DAY)}
    assert early == {
        "t-comida": (True, madrid("2030-01-10T13:20")),
        "t-meditacion": (False, madrid("2030-01-10T09:30")),
        "t-llamar": (False, madrid("2030-01-10T08:00")),
    }


def test_un_estado_que_no_es_hecha_borra_la_marca(conn):
    """Pilla un [Deshacer] que devuelve a pendiente pero deja `marked_at` y `marked_early`."""
    with_plan(conn, ("t-comida", madrid("2030-01-10T14:00")))
    day_plans.set_status(conn, DAY, "t-comida", "done", madrid("2030-01-10T13:20"))
    day_plans.set_status(conn, DAY, "t-comida", "pending")
    (item,) = day_plans.items(conn, DAY)
    assert (item.status, item.marked_at, item.marked_early) == ("pending", None, False)


def test_una_tarea_fuera_del_plan_no_cambia_nada(conn):
    """Pilla `set_status` que inserta una tarea que no estaba en el plan."""
    with_plan(conn, ("t-1", None))
    assert not day_plans.set_status(conn, DAY, "t-otra", "done", SENT)
    assert [i.task_id for i in day_plans.items(conn, DAY)] == ["t-1"]


def test_borrarla_con_los_botones_la_saca_del_plan(conn):
    with_plan(conn, ("t-1", None), ("t-2", None))
    assert day_plans.remove_item(conn, DAY, "t-1")
    assert [i.task_id for i in day_plans.items(conn, DAY)] == ["t-2"]


def test_la_cifra_definitiva_cuenta_las_tareas_del_plan_y_se_guarda_una_vez(conn):
    """Pilla una cifra que no cuenta las tareas del plan, o un día que sigue sin asentar."""
    with_plan(conn, *((f"t-{n}", None) for n in range(7)))
    assert day_plans.unsettled(conn, DAY + timedelta(days=1)) == [DAY]
    assert day_plans.figures(conn, DAY) is None
    day_plans.settle(conn, DAY, 4, madrid("2030-01-11T07:00"))
    assert day_plans.figures(conn, DAY) == day_plans.Figures(
        planned=7, fulfilled=4, settled_at=madrid("2030-01-11T07:00")
    )
    assert day_plans.unsettled(conn, DAY + timedelta(days=1)) == []


def test_el_dia_de_hoy_no_se_asienta_todavia(conn):
    """Pilla `unsettled` que incluye el día que aún no ha acabado."""
    with_plan(conn, ("t-1", None))
    assert day_plans.unsettled(conn, DAY) == []


def test_la_valoracion_se_guarda_una_vez(conn):
    """Pilla una valoración que se sobrescribe con un segundo toque."""
    assert day_plans.rate(conn, DAY, 4, madrid("2030-01-10T21:40"))
    assert not day_plans.rate(conn, DAY, 1, madrid("2030-01-10T21:41"))
    assert conn.execute("SELECT day, rating FROM day_ratings").fetchall() == [("2030-01-10", 4)]
