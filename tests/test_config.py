"""`.env`: se valida al arrancar y el error nombra la clave sin imprimir nunca un valor."""

import os
from pathlib import Path

import pytest
from conftest import VALID_ENV, write_env

from jimic.config import ConfigError, load_settings
from jimic.paths import resolve_paths


def distinct_values(values: dict[str, str]) -> list[str]:
    """Return the values that could not appear in a message by chance (not `prod` or a zone)."""
    return [v for k, v in values.items() if k not in ("ENTORNO", "ZONA_HORARIA")]


def load_problems(values: dict[str, str]) -> list[str]:
    """Write `values`, load them, and return the problem lines of the `ConfigError` message.

    The message must be exactly the header plus those lines: any extra text (such as a
    truncated `input_value` from Pydantic) changes the list and fails the test.
    """
    env_file = write_env(values)
    with pytest.raises(ConfigError) as excinfo:
        load_settings(resolve_paths(os.environ))
    error = excinfo.value
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__
    header, *problems = str(error).splitlines()
    assert header == f"{env_file} no es válido:"
    return problems


def assert_no_values(problems: list[str], values: list[str]) -> None:
    message = "\n".join(problems)
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
    """Pilla `str(exc)` de Pydantic: su `input_value` lleva los valores del `.env`, recortados."""
    values = {k: v for k, v in VALID_ENV.items() if k != "TOKEN_TODOIST"}
    problems = load_problems(values)
    assert problems == ["- falta TOKEN_TODOIST"]
    assert_no_values(problems, distinct_values(values))


def test_valores_invalidos_se_nombran_sin_imprimirlos():
    """Pilla el `msg` de Pydantic, que repite el valor («invalid timezone: …»)."""
    values = VALID_ENV | {"ZONA_HORARIA": "Marte/Olimpo-inventada", "CHAT_TELEGRAM": "chat-xyz"}
    problems = load_problems(values)
    assert problems == [
        "- ZONA_HORARIA no es válida (zoneinfo_str)",
        "- CHAT_TELEGRAM no es válida (int_parsing)",
    ]
    assert_no_values(problems, ["Marte/Olimpo-inventada", *distinct_values(values)])


def test_un_valor_vacio_es_un_error():
    """Pilla `RUTA_APEX=` aceptada como `.` y un token vacío aceptado."""
    problems = load_problems(VALID_ENV | {"TOKEN_TELEGRAM": "", "RUTA_APEX": ""})
    assert problems == ["- TOKEN_TELEGRAM está vacía", "- RUTA_APEX está vacía"]


def test_una_clave_desconocida_es_un_error_sin_nombrarla():
    """Pilla erratas que se ignorarían en silencio; ni la clave ni su valor salen en el error."""
    problems = load_problems(VALID_ENV | {"TOKEN_TELEGRAMM": "test-errata-000"})
    assert problems == ["- hay 1 línea que no es una clave conocida (no se muestra)"]
    assert_no_values(problems, ["test-errata-000", *distinct_values(VALID_ENV)])


def test_un_token_con_igual_pegado_solo_no_sale_en_el_error():
    """Pilla nombrar la clave desconocida: `abc==` se lee como clave `abc` con valor `=`."""
    env_file = write_env(VALID_ENV)
    with env_file.open("a") as env:
        env.write("dGVzdC10b2tlbi1zdWVsdG8==\nTOKEN_TELEGRAMM=test-errata-000\n")
    with pytest.raises(ConfigError) as excinfo:
        load_settings(resolve_paths(os.environ))
    message = str(excinfo.value)
    assert message.splitlines()[1:] == [
        "- hay 2 líneas que no son claves conocidas (no se muestran)"
    ]
    for secret in ("dGVzdC10b2tlbi1zdWVsdG8", "TOKEN_TELEGRAMM", "test-errata-000"):
        assert secret.lower() not in message.lower()


def test_una_linea_suelta_sin_igual_se_ignora():
    """Documenta python-dotenv: una línea sin `=` es una clave sin valor y no llega a Pydantic."""
    env_file = write_env(VALID_ENV)
    with env_file.open("a") as env:
        env.write("test-token-suelto-321\n")
    assert load_settings(resolve_paths(os.environ)).environment == "prod"


def test_sin_env_dice_donde_lo_busca():
    """Pilla un error genérico o una lista de «falta» por cada clave cuando no hay fichero."""
    paths = resolve_paths(os.environ)
    with pytest.raises(ConfigError, match=f"^no existe {paths.env_file}$"):
        load_settings(paths)


def test_un_env_vacio_dice_todas_las_claves_que_faltan():
    """Pilla que solo se informe del primer fallo: salen las nueve claves obligatorias."""
    problems = load_problems({})
    assert problems == [f"- falta {key}" for key in VALID_ENV]


def test_las_variables_del_proceso_no_cuentan(monkeypatch):
    """Pilla que una variable suelta del entorno complete o cambie el `.env`."""
    monkeypatch.setenv("TOKEN_TODOIST", "test-desde-el-entorno")
    values = {k: v for k, v in VALID_ENV.items() if k != "TOKEN_TODOIST"}
    assert load_problems(values) == ["- falta TOKEN_TODOIST"]


def test_dev_exige_el_proyecto_de_pruebas(monkeypatch):
    """Pilla que la copia de pruebas arranque sin proyecto de pruebas de Todoist."""
    monkeypatch.setenv("JIMIC_ENTORNO", "dev")
    problems = load_problems(VALID_ENV | {"ENTORNO": "dev"})
    assert problems == ["- falta PROYECTO_PRUEBAS_TODOIST (obligatoria con ENTORNO=dev)"]


def test_dev_con_proyecto_de_pruebas_se_carga(monkeypatch):
    """El caso válido de dev: el proyecto de pruebas se lee."""
    monkeypatch.setenv("JIMIC_ENTORNO", "dev")
    write_env(VALID_ENV | {"ENTORNO": "dev", "PROYECTO_PRUEBAS_TODOIST": "proyecto-falso-1"})
    settings = load_settings(resolve_paths(os.environ))
    assert settings.todoist_test_project == "proyecto-falso-1"


def test_el_entorno_del_env_debe_coincidir_con_la_variable(monkeypatch):
    """Pilla el `.env` de producción copiado en la carpeta de pruebas."""
    monkeypatch.setenv("JIMIC_ENTORNO", "dev")
    problems = load_problems(VALID_ENV | {"PROYECTO_PRUEBAS_TODOIST": "proyecto-falso-1"})
    assert problems == ["- ENTORNO no coincide con el que elige JIMIC_ENTORNO"]


def test_un_env_que_no_es_utf8_es_un_error_sin_bytes():
    """Pilla un `UnicodeDecodeError` que escapa con un traceback y el byte que no se pudo leer."""
    env_file = write_env(VALID_ENV)
    env_file.write_bytes(env_file.read_bytes() + b"TOKEN_TODOIST=test-\xf1\xff\n")
    with pytest.raises(ConfigError) as excinfo:
        load_settings(resolve_paths(os.environ))
    assert str(excinfo.value) == f"no se puede leer {env_file} (UnicodeDecodeError)"


def test_un_env_sin_permiso_de_lectura_es_un_error():
    """Pilla un `PermissionError` que escapa con un traceback en vez de un error claro."""
    env_file = write_env(VALID_ENV)
    env_file.chmod(0o000)
    with pytest.raises(ConfigError) as excinfo:
        load_settings(resolve_paths(os.environ))
    assert str(excinfo.value) == f"no se puede leer {env_file} (PermissionError)"
