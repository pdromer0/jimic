"""Las herramientas del agente: exactamente las de la tabla, que solo leen y proponen (H1-1).

Se listan y se llaman por el puente MCP del SDK, el mismo camino que usa el motor. Las fuentes
son dobles con datos inventados y el escritor de Todoist es un espía que no debe llamarse nunca.
"""

import asyncio
import json
import logging

import pytest
from agent_data import (
    SECRET_ARG,
    SECRET_IDEA,
    SpyWriter,
    agent_sources,
    approvals,
    call_tool,
    context,
    listed_tools,
)
from calendar_data import SECRET_TITLE
from heartbeat_data import CountingCalendar, calendar_with, event, madrid, sources, task
from heartbeat_data import limits as limits_of
from memory_data import QUIET_LINE, shas, write_personal

from jimic.agent.tools import RunState, build_server, model_name
from jimic.heartbeat.sources import Sources
from jimic.memory.conversations import read_external
from jimic.memory.database import open_database
from jimic.memory.schemas import TOOL_NAMES
from jimic.tools.calendar import CalendarDay
from jimic.tools.failures import ToolFailure
from jimic.tools.todoist import TaskList

SPEC_TABLE = [
    "read_calendar",
    "read_tasks",
    "read_apex_today",
    "propose_plan",
    "propose_change",
    "log_idea",
]
"""La tabla «Lo que puede hacer el agente» de la especificación, copiada a mano."""

CREATE = {"action": "create", "title": "Tarea nueva", "due": "2030-01-07T18:00", "reason": "r"}


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


@pytest.fixture
def config_dir(tmp_path):
    folder = tmp_path / "config"
    folder.mkdir()
    return folder


def server(conn, config_dir, **kwargs):
    return build_server(context(conn, config_dir, **kwargs))


def call(conn, config_dir, name, args, **kwargs):
    return asyncio.run(call_tool(server(conn, config_dir, **kwargs), name, args))


# --- La lista ----------------------------------------------------------------------------------


def test_el_servidor_registra_exactamente_las_herramientas_de_la_tabla(conn, config_dir):
    """Pilla una herramienta de más, como `delete_task` (versión incorrecta 1 y 2 de la
    especificación), o una de menos. La lista sale del servidor, no de una lista interna."""
    tools = asyncio.run(listed_tools(server(conn, config_dir)))
    assert [tool["name"] for tool in tools] == SPEC_TABLE
    assert list(TOOL_NAMES) == SPEC_TABLE


def test_los_nombres_que_ve_el_modelo_llevan_el_servidor_jimic():
    assert model_name("read_tasks") == "mcp__jimic__read_tasks"


# --- Leer ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "contaminates"), [("read_calendar", True), ("read_tasks", True)])
def test_leer_calendario_o_tareas_devuelve_sus_datos_y_marca_la_sesion(
    conn, config_dir, name, contaminates
):
    text, is_error = call(conn, config_dir, name, {})
    assert not is_error
    data = json.loads(text)
    assert data["available"] is True
    assert SECRET_TITLE in text
    assert read_external(conn, context(conn, config_dir).day) is contaminates


def test_leer_apex_no_marca_la_sesion(conn, config_dir):
    text, is_error = call(conn, config_dir, "read_apex_today", {})
    assert not is_error and json.loads(text)["available"] is True
    assert not read_external(conn, context(conn, config_dir).day)


def test_read_calendar_lee_el_dia_que_se_pide(conn, config_dir):
    srcs = agent_sources()
    call(conn, config_dir, "read_calendar", {"day": "2030-01-09"}, srcs=srcs)
    assert srcs.calendar.reads == 1  # pyright: ignore[reportAttributeAccessIssue] - the double counts
    text, is_error = call(conn, config_dir, "read_calendar", {"day": "9 de enero"})
    assert is_error and "YYYY-MM-DD" in text


def test_una_fuente_caida_devuelve_su_codigo(conn, config_dir):
    srcs = agent_sources(tasks=TaskList.unavailable("timeout"))
    text, is_error = call(conn, config_dir, "read_tasks", {}, srcs=srcs)
    assert json.loads(text) == {"available": False, "unavailable_reason": "timeout"}
    assert not is_error


def test_un_fallo_inesperado_da_su_tipo_y_no_lanza(conn, config_dir):
    srcs = agent_sources()

    async def broken(day):
        raise KeyError(SECRET_ARG)

    srcs.calendar.read_day = broken
    text, is_error = call(conn, config_dir, "read_calendar", {}, srcs=srcs)
    assert is_error and json.loads(text) == {"error": "unexpected:KeyError"}


