"""Todoist: lector sin escritura, escritor aparte, fallos con código y logs sin contenido."""

import asyncio
import json
import logging
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from apex_data import MADRID, madrid
from conftest import FixedClock
from todoist_data import (
    MAX_CALLS,
    PRIVATE_TITLE,
    REAL_PROJECT,
    TEST_PROJECT,
    FakeTodoistApi,
    FixedTodoistReader,
    RunawayDouble,
    http_status_error,
    raw_task,
)

from jimic.tools import todoist
from jimic.tools.failures import MAX_PAGES, ToolFailure
from jimic.tools.todoist import (
    ApiTodoistReader,
    ApiTodoistWriter,
    TaskList,
    TodoistReader,
    TodoistTask,
    TodoistWriter,
)

LOGGER = "jimic.tools.todoist"
NOW = madrid("2030-01-07 09:00")

READ_MEMBERS = {"read_today", "read_project", "get_task"}
WRITE_MEMBERS = {"create", "move", "complete", "delete"}


def make_reader(api: FakeTodoistApi) -> ApiTodoistReader:
    return ApiTodoistReader(api, FixedClock(NOW), MADRID)


def make_writer(api: FakeTodoistApi) -> ApiTodoistWriter:
    return ApiTodoistWriter(api, MADRID)


def run(coroutine):
    return asyncio.run(coroutine)


def public_members(cls: type) -> set[str]:
    return {name for name in dir(cls) if not name.startswith("_")}


def ids(result: TaskList) -> list[str]:
    return [task.id for task in result.tasks]


# --- Lector sin escritura, escritor aparte ---------------------------------------------------


def test_el_lector_no_tiene_ninguna_funcion_que_escriba():
    """Pilla un método de escritura en la interfaz del lector o en cualquiera de sus versiones."""
    for cls in (TodoistReader, ApiTodoistReader, todoist.GuardedReader, FixedTodoistReader):
        assert public_members(cls) == READ_MEMBERS, cls


def test_el_escritor_tiene_las_cuatro_funciones_y_ninguna_de_lectura():
    for cls in (TodoistWriter, ApiTodoistWriter, todoist.GuardedWriter):
        assert public_members(cls) == WRITE_MEMBERS, cls


def test_el_lector_y_el_escritor_son_objetos_distintos():
    """Pilla un solo objeto que lea y escriba: a la 1.10 se le da solo el lector."""
    api = FakeTodoistApi()
    assert not isinstance(make_reader(api), ApiTodoistWriter)
    assert not isinstance(make_writer(api), ApiTodoistReader)
    assert public_members(ApiTodoistReader) & public_members(ApiTodoistWriter) == set()


# --- Qué tarea llega --------------------------------------------------------------------------


def test_la_tarea_llega_con_su_id_titulo_proyecto_fecha_recurrencia_subtareas_y_duracion():
    api = FakeTodoistApi(
        [
            raw_task("padre", "Padre", due="2030-01-07T09:00:00", recurring=True, duration=75),
            raw_task("hija", "Hija", parent_id="padre", due="2030-01-07"),
        ]
    )
    padre, hija = run(make_reader(api).read_project(TEST_PROJECT)).tasks
    assert padre == TodoistTask(
        id="padre",
        title="Padre",
        project_id=TEST_PROJECT,
        due=datetime(2030, 1, 7, 9, 0, tzinfo=MADRID),
        is_recurring=True,
        has_subtasks=True,
        duration_minutes=75,
    )
    assert (hija.has_subtasks, hija.is_recurring, hija.duration_minutes) == (False, False, None)
    assert hija.due == date(2030, 1, 7)
    assert not isinstance(hija.due, datetime)


def test_una_duracion_en_dias_se_pasa_a_minutos():
    raw = raw_task("t", due="2030-01-07")
    raw["duration"] = {"amount": 2, "unit": "day"}
    (task,) = run(make_reader(FakeTodoistApi([raw])).read_project(TEST_PROJECT)).tasks
    assert task.duration_minutes == 2 * 24 * 60


