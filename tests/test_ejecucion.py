"""La ejecución por el código tras el botón (H1-1): crear, mover, [Créalo todo], [Deshacer] y
la caducidad (1.11-c). Todoist es el doble, detrás del lector y el escritor de verdad.
"""

import asyncio
from datetime import date, datetime

import pytest
from execution_data import ExecutionWorld, todoist_task
from heartbeat_data import MADRID, madrid

from jimic.channel.message import render
from jimic.channel.taps import sweep
from jimic.execution.execute import DoneNow
from jimic.execution.plan_message import (
    UNDO_KIND,
    move_label,
    plan_message,
    undo_message,
    when,
)
from jimic.memory.database import open_database

TODAY = date(2030, 1, 7)


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def labels(message, approval=1):
    return [[button.label for button in row] for row in render(message, approval).keyboard]


# --- El mensaje -------------------------------------------------------------------------------


def test_las_etiquetas_son_las_del_formato_del_plan(conn):
    """Pilla otras etiquetas o una fila global que ofrece el borrado (versión incorrecta 5)."""
    world = ExecutionWorld(conn, [todoist_task("t1", "Webinar"), todoist_task("t2", "Andar")])
    message = plan_message(
        [
            world.create_entry("Gimnasio", "2030-01-07T12:00", duration_minutes=75),
            world.move_entry("t1", "2030-01-07T09:50"),
            world.delete_entry("t2"),
        ],
        TODAY,
        MADRID,
    )
    assert labels(message) == [
        ["1 ✅", "1 ⏭"],
        ["2 🔄 A las 9:50", "2 🆗 Déjalo"],
        ["3 🗑️ Bórrala", "3 🆗 Déjala"],
        ["✅ Créalo todo", "⏭ Nada"],
    ]
    assert [item.title for item in message.items] == [
        "➕ Gimnasio · hoy 12:00 (75 min)",
        "🔄 Webinar · hoy → hoy 9:50",
        "🗑️ Andar · hoy",
    ]
    assert message.header == "Te propongo"
    delete = message.items[2]
    assert not any(delete.offers(option.option) for option in message.global_options)


@pytest.mark.parametrize(
    ("due", "label"),
    [
        (madrid("2030-01-07T09:50"), "🔄 A las 9:50"),
        (date(2030, 1, 7), "🔄 Hoy"),
        (date(2030, 1, 8), "🔄 Mañana"),
        (madrid("2030-01-08T23:45"), "🔄 Mañana 23:45"),
        (date(2030, 11, 24), "🔄 24/11"),
        (madrid("2030-11-24T09:05"), "🔄 24/11 9:05"),
    ],
)
def test_la_etiqueta_de_mover_dice_adonde(due, label):
    assert move_label(due, TODAY, MADRID) == label


def test_el_cuando_va_en_la_zona_local():
    assert when(datetime.fromisoformat("2030-01-07T22:30+00:00"), TODAY, MADRID) == "hoy 23:30"
    assert when(datetime.fromisoformat("2030-01-07T23:30+00:00"), TODAY, MADRID) == "mañana 0:30"
    assert when(date(2030, 1, 6), TODAY, MADRID) == "ayer"
    assert when(None, TODAY, MADRID) == "sin fecha"


def test_ocho_titulos_largos_se_recortan_para_que_quepa(conn):
    world = ExecutionWorld(conn)
    message = plan_message([world.create_entry("x" * 500)] * 8, TODAY, MADRID)
    assert all(item.title.endswith("…") for item in message.items)
    assert all(item.data and item.data["title"] == "x" * 500 for item in message.items)


# --- Botones ----------------------------------------------------------------------------------


def test_crear_con_su_boton_va_a_la_bandeja_con_su_duracion(conn):
    world = ExecutionWorld(conn)
    approval, _ = world.show([world.create_entry("Gimnasio", duration_minutes=75)])
    world.tap(approval, 1, "ok")
    (call,) = world.api.calls_of("add_task")
    assert call["project_id"] is None
    assert call["due_datetime"] == madrid("2030-01-07T18:00")
    assert call["description"] == "Duración: 75 min"  # el plan gratis no guarda `duration`
    assert world.message(approval).items[0].result == "✓ creada"


def test_mover_una_recurrente_con_su_boton_conserva_la_recurrencia(conn):
    world = ExecutionWorld(conn, [todoist_task("t1", recurring=True, due_string="every day")])
    approval, _ = world.show([world.move_entry("t1", "2030-01-08")])
    world.tap(approval, 1, "ok")
    (call,) = world.api.calls_of("update_task")
    assert (call["due_string"], call["due_date"]) == ("every day", date(2030, 1, 8))
    assert world.message(approval).items[0].result == "✓ movida"


def test_crealo_todo_crea_y_mueve_y_nunca_borra(conn):
    """Pilla una fila global que incluye un borrado (versión incorrecta 5)."""
    world = ExecutionWorld(conn, [todoist_task("t1"), todoist_task("t2")])
    approval, _ = world.show(
        [world.create_entry(), world.move_entry("t1", "2030-01-08"), world.delete_entry("t2")]
    )
    world.tap(approval, None, "all")
    assert world.writes() == ["add_task", "update_task"]
    assert [item.result for item in world.message(approval).items] == [
        "✓ creada",
        "✓ movida",
        None,
    ]
    assert world.api.find("t2") is not None
    assert world.status(approval) == "pending"  # el borrado sigue esperando su botón