# --- propose_plan -------------------------------------------------------------------------------


def test_propose_plan_anota_en_la_ejecucion_y_no_llama_al_escritor(conn, config_dir):
    """Pilla `propose_plan` que llama al escritor (versión incorrecta 5 de la 1.10) o que guarda
    la propuesta en `approvals` por su cuenta (1.11: el código la guarda al acabar la ejecución,
    con sus botones). Mover y borrar llevan la foto de la tarea."""
    spy = SpyWriter()
    run = RunState()
    items = [
        CREATE | {"duration_minutes": 30},
        {"action": "move", "task_id": "t1", "due": "2030-01-08", "reason": "r"},
        {"action": "delete", "task_id": "t2", "reason": "r"},
    ]
    text, is_error = call(
        conn, config_dir, "propose_plan", {"items": items}, srcs=agent_sources(writer=spy), run=run
    )
    assert not is_error
    assert spy.calls == []
    assert approvals(conn) == []
    assert [item["action"] for item in run.items] == ["create", "move", "delete"]
    assert "snapshot" not in run.items[0]
    assert run.items[1]["snapshot"] == {
        "title": SECRET_TITLE,
        "due": "2030-01-07",
        "project_id": "proyecto-inventado",
        "is_recurring": False,
    }
    assert run.requested_by_pedro is False
    assert text.startswith("Anotado: crear 1, mover 1, borrar 1. No se ha ejecutado nada.")
    assert "No repitas la lista ni anuncies resultados" in text


def test_requested_by_pedro_cuenta_si_todas_las_llamadas_lo_marcan(conn, config_dir):
    run = RunState()
    move = {"action": "move", "task_id": "t1", "due": "2030-01-08", "reason": "r"}
    call(conn, config_dir, "propose_plan", {"items": [move], "requested_by_pedro": True}, run=run)
    assert run.requested_by_pedro is True
    call(conn, config_dir, "propose_plan", {"items": [CREATE]}, run=run)
    assert run.requested_by_pedro is False
    assert len(run.items) == 2


def test_varias_llamadas_suman_hasta_ocho_y_la_que_se_pasa_no_anade_nada(conn, config_dir):
    """Decisión de la 1.11: un solo teclado por respuesta, de 8 elementos como mucho."""
    run = RunState()
    call(conn, config_dir, "propose_plan", {"items": [CREATE] * 5}, run=run)
    text, is_error = call(conn, config_dir, "propose_plan", {"items": [CREATE] * 4}, run=run)
    assert is_error and "ya hay 5" in text
    assert len(run.items) == 5
    _, is_error = call(conn, config_dir, "propose_plan", {"items": [CREATE] * 3}, run=run)
    assert not is_error and len(run.items) == 8


def test_una_tarea_ya_propuesta_en_la_ejecucion_se_rechaza(conn, config_dir):
    run = RunState()
    move = {"action": "move", "task_id": "t1", "due": "2030-01-08", "reason": "r"}
    call(conn, config_dir, "propose_plan", {"items": [move]}, run=run)
    delete = {"action": "delete", "task_id": "t1", "reason": "r"}
    text, is_error = call(conn, config_dir, "propose_plan", {"items": [CREATE, delete]}, run=run)
    assert is_error and "elemento 2" in text and "ya está propuesta" in text
    assert len(run.items) == 1


@pytest.mark.parametrize(
    ("changes", "words"),
    [
        ({"is_recurring": True}, "recurrente"),
        ({"has_subtasks": True}, "subtareas"),
        ({"due": madrid("2030-01-08T09:00")}, "de hoy o vencidas"),
        ({"due": None}, "de hoy o vencidas"),
    ],
)
def test_un_borrado_que_no_se_podra_hacer_se_rechaza_al_proponer(conn, config_dir, changes, words):
    """Las reglas de H1-2 que dependen de la tarea, ya al proponer: Pedro nunca ve un botón que
    se va a negar, y el agente le remite a Todoist."""
    run = RunState()
    target = task("t9").model_copy(update=changes)
    srcs = agent_sources(tasks=TaskList(available=True, tasks=[target]))
    delete = {"action": "delete", "task_id": "t9", "reason": "r"}
    text, is_error = call(conn, config_dir, "propose_plan", {"items": [delete]}, srcs=srcs, run=run)
    assert is_error and words in text
    assert run.items == []