def test_una_hora_en_utc_se_pasa_a_la_zona_de_la_configuracion_y_una_flotante_es_la_de_pedro():
    """Pilla no convertir la zona: 09:00Z es 10:00 en Madrid; la hora sin zona es ya la local."""
    api = FakeTodoistApi(
        [
            raw_task("utc", due="2030-01-07T09:00:00Z"),
            raw_task("flotante", due="2030-01-07T09:00:00"),
        ]
    )
    by_id = {t.id: t for t in run(make_reader(api).read_project(TEST_PROJECT)).tasks}
    assert by_id["utc"].due == datetime(2030, 1, 7, 10, 0, tzinfo=MADRID)
    assert by_id["flotante"].due == datetime(2030, 1, 7, 9, 0, tzinfo=MADRID)


# --- Hoy y vencidas ---------------------------------------------------------------------------


def test_hoy_y_vencidas_son_las_de_un_dia_local_hasta_hoy():
    """Con hoy = 2030-01-07 en Madrid. Pilla `<` por `<=`, ignorar la zona o meter lo sin fecha."""
    api = FakeTodoistApi(
        [
            raw_task("vencida", due="2030-01-06"),
            raw_task("hoy", due="2030-01-07"),
            raw_task("manana", due="2030-01-08"),
            raw_task("sin-fecha"),
            raw_task("flotante-tarde", due="2030-01-07T23:30:00"),
            raw_task("utc-ya-es-manana", due="2030-01-07T23:30:00Z"),  # 00:30 del 8 en Madrid
            raw_task("utc-ya-es-hoy", due="2030-01-06T23:30:00Z"),  # 00:30 del 7 en Madrid
        ]
    )
    result = run(make_reader(api).read_today())
    assert sorted(ids(result)) == ["flotante-tarde", "hoy", "utc-ya-es-hoy", "vencida"]


def test_el_orden_es_por_dia_luego_las_de_dia_entero_y_luego_por_hora():
    api = FakeTodoistApi(
        [
            raw_task("c-tarde", due="2030-01-07T18:00:00"),
            raw_task("b-temprano", due="2030-01-07T08:00:00"),
            raw_task("a-dia", due="2030-01-07"),
            raw_task("z-ayer-tarde", due="2030-01-06T20:00:00"),
        ]
    )
    assert ids(run(make_reader(api).read_today())) == [
        "z-ayer-tarde",
        "a-dia",
        "b-temprano",
        "c-tarde",
    ]


def test_hoy_usa_el_reloj_inyectado_y_no_el_del_sistema():
    api = FakeTodoistApi([raw_task("a", due="2030-01-07"), raw_task("b", due="2030-01-08")])
    clock = FixedClock(madrid("2030-01-08 07:00"))
    reader = ApiTodoistReader(api, clock, MADRID)
    assert ids(run(reader.read_today())) == ["a", "b"]


def test_el_dia_cambia_a_medianoche_de_madrid_y_no_de_utc():
    """A las 00:30 de Madrid ya es el 8 aunque en UTC aún sea el 7."""
    api = FakeTodoistApi([raw_task("b", due="2030-01-08")])
    clock = FixedClock(datetime(2030, 1, 8, 0, 30, tzinfo=MADRID))
    assert clock.now().astimezone(UTC).date() == date(2030, 1, 7)
    assert ids(run(ApiTodoistReader(api, clock, MADRID).read_today())) == ["b"]


def test_tener_subtareas_se_sabe_aunque_la_hija_no_sea_de_hoy():
    api = FakeTodoistApi(
        [
            raw_task("padre", due="2030-01-07"),
            raw_task("hija", parent_id="padre", due="2030-02-01"),
        ]
    )
    (padre,) = run(make_reader(api).read_today()).tasks
    assert padre.id == "padre"
    assert padre.has_subtasks


