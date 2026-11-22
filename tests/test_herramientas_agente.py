"""Las herramientas del agente: exactamente las de la tabla, que solo leen y proponen (H1-1).

Se listan y se llaman por el puente MCP del SDK, el mismo camino que usa el motor. Las fuentes
son dobles con datos inventados y el escritor de Todoist es un espía que no debe llamarse nunca.
"""

import asyncio
import json
import logging
import os
import stat

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
from heartbeat_data import task

from jimic.agent.tools import build_server, model_name
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


def test_propose_plan_guarda_la_aprobacion_y_no_llama_al_escritor(conn, config_dir):
    """Pilla `propose_plan` que llama al escritor (versión incorrecta 5): el espía no debe ver
    ni una llamada, y la propuesta queda como datos, pendiente, con la foto de lo que mueve o
    borra y caducidad al acabar el día."""
    spy = SpyWriter()
    items = [
        CREATE | {"duration_minutes": 30},
        {"action": "move", "task_id": "t1", "due": "2030-01-08", "reason": "r"},
        {"action": "delete", "task_id": "t2", "reason": "r"},
    ]
    text, is_error = call(
        conn, config_dir, "propose_plan", {"items": items}, srcs=agent_sources(writer=spy)
    )
    assert not is_error
    assert spy.calls == []
    ((kind, payload, status, expires),) = approvals(conn)
    assert (kind, status, expires) == ("plan", "pending", "2030-01-07T23:00:00+00:00")
    assert [item["action"] for item in payload["items"]] == ["create", "move", "delete"]
    assert "snapshot" not in payload["items"][0]
    assert payload["items"][1]["snapshot"] == {
        "title": SECRET_TITLE,
        "due": "2030-01-07",
        "project_id": "proyecto-inventado",
    }
    assert text.startswith("Propuesta 1 guardada: crear 1, mover 1, borrar 1. No se ha ejecutado")


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
    ],
)
def test_una_propuesta_no_valida_se_rechaza_con_un_mensaje_y_no_guarda_nada(
    conn, config_dir, items
):
    text, is_error = call(conn, config_dir, "propose_plan", {"items": items})
    assert is_error and text
    assert approvals(conn) == []


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


# --- propose_change -----------------------------------------------------------------------------


CHANGE = {"file": "preferencias.md", "operation": "add", "text": "Café a las 11", "reason": "r"}


def test_propose_change_guarda_el_cambio_sin_tocar_el_fichero(conn, config_dir):
    text, is_error = call(conn, config_dir, "propose_change", CHANGE)
    assert not is_error
    ((kind, payload, status, _),) = approvals(conn)
    assert (kind, status) == ("memory_change", "pending")
    assert payload == CHANGE | {"after_external_content": False}
    assert list(config_dir.iterdir()) == []


def test_tras_leer_las_tareas_en_la_sesion_del_dia_el_cambio_lleva_la_marca(conn, config_dir):
    """Ajuste 4 del chat: se mide por sesión del día, no por ejecución. Dos servidores (dos
    mensajes de Pedro) del mismo día: el primero lee las tareas, el segundo propone."""
    call(conn, config_dir, "read_tasks", {})
    call(conn, config_dir, "propose_change", CHANGE, now="2030-01-07T17:00")
    ((_, payload, _, _),) = approvals(conn)
    assert payload["after_external_content"] is True


def test_al_dia_siguiente_la_marca_empieza_de_cero(conn, config_dir):
    call(conn, config_dir, "read_tasks", {})
    call(conn, config_dir, "propose_change", CHANGE, now="2030-01-08T09:00")
    ((_, payload, _, _),) = approvals(conn)
    assert payload["after_external_content"] is False


@pytest.mark.parametrize(
    "change",
    [CHANGE | {"file": ".env"}, CHANGE | {"operation": "rewrite"}, CHANGE | {"text": "  "}],
)
def test_un_cambio_no_valido_se_rechaza(conn, config_dir, change):
    _, is_error = call(conn, config_dir, "propose_change", change)
    assert is_error and approvals(conn) == []


# --- log_idea -----------------------------------------------------------------------------------


def test_log_idea_anade_una_linea_con_fecha_y_no_reescribe(conn, config_dir):
    ideas = config_dir / "ideas.md"
    ideas.write_text("# Ideas\n- 2030-01-01 · una anterior\n", encoding="utf-8")
    os.chmod(ideas, 0o600)
    call(conn, config_dir, "log_idea", {"text": "  Probar   la app\nde notas  "})
    call(conn, config_dir, "log_idea", {"text": "Otra 🎯"})
    assert ideas.read_text(encoding="utf-8") == (
        "# Ideas\n- 2030-01-01 · una anterior\n"
        "- 2030-01-07 · Probar la app de notas\n"
        "- 2030-01-07 · Otra 🎯\n"
    )


def test_log_idea_crea_el_fichero_privado(conn, config_dir):
    call(conn, config_dir, "log_idea", {"text": "Primera"})
    assert stat.S_IMODE((config_dir / "ideas.md").stat().st_mode) == 0o600


@pytest.mark.parametrize(("length", "ok"), [(500, True), (501, False)])
def test_log_idea_tiene_tope_por_linea(conn, config_dir, length, ok):
    _, is_error = call(conn, config_dir, "log_idea", {"text": "x" * length})
    assert is_error is not ok
    assert (config_dir / "ideas.md").exists() is ok


def test_log_idea_vacia_tras_juntar_espacios_se_rechaza(conn, config_dir):
    _, is_error = call(conn, config_dir, "log_idea", {"text": " \n\t "})
    assert is_error and not (config_dir / "ideas.md").exists()


# --- Logs ---------------------------------------------------------------------------------------


def test_los_logs_de_las_herramientas_no_llevan_contenido(conn, config_dir, caplog):
    caplog.set_level(logging.DEBUG)
    srcs = agent_sources(TaskList(available=True, tasks=[task("t1", title=SECRET_TITLE)]))
    call(conn, config_dir, "read_tasks", {}, srcs=srcs)
    call(conn, config_dir, "log_idea", {"text": SECRET_IDEA})
    call(conn, config_dir, "propose_plan", {"items": [CREATE | {"title": SECRET_ARG}]})
    call(conn, config_dir, "propose_change", CHANGE | {"text": SECRET_ARG})
    call(conn, config_dir, "propose_plan", {"items": [CREATE | {"due": SECRET_ARG}]})
    for secret in (SECRET_TITLE, SECRET_IDEA, SECRET_ARG):
        assert secret not in caplog.text
    assert "tool propose_plan: ok" in caplog.text


def test_un_calendario_caido_no_es_un_error_del_agente(conn, config_dir):
    srcs = agent_sources()
    srcs.calendar.day = CalendarDay.unavailable("auth")  # pyright: ignore[reportAttributeAccessIssue] - the double's day
    text, is_error = call(conn, config_dir, "read_calendar", {}, srcs=srcs)
    assert json.loads(text)["unavailable_reason"] == "auth" and not is_error