def test_un_borrado_vencido_o_de_hoy_con_hora_se_puede_proponer(conn, config_dir):
    for due in (madrid("2030-01-06T09:00"), madrid("2030-01-07T23:30")):
        run = RunState()
        target = task("t9").model_copy(update={"due": due})
        srcs = agent_sources(tasks=TaskList(available=True, tasks=[target]))
        delete = {"action": "delete", "task_id": "t9", "reason": "r"}
        _, is_error = call(
            conn, config_dir, "propose_plan", {"items": [delete]}, srcs=srcs, run=run
        )
        assert not is_error and len(run.items) == 1


@pytest.mark.parametrize(
    "items",
    [
        [{"action": "move", "due": "2030-01-08", "reason": "r"}],  # mover sin task_id
        [{"action": "create", "due": "2030-01-08", "reason": "r"}],  # crear sin título
        [CREATE | {"due": "2030-01-07T25:00"}],
        [CREATE | {"due": "2030-01-07T10:00Z"}],
        [CREATE | {"due": "7 de enero"}],
        [CREATE | {"duration_minutes": 0}],
        [CREATE | {"duration_minutes": 1441}],
        [{"action": "archive", "task_id": "t1", "reason": "r"}],
        [],
        [CREATE] * 9,
        # La misma tarea dos veces (hallazgo 4 de la revisión): movida y borrada, o movida dos.
        [
            {"action": "move", "task_id": "t1", "due": "2030-01-08", "reason": "r"},
            {"action": "delete", "task_id": "t1", "reason": "r"},
        ],
        [
            {"action": "move", "task_id": "t1", "due": "2030-01-08", "reason": "r"},
            {"action": "move", "task_id": "t1", "due": "2030-01-09", "reason": "r"},
        ],
    ],
)
def test_una_propuesta_no_valida_se_rechaza_con_un_mensaje_y_no_guarda_nada(
    conn, config_dir, items
):
    run = RunState()
    text, is_error = call(conn, config_dir, "propose_plan", {"items": items}, run=run)
    assert is_error and text
    assert approvals(conn) == [] and run.items == []


def test_mover_una_tarea_que_no_existe_se_rechaza(conn, config_dir):
    move = {"action": "move", "task_id": "no-existe", "due": "2030-01-08", "reason": "r"}
    text, is_error = call(conn, config_dir, "propose_plan", {"items": [CREATE, move]})
    assert is_error and "elemento 2" in text and "no existe" in text
    assert approvals(conn) == []


def test_una_tarea_fuera_del_proyecto_de_pruebas_da_su_codigo(conn, config_dir):
    srcs = agent_sources()

    async def outside(task_id):
        raise ToolFailure("outside_test_project")

    srcs.todoist.reader.get_task = outside
    delete = {"action": "delete", "task_id": "t1", "reason": "r"}
    text, is_error = call(conn, config_dir, "propose_plan", {"items": [delete]}, srcs=srcs)
    assert is_error and json.loads(text) == {"error": "outside_test_project"}
    assert approvals(conn) == []


# --- propose_change (1.12-a) -------------------------------------------------------------------


PEDRO = "No me escribas antes de las 8"
CHANGE = {
    "file": "limites.yaml",
    "operation": "replace",
    "before": QUIET_LINE,
    "after": QUIET_LINE.replace('"07:00"', '"08:00"'),
    "reason": "Pedro no quiere mensajes antes de las 8",
}


def clean_run() -> RunState:
    return RunState(origin=PEDRO)


def test_propose_change_en_una_ejecucion_limpia_se_anota_con_el_origen(conn, tmp_path):
    """Se anota en la ejecución, con el texto de Pedro como origen; no escribe nada, ni el
    fichero, ni `changes`, ni `approvals` (la aprobación la crea el canal al enviar)."""
    config_dir = write_personal(tmp_path / "config")
    before = shas(config_dir)
    run = clean_run()
    text, is_error = call(conn, config_dir, "propose_change", CHANGE, run=run)
    assert not is_error and "No se ha guardado nada" in text
    (entry,) = run.memory
    assert entry == {"action": "change"} | CHANGE | {
        "origin": PEDRO,
        "after_external_content": False,
        "follow_up": False,
    }
    assert shas(config_dir) == before
    assert conn.execute("SELECT COUNT(*) FROM changes").fetchone() == (0,)
    assert approvals(conn) == []


def test_read_tasks_y_luego_propose_change_en_la_misma_ejecucion_se_rechaza(conn, tmp_path):
    config_dir = write_personal(tmp_path / "config")
    run = clean_run()
    srv = server(conn, config_dir, run=run)
    asyncio.run(call_tool(srv, "read_tasks", {}))
    text, is_error = asyncio.run(call_tool(srv, "propose_change", CHANGE))
    assert is_error and "otro mensaje" in text
    assert run.memory == [] and approvals(conn) == []


