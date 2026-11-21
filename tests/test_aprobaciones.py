"""`approvals`: el mensaje interactivo se guarda, se recarga y solo sale de `pending` una vez."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import FixedClock

from jimic.channel import store
from jimic.channel.message import (
    GlobalOption,
    InteractiveMessage,
    Item,
    MessageTooLong,
    Option,
    Outcome,
    final_status,
    resolve,
)
from jimic.memory.database import open_database

MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 11, 21, 19, 0, tzinfo=MADRID)


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def make_message(title: str = "Gimnasio 13:00") -> InteractiveMessage:
    options = (Option(key="y", label="Sí"), Option(key="n", label="No", approves=False))
    return InteractiveMessage(
        header="Plan de prueba",
        items=(Item(title=title, options=options), Item(title="Leer", options=options)),
        global_options=(GlobalOption(key="all", label="Créalo todo", option="y"),),
    )


def test_se_guarda_y_se_recarga_igual_como_tras_reiniciar(conn, tmp_path):
    """Lo guardado en `payload` reconstruye el mismo mensaje con una conexión nueva."""
    clock = FixedClock(NOW)
    approval_id = store.create(conn, clock, "demo", make_message(), NOW + timedelta(hours=1))
    conn.close()
    reopened = open_database(tmp_path / "jimic.db")
    try:
        approval = store.load(reopened, approval_id)
    finally:
        reopened.close()
    assert approval is not None
    assert approval.kind == "demo"
    assert approval.status == "pending"
    assert approval.message == make_message()
    assert approval.expires_at == NOW + timedelta(hours=1)


def test_los_momentos_se_guardan_en_utc_al_segundo(conn):
    """La fila usa el formato de `to_db`: lo comprueba el CHECK de la migración y este test."""
    clock = FixedClock(NOW.replace(microsecond=999999))
    approval_id = store.create(conn, clock, "demo", make_message(), NOW + timedelta(hours=1))
    row = conn.execute(
        "SELECT created_at, expires_at FROM approvals WHERE id = ?", (approval_id,)
    ).fetchone()
    assert row == ("2026-11-21T18:00:00+00:00", "2026-11-21T19:00:00+00:00")


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(seconds=-1)])
def test_una_aprobacion_que_ya_caduco_no_se_crea(conn, delta):
    """El borde: caducar justo ahora es caducar ya."""
    with pytest.raises(ValueError, match="future"):
        store.create(conn, FixedClock(NOW), "demo", make_message(), NOW + delta)
    assert conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)


def test_un_kind_vacio_no_se_crea(conn):
    with pytest.raises(ValueError, match="kind"):
        store.create(conn, FixedClock(NOW), "", make_message(), NOW + timedelta(hours=1))


def test_un_mensaje_que_no_cabe_no_se_guarda(conn):
    """Pilla un mensaje demasiado largo que llega a la base y falla luego, al enviarlo."""
    with pytest.raises(MessageTooLong):
        store.create(
            conn, FixedClock(NOW), "demo", make_message("x" * 4000), NOW + timedelta(hours=1)
        )
    assert conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)


def test_un_id_que_no_existe_da_none(conn):
    assert store.load(conn, 12345) is None


def test_un_payload_que_no_es_un_mensaje_da_value_error(conn):
    """Un `payload` válido como JSON pero que no es un mensaje: error claro."""
    conn.execute(
        "INSERT INTO approvals (created_at, expires_at, kind, payload) VALUES (?, ?, 'demo', '{}')",
        ("2026-11-21T18:00:00+00:00", "2026-11-21T19:00:00+00:00"),
    )
    with pytest.raises(ValueError):
        store.load(conn, 1)


def test_guardar_con_estado_final_pone_resolved_at_y_no_se_vuelve_a_pisar(conn):
    """Solo se sale de `pending` una vez: el segundo guardado no escribe nada."""
    clock = FixedClock(NOW)
    approval_id = store.create(conn, clock, "demo", make_message(), NOW + timedelta(hours=1))
    message = resolve(make_message(), {0: Outcome("y", "ok"), 1: Outcome("y", "ok")})
    later = NOW + timedelta(minutes=5)
    assert store.save(conn, approval_id, message, final_status(message), later) is True
    assert conn.execute(
        "SELECT status, resolved_at FROM approvals WHERE id = ?", (approval_id,)
    ).fetchone() == ("approved", "2026-11-21T18:05:00+00:00")
    other = resolve(make_message(), {0: Outcome("n", "no"), 1: Outcome("n", "no")})
    assert store.save(conn, approval_id, other, "rejected", later) is False
    approval = store.load(conn, approval_id)
    assert approval is not None
    assert approval.status == "approved"
    assert approval.message == message


def test_guardar_sin_estado_final_deja_la_fila_pendiente(conn):
    """Con algo aún pendiente el estado sigue `pending` y no hay `resolved_at`."""
    approval_id = store.create(conn, FixedClock(NOW), "demo", make_message(), NOW + timedelta(1))
    message = resolve(make_message(), {0: Outcome("y", "ok")})
    assert store.save(conn, approval_id, message, None, NOW) is True
    assert conn.execute(
        "SELECT status, resolved_at FROM approvals WHERE id = ?", (approval_id,)
    ).fetchone() == ("pending", None)
