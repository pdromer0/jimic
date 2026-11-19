"""`.env`: se valida al arrancar y el error nombra la clave sin imprimir nunca un valor."""

import os
from pathlib import Path

import pytest

from jimic.config import ConfigError, load_settings
from jimic.paths import resolve_paths

VALID_ENV = {
    "ENTORNO": "prod",
    "ZONA_HORARIA": "Europe/Madrid",
    "MODELO_LATIDO": "modelo-latido-de-prueba",
    "MODELO_AGENTE": "modelo-agente-de-prueba",
    "CLAVE_ANTHROPIC": "test-clave-anthropic-123",
    "TOKEN_TELEGRAM": "test-token-telegram-456",
    "CHAT_TELEGRAM": "987654321",
    "TOKEN_TODOIST": "test-token-todoist-789",
    "RUTA_APEX": "/datos/inventados/apex.json",
}
"""Un `.env` válido de producción con valores inventados y fáciles de buscar en un texto."""


def distinct_values(values: dict[str, str]) -> list[str]:
    """Return the values that could not appear in a message by chance (not `prod` or a zone)."""
    return [v for k, v in values.items() if k not in ("ENTORNO", "ZONA_HORARIA")]


def write_env(values: dict[str, str]) -> Path:
    """Write `values` as the `.env` of the environment selected by `os.environ`."""
    paths = resolve_paths(os.environ)
    paths.config_dir.mkdir(parents=True)
    paths.env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    return paths.env_file


def load_error(values: dict[str, str]) -> str:
    """Write `values`, load them, and return the `ConfigError` message (fails if none)."""
    write_env(values)
    with pytest.raises(ConfigError) as excinfo:
        load_settings(resolve_paths(os.environ))
    error = excinfo.value
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__
    return str(excinfo.value)


def assert_no_values(message: str, values: list[str]) -> None:
    for value in values:
        assert value not in message, f"el error imprime un valor: {value!r}"


def test_un_env_valido_se_carga_con_sus_tipos():
    """Pilla tipos perdidos: zona como `ZoneInfo`, chat como entero y los secretos ocultos."""
    write_env(VALID_ENV)
    settings = load_settings(resolve_paths(os.environ))
    assert settings.timezone.key == "Europe/Madrid"
    assert settings.telegram_chat_id == 987654321
    assert settings.apex_path == Path("/datos/inventados/apex.json")
    assert settings.todoist_test_project is None
    assert settings.telegram_token.get_secret_value() == "test-token-telegram-456"
    assert "test-token-telegram-456" not in repr(settings)


def test_una_clave_que_falta_se_nombra_sin_imprimir_valores():
    """Pilla `str(exc)` de Pydantic: su `input_value` lleva todos los valores del `.env`."""
    values = {k: v for k, v in VALID_ENV.items() if k != "TOKEN_TODOIST"}
    message = load_error(values)
    assert "- falta TOKEN_TODOIST" in message.splitlines()
    assert_no_values(message, distinct_values(values))


def test_valores_invalidos_se_nombran_sin_imprimirlos():
    """Pilla el `msg` de Pydantic, que repite el valor («invalid timezone: …»)."""
    values = VALID_ENV | {"ZONA_HORARIA": "Marte/Olimpo-inventada", "CHAT_TELEGRAM": "chat-xyz"}
    message = load_error(values)
    lines = message.splitlines()
    assert "- ZONA_HORARIA no es válida (zoneinfo_str)" in lines
    assert "- CHAT_TELEGRAM no es válida (int_parsing)" in lines
    assert_no_values(message, ["Marte/Olimpo-inventada", *distinct_values(values)])


def test_un_valor_vacio_es_un_error():
    """Pilla `RUTA_APEX=` aceptada como `.` y un token vacío aceptado."""
    message = load_error(VALID_ENV | {"TOKEN_TELEGRAM": "", "RUTA_APEX": ""})
    lines = message.splitlines()
    assert "- TOKEN_TELEGRAM está vacía" in lines
    assert "- RUTA_APEX está vacía" in lines


def test_una_clave_desconocida_es_un_error():
    """Pilla erratas que se ignorarían en silencio; tampoco imprime su valor."""
    message = load_error(VALID_ENV | {"TOKEN_TELEGRAMM": "test-errata-000"})
    assert "- TOKEN_TELEGRAMM no es una clave conocida" in message.splitlines()
    assert_no_values(message, ["test-errata-000", *distinct_values(VALID_ENV)])


def test_sin_env_dice_donde_lo_busca():
    """Pilla un error genérico o una lista de «falta» por cada clave cuando no hay fichero."""
    paths = resolve_paths(os.environ)
    with pytest.raises(ConfigError, match=f"^no existe {paths.env_file}$"):
        load_settings(paths)


def test_un_env_vacio_dice_todas_las_claves_que_faltan():
    """Pilla que solo se informe del primer fallo: salen las nueve claves obligatorias."""
    message = load_error({})
    missing = [line for line in message.splitlines() if line.startswith("- falta ")]
    assert len(missing) == 9


def test_las_variables_del_proceso_no_cuentan(monkeypatch):
    """Pilla que una variable suelta del entorno complete o cambie el `.env`."""
    monkeypatch.setenv("TOKEN_TODOIST", "test-desde-el-entorno")
    values = {k: v for k, v in VALID_ENV.items() if k != "TOKEN_TODOIST"}
    message = load_error(values)
    assert "- falta TOKEN_TODOIST" in message.splitlines()


def test_dev_exige_el_proyecto_de_pruebas(monkeypatch):
    """Pilla que la copia de pruebas arranque sin proyecto de pruebas de Todoist."""
    monkeypatch.setenv("JIMIC_ENTORNO", "dev")
    message = load_error(VALID_ENV | {"ENTORNO": "dev"})
    assert "- falta PROYECTO_PRUEBAS_TODOIST (obligatoria con ENTORNO=dev)" in message


def test_dev_con_proyecto_de_pruebas_se_carga(monkeypatch):
    """El caso válido de dev: el proyecto de pruebas se lee."""
    monkeypatch.setenv("JIMIC_ENTORNO", "dev")
    write_env(VALID_ENV | {"ENTORNO": "dev", "PROYECTO_PRUEBAS_TODOIST": "proyecto-falso-1"})
    settings = load_settings(resolve_paths(os.environ))
    assert settings.todoist_test_project == "proyecto-falso-1"


def test_el_entorno_del_env_debe_coincidir_con_la_variable(monkeypatch):
    """Pilla el `.env` de producción copiado en la carpeta de pruebas."""
    monkeypatch.setenv("JIMIC_ENTORNO", "dev")
    message = load_error(VALID_ENV | {"PROYECTO_PRUEBAS_TODOIST": "proyecto-falso-1"})
    assert "- ENTORNO no coincide con el que elige JIMIC_ENTORNO" in message.splitlines()
