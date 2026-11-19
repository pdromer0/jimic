"""El permiso de Google: autorizar una vez, token 600 y atómico, renovarlo y no enseñar nada."""

import asyncio
import json
import logging
import os
import stat
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_data import (
    OTHER_SCOPE,
    PAST,
    FakeFlow,
    granted,
    install_flow,
    token_info,
    write_client,
    write_token,
)

from jimic.paths import Paths, resolve_paths
from jimic.tools import google_auth
from jimic.tools.calendar import build_calendar
from jimic.tools.google_auth import (
    SCOPE,
    CalendarConnection,
    GoogleAuthError,
    authorize,
    load_credentials,
)

SECRET = "test-secreto-del-token-9921"
"""Un valor que no debe salir en ningún mensaje ni log."""


@pytest.fixture
def paths() -> Paths:
    found = resolve_paths(os.environ)
    found.config_dir.mkdir(parents=True)
    return found


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def leftovers(paths: Paths) -> list[str]:
    """Temporary files of an atomic write that nobody cleaned up."""
    return [p.name for p in paths.config_dir.iterdir() if p.name.startswith(".google_token")]


# --- Autorizar --------------------------------------------------------------------------------


def test_autorizar_guarda_el_token_con_permisos_600(paths, monkeypatch):
    """Pilla un token legible por otros usuarios (por ejemplo, 644)."""
    install_flow(monkeypatch)
    write_client(paths)
    authorize(paths)
    assert mode(paths.google_token_file) == 0o600
    saved = json.loads(paths.google_token_file.read_text())
    assert saved["scopes"] == [SCOPE]
    assert saved["refresh_token"] == "test-token-renovacion-2"


def test_autorizar_pide_solo_el_permiso_de_lectura_del_calendario(paths, monkeypatch):
    """Pilla un permiso de más en la petición: el único es `calendar.readonly`."""
    flow, calls = install_flow(monkeypatch)
    write_client(paths)
    authorize(paths)
    ((client, scopes),) = calls
    assert client == str(paths.google_client_file)
    assert scopes == ["https://www.googleapis.com/auth/calendar.readonly"]
    assert flow.kwargs["port"] == 0
    assert flow.kwargs["open_browser"] is False
    assert "{url}" in flow.kwargs["authorization_prompt_message"]
    assert flow.kwargs["timeout_seconds"] == google_auth.FLOW_TIMEOUT


def test_autorizar_sin_cliente_dice_que_falta_y_que_comando_lanzar(paths, monkeypatch):
    install_flow(monkeypatch)
    with pytest.raises(GoogleAuthError) as caught:
        authorize(paths)
    assert "google_cliente.json" in caught.value.message
    assert "jimic google autorizar" in caught.value.message
    assert str(paths.config_dir) not in caught.value.message
    assert not paths.google_token_file.exists()


def test_un_cliente_invalido_no_enseña_su_contenido(paths, monkeypatch):
    install_flow(monkeypatch, client_error=ValueError(SECRET))
    write_client(paths)
    with pytest.raises(GoogleAuthError) as caught:
        authorize(paths)
    assert SECRET not in caught.value.message
    assert "google_cliente.json" in caught.value.message


def test_si_el_flujo_falla_solo_se_dice_el_tipo_y_no_se_guarda_nada(paths, monkeypatch):
    install_flow(monkeypatch, FakeFlow(error=RuntimeError(SECRET)))
    write_client(paths)
    with pytest.raises(GoogleAuthError) as caught:
        authorize(paths)
    assert "RuntimeError" in caught.value.message
    assert SECRET not in caught.value.message
    assert not paths.google_token_file.exists()


def test_un_permiso_distinto_del_pedido_no_se_guarda(paths, monkeypatch):
    """Pilla guardar un token con un alcance mayor que el que JimiC necesita."""
    install_flow(monkeypatch, FakeFlow(granted([SCOPE, OTHER_SCOPE])))
    write_client(paths)
    old = write_token(paths, token="test-token-viejo")
    with pytest.raises(GoogleAuthError, match="distinto"):
        authorize(paths)
    assert json.loads(old.read_text())["token"] == "test-token-viejo"


def test_si_no_se_puede_escribir_el_token_viejo_sigue_entero_y_no_quedan_temporales(
    paths, monkeypatch
):
    """Pilla una escritura a medias: el fichero es el viejo o el nuevo, nunca la mitad."""
    install_flow(monkeypatch)
    write_client(paths)
    old = write_token(paths, token="test-token-viejo")

    def broken_fsync(_fd: int) -> None:
        raise OSError(SECRET)

    monkeypatch.setattr("jimic.files.os.fsync", broken_fsync)
    with pytest.raises(GoogleAuthError, match="OSError") as caught:
        authorize(paths)
    assert SECRET not in caught.value.message
    assert json.loads(old.read_text())["token"] == "test-token-viejo"
    assert leftovers(paths) == []


def test_autorizar_otra_vez_reemplaza_el_token(paths, monkeypatch):
    install_flow(monkeypatch)
    write_client(paths)
    write_token(paths, token="test-token-viejo")
    authorize(paths)
    assert json.loads(paths.google_token_file.read_text())["token"] == "test-token-acceso-1"
    assert leftovers(paths) == []


# --- Cargar el permiso ------------------------------------------------------------------------


def test_sin_token_el_error_dice_que_falta_y_que_comando_lanzar(paths):
    with pytest.raises(GoogleAuthError) as caught:
        load_credentials(paths)
    assert caught.value.code == "auth"
    assert "jimic google autorizar" in caught.value.message
    assert str(paths.config_dir) not in caught.value.message


