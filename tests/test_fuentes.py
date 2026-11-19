"""Las tres fuentes del día se construyen desde la configuración y se cierran al terminar."""

import asyncio
import os

import pytest
from apex_data import madrid
from conftest import VALID_ENV, FixedClock, write_env
from todoist_data import TEST_PROJECT, FakeTodoistApi

from jimic.config import Settings, load_settings
from jimic.heartbeat.sources import open_sources
from jimic.paths import resolve_paths
from jimic.tools.apex import LocalApexReader
from jimic.tools.calendar import GoogleCalendarReader
from jimic.tools.todoist import ApiTodoistReader, GuardedReader, GuardedWriter

CLOCK = FixedClock(madrid("2030-01-07 09:00"))


def settings_for(environment: str, monkeypatch) -> Settings:
    values = {**VALID_ENV, "ENTORNO": environment}
    if environment == "dev":
        monkeypatch.setenv("JIMIC_ENTORNO", "dev")
        values["PROYECTO_PRUEBAS_TODOIST"] = TEST_PROJECT
    write_env(values)
    return load_settings(resolve_paths(os.environ))


def open_and_look(settings: Settings, api: FakeTodoistApi):
    async def look():
        async with open_sources(settings, resolve_paths(os.environ), CLOCK, api) as sources:
            return sources, api.closed

    return asyncio.run(look())


def test_en_dev_las_fuentes_llevan_la_guardia_de_todoist(monkeypatch):
    """Pilla un servicio que construya Todoist sin pasar por `build_todoist`."""
    sources, _ = open_and_look(settings_for("dev", monkeypatch), FakeTodoistApi())
    assert isinstance(sources.todoist.reader, GuardedReader)
    assert isinstance(sources.todoist.writer, GuardedWriter)


def test_en_prod_se_construyen_las_tres(monkeypatch):
    sources, open_while_inside = open_and_look(settings_for("prod", monkeypatch), FakeTodoistApi())
    assert isinstance(sources.todoist.reader, ApiTodoistReader)
    assert isinstance(sources.calendar, GoogleCalendarReader)
    assert isinstance(sources.apex, LocalApexReader)
    assert open_while_inside is False


def test_apex_se_lee_de_la_ruta_de_la_configuracion(monkeypatch):
    """La ruta inventada no existe: el lector lo dice con su código, sin romper nada."""
    sources, _ = open_and_look(settings_for("prod", monkeypatch), FakeTodoistApi())
    assert sources.apex.read_today().unavailable_reason == "file_missing"


def test_al_salir_se_cierra_todoist(monkeypatch):
    api = FakeTodoistApi()
    open_and_look(settings_for("prod", monkeypatch), api)
    assert api.closed


def test_tambien_se_cierra_tras_un_error(monkeypatch):
    settings = settings_for("prod", monkeypatch)
    api = FakeTodoistApi()

    async def fail():
        async with open_sources(settings, resolve_paths(os.environ), CLOCK, api):
            raise RuntimeError("fallo inventado")

    with pytest.raises(RuntimeError):
        asyncio.run(fail())
    assert api.closed
