"""Lo que hace un toque, sin Telegram: manejador inyectado, caducidad y estado guardado."""

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import FixedClock

from jimic.channel import store
from jimic.channel.message import (
    GlobalOption,
    InteractiveMessage,
    Item,
    Option,
    Tap,
)
from jimic.channel.taps import process_tap
from jimic.memory.database import open_database

NOW = datetime(2026, 11, 21, 19, 0, tzinfo=ZoneInfo("Europe/Madrid"))
EXPIRES = NOW + timedelta(hours=1)
MAX_CALLS = 20
"""Tope del doble: un manejador al que el código llama en bucle falla con un error claro."""

SECRET_TITLE = "Título secreto 17:30"


class RecordingHandler:
    """A handler that records what it was called with and answers `✓`/`✗`; it can fail."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_on = fail_on or set()

    async def __call__(self, item: Item, option: str) -> str:
        self.calls.append((item.title, option))
        if len(self.calls) > MAX_CALLS:
            raise AssertionError("el manejador se ha llamado en bucle")
        if item.title in self.fail_on:
            raise RuntimeError(f"falla con {item.title}")
        return "✓ creada" if option == "y" else "✗ descartada"


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


@pytest.fixture
def clock():
    return FixedClock(NOW)


def make_message() -> InteractiveMessage:
    options = (Option(key="y", label="Sí"), Option(key="n", label="No", approves=False))
    return InteractiveMessage(
        header="Plan de prueba",
        items=(
            Item(title="Gimnasio 13:00", options=options),
            Item(title=SECRET_TITLE, options=options),
            Item(title="Leer 21:00", options=options),
        ),
        global_options=(
            GlobalOption(key="all", label="Créalo todo", option="y"),
            GlobalOption(key="none", label="Nada", option="n"),
        ),
    )


@pytest.fixture
def approval_id(conn, clock):
    return store.create(conn, clock, "demo", make_message(), EXPIRES)


def run(conn, clock, handler, tap, kind="demo"):
    return asyncio.run(process_tap(conn, clock, {kind: handler}, tap))


def stored(conn, approval_id):
    approval = store.load(conn, approval_id)
    assert approval is not None
    return approval


def test_un_toque_llama_al_manejador_resuelve_el_elemento_y_quita_su_fila(conn, clock, approval_id):
    """Pilla un toque que no llama al manejador, no guarda el resultado o deja la fila."""
    handler = RecordingHandler()
    result = run(conn, clock, handler, Tap(approval_id, 1, "y"))
    assert handler.calls == [("Gimnasio 13:00", "y")]
    assert result.code == "resolved"
    assert result.view is not None
    assert "1. Gimnasio 13:00 — ✓ creada" in result.view.text
    assert [[b.label for b in row] for row in result.view.keyboard] == [
        ["2 · Sí", "2 · No"],
        ["3 · Sí", "3 · No"],
        ["Créalo todo", "Nada"],
    ]
    approval = stored(conn, approval_id)
    assert approval.status == "pending"
    assert approval.message.items[0].result == "✓ creada"


def test_el_segundo_toque_sobre_lo_resuelto_no_hace_nada(conn, clock, approval_id):
    """No llama otra vez al manejador y no pide editar el mensaje."""
    handler = RecordingHandler()
    run(conn, clock, handler, Tap(approval_id, 1, "y"))
    again = run(conn, clock, handler, Tap(approval_id, 1, "n"))
    assert again.view is None
    assert again.code == "nothing_to_do"
    assert len(handler.calls) == 1
    assert stored(conn, approval_id).message.items[0].chosen == "y"


def test_creelo_todo_resuelve_lo_pendiente_y_no_toca_lo_ya_resuelto(conn, clock, approval_id):
    """El manejador no se llama para lo que ya estaba resuelto; al final no queda teclado."""
    handler = RecordingHandler()
    run(conn, clock, handler, Tap(approval_id, 2, "n"))
    result = run(conn, clock, handler, Tap(approval_id, None, "all"))
    assert handler.calls == [(SECRET_TITLE, "n"), ("Gimnasio 13:00", "y"), ("Leer 21:00", "y")]
    assert result.view is not None
    assert result.view.keyboard == ()
    assert f"2. {SECRET_TITLE} — ✗ descartada" in result.view.text
    approval = stored(conn, approval_id)
    assert approval.status == "approved"


def test_todo_con_noes_queda_rechazado(conn, clock, approval_id):
    """[Nada] resuelve todo con una opción que no aprueba: la fila queda `rejected`."""
    run(conn, clock, RecordingHandler(), Tap(approval_id, None, "none"))
    assert stored(conn, approval_id).status == "rejected"


def test_un_manejador_que_falla_deja_ese_elemento_pendiente_sin_contenido_en_el_log(
    conn, clock, approval_id, caplog
):
    """Pilla un fallo que resuelve el elemento igualmente o que escribe el título en el log."""
    handler = RecordingHandler(fail_on={SECRET_TITLE})
    with caplog.at_level(logging.INFO):
        result = run(conn, clock, handler, Tap(approval_id, None, "all"))
    assert result.view is not None
    assert f"2. {SECRET_TITLE}\n3. Leer 21:00 — ✓ creada" in result.view.text
    assert "1. Gimnasio 13:00 — ✓ creada" in result.view.text
    assert [[b.label for b in row] for row in result.view.keyboard] == [
        ["2 · Sí", "2 · No"],
        ["Créalo todo", "Nada"],
    ]
    assert "RuntimeError" in caplog.text
    assert SECRET_TITLE not in caplog.text
    assert stored(conn, approval_id).status == "pending"


def test_si_el_unico_manejador_falla_no_se_cambia_el_mensaje(conn, clock, approval_id):
    result = run(
        conn, clock, RecordingHandler(fail_on={"Gimnasio 13:00"}), Tap(approval_id, 1, "y")
    )
    assert (result.code, result.view) == ("handler_failed", None)
    assert stored(conn, approval_id).message == make_message()


def test_un_toque_caducado_marca_caducado_y_quita_el_teclado(conn, clock, approval_id):
    """El borde: justo en `expires_at` ya caduca. No llama al manejador."""
    clock.moment = EXPIRES
    handler = RecordingHandler()
    result = run(conn, clock, handler, Tap(approval_id, 1, "y"))
    assert handler.calls == []
    assert result.code == "expired"
    assert result.view is not None
    assert result.view.keyboard == ()
    assert result.view.text.count("— caducado") == 3
    assert stored(conn, approval_id).status == "expired"


def test_un_segundo_toque_caducado_vuelve_a_quitar_el_teclado(conn, clock, approval_id):
    """Si la primera edición falló y el teclado sigue en el móvil, el siguiente toque lo quita."""
    clock.moment = EXPIRES + timedelta(minutes=5)
    run(conn, clock, RecordingHandler(), Tap(approval_id, 1, "y"))
    again = run(conn, clock, RecordingHandler(), Tap(approval_id, 2, "y"))
    assert again.code == "expired"
    assert again.view is not None
    assert again.view.keyboard == ()


def test_un_segundo_antes_de_caducar_aun_se_atiende(conn, clock, approval_id):
    clock.moment = EXPIRES - timedelta(seconds=1)
    assert run(conn, clock, RecordingHandler(), Tap(approval_id, 1, "y")).code == "resolved"


def test_caducar_no_toca_lo_ya_resuelto(conn, clock, approval_id):
    run(conn, clock, RecordingHandler(), Tap(approval_id, 1, "y"))
    clock.moment = EXPIRES
    result = run(conn, clock, RecordingHandler(), Tap(approval_id, 2, "y"))
    assert result.view is not None
    assert "1. Gimnasio 13:00 — ✓ creada" in result.view.text
    assert "2. " + SECRET_TITLE + " — caducado" in result.view.text


def test_un_id_inexistente_un_kind_sin_manejador_y_un_payload_roto_no_cambian_nada(
    conn, clock, approval_id
):
    """Ninguno lanza: el chat de Pedro recibe un toque de nada y el mensaje se queda."""
    handler = RecordingHandler()
    assert run(conn, clock, handler, Tap(999, 1, "y")).code == "unknown_approval"
    assert run(conn, clock, handler, Tap(approval_id, 1, "y"), kind="otro").code == "no_handler"
    conn.execute("UPDATE approvals SET payload = '{}' WHERE id = ?", (approval_id,))
    assert run(conn, clock, handler, Tap(approval_id, 1, "y")).code == "invalid_payload"
    assert handler.calls == []


def test_tras_resolver_todo_un_toque_viejo_no_hace_nada(conn, clock, approval_id):
    """La fila ya salió de `pending`: el toque sobre un teclado viejo no cambia el mensaje."""
    run(conn, clock, RecordingHandler(), Tap(approval_id, None, "all"))
    result = run(conn, clock, RecordingHandler(), Tap(approval_id, 1, "n"))
    assert (result.code, result.view) == ("already_resolved", None)