def test_el_proyecto_se_pide_a_la_api_y_no_se_filtra_despues():
    """Pilla traer todo y filtrar: con proyecto, la petición lleva el `project_id`."""
    api = FakeTodoistApi([raw_task("a"), raw_task("b", project_id=REAL_PROJECT)])
    result = run(make_reader(api).read_project(TEST_PROJECT))
    assert ids(result) == ["a"]
    assert api.calls_of("get_tasks")[0]["project_id"] == TEST_PROJECT
    run(make_reader(api).read_today(TEST_PROJECT))
    assert api.calls_of("get_tasks")[1]["project_id"] == TEST_PROJECT


# --- Paginación con tope ----------------------------------------------------------------------


def many(count: int) -> list[dict[str, Any]]:
    return [raw_task(f"t{n}", due="2030-01-07") for n in range(count)]


def test_las_paginas_se_recorren_todas_y_se_pide_el_tamano_maximo():
    api = FakeTodoistApi(many(5), page_size=2)
    result = run(make_reader(api).read_project(TEST_PROJECT))
    assert ids(result) == [f"t{n}" for n in range(5)]
    assert api.calls_of("get_tasks")[0]["limit"] == 200


def test_diez_paginas_se_leen():
    api = FakeTodoistApi(many(MAX_PAGES), page_size=1)
    assert run(make_reader(api).read_project(TEST_PROJECT)).available


def test_once_paginas_son_un_error_claro_y_no_un_bucle():
    """Pilla la paginación sin tope."""
    api = FakeTodoistApi(many(MAX_PAGES + 1), page_size=1)
    result = run(make_reader(api).read_project(TEST_PROJECT))
    assert result.unavailable_reason == "too_many_pages"


def test_el_doble_lanza_un_error_claro_si_se_le_llama_en_bucle():
    """El tope del doble: el error no es una `Exception`, así que el lector no lo disimula."""
    api = FakeTodoistApi()
    with pytest.raises(RunawayDouble, match=str(MAX_CALLS)):
        for _ in range(MAX_CALLS + 2):
            api.record("get_task")


# --- get_task ---------------------------------------------------------------------------------


def test_get_task_devuelve_la_tarea_y_si_tiene_subtareas():
    api = FakeTodoistApi([raw_task("padre"), raw_task("hija", parent_id="padre")])
    reader = make_reader(api)
    assert run(reader.get_task("padre")).has_subtasks  # type: ignore[union-attr] # existe
    assert not run(reader.get_task("hija")).has_subtasks  # type: ignore[union-attr] # existe
    assert api.calls_of("get_tasks")[0]["parent_id"] == "padre"


def test_get_task_de_una_tarea_que_no_existe_es_none():
    assert run(make_reader(FakeTodoistApi()).get_task("no-existe")) is None


def test_get_task_si_falla_lanza_con_el_codigo_y_sin_el_titulo():
    api = FakeTodoistApi([raw_task("a", PRIVATE_TITLE)], error=http_status_error(500))
    with pytest.raises(ToolFailure) as caught:
        run(make_reader(api).get_task("a"))
    assert caught.value.code == "http_500"
    assert PRIVATE_TITLE not in str(caught.value)


# --- Fallos con código ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (http_status_error(401), "auth"),
        (http_status_error(403), "auth"),
        (http_status_error(429), "http_429"),
        (http_status_error(503), "http_503"),
        (TimeoutError("lento"), "timeout"),
        (httpx.ReadTimeout("lento"), "timeout"),
        (httpx.ConnectError("sin red"), "network"),
        (ConnectionError("sin red"), "network"),
        (ValueError(PRIVATE_TITLE), "invalid_format"),
        (TypeError(PRIVATE_TITLE), "invalid_format"),
        (RuntimeError(PRIVATE_TITLE), "unexpected:RuntimeError"),
    ],
)
def test_cada_fallo_da_su_codigo_y_el_lector_no_lanza(error, code):
    """Pilla un fallo que lance al servicio o un código que dependa del mensaje."""
    result = run(make_reader(FakeTodoistApi(error=error)).read_today())
    assert not result.available
    assert result.unavailable_reason == code
    assert result.tasks == []


