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

READ_MEMBERS = {
    "read_today",
    "read_project",
    "read_day",
    "get_task",
    "get_photo",
    "read_completed_today",
}
"""`read_completed_today`, de la 1.15: el «hecho» de una tarea clave marcada en la app;
`read_day`, de la 1.16: lo que ya hay mañana cuando el cierre mueve una tarea."""
WRITE_MEMBERS = {"create", "move", "complete", "delete", "recreate", "reopen"}
"""Las cuatro de la 1.5, `recreate` (el /deshacer de un borrado, 1.12) y `reopen` (el [Deshacer]
de [Ya entrené], 1.15)."""


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


def test_el_escritor_tiene_sus_seis_funciones_y_ninguna_de_lectura():
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
    assert call["description"] == "Duración: 75 min"
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
    assert call["description"] is None


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
    assert [raw["id"] for raw in api.tasks] == ["a"] and api.tasks[0]["checked"]
    assert run(make_reader(api).read_project(TEST_PROJECT)).tasks == []


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


# --- La duración en el plan gratuito: la descripción la lleva --------------------------------


def test_en_el_plan_gratuito_la_duracion_se_conserva_por_la_descripcion():
    """Contrato del 19-nov-2026: Todoist gratuito no guarda `duration`. Pilla perder los 75 min."""
    api = FakeTodoistApi(free_plan=True)
    created = run(make_writer(api).create("Gimnasio", TEST_PROJECT, date(2030, 1, 7), 75))
    assert api.find(created.id)["duration"] is None  # type: ignore[index] # existe
    (read,) = run(make_reader(api).read_project(TEST_PROJECT)).tasks
    assert read.duration_minutes == 75
    assert created.duration_minutes == 75


def test_con_el_plan_de_pago_la_duracion_viene_del_campo_y_manda_sobre_la_descripcion():
    api = FakeTodoistApi(free_plan=False)
    run(make_writer(api).create("Gimnasio", TEST_PROJECT, date(2030, 1, 7), 75))
    api.tasks[0]["description"] = "Duración: 10 min"  # la descripción no manda si hay campo
    (read,) = run(make_reader(api).read_project(TEST_PROJECT)).tasks
    assert api.tasks[0]["duration"]["amount"] == 75
    assert read.duration_minutes == 75


@pytest.mark.parametrize(
    ("description", "minutes"),
    [
        ("Duración: 75 min", 75),
        ("Nota mía\nDuración: 20 min\notra línea", 20),
        ("Duración: 0 min", None),
        ("Duración: 75 minutos", None),
        ("duración: 75 min", None),
        ("Duración: abc min", None),
        ("Duración: ٧٥ min", None),  # dígitos que no son ASCII
        ("Duración: 123456 min", None),
        ("Dura 75", None),
        ("", None),
    ],
)
def test_la_duracion_se_lee_solo_de_la_linea_exacta_de_la_descripcion(description, minutes):
    """Pilla una expresión demasiado permisiva: solo la línea `Duración: N min`, con N ASCII."""
    api = FakeTodoistApi([raw_task("a", description=description)])
    (task,) = run(make_reader(api).read_project(TEST_PROJECT)).tasks
    assert task.duration_minutes == minutes


# --- Mover una recurrente: la recurrencia se conserva -----------------------------------------


def recurring_api(due_string: str = "every day") -> FakeTodoistApi:
    return FakeTodoistApi(
        [raw_task("rec", due="2030-01-07", recurring=True, due_string=due_string)]
    )


@pytest.mark.parametrize(
    "target", [date(2030, 1, 9), datetime(2030, 1, 9, 18, 0, tzinfo=MADRID)], ids=["dia", "hora"]
)
def test_mover_una_recurrente_manda_el_texto_de_su_recurrencia_y_sigue_siendo_recurrente(target):
    """El contrato del 19-nov-2026: sin `due_string` la recurrente pasa a ser una normal."""
    api = recurring_api("every day at 9:00")
    run(make_writer(api).move("rec", target))
    (call,) = api.calls_of("update_task")
    assert call["due_string"] == "every day at 9:00"
    (task,) = run(make_reader(api).read_project(TEST_PROJECT)).tasks
    assert task.is_recurring


