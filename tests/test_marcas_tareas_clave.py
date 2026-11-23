"""Las marcas de las tareas clave en SQLite (tarea 1.15): por día local y sin texto de Pedro."""

import sqlite3
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from jimic.memory import key_marks
from jimic.memory.database import open_database

MADRID = ZoneInfo("Europe/Madrid")
DAY = date(2030, 1, 10)


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.sqlite3")
    yield connection
    connection.close()


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2030, 1, 10, hour, minute, tzinfo=MADRID)


def test_cada_marca_se_lee_por_su_intencion(conn):
    key_marks.record(conn, DAY, "gimnasio", "done", at(20, 15), "t-1")
    key_marks.record(conn, DAY, "ofertas", "not_today", at(9))
    marks = key_marks.of_day(conn, DAY)
    assert marks.of("gimnasio").done and not marks.of("gimnasio").not_today
    assert marks.of("ofertas").not_today and not marks.of("ofertas").done
    assert marks.of("otra") == key_marks.IntentMarks()


def test_de_dos_voy_ahora_manda_el_ultimo(conn):
    """Pilla el primero en vez del último: la pausa cuenta desde el toque más reciente."""
    key_marks.record(conn, DAY, "gimnasio", "going", at(13, 30))
    key_marks.record(conn, DAY, "gimnasio", "going", at(12, 0))  # escrito después, más antiguo
    key_marks.record(conn, DAY, "gimnasio", "going", at(14, 0))
    assert key_marks.of_day(conn, DAY).of("gimnasio").going_at == at(14, 0).astimezone(UTC)


def test_las_marcas_son_del_dia_local_que_se_les_dice(conn):
    """Pilla el día en UTC: a las 00:30 de Madrid, en UTC aún es el día anterior."""
    moment = datetime(2030, 1, 11, 0, 30, tzinfo=MADRID)
    key_marks.record(conn, date(2030, 1, 11), "gimnasio", "not_today", moment)
    assert key_marks.of_day(conn, date(2030, 1, 11)).of("gimnasio").not_today
    assert not key_marks.of_day(conn, DAY).of("gimnasio").not_today


def test_deshacer_borra_la_marca(conn):
    mark = key_marks.record(conn, DAY, "gimnasio", "done", at(20, 15))
    assert key_marks.remove(conn, mark)
    assert not key_marks.of_day(conn, DAY).of("gimnasio").done
    assert not key_marks.remove(conn, mark)


def test_vista_se_anota_una_vez_por_tarea_y_dia(conn):
    for hour in (12, 13):
        key_marks.note_seen(conn, DAY, "gimnasio", "t-1", at(hour))
    key_marks.note_seen(conn, DAY, "gimnasio", "t-2", at(14))
    (rows,) = conn.execute("SELECT COUNT(*) FROM key_task_marks").fetchone()
    assert rows == 2
    assert key_marks.of_day(conn, DAY).of("gimnasio").seen == {"t-1", "t-2"}


def test_los_movimientos_de_jimic_son_del_dia(conn):
    key_marks.note_moved(conn, DAY, "t-1", at(13, 35))
    key_marks.note_moved(conn, date(2030, 1, 9), "t-2", at(9))
    assert key_marks.of_day(conn, DAY).moved == {"t-1"}


def test_ninguna_columna_nueva_guarda_texto_de_pedro(conn):
    """Las dos tablas de la 1.15 solo tienen días, momentos, ids y la marca."""
    for table, expected in (
        ("key_task_marks", ["id", "day", "intent", "mark", "marked_at", "todoist_task_id"]),
        ("task_moves", ["id", "day", "todoist_task_id", "moved_at"]),
    ):
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        assert columns == expected


def test_una_marca_desconocida_se_rechaza(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO key_task_marks (day, intent, mark, marked_at)"
            " VALUES ('2030-01-10', 'x', 'motivo', '2030-01-10T10:00:00+00:00')"
        )
