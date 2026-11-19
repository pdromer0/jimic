"""Los proactivos de un día salen de `notices`: el corte del día local y los mensajes agrupados."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from jimic.clock import local_day_bounds, to_db
from jimic.memory.database import open_database
from jimic.memory.notices import sent_between

MADRID = ZoneInfo("Europe/Madrid")


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def add(conn, intent: str, local: str, message_id: int | None = None) -> None:
    moment = datetime.fromisoformat(local).replace(tzinfo=MADRID)
    conn.execute(
        "INSERT INTO notices (sent_at, intent, telegram_message_id) VALUES (?, ?, ?)",
        (to_db(moment), intent, message_id),
    )


def today(conn, day: date = date(2030, 1, 7)):
    return sent_between(conn, *local_day_bounds(day, MADRID))


def test_el_dia_local_corta_en_la_medianoche_de_madrid_no_en_la_de_utc(conn):
    """Pilla cortar el día en UTC: a las 00:30 de Madrid aún es ayer en UTC."""
    add(conn, "ayer", "2030-01-06T23:59:59", 1)
    add(conn, "primero", "2030-01-07T00:00:00", 2)
    add(conn, "de_madrugada", "2030-01-07T00:30:00", 3)
    add(conn, "ultimo", "2030-01-07T23:59:59", 4)
    add(conn, "manana", "2030-01-08T00:00:00", 5)
    sent = today(conn)
    assert [n.intent for n in sent.notices] == ["primero", "de_madrugada", "ultimo"]
    assert sent.message_count == 3


def test_un_mensaje_agrupado_cuenta_uno_y_cada_intencion_cuenta_la_suya(conn):
    """Pilla contar filas en lugar de mensajes (1.7-b)."""
    add(conn, "aviso_a", "2030-01-07T10:30:00", 77)
    add(conn, "aviso_b", "2030-01-07T10:30:00", 77)
    add(conn, "plan_del_dia", "2030-01-07T08:00:00", 70)
    sent = today(conn)
    assert sent.message_count == 2
    assert sent.count("aviso_a") == 1
    assert sent.count("aviso_b") == 1
    assert sent.count("otra") == 0


def test_dos_filas_sin_id_de_telegram_cuentan_dos(conn):
    """Pilla que las filas sin id se junten en un solo mensaje: sin id, lo prudente."""
    add(conn, "aviso_a", "2030-01-07T10:30:00")
    add(conn, "aviso_b", "2030-01-07T10:30:00")
    assert today(conn).message_count == 2


def test_el_ultimo_proactivo_es_el_mas_reciente_y_sin_avisos_no_hay(conn):
    assert today(conn).last_sent_at is None
    add(conn, "tarde", "2030-01-07T18:00:00", 2)
    add(conn, "manana", "2030-01-07T09:00:00", 1)
    last = today(conn).last_sent_at
    assert last == datetime(2030, 1, 7, 18, 0, tzinfo=MADRID)


@pytest.mark.parametrize(
    ("day", "inside", "outside"),
    [
        # 31-mar-2030: el día dura 23 h; 03:30 locales ya son horario de verano.
        (
            date(2030, 3, 31),
            ["2030-03-31T00:00:00", "2030-03-31T03:30:00", "2030-03-31T23:59:59"],
            ["2030-03-30T23:59:59", "2030-04-01T00:00:00"],
        ),
        # 27-oct-2030: el día dura 25 h.
        (
            date(2030, 10, 27),
            ["2030-10-27T00:00:00", "2030-10-27T23:59:59"],
            ["2030-10-26T23:59:59", "2030-10-28T00:00:00"],
        ),
    ],
)
def test_los_dias_del_cambio_de_hora_cortan_en_su_medianoche(conn, day, inside, outside):
    """Pilla calcular el final del día sumando 24 h: en marzo entraría la 1:00 de mañana y en
    octubre se perdería la última hora."""
    for number, moment in enumerate(inside + outside):
        add(conn, f"aviso_{number}", moment, number + 1)
    sent = today(conn, day)
    assert sent.message_count == len(inside)
