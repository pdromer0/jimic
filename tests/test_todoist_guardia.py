"""La guardia de `dev` (1.5-b): el JimiC de pruebas solo ve y toca su proyecto de Todoist.

El proyecto de pruebas vive en la cuenta real de Pedro, así que la guardia es la única barrera
y estos tests son obligatorios. Se prueba con dobles: nunca contra tareas reales.
"""

import asyncio
import logging
import os
from datetime import date, datetime

import pytest
from apex_data import MADRID, madrid
from conftest import VALID_ENV, FixedClock, write_env
from todoist_data import (
    PRIVATE_TITLE,
    REAL_PROJECT,
    TEST_PROJECT,
    FakeTodoistApi,
    FixedTodoistReader,
    http_status_error,
    raw_task,
)

from jimic.config import Settings, load_settings
from jimic.paths import resolve_paths
from jimic.tools.failures import ToolFailure
from jimic.tools.todoist import (
    OUTSIDE_TEST_PROJECT,
    TASK_NOT_FOUND,
    ApiTodoistReader,
    ApiTodoistWriter,
    GuardedReader,
    GuardedWriter,
    TaskList,
    TodoistTask,
    TodoistTools,
    build_todoist,
)

LOGGER = "jimic.tools.todoist"
CLOCK = FixedClock(madrid("2030-01-07 09:00"))
WRITES = ("update_task", "complete_task", "delete_task", "add_task")


def run(coroutine):
    return asyncio.run(coroutine)


def settings_for(environment: str, monkeypatch) -> Settings:
    """The settings of a copy, from a real `.env` in a fake home (the code validates it)."""
    values = {**VALID_ENV, "ENTORNO": environment}
    if environment == "dev":
        monkeypatch.setenv("JIMIC_ENTORNO", "dev")
        values["PROYECTO_PRUEBAS_TODOIST"] = TEST_PROJECT
    write_env(values)
    return load_settings(resolve_paths(os.environ))


def account() -> FakeTodoistApi:
    """An account with tasks in the test project and in a real one (with a secret title)."""
    return FakeTodoistApi(
        [
            raw_task("prueba-1", "Tarea de pruebas", TEST_PROJECT, due="2030-01-07"),
            raw_task("prueba-2", "Otra de pruebas", TEST_PROJECT, due="2030-01-06"),
            raw_task("real-1", PRIVATE_TITLE, REAL_PROJECT, due="2030-01-07"),
            raw_task("real-2", "Otra real", REAL_PROJECT, due="2030-01-07"),
        ]
    )


@pytest.fixture
def dev(monkeypatch) -> tuple[TodoistTools, FakeTodoistApi]:
    api = account()
    return build_todoist(settings_for("dev", monkeypatch), CLOCK, api), api


def write_calls(api: FakeTodoistApi) -> list[str]:
    return [name for name, _ in api.calls if name in WRITES]


# --- Lectura ----------------------------------------------------------------------------------


def test_en_dev_la_lectura_pide_a_la_api_solo_el_proyecto_de_pruebas(dev):
    """Pilla una lectura sin filtrar: las tareas reales ni se descargan."""
    tools, api = dev
    result = run(tools.reader.read_today())
    assert sorted(t.id for t in result.tasks) == ["prueba-1", "prueba-2"]
    assert {call["project_id"] for call in api.calls_of("get_tasks")} == {TEST_PROJECT}
    assert PRIVATE_TITLE not in result.to_json()  # el JSON lleva los títulos: ninguno es real


def test_en_dev_la_lectura_de_un_proyecto_lo_acota_igual(dev):
    tools, api = dev
    result = run(tools.reader.read_project(TEST_PROJECT))
    assert sorted(t.id for t in result.tasks) == ["prueba-1", "prueba-2"]
    assert {call["project_id"] for call in api.calls_of("get_tasks")} == {TEST_PROJECT}


@pytest.mark.parametrize("read", ["read_today", "read_project"])
def test_pedir_otro_proyecto_no_llega_a_la_api(dev, read):
    """Pilla pedir un proyecto real: no está disponible y la API ni se llama."""
    tools, api = dev
    result = run(getattr(tools.reader, read)(REAL_PROJECT))
    assert not result.available
    assert result.unavailable_reason == OUTSIDE_TEST_PROJECT
    assert api.calls == []


def test_si_el_lector_de_dentro_trajera_tareas_de_otro_proyecto_la_guardia_las_descarta(caplog):
    """Segunda barrera: aunque la petición se acote, lo que no sea del proyecto no sale."""
    leaking = FixedTodoistReader(
        TaskList(
            available=True,
            tasks=[
                TodoistTask(
                    id=f"t-{project}",
                    title=PRIVATE_TITLE if project == REAL_PROJECT else "propia",
                    project_id=project,
                    due=date(2030, 1, 7),
                    is_recurring=False,
                    has_subtasks=False,
                    duration_minutes=None,
                )
                for project in (TEST_PROJECT, REAL_PROJECT)
            ],
        )
    )
    guarded = GuardedReader(leaking, TEST_PROJECT)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        today = run(guarded.read_today())
        project = run(guarded.read_project(TEST_PROJECT))
    assert [t.id for t in today.tasks] == [f"t-{TEST_PROJECT}"]
    assert [t.id for t in project.tasks] == [f"t-{TEST_PROJECT}"]
    assert [r.getMessage() for r in caplog.records] == [
        "the guard dropped 1 tasks outside the test project"
    ] * 2
    assert PRIVATE_TITLE not in caplog.text