def test_los_errores_de_la_biblioteca_al_leer_lo_que_llega_son_formato_invalido():
    """La biblioteca lanza un error propio si a la tarea le faltan campos."""
    broken = raw_task("a")
    del broken["project_id"]
    result = run(make_reader(FakeTodoistApi([broken])).read_today())
    assert result.unavailable_reason == "invalid_format"


def test_una_llamada_que_no_responde_acaba_en_timeout(monkeypatch):
    """Pilla una llamada sin límite de tiempo: el servicio no se queda esperando."""
    monkeypatch.setattr(todoist, "CALL_TIMEOUT", 0.05)
    api = FakeTodoistApi([raw_task("a")])
    api.hang = True
    result = run(make_reader(api).read_today())
    assert result.unavailable_reason == "timeout"


# --- Avisos y logs ----------------------------------------------------------------------------


def warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_el_aviso_sale_una_sola_vez_mientras_siga_roto(caplog):
    api = FakeTodoistApi(error=http_status_error(503))
    reader = make_reader(api)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(3):
            run(reader.read_today())
    assert warnings(caplog) == ["Todoist unavailable (http_503)"]


def test_si_se_recupera_y_vuelve_a_fallar_avisa_otra_vez(caplog):
    api = FakeTodoistApi([raw_task("a", due="2030-01-07")])
    reader = make_reader(api)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        api.error = http_status_error(503)
        run(reader.read_today())
        api.error = None
        assert run(reader.read_today()).available
        api.error = TimeoutError()
        run(reader.read_today())
    assert warnings(caplog) == ["Todoist unavailable (http_503)", "Todoist unavailable (timeout)"]


def test_un_todoist_que_funciona_no_deja_avisos(caplog):
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        run(make_reader(FakeTodoistApi([raw_task("a", due="2030-01-07")])).read_today())
    assert caplog.records == []


def test_ningun_titulo_ni_proyecto_llega_a_los_logs_ni_a_los_codigos_ni_al_repr(caplog):
    """Pilla un título o un proyecto en un log, en un código de fallo o en un `repr`."""
    working = FakeTodoistApi([raw_task("a", PRIVATE_TITLE, due="2030-01-07")])
    failing = FakeTodoistApi(error=http_status_error(500))
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        good = run(make_reader(working).read_today())
        bad = run(make_reader(failing).read_today())
    assert good.tasks[0].title == PRIVATE_TITLE  # llega a quien lo usa
    for text in (caplog.text, repr(good), repr(good.tasks[0]), repr(bad)):
        assert PRIVATE_TITLE not in text
        assert TEST_PROJECT not in text
    assert bad.unavailable_reason == "http_500"


def test_la_lista_dice_su_forma_en_json_sin_campos_vacios():
    assert json.loads(TaskList.unavailable("auth").to_json()) == {
        "available": False,
        "unavailable_reason": "auth",
        "tasks": [],
    }


def test_el_doble_de_datos_fijos_devuelve_la_lista_que_se_le_da():
    given = TaskList(available=False, unavailable_reason="auth")
    assert run(FixedTodoistReader(given).read_today()) is given


# --- Escritor ---------------------------------------------------------------------------------


def test_crear_con_dia_hora_y_duracion_manda_a_la_api_lo_que_toca():
    api = FakeTodoistApi()
    writer = make_writer(api)
    created = run(
        writer.create("Gimnasio", TEST_PROJECT, datetime(2030, 1, 7, 12, 0, tzinfo=MADRID), 75)
    )
    (call,) = api.calls_of("add_task")
    assert call["content"] == "Gimnasio"
    assert call["project_id"] == TEST_PROJECT
    assert call["due_datetime"] == datetime(2030, 1, 7, 12, 0, tzinfo=MADRID)
    assert call["due_date"] is None
    assert (call["duration"], call["duration_unit"]) == (75, "minute")
    assert created.id == "t101"
    assert created.duration_minutes == 75
    assert created.due == datetime(2030, 1, 7, 12, 0, tzinfo=MADRID)