@pytest.mark.parametrize(
    "text",
    [
        "esto no es json " + SECRET,
        "[]",
        json.dumps({"token": SECRET}),
        json.dumps(token_info(scopes=[OTHER_SCOPE])),
        json.dumps(token_info(scopes=[SCOPE, OTHER_SCOPE])),
        json.dumps(token_info(refresh_token="")),
        json.dumps(token_info(scopes=[])),
    ],
)
def test_un_token_que_no_sirve_es_un_error_sin_su_contenido(paths, text):
    """Pilla aceptar un token roto, incompleto o de otro alcance, o enseñar lo que contiene."""
    paths.google_token_file.write_text(text)
    with pytest.raises(GoogleAuthError) as caught:
        load_credentials(paths)
    assert SECRET not in caught.value.message
    assert "jimic google autorizar" in caught.value.message


def test_un_token_con_el_alcance_exacto_se_carga(paths):
    write_token(paths)
    credentials = load_credentials(paths)
    assert set(credentials.scopes or ()) == {SCOPE}


# --- La conexión ------------------------------------------------------------------------------


@pytest.fixture
def renewals(monkeypatch):
    """Replace the renewal (it would go to the network); returns how many times it ran."""
    calls: list[int] = []

    def refresh(self, _request: Any) -> None:
        from datetime import datetime, timedelta

        calls.append(1)
        self.token = "test-token-renovado-5"
        self.expiry = datetime(2099, 1, 1) + timedelta(seconds=len(calls))

    monkeypatch.setattr(Credentials, "refresh", refresh)
    return calls


def test_un_token_en_vigor_no_se_renueva_ni_se_reescribe(paths, renewals):
    write_token(paths)
    before = paths.google_token_file.read_text()
    service = CalendarConnection(paths, build_service=lambda _credentials: "servicio")()
    assert service == "servicio"
    assert renewals == []
    assert paths.google_token_file.read_text() == before


def test_un_token_caducado_se_renueva_y_se_reescribe_con_permisos_600(paths, renewals):
    """Pilla un token renovado que no se guarda, o que se guarda con otros permisos."""
    write_token(paths, expiry=PAST)
    paths.google_token_file.chmod(0o644)  # un 644 antiguo no debe sobrevivir a la renovación
    connection = CalendarConnection(paths, build_service=lambda credentials: credentials.token)
    assert connection() == "test-token-renovado-5"
    assert len(renewals) == 1
    assert json.loads(paths.google_token_file.read_text())["token"] == "test-token-renovado-5"
    assert mode(paths.google_token_file) == 0o600
    assert leftovers(paths) == []


def test_el_token_renovado_sirve_en_la_siguiente_lectura_sin_volver_a_renovar(paths, renewals):
    write_token(paths, expiry=PAST)
    connection = CalendarConnection(paths, build_service=lambda _credentials: object())
    first = connection()
    assert connection() is first
    assert len(renewals) == 1


def test_si_la_renovacion_falla_es_un_error_de_permiso_y_el_token_no_se_toca(paths, monkeypatch):
    """Pilla una renovación fallida que borre o pise el token."""

    def refuse(self, _request: Any) -> None:
        raise RefreshError(SECRET)

    monkeypatch.setattr(Credentials, "refresh", refuse)
    write_token(paths, expiry=PAST)
    before = paths.google_token_file.read_text()
    with pytest.raises(GoogleAuthError) as caught:
        CalendarConnection(paths)()
    assert SECRET not in caught.value.message
    assert caught.value.code == "auth"
    assert paths.google_token_file.read_text() == before


def test_si_no_se_puede_guardar_el_token_renovado_se_sigue_y_se_avisa_sin_contenido(
    paths, renewals, monkeypatch, caplog
):
    write_token(paths, expiry=PAST)

    def broken_fsync(_fd: int) -> None:
        raise OSError(SECRET)

    monkeypatch.setattr("jimic.files.os.fsync", broken_fsync)
    with caplog.at_level(logging.INFO, logger="jimic.tools.google_auth"):
        service = CalendarConnection(paths, build_service=lambda _credentials: "servicio")()
    assert service == "servicio"
    assert SECRET not in caplog.text
    assert len(caplog.records) == 1


def test_el_servicio_real_se_construye_sin_red_y_con_tiempo_limite(paths, monkeypatch):
    """Pilla un cliente sin límite de tiempo: la petición lleva `REQUEST_TIMEOUT`."""
    write_token(paths)
    seen: dict[str, Any] = {}
    real_build = google_auth.build

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(google_auth, "build", spy)
    service = CalendarConnection(paths)()
    assert callable(service.events) and callable(service.calendarList)
    assert seen["static_discovery"] is True
    assert seen["http"].http.timeout == google_auth.REQUEST_TIMEOUT == 15


def test_sin_permiso_el_lector_dice_auth_y_el_log_dice_que_comando_lanzar(paths, caplog):
    """De punta a punta: el calendario queda «no disponible» y el aviso nombra el comando."""
    reader = build_calendar(paths, ZoneInfo("Europe/Madrid"))
    with caplog.at_level(logging.INFO, logger="jimic.tools.calendar"):
        day = asyncio.run(reader.read_day(date(2030, 1, 7)))
    assert day.unavailable_reason == "auth"
    assert [r.getMessage() for r in caplog.records] == [
        "Calendar unavailable (auth); run `jimic google autorizar`"
    ]