def test_el_texto_de_la_recurrencia_viaja_con_su_idioma():
    """Pilla un texto en español mandado como si fuera inglés: Todoist lo leería mal."""
    api = FakeTodoistApi(
        [
            raw_task(
                "rec", due="2030-01-07", recurring=True, due_string="todos los días", due_lang="es"
            )
        ]
    )
    run(make_writer(api).move("rec", date(2030, 1, 9)))
    (call,) = api.calls_of("update_task")
    assert (call["due_string"], call["due_lang"]) == ("todos los días", "es")


def test_mover_una_normal_no_manda_texto_de_recurrencia():
    """Pilla un `due_string` en una tarea normal: su texto es la fecha, no una recurrencia."""
    api = FakeTodoistApi([raw_task("a", due="2030-01-07", due_string="2030-01-07")])
    run(make_writer(api).move("a", date(2030, 1, 9)))
    (call,) = api.calls_of("update_task")
    assert (call["due_string"], call["due_lang"]) == (None, None)
    (task,) = run(make_reader(api).read_project(TEST_PROJECT)).tasks
    assert not task.is_recurring and task.due == date(2030, 1, 9)


def test_el_doble_imita_a_todoist_sin_el_texto_la_recurrente_se_pierde():
    """La prueba del doble: es lo que la API real hizo en el contrato."""
    api = recurring_api()
    run(api.update_task("rec", due_date=date(2030, 1, 9)))
    assert api.tasks[0]["due"]["is_recurring"] is False


def test_mover_una_tarea_que_no_existe_es_task_not_found_y_no_escribe():
    api = FakeTodoistApi()
    with pytest.raises(ToolFailure) as caught:
        run(make_writer(api).move("no-existe", date(2030, 1, 9)))
    assert caught.value.code == "task_not_found"
    assert api.calls_of("update_task") == []


def test_si_falla_la_lectura_previa_al_mover_no_se_escribe():
    api = FakeTodoistApi([raw_task("a")], error=http_status_error(503))
    with pytest.raises(ToolFailure) as caught:
        run(make_writer(api).move("a", date(2030, 1, 9)))
    assert caught.value.code == "http_503"
    assert api.calls_of("update_task") == []


# --- La foto, la bandeja de entrada y quitar la fecha (tarea 1.11) ----------------------------


def test_la_foto_lleva_descripcion_etiquetas_y_prioridad_y_la_tarea_igual_que_get_task():
    """Pilla una foto sin lo que `deleted_tasks` guarda, o con otra tarea que la de `get_task`."""
    raw = raw_task("t1", due="2030-01-07", description="Notas inventadas")
    raw["labels"] = ["casa", "rápido"]
    raw["priority"] = 3
    api = FakeTodoistApi([raw, raw_task("hija", parent_id="t1")])
    reader = make_reader(api)
    photo = run(reader.get_photo("t1"))
    assert photo is not None
    assert (photo.description, photo.labels, photo.priority) == (
        "Notas inventadas",
        ("casa", "rápido"),
        3,
    )
    assert photo.task == run(reader.get_task("t1"))
    assert photo.task.has_subtasks


def test_la_foto_de_una_tarea_que_no_existe_es_none():
    assert run(make_reader(FakeTodoistApi()).get_photo("nada")) is None


def test_la_foto_no_ensena_la_descripcion_en_su_repr():
    raw = raw_task("t1", title=PRIVATE_TITLE, description=PRIVATE_TITLE)
    photo = run(make_reader(FakeTodoistApi([raw])).get_photo("t1"))
    assert PRIVATE_TITLE not in repr(photo)