def test_get_task_de_otro_proyecto_lanza_con_codigo_y_sin_titulo(dev, caplog):
    tools, _ = dev
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        with pytest.raises(ToolFailure) as caught:
            run(tools.reader.get_task("real-1"))
    assert caught.value.code == OUTSIDE_TEST_PROJECT
    assert PRIVATE_TITLE not in str(caught.value) + repr(caught.value) + caplog.text


def test_get_task_de_una_tarea_de_pruebas_y_de_una_que_no_existe(dev):
    tools, _ = dev
    task = run(tools.reader.get_task("prueba-1"))
    assert task is not None and task.id == "prueba-1"
    assert run(tools.reader.get_task("no-existe")) is None


# --- Escritura --------------------------------------------------------------------------------


def operation(tools: TodoistTools, name: str, task_id: str):
    if name == "move":
        return tools.writer.move(task_id, date(2030, 1, 9))
    return getattr(tools.writer, name)(task_id)


@pytest.mark.parametrize("name", ["move", "complete", "delete"])
def test_no_se_mueve_ni_se_completa_ni_se_borra_una_tarea_de_otro_proyecto(dev, name, caplog):
    """Pilla una escritura sin comprobar el proyecto: la API no recibe ninguna orden."""
    tools, api = dev
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        with pytest.raises(ToolFailure) as caught:
            run(operation(tools, name, "real-1"))
    assert caught.value.code == OUTSIDE_TEST_PROJECT
    assert write_calls(api) == []
    assert PRIVATE_TITLE not in str(caught.value) + repr(caught.value) + caplog.text
    assert api.find("real-1") is not None


@pytest.mark.parametrize("name", ["move", "complete", "delete"])
def test_tampoco_si_la_tarea_no_existe(dev, name):
    tools, api = dev
    with pytest.raises(ToolFailure) as caught:
        run(operation(tools, name, "no-existe"))
    assert caught.value.code == TASK_NOT_FOUND
    assert write_calls(api) == []


@pytest.mark.parametrize(
    ("name", "write"),
    [("move", "update_task"), ("complete", "complete_task"), ("delete", "delete_task")],
)
def test_con_una_tarea_de_pruebas_se_lee_fresca_y_despues_se_escribe(dev, name, write):
    tools, api = dev
    run(operation(tools, name, "prueba-1"))
    calls = [call for call, _ in api.calls if call in ("get_task", write)]
    assert calls[0] == "get_task"  # primero se lee fresca
    assert calls[-1] == write and calls.count(write) == 1  # y la escritura va al final, una vez
    assert api.calls_of(write)[0]["task_id"] == "prueba-1"


def test_crear_va_siempre_al_proyecto_de_pruebas_pida_lo_que_pida(dev):
    """Pilla un `create` que respete el proyecto que le pasan."""
    tools, api = dev
    created = run(
        tools.writer.create(
            "jimic-contrato-1", REAL_PROJECT, datetime(2030, 1, 7, 12, 0, tzinfo=MADRID), 30
        )
    )
    (call,) = api.calls_of("add_task")
    assert call["project_id"] == TEST_PROJECT
    assert created.project_id == TEST_PROJECT


def test_si_la_lectura_previa_falla_no_se_escribe(dev, monkeypatch):
    """Sin poder comprobar el proyecto, no se toca nada."""
    tools, api = dev

    async def broken(task_id: str):
        raise http_status_error(500)

    monkeypatch.setattr(api, "get_task", broken)
    with pytest.raises(ToolFailure) as caught:
        run(tools.writer.delete("prueba-1"))
    assert caught.value.code == "http_500"
    assert write_calls(api) == []


# --- build_todoist ----------------------------------------------------------------------------


def test_en_dev_build_todoist_devuelve_la_guardia(dev):
    """Pilla que el servicio pueda olvidarse de la guardia: se aplica al construir."""
    tools, _ = dev
    assert isinstance(tools.reader, GuardedReader)
    assert isinstance(tools.writer, GuardedWriter)


def test_en_prod_build_todoist_no_pone_guardia_y_lee_toda_la_cuenta(monkeypatch):
    api = account()
    tools = build_todoist(settings_for("prod", monkeypatch), CLOCK, api)
    assert isinstance(tools.reader, ApiTodoistReader)
    assert isinstance(tools.writer, ApiTodoistWriter)
    result = run(tools.reader.read_today())
    assert len(result.tasks) == 4
    assert api.calls_of("get_tasks")[0]["project_id"] is None


def test_en_dev_sin_proyecto_de_pruebas_no_se_construye_y_el_error_no_dice_valores(monkeypatch):
    settings = settings_for("dev", monkeypatch).model_copy(update={"todoist_test_project": None})
    with pytest.raises(ValueError, match="PROYECTO_PRUEBAS_TODOIST") as caught:
        build_todoist(settings, CLOCK, account())
    assert TEST_PROJECT not in str(caught.value)


def test_cerrar_las_herramientas_cierra_la_conexion(dev):
    tools, api = dev
    run(tools.close())
    assert api.closed


def test_sin_api_dada_se_construye_la_de_la_biblioteca_con_el_token_de_la_configuracion(
    monkeypatch,
):
    """Sin red: solo se construye y se cierra."""
    tools = build_todoist(settings_for("prod", monkeypatch), CLOCK)
    assert isinstance(tools.reader, ApiTodoistReader)
    run(tools.close())
