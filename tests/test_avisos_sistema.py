"""El registro de los avisos del sistema (decisión 1.8-c): uno por tipo y día, el del presupuesto
uno al mes, y sin texto."""

import sqlite3
from datetime import UTC, date, datetime

import pytest

from jimic.memory.database import open_database
from jimic.memory.system_notices import detect, mark_sent, pending

DAY = date(2030, 1, 7)
MOMENT = datetime(2030, 1, 7, 9, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.sqlite3")
    yield connection
    connection.close()


def kinds(conn, day):
    return [notice.kind for notice in pending(conn, day)]


def test_uno_por_tipo_y_dia(conn):
    """Pilla un registro sin `UNIQUE`: el segundo «API caída» del día no se apunta."""
    assert detect(conn, "api_down", DAY, MOMENT)
    assert not detect(conn, "api_down", DAY, MOMENT)
    assert detect(conn, "source_down", DAY, MOMENT, "calendar:auth")
    assert detect(conn, "api_down", date(2030, 1, 8), MOMENT)
    assert kinds(conn, DAY) == ["api_down", "source_down"]


def test_el_del_presupuesto_es_uno_al_mes(conn):
    assert detect(conn, "budget", date(2030, 1, 3), MOMENT)
    assert not detect(conn, "budget", date(2030, 1, 31), MOMENT)
    assert detect(conn, "budget", date(2030, 2, 1), MOMENT)


def test_lo_enviado_ya_no_espera(conn):
    detect(conn, "api_down", DAY, MOMENT)
    (notice,) = pending(conn, DAY)
    mark_sent(conn, notice.id, MOMENT)
    assert pending(conn, DAY) == []


def test_uno_diario_de_ayer_que_sigue_esperando_ya_no_sale_y_el_del_mes_si(conn):
    """El de un día viejo caduca; el del presupuesto vale todo su mes."""
    detect(conn, "api_down", DAY, MOMENT)
    detect(conn, "budget", DAY, MOMENT)
    assert kinds(conn, date(2030, 1, 8)) == ["budget"]
    assert kinds(conn, date(2030, 2, 1)) == []


@pytest.mark.parametrize("detail", ["Título secreto", "calendar:auth; rm", "a b"])
def test_el_detalle_solo_admite_codigos(conn, detail):
    """Pilla un registro que podría guardar texto: el `CHECK` solo deja letras, cifras y `_:,`."""
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        detect(conn, "source_down", DAY, MOMENT, detail)


def test_el_detalle_admite_los_codigos_de_las_fuentes(conn):
    assert detect(conn, "source_down", DAY, MOMENT, "calendar:auth,apex:unexpected:KeyError")