def test_nada_descarta_sin_escribir_y_deja_el_borrado(conn):
    world = ExecutionWorld(conn, [todoist_task("t1"), todoist_task("t2")])
    approval, _ = world.show(
        [world.create_entry(), world.move_entry("t1", "2030-01-08"), world.delete_entry("t2")]
    )
    world.tap(approval, None, "none")
    assert world.writes() == []
    assert [item.result for item in world.message(approval).items] == [
        "⏭ descartada",
        "🆗 se queda",
        None,
    ]


def test_mover_una_tarea_que_ya_no_existe_lo_dice(conn):
    world = ExecutionWorld(conn, [todoist_task("t1")])
    approval, _ = world.show([world.move_entry("t1", "2030-01-08")])
    world.api.tasks.clear()
    world.tap(approval, 1, "ok")
    assert world.message(approval).items[0].result == "ya no existe"


def test_una_propuesta_sin_toque_no_ejecuta_nada_y_caduca_a_medianoche(conn):
    """Pilla una propuesta que ejecuta sin botón o que conserva sus botones al caducar."""
    world = ExecutionWorld(conn, [todoist_task("t1")])
    approval, message_id = world.show([world.create_entry(), world.delete_entry("t1")])
    world.clock.moment = madrid("2030-01-07T23:59:59")
    assert sweep(conn, world.clock) == []
    world.clock.moment = madrid("2030-01-08T00:01")
    (swept,) = sweep(conn, world.clock)
    assert swept.telegram_message_id == message_id
    assert swept.view is not None and swept.view.keyboard == ()
    assert world.writes() == []
    assert world.status(approval) == "expired"
    assert world.tap(approval, 1, "ok").code == "expired"
    assert world.writes() == []


# --- [Deshacer] -------------------------------------------------------------------------------


def done_now(world, entry):
    done = asyncio.run(world.executor.now(entry))
    assert isinstance(done, DoneNow), done
    message = undo_message(done.title, done.undo)
    approval, _ = world.show([], kind=UNDO_KIND, message=message)
    return approval


def test_deshacer_de_crear_borra_lo_creado(conn):
    world = ExecutionWorld(conn)
    approval = done_now(world, world.create_entry())
    (created,) = world.api.tasks
    world.tap(approval, 1, "undo")
    assert world.api.tasks == []
    assert world.message(approval).items[0].result == "↩️ deshecho"
    assert world.photos() == []  # deshacer lo de JimiC no es un borrado de Pedro
    assert created["project_id"] == "inbox"


def test_deshacer_de_crear_no_borra_si_pedro_la_cambio(conn):
    world = ExecutionWorld(conn)
    approval = done_now(world, world.create_entry())
    world.api.tasks[0]["content"] = "Pedro la ha cambiado"
    world.tap(approval, 1, "undo")
    assert world.message(approval).items[0].result == "ha cambiado: no lo deshago"
    assert len(world.api.tasks) == 1


def test_deshacer_de_crear_no_borra_si_tiene_subtareas(conn):
    world = ExecutionWorld(conn)
    approval = done_now(world, world.create_entry())
    world.api.tasks.append(todoist_task("hija", parent_id=world.api.tasks[0]["id"]))
    world.tap(approval, 1, "undo")
    assert world.message(approval).items[0].result == "ha cambiado: no lo deshago"


def test_deshacer_de_mover_devuelve_la_fecha_y_hora_anteriores(conn):
    world = ExecutionWorld(conn, [todoist_task("t1", due="2030-01-07T09:00:00")])
    approval = done_now(world, world.move_entry("t1", "2030-01-08T10:00"))
    assert world.api.find("t1")["due"]["date"] == "2030-01-08T09:00:00Z"  # pyright: ignore[reportOptionalSubscript]
    world.tap(approval, 1, "undo")
    last = world.api.calls_of("update_task")[-1]
    assert last["due_datetime"] == madrid("2030-01-07T09:00")
    assert world.message(approval).items[0].result == "↩️ deshecho"


def test_deshacer_de_mover_una_sin_fecha_le_quita_la_fecha(conn):
    """El ajuste 6 del chat: con «no date», el [Deshacer] la devuelve a «sin fecha»."""
    world = ExecutionWorld(conn, [todoist_task("t1", due=None)])
    approval = done_now(world, world.move_entry("t1", "2030-01-08"))
    world.tap(approval, 1, "undo")
    assert world.api.find("t1")["due"] is None  # pyright: ignore[reportOptionalSubscript]


def test_deshacer_de_mover_no_pisa_lo_que_pedro_cambio_despues(conn):
    """Pilla un [Deshacer] que pisa sin aviso una fecha que Pedro puso después (ajuste 5)."""
    world = ExecutionWorld(conn, [todoist_task("t1", due="2030-01-07")])
    approval = done_now(world, world.move_entry("t1", "2030-01-08"))
    world.api.find("t1")["due"]["date"] = "2030-01-09"  # pyright: ignore[reportOptionalSubscript]
    calls = len(world.api.calls_of("update_task"))
    world.tap(approval, 1, "undo")
    assert world.message(approval).items[0].result == "ha cambiado: no lo deshago"
    assert len(world.api.calls_of("update_task")) == calls


def test_deshacer_caduca_al_acabar_el_dia(conn):
    world = ExecutionWorld(conn)
    approval = done_now(world, world.create_entry())
    world.clock.moment = madrid("2030-01-08T00:01")
    (swept,) = sweep(conn, world.clock)
    assert swept.view is not None and "— Deshacer caducado" in swept.view.text
    assert world.tap(approval, 1, "undo").code == "expired"
    assert len(world.api.tasks) == 1