def test_fuera_de_una_conversacion_no_hay_cambio_posible(conn, tmp_path):
    """Un latido o un plan que pasa el latido: la ejecución no tiene mensaje de Pedro."""
    config_dir = write_personal(tmp_path / "config")
    run = RunState()
    for name, args in (("propose_change", CHANGE), ("log_idea", {"text": "una idea"})):
        text, is_error = call(conn, config_dir, name, args, run=run)
        assert is_error and "Fuera de una conversación" in text
    assert run.memory == []


def test_la_marca_del_dia_solo_informa(conn, tmp_path):
    """La sesión del día leyó las tareas en otro mensaje: esta ejecución, limpia, sí propone, y
    el dato `after_external_content` queda en el elemento."""
    config_dir = write_personal(tmp_path / "config")
    call(conn, config_dir, "read_tasks", {}, run=RunState(origin="otro"))
    run = clean_run()
    _, is_error = call(conn, config_dir, "propose_change", CHANGE, now="2030-01-07T17:00", run=run)
    assert not is_error and run.memory[0]["after_external_content"] is True
    tomorrow = clean_run()
    call(conn, config_dir, "propose_change", CHANGE, now="2030-01-08T09:00", run=tomorrow)
    assert tomorrow.memory[0]["after_external_content"] is False


@pytest.mark.parametrize(
    "change",
    [
        CHANGE | {"file": ".env"},
        CHANGE | {"operation": "rewrite"},
        CHANGE | {"after": "  "},
        CHANGE | {"after": CHANGE["before"]},
        {k: v for k, v in CHANGE.items() if k != "before"},
        CHANGE | {"operation": "add"},
        {"file": "rutina.md", "operation": "remove", "before": "x", "after": "y", "reason": "r"},
    ],
    ids=[
        "fichero",
        "operacion",
        "espacios",
        "igual",
        "sin-antes",
        "add-con-antes",
        "remove-con-despues",
    ],
)
def test_unos_argumentos_que_no_cuadran_se_rechazan(conn, tmp_path, change):
    config_dir = write_personal(tmp_path / "config")
    run = clean_run()
    _, is_error = call(conn, config_dir, "propose_change", change, run=run)
    assert is_error and run.memory == []


def test_un_cambio_que_no_valida_no_se_ofrece(conn, tmp_path):
    config_dir = write_personal(tmp_path / "config")
    run = clean_run()
    broken = {"file": "rutina.md", "operation": "replace", "before": "## Comidas", "after": "## C"}
    text, is_error = call(conn, config_dir, "propose_change", broken | {"reason": "r"}, run=run)
    assert is_error and "Comidas" in text and run.memory == []


def test_escribeme_15_veces_al_dia_se_rechaza_con_su_explicacion(conn, tmp_path):
    config_dir = write_personal(tmp_path / "config")
    run = RunState(origin="Escríbeme 15 veces al día")
    change = CHANGE | {
        "before": "max_messages_per_day: 8",
        "after": "max_messages_per_day: 15",
    }
    text, is_error = call(conn, config_dir, "propose_change", change, run=run)
    assert is_error and "max_messages_per_day" in text and "a mano" in text
    assert run.memory == []


def test_un_cambio_que_no_cabe_entero_en_telegram_no_se_ofrece(conn, tmp_path):
    """El tope de tamaño: nunca se recorta lo que se enseña."""
    config_dir = write_personal(tmp_path / "config")
    run = clean_run()
    big = {"file": "preferencias.md", "operation": "add", "after": "&" * 2000, "reason": "r"}
    text, is_error = call(conn, config_dir, "propose_change", big, run=run)
    assert is_error and "demasiado largo" in text and run.memory == []


def test_como_mucho_tres_propuestas_de_memoria_por_ejecucion(conn, tmp_path):
    config_dir = write_personal(tmp_path / "config")
    run = clean_run()
    srv = server(conn, config_dir, run=run)
    results = [asyncio.run(call_tool(srv, "log_idea", {"text": f"idea {n}"}))[1] for n in range(4)]
    assert results == [False, False, False, True]
    assert len(run.memory) == 3


# --- log_idea (1.12-b) --------------------------------------------------------------------------


