"""El estado del día: lo que recibe la decisión, en JSON compacto, con cada fuente marcada."""

import json
from datetime import date

import pytest
from apex_data import invented_snapshot
from calendar_data import SECRET_TITLE
from heartbeat_data import (
    CLOSE,
    MADRID,
    PLAN,
    PLANTS,
    SECRET_TEXT,
    add_notice,
    calendar_with,
    event,
    intentions,
    limits,
    madrid,
    outgoing,
    task,
)

from jimic.clock import local_day_bounds
from jimic.heartbeat.limits import WaitingNotices
from jimic.heartbeat.state import build_day_state
from jimic.memory.database import open_database
from jimic.memory.notices import sent_between
from jimic.tools.apex_schema import ApexSnapshot
from jimic.tools.calendar import CalendarDay
from jimic.tools.todoist import TaskList

HIDDEN_PURPOSE = "Que no se me olvide regar los cactus del balcon"


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def state(
    conn,
    now: str = "2030-01-07T10:15",
    calendar: CalendarDay | None = None,
    tasks: TaskList | None = None,
    apex: ApexSnapshot | None = None,
    its=None,
    queue: WaitingNotices | None = None,
):
    moment = madrid(now)
    return build_day_state(
        moment,
        MADRID,
        calendar if calendar is not None else calendar_with(event(madrid("2030-01-07T12:00"))),
        tasks if tasks is not None else TaskList(available=True, tasks=[task("t1")]),
        apex if apex is not None else invented_snapshot("2030-01-07 10:15"),
        sent_between(conn, *local_day_bounds(moment.date(), MADRID)),
        its if its is not None else intentions(),
        limits(),
        queue if queue is not None else WaitingNotices(),
    )


def test_con_todo_disponible_el_json_lleva_cada_parte(conn):
    """Pilla una parte que no llega al modelo."""
    document = json.loads(state(conn).to_json())
    assert document["now"] == "2030-01-07T10:15+01:00"
    assert document["sources"] == {
        "calendar": {"available": True},
        "tasks": {"available": True},
        "apex": {"available": True},
    }
    assert [e["title"] for e in document["events"]] == [SECRET_TITLE]  # el modelo sí lo ve
    assert [t["id"] for t in document["tasks"]] == ["t1"]
    assert "available" not in document["apex"]
    assert document["apex"]["planned_today"]
    assert document["sent_today"] == []
    assert (document["messages_left"], document["close_slot_kept"]) == (7, True)
    assert document["waiting"] == []


@pytest.mark.parametrize(
    ("source", "kwargs", "data_key"),
    [
        ("calendar", {"calendar": CalendarDay.unavailable("auth")}, "events"),
        ("tasks", {"tasks": TaskList.unavailable("timeout")}, "tasks"),
        ("apex", {"apex": ApexSnapshot.unavailable("file_missing")}, "apex"),
    ],
)
def test_una_fuente_caida_lleva_su_codigo_y_no_inventa_listas_vacias(
    conn, source, kwargs, data_key
):
    """Pilla un `"events": []` de una fuente caída: el modelo creería que el día está libre."""
    document = json.loads(state(conn, **kwargs).to_json())
    (down,) = kwargs.values()
    reason = down.unavailable_reason
    assert document["sources"][source] == {"available": False, "unavailable_reason": reason}
    assert data_key not in document
    others = {"calendar", "tasks", "apex"} - {source}
    assert all(document["sources"][other] == {"available": True} for other in others)


def test_los_proactivos_de_hoy_van_con_su_hora_local_y_el_cupo_con_el_hueco(conn):
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T08:05"), 1)
    add_notice(conn, "regar", madrid("2030-01-07T09:40"), 2)
    add_notice(conn, "basura", madrid("2030-01-07T09:40"), 2)  # agrupado con el riego
    document = json.loads(state(conn).to_json())
    assert document["sent_today"] == [
        {"intent": "plan_del_dia", "at": "08:05"},
        {"intent": "regar", "at": "09:40"},
        {"intent": "basura", "at": "09:40"},
    ]
    assert (document["messages_left"], document["close_slot_kept"]) == (5, True)


def test_las_intenciones_activas_son_las_de_hoy_con_lo_que_les_queda(conn):
    """El riego toca lunes y jueves; el martes no está. Uno desactivado, tampoco."""
    add_notice(conn, "insistir_gimnasio", madrid("2030-01-07T09:00"), 1)
    monday = json.loads(state(conn).to_json())["intentions"]
    by_id = {i["id"]: i for i in monday}
    assert list(by_id) == ["plan_del_dia", "cierre_del_dia", "insistir_gimnasio", "regar", "basura"]
    assert by_id["insistir_gimnasio"]["left_today"] == 2
    assert by_id["insistir_gimnasio"]["habit"] == "gimnasio"
    assert by_id["plan_del_dia"]["window"] == {"from": "07:30", "to": "09:30"}
    assert by_id["plan_del_dia"]["default_time"] == "08:00"
    tuesday = json.loads(state(conn, now="2030-01-08T10:00").to_json())["intentions"]
    assert "regar" not in {i["id"] for i in tuesday}
    its = intentions(PLAN, {**CLOSE, "enabled": False}, PLANTS)
    assert "cierre_del_dia" not in {
        i["id"] for i in json.loads(state(conn, its=its).to_json())["intentions"]
    }


def test_lo_que_espera_va_solo_con_su_id(conn):
    queue = WaitingNotices()
    queue.add(outgoing("regar"), date(2030, 1, 7))
    day = state(conn, queue=queue)
    assert json.loads(day.to_json())["waiting"] == ["regar"]
    assert SECRET_TEXT not in day.to_json()


def test_el_repr_no_lleva_titulos_ni_propositos(conn):
    """Pilla un título de tarea o de evento, o el propósito de una intención, en el `repr`."""
    its = intentions(PLAN, CLOSE, {**PLANTS, "purpose": HIDDEN_PURPOSE})
    day = state(conn, its=its)
    assert SECRET_TITLE in day.to_json() and HIDDEN_PURPOSE in day.to_json()
    assert SECRET_TITLE not in repr(day)
    assert HIDDEN_PURPOSE not in repr(day)