def test_crear_sin_proyecto_va_a_la_bandeja_de_entrada():
    """Pilla un proyecto inventado cuando se pide la bandeja (`project_id` ausente en la API)."""
    api = FakeTodoistApi()
    run(make_writer(api).create("Tarea nueva", None, date(2030, 1, 7)))
    assert api.calls_of("add_task")[0]["project_id"] is None


def test_mover_a_none_quita_la_fecha_con_no_date():
    """Pilla un [Deshacer] que no devuelve a «sin fecha» lo que no la tenía."""
    api = FakeTodoistApi([raw_task("t1", due="2030-01-07")])
    run(make_writer(api).move("t1", None))
    assert api.calls_of("update_task") == [
        {
            "task_id": "t1",
            "due_string": "no date",
            "due_lang": "en",
            "due_date": None,
            "due_datetime": None,
        }
    ]
    moved = run(make_reader(api).get_task("t1"))
    assert moved is not None and moved.due is None


def test_quitar_la_fecha_de_una_recurrente_se_niega_sin_escribir():
    """Pilla quitar la fecha a una recurrente, que perdería su recurrencia."""
    api = FakeTodoistApi([raw_task("t1", due="2030-01-07", recurring=True)])
    with pytest.raises(ToolFailure) as caught:
        run(make_writer(api).move("t1", None))
    assert caught.value.code == "invalid_due"
    assert api.calls_of("update_task") == []


def test_la_guardia_de_dev_niega_la_foto_de_otro_proyecto():
    api = FakeTodoistApi([raw_task("t1", project_id=REAL_PROJECT, title=PRIVATE_TITLE)])
    reader = todoist.GuardedReader(make_reader(api), TEST_PROJECT)
    with pytest.raises(ToolFailure) as caught:
        run(reader.get_photo("t1"))
    assert caught.value.code == "outside_test_project"


# --- Completadas de hoy y reabrir (tarea 1.15) -------------------------------------------------


def test_las_completadas_de_hoy_se_piden_desde_la_medianoche_local_hasta_ahora():
    """Pilla un «desde» en UTC o en la medianoche equivocada: el día es el de Madrid."""
    api = FakeTodoistApi([raw_task("a", "Gimnasio", due="2030-01-07")])
    run(make_writer(api).complete("a"))
    result = run(make_reader(api).read_completed_today())
    assert result.available and ids(result) == ["a"]
    (call,) = api.calls_of("get_completed_tasks_by_completion_date")
    assert call["since"] == datetime(2030, 1, 6, 23, 0, tzinfo=UTC)
    assert call["until"] == NOW


def test_una_completada_antes_de_hoy_no_es_de_hoy():
    api = FakeTodoistApi([raw_task("a", "Gimnasio", due="2030-01-06")])
    api.completed_at = "2030-01-06T22:59:00.000000Z"  # las 23:59 de ayer en Madrid
    run(make_writer(api).complete("a"))
    assert ids(run(make_reader(api).read_completed_today())) == []


def test_el_doble_imita_a_todoist_una_recurrente_completada_no_sale_en_las_completadas():
    """Lo que vio el contrato del 23-nov-2026: pasa a mañana y no aparece entre las completadas."""
    api = FakeTodoistApi([raw_task("r", "Gimnasio", due="2030-01-07T12:00:00", recurring=True)])
    run(make_writer(api).complete("r"))
    assert ids(run(make_reader(api).read_completed_today())) == []
    task = run(make_reader(api).get_task("r"))
    assert task is not None and task.due == datetime(2030, 1, 8, 12, tzinfo=MADRID)


def test_si_fallan_las_completadas_la_lista_lo_dice_con_su_codigo_y_no_lanza():
    api = FakeTodoistApi(error=http_status_error(503))
    result = run(make_reader(api).read_completed_today())
    assert not result.available and result.unavailable_reason == "http_503"


def test_reabrir_devuelve_la_tarea_a_las_activas():
    api = FakeTodoistApi([raw_task("a", due="2030-01-07")])
    writer = make_writer(api)
    run(writer.complete("a"))
    run(writer.reopen("a"))
    assert api.calls_of("uncomplete_task") == [{"task_id": "a"}]
    assert ids(run(make_reader(api).read_today())) == ["a"]