def test_log_idea_limpio_da_una_propuesta_y_no_escribe(conn, tmp_path):
    config_dir = write_personal(tmp_path / "config")
    run = clean_run()
    text, is_error = call(
        conn, config_dir, "log_idea", {"text": "  Probar   la app\nde notas  "}, run=run
    )
    assert not is_error and "Apuntar" in text
    (entry,) = run.memory
    assert entry["line"] == "- 2030-01-07 · Probar la app de notas"
    assert entry["origin"] == PEDRO
    assert not (config_dir / "ideas.md").exists()


def test_log_idea_contaminado_se_rechaza(conn, tmp_path):
    config_dir = write_personal(tmp_path / "config")
    run = clean_run()
    srv = server(conn, config_dir, run=run)
    asyncio.run(call_tool(srv, "read_calendar", {}))
    text, is_error = asyncio.run(call_tool(srv, "log_idea", {"text": "una"}))
    assert is_error and "otro mensaje" in text and run.memory == []


def test_log_idea_ya_no_depende_de_la_marca_del_dia(conn, tmp_path):
    """La decisión 100, revisada: la sesión leyó por la mañana; esta ejecución, limpia, sí."""
    config_dir = write_personal(tmp_path / "config")
    call(conn, config_dir, "read_tasks", {}, run=RunState(origin="otro"))
    assert read_external(conn, madrid("2030-01-07T10:15").date())
    run = clean_run()
    _, is_error = call(conn, config_dir, "log_idea", {"text": "una"}, run=run)
    assert not is_error and len(run.memory) == 1


@pytest.mark.parametrize(("length", "ok"), [(500, True), (501, False)])
def test_log_idea_tiene_tope_por_linea(conn, tmp_path, length, ok):
    config_dir = write_personal(tmp_path / "config")
    run = clean_run()
    _, is_error = call(conn, config_dir, "log_idea", {"text": "x" * length}, run=run)
    assert is_error is not ok
    assert len(run.memory) == int(ok)


def test_log_idea_vacia_tras_juntar_espacios_se_rechaza(conn, config_dir):
    run = clean_run()
    _, is_error = call(conn, config_dir, "log_idea", {"text": " \n\t "}, run=run)
    assert is_error and run.memory == []


def test_log_idea_con_ideas_como_enlace_se_rechaza(conn, tmp_path):
    config_dir = write_personal(tmp_path / "config")
    (config_dir / "ideas.md").symlink_to(tmp_path / "fuera.md")
    run = clean_run()
    _, is_error = call(conn, config_dir, "log_idea", {"text": "una"}, run=run)
    assert is_error and run.memory == []


# --- Logs ---------------------------------------------------------------------------------------


def test_los_logs_de_las_herramientas_no_llevan_contenido(conn, config_dir, caplog):
    caplog.set_level(logging.DEBUG)
    srcs = agent_sources(TaskList(available=True, tasks=[task("t1", title=SECRET_TITLE)]))
    call(conn, config_dir, "read_tasks", {}, srcs=srcs)
    write_personal(config_dir)
    call(conn, config_dir, "log_idea", {"text": SECRET_IDEA}, run=clean_run())
    call(conn, config_dir, "propose_plan", {"items": [CREATE | {"title": SECRET_ARG}]})
    call(conn, config_dir, "propose_change", CHANGE | {"after": SECRET_ARG}, run=clean_run())
    call(conn, config_dir, "propose_change", CHANGE | {"before": SECRET_ARG}, run=clean_run())
    call(conn, config_dir, "propose_plan", {"items": [CREATE | {"due": SECRET_ARG}]})
    for secret in (SECRET_TITLE, SECRET_IDEA, SECRET_ARG):
        assert secret not in caplog.text
    assert "tool propose_plan: ok" in caplog.text


def test_un_calendario_caido_no_es_un_error_del_agente(conn, config_dir):
    srcs = agent_sources()
    srcs.calendar.day = CalendarDay.unavailable("auth")  # pyright: ignore[reportAttributeAccessIssue] - the double's day
    text, is_error = call(conn, config_dir, "read_calendar", {}, srcs=srcs)
    assert json.loads(text)["unavailable_reason"] == "auth" and not is_error


@pytest.mark.parametrize(
    ("name", "contaminates"),
    [("read_calendar", True), ("read_tasks", True), ("read_apex_today", False)],
)
def test_leer_marca_la_ejecucion_que_lee(conn, config_dir, name, contaminates):
    """La marca por ejecución que decide la ejecución inmediata (condición 2 de 1.11-b)."""
    run = RunState()
    call(conn, config_dir, name, {}, run=run)
    assert run.contaminated is contaminates