def test_crear_con_un_dia_sin_duracion():
    api = FakeTodoistApi()
    run(make_writer(api).create("Llamar", TEST_PROJECT, date(2030, 1, 7)))
    (call,) = api.calls_of("add_task")
    assert call["due_date"] == date(2030, 1, 7)
    assert call["due_datetime"] is None
    assert (call["duration"], call["duration_unit"]) == (None, None)


def test_una_hora_sin_zona_se_rechaza_sin_llamar_a_la_api():
    """Pilla enviar una hora sin zona: ¿de qué zona? Lo dice quien llama."""
    api = FakeTodoistApi()
    for operation in (
        make_writer(api).create("x", TEST_PROJECT, datetime(2030, 1, 7, 12, 0)),
        make_writer(api).move("a", datetime(2030, 1, 7, 12, 0)),
    ):
        with pytest.raises(ToolFailure) as caught:
            run(operation)
        assert caught.value.code == "invalid_due"
    assert api.calls == []


@pytest.mark.parametrize("minutes", [0, -5])
def test_una_duracion_menor_que_uno_se_rechaza(minutes):
    api = FakeTodoistApi()
    with pytest.raises(ToolFailure) as caught:
        run(make_writer(api).create("x", TEST_PROJECT, date(2030, 1, 7), minutes))
    assert caught.value.code == "invalid_duration"
    assert api.calls == []


def test_mover_cambia_el_dia_o_la_hora():
    api = FakeTodoistApi([raw_task("a", due="2030-01-07")])
    writer = make_writer(api)
    run(writer.move("a", date(2030, 1, 9)))
    run(writer.move("a", datetime(2030, 1, 9, 18, 0, tzinfo=MADRID)))
    first, second = api.calls_of("update_task")
    assert (first["due_date"], first["due_datetime"]) == (date(2030, 1, 9), None)
    assert (second["due_date"], second["due_datetime"]) == (
        None,
        datetime(2030, 1, 9, 18, 0, tzinfo=MADRID),
    )


def test_completar_cierra_la_tarea_y_borrar_la_borra():
    api = FakeTodoistApi([raw_task("a"), raw_task("b")])
    writer = make_writer(api)
    run(writer.complete("a"))
    run(writer.delete("b"))
    assert api.calls_of("complete_task") == [{"task_id": "a"}]
    assert api.calls_of("delete_task") == [{"task_id": "b"}]
    assert api.tasks == []


@pytest.mark.parametrize("operation", ["move", "complete", "delete"])
def test_una_escritura_que_falla_lanza_con_el_codigo_y_sin_el_titulo(operation):
    api = FakeTodoistApi([raw_task("a", PRIVATE_TITLE)], error=http_status_error(500))
    writer = make_writer(api)
    arguments = ("a", date(2030, 1, 9)) if operation == "move" else ("a",)
    with pytest.raises(ToolFailure) as caught:
        run(getattr(writer, operation)(*arguments))
    assert caught.value.code == "http_500"
    assert PRIVATE_TITLE not in str(caught.value)


@pytest.mark.parametrize(
    ("operation", "code"), [("complete", "not_completed"), ("delete", "not_deleted")]
)
def test_si_la_api_dice_que_no_lo_hizo_es_un_error(operation, code, monkeypatch):
    api = FakeTodoistApi([raw_task("a")])

    async def refuse(task_id: str) -> bool:
        return False

    monkeypatch.setattr(api, "complete_task" if operation == "complete" else "delete_task", refuse)
    with pytest.raises(ToolFailure) as caught:
        run(getattr(make_writer(api), operation)("a"))
    assert caught.value.code == code


def test_una_escritura_tambien_tiene_limite_de_tiempo(monkeypatch):
    monkeypatch.setattr(todoist, "CALL_TIMEOUT", 0.05)
    api = FakeTodoistApi([raw_task("a")])
    api.hang = True
    with pytest.raises(ToolFailure) as caught:
        run(make_writer(api).delete("a"))
    assert caught.value.code == "timeout"