def test_si_la_api_dice_que_no_reabrio_es_un_error(monkeypatch):
    api = FakeTodoistApi([raw_task("a", due="2030-01-07")])

    async def refuse(task_id: str) -> bool:
        return False

    monkeypatch.setattr(api, "uncomplete_task", refuse)
    with pytest.raises(ToolFailure) as caught:
        run(make_writer(api).reopen("a"))
    assert caught.value.code == "not_reopened"


# --- Un día concreto y la hora de completar (tarea 1.16) ---------------------------------------


def test_un_dia_concreto_son_sus_abiertas_por_dia_local_y_por_hora():
    """Mañana = 2030-01-08 en Madrid. Pilla un día en UTC, las de antes (vencidas) colándose, o
    el orden de `read_today` perdido."""
    api = FakeTodoistApi(
        [
            raw_task("hoy", due="2030-01-07"),
            raw_task("tarde", due="2030-01-08T19:00:00"),
            raw_task("dia-entero", due="2030-01-08"),
            raw_task("utc-aun-es-hoy", due="2030-01-07T22:30:00Z"),  # 23:30 del 7 en Madrid
            raw_task("utc-ya-es-manana", due="2030-01-07T23:30:00Z"),  # 00:30 del 8
            raw_task("pasado", due="2030-01-09T09:00:00"),
            raw_task("sin-fecha"),
        ]
    )
    result = run(make_reader(api).read_day(date(2030, 1, 8)))
    assert ids(result) == ["dia-entero", "utc-ya-es-manana", "tarde"]


def test_un_dia_concreto_si_falla_lo_dice_con_su_codigo_y_no_lanza():
    api = FakeTodoistApi(error=http_status_error(503))
    result = run(make_reader(api).read_day(date(2030, 1, 8)))
    assert not result.available and result.unavailable_reason == "http_503"


def test_un_dia_concreto_en_dev_solo_ve_el_proyecto_de_pruebas():
    """Pilla `read_day` sin la barrera de la copia de pruebas."""
    api = FakeTodoistApi(
        [
            raw_task("mia", project_id=TEST_PROJECT, due="2030-01-08"),
            raw_task("real", project_id=REAL_PROJECT, due="2030-01-08", title=PRIVATE_TITLE),
        ]
    )
    reader = todoist.GuardedReader(make_reader(api), TEST_PROJECT)
    assert ids(run(reader.read_day(date(2030, 1, 8)))) == ["mia"]
    assert not run(reader.read_day(date(2030, 1, 8), REAL_PROJECT)).available


def test_la_completada_sabe_cuando_se_completo_en_la_zona_de_la_configuracion():
    """Pilla `completed_at` perdido o en UTC: lo marcado antes de su hora sale de aquí."""
    api = FakeTodoistApi([raw_task("a", "Meditación", due="2030-01-07T09:30:00")])
    api.completed_at = "2030-01-07T07:20:00.000000Z"  # las 8:20 en Madrid; el reloj, las 9:00
    run(make_writer(api).complete("a"))
    (task,) = run(make_reader(api).read_completed_today()).tasks
    assert task.completed_at == datetime(2030, 1, 7, 8, 20, tzinfo=MADRID)
    assert task.completed_at is not None and str(task.completed_at.tzinfo) == "Europe/Madrid"
    again = run(make_reader(api).get_task("a"))
    assert again is not None and again.completed_at == task.completed_at


def test_la_hora_de_completar_no_llega_al_modelo():
    """Pilla `completed_at` en el JSON que ve el modelo (es solo del código, como `completed`)."""
    api = FakeTodoistApi([raw_task("a", due="2030-01-07")])
    run(make_writer(api).complete("a"))
    assert "completed" not in run(make_reader(api).read_completed_today()).to_json()