# --- El plan del día y `follow_up` (tarea 1.14) ----------------------------------------------


def creates(count: int, reason: str = "toca hoy") -> list[dict]:
    """Sin hora: varias a la misma hora se pisarían (decisión 1.16-e)."""
    return [
        CREATE | {"title": f"Tarea {n}", "reason": reason, "due": "2030-01-07"}
        for n in range(count)
    ]


def plan_run() -> RunState:
    return RunState(origin=None, mode="plan")


def test_en_el_plan_una_quinta_tarea_nueva_se_rechaza_aunque_llegue_en_otra_llamada(
    conn, config_dir
):
    """Pilla un tope por llamada en vez de por ejecución, o ninguno (decisión 1.14-b)."""
    run = plan_run()
    srv = server(conn, config_dir, run=run)
    _, is_error = asyncio.run(call_tool(srv, "propose_plan", {"items": creates(3)}))
    assert not is_error
    text, is_error = asyncio.run(call_tool(srv, "propose_plan", {"items": creates(2)}))
    assert is_error and "4 tareas nuevas como mucho" in text
    assert len(run.items) == 3
    _, is_error = asyncio.run(call_tool(srv, "propose_plan", {"items": creates(1)}))
    assert not is_error and len(run.items) == 4


def test_fuera_del_plan_no_hay_tope_de_cuatro(conn, config_dir):
    run = RunState(origin="crea cinco", mode="message")
    _, is_error = call(conn, config_dir, "propose_plan", {"items": creates(5)}, run=run)
    assert not is_error and len(run.items) == 5


def test_en_el_plan_un_motivo_de_mas_de_ocho_palabras_se_rechaza(conn, config_dir):
    run = plan_run()
    long = "uno dos tres cuatro cinco seis siete ocho nueve"
    text, is_error = call(conn, config_dir, "propose_plan", {"items": creates(1, long)}, run=run)
    assert is_error and "8 palabras" in text and run.items == []
    eight = "uno dos tres cuatro cinco seis siete ocho"
    _, is_error = call(conn, config_dir, "propose_plan", {"items": creates(1, eight)}, run=run)
    assert not is_error


def test_en_el_plan_los_campos_se_guardan_y_una_llamada_posterior_los_sustituye(conn, config_dir):
    run = plan_run()
    srv = server(conn, config_dir, run=run)
    first = {
        "greeting": "Buenos días, máquina",
        "training_line": "Hoy toca pierna",
        "left_out": [{"habit": "gimnasio", "line": "Hoy el gimnasio descansa"}],
    }
    _, is_error = asyncio.run(call_tool(srv, "propose_plan", first))
    assert not is_error and run.items == [] and run.proposed_plan
    asyncio.run(call_tool(srv, "propose_plan", {"greeting": "Otra vez hola"}))
    assert run.plan == {
        "greeting": "Otra vez hola",
        "training_line": "Hoy toca pierna",
        "left_out": [{"habit": "gimnasio", "line": "Hoy el gimnasio descansa"}],
    }


def test_en_la_conversacion_los_campos_del_plan_se_ignoran_y_hace_falta_un_elemento(
    conn, config_dir
):
    run = RunState(origin="hola")
    text, is_error = call(conn, config_dir, "propose_plan", {"greeting": "Hola"}, run=run)
    assert is_error and "items está vacío" in text
    _, is_error = call(
        conn, config_dir, "propose_plan", {"items": creates(1), "greeting": "Hola"}, run=run
    )
    assert not is_error and run.plan == {}


@pytest.mark.parametrize("bad", ["dos\nlíneas", "x" * 121])
def test_una_linea_del_plan_es_una_sola_y_corta(conn, config_dir, bad):
    run = plan_run()
    text, is_error = call(conn, config_dir, "propose_plan", {"greeting": bad}, run=run)
    assert is_error and run.plan == {}


def test_un_icono_es_un_emoji_no_texto(conn, config_dir):
    run = plan_run()
    _, is_error = call(
        conn, config_dir, "propose_plan", {"items": [CREATE | {"icon": "gym"}]}, run=run
    )
    assert is_error
    _, is_error = call(
        conn, config_dir, "propose_plan", {"items": [CREATE | {"icon": "🏋️"}]}, run=run
    )
    assert not is_error and run.items[0]["icon"] == "🏋️"


def test_follow_up_queda_en_cada_elemento_de_las_tres_propuestas(conn, tmp_path):
    """Lo lee la reanudación (1.13-d) en los datos de cada elemento del mensaje."""
    config_dir = tmp_path / "config"
    write_personal(config_dir)
    run = RunState(origin=PEDRO)
    srv = server(conn, config_dir, run=run)
    asyncio.run(call_tool(srv, "propose_plan", {"items": creates(2), "follow_up": True}))
    asyncio.run(call_tool(srv, "propose_change", CHANGE | {"follow_up": True}))
    asyncio.run(call_tool(srv, "log_idea", {"text": "una idea", "follow_up": True}))
    asyncio.run(call_tool(srv, "log_idea", {"text": "otra idea"}))
    assert [entry["follow_up"] for entry in run.items] == [True, True]
    assert [entry["follow_up"] for entry in run.memory] == [True, True, False]


# --- El cierre del día (tarea 1.16) ----------------------------------------------------------


def test_en_el_cierre_propose_plan_se_rechaza_y_no_anota_nada(conn, config_dir):
    """Pilla un cierre en el que el agente propone algo: del cierre, solo la nota es suya
    (decisión 1.16-a). La lectura sigue libre."""
    run = RunState(origin=None, mode="close")
    text, is_error = call(conn, config_dir, "propose_plan", {"items": creates(1)}, run=run)
    assert is_error and "En el cierre del día no se propone nada" in text
    assert run.items == [] and not run.proposed_plan
    _, is_error = call(conn, config_dir, "read_apex_today", {}, run=run)
    assert not is_error


# --- Las reglas duras del plan, en el código (decisiones 1.16-e, F2-a y F2-b) -----------------

ON_THE_APPOINTMENT = {
    "action": "create",
    "title": "Revisar ofertas",
    "due": "2030-01-07T10:30",
    "duration_minutes": 30,
    "reason": "r",
}
STRETCH_HABIT = """\
intentions: []
habits:
  - {id: estiramientos, name: "Estiramientos", priority: medium, duration: 15m,
     moment: evening, todoist_task: "Estiramientos", days: [sat, sun]}
"""


def test_en_el_plan_lo_que_pisa_un_evento_se_rechaza_con_su_motivo_y_lo_contiguo_no(
    conn, config_dir
):
    run = plan_run()
    text, is_error = call(
        conn, config_dir, "propose_plan", {"items": [ON_THE_APPOINTMENT]}, run=run
    )
    assert is_error and run.items == []
    assert text == (
        f"El elemento 1: pisa «{SECRET_TITLE}» 10:00–11:00. No se ha añadido nada: corrígelo y"
        " vuelve a llamar a propose_plan."
    )
    after = ON_THE_APPOINTMENT | {"due": "2030-01-07T11:00"}
    _, is_error = call(conn, config_dir, "propose_plan", {"items": [after]}, run=run)
    assert not is_error and len(run.items) == 1


@pytest.mark.parametrize("mode", ["message", "follow_up"])
def test_fuera_del_plan_la_conversacion_no_cambia(conn, config_dir, mode):
    """Pilla la regla aplicada en todos los modos (versión incorrecta 7): «crea X a las 10:30»
    encima de una cita se sigue proponiendo, y propose_plan no lee ni contamina."""
    run = RunState(origin="crea" if mode == "message" else None, mode=mode)
    _, is_error = call(conn, config_dir, "propose_plan", {"items": [ON_THE_APPOINTMENT]}, run=run)
    assert not is_error and len(run.items) == 1
    assert not run.contaminated and run.day_sources is None


def test_en_el_plan_las_fuentes_se_leen_una_vez_por_ejecucion_y_contaminan(conn, config_dir):
    base = agent_sources()
    counting = CountingCalendar(calendar_with(event(madrid("2030-01-07T10:00"))))
    srcs = Sources(todoist=base.todoist, calendar=counting, apex=base.apex)
    run = plan_run()
    srv = server(conn, config_dir, srcs=srcs, run=run)
    for _ in range(2):
        asyncio.run(call_tool(srv, "propose_plan", {"items": [ON_THE_APPOINTMENT]}))
    assert counting.reads == 1 and run.contaminated


def test_con_el_calendario_caido_no_se_puede_saber_y_se_acepta(conn, config_dir):
    base = agent_sources()
    down = sources(CalendarDay.unavailable("timeout"))
    srcs = Sources(todoist=base.todoist, calendar=down.calendar, apex=base.apex)
    run = plan_run()
    _, is_error = call(
        conn, config_dir, "propose_plan", {"items": [ON_THE_APPOINTMENT]}, srcs=srcs, run=run
    )
    assert not is_error and len(run.items) == 1


def test_un_calendario_que_lanza_cuenta_como_caido_y_no_sale_en_el_log(conn, config_dir, caplog):
    srcs = agent_sources()

    async def broken(day):
        raise KeyError(SECRET_ARG)

    srcs.calendar.read_day = broken
    run = plan_run()
    with caplog.at_level(logging.INFO):
        _, is_error = call(
            conn, config_dir, "propose_plan", {"items": [ON_THE_APPOINTMENT]}, srcs=srcs, run=run
        )
    assert not is_error and len(run.items) == 1
    assert "plan check without calendar (unexpected:KeyError)" in caplog.text
    assert SECRET_ARG not in caplog.text


def test_en_el_plan_una_tarea_de_esta_manana_que_no_es_de_un_habito_no_se_toca(conn, config_dir):
    webinar = task("t-web", "Webinar inventado", madrid("2030-01-07T09:00"))
    srcs = agent_sources(TaskList(available=True, tasks=[webinar]))
    run = plan_run()
    item = {"action": "move", "task_id": "t-web", "due": "2030-01-07T16:00", "reason": "r"}
    text, is_error = call(conn, config_dir, "propose_plan", {"items": [item]}, srcs=srcs, run=run)
    assert is_error and "era hoy a las 9:00 y ya pasó: lo ve el cierre" in text
    assert run.items == []


def test_en_el_plan_el_habito_de_un_dia_que_no_le_toca_se_rechaza(conn, config_dir):
    """El 7-ene-2030 es lunes; con `intenciones.yaml` inválido no hay regla de días."""
    stretch = CREATE | {"title": "Estiramientos", "due": "2030-01-07T20:00"}
    (config_dir / "intenciones.yaml").write_text(STRETCH_HABIT, encoding="utf-8")
    text, is_error = call(conn, config_dir, "propose_plan", {"items": [stretch]}, run=plan_run())
    assert is_error and "«Estiramientos» no toca el lunes (sus días: sábado, domingo)" in text
    (config_dir / "intenciones.yaml").write_text("habits: [", encoding="utf-8")
    _, is_error = call(conn, config_dir, "propose_plan", {"items": [stretch]}, run=plan_run())
    assert not is_error


def test_lo_movido_en_una_llamada_anterior_conserva_su_duracion(conn, config_dir):
    """Revisión propia: una tarea que no sale entre las de hoy, movida en la primera llamada,
    ocupa sus 60 minutos en la segunda (sin su duración, sería un instante a las 15:00)."""
    far = task("t-lejos", "Tarea inventada", madrid("2030-01-20T09:00")).model_copy(
        update={"duration_minutes": 60}
    )
    srcs = agent_sources(TaskList(available=True, tasks=[far]))

    async def nothing_today() -> TaskList:
        return TaskList(available=True, tasks=[])

    setattr(srcs.todoist.reader, "read_today", nothing_today)  # noqa: B010 - un doble de un método
    run = plan_run()
    srv = server(conn, config_dir, srcs=srcs, run=run)
    to_today = {"action": "move", "task_id": "t-lejos", "due": "2030-01-07T15:00", "reason": "r"}
    _, is_error = asyncio.run(call_tool(srv, "propose_plan", {"items": [to_today]}))
    assert not is_error
    on_it = ON_THE_APPOINTMENT | {"due": "2030-01-07T15:30"}
    text, is_error = asyncio.run(call_tool(srv, "propose_plan", {"items": [on_it]}))
    assert is_error and "pisa «Tarea inventada» 15:00–16:00" in text


def test_con_read_calendar_bloqueado_no_se_lee_el_calendario(conn, config_dir):
    """Revisión propia: el rechazo no le enseña al agente un calendario que no puede leer."""
    counting = CountingCalendar(calendar_with(event(madrid("2030-01-07T10:00"))))
    base = agent_sources()
    srcs = Sources(todoist=base.todoist, calendar=counting, apex=base.apex)
    blocked = limits_of()
    blocked = blocked.model_copy(
        update={"tool_levels": {**blocked.tool_levels, "read_calendar": "blocked"}}
    )
    run = plan_run()
    _, is_error = call(
        conn,
        config_dir,
        "propose_plan",
        {"items": [ON_THE_APPOINTMENT]},
        srcs=srcs,
        run=run,
        limits=blocked,
    )
    assert not is_error and counting.reads == 0
    _, is_error = call(
        conn,
        config_dir,
        "propose_plan",
        {"items": [ON_THE_APPOINTMENT]},
        srcs=srcs,
        run=plan_run(),
        limits=limits_of(),
    )
    assert is_error and counting.reads == 1
