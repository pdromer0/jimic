"""Retención: cada tabla guarda lo suyo, con cortes absolutos en UTC y días locales."""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import FixedClock

from jimic.memory.database import open_database
from jimic.memory.retention import purge

MADRID = ZoneInfo("Europe/Madrid")
SUMMER_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=MADRID)
"""10:00Z; ninguna retención cruza un cambio de hora desde aquí (calculado a mano en el plan)."""


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.sqlite3")
    yield connection
    connection.close()


def add_decision(conn: sqlite3.Connection, created_at: str) -> None:
    conn.execute(
        "INSERT INTO decisions (created_at, speak, reason) VALUES (?, 0, 'motivo inventado')",
        (created_at,),
    )


def add_notice(conn: sqlite3.Connection, sent_at: str) -> None:
    conn.execute(
        "INSERT INTO notices (sent_at, intent) VALUES (?, 'intención inventada')", (sent_at,)
    )


def add_approval(conn: sqlite3.Connection, created_at: str) -> None:
    conn.execute(
        "INSERT INTO approvals (created_at, expires_at, kind, payload) VALUES (?, ?, 'x', '{}')",
        (created_at, created_at),
    )


def add_deleted_task(conn: sqlite3.Connection, deleted_at: str, content: str = "Tarea") -> None:
    conn.execute(
        "INSERT INTO deleted_tasks (deleted_at, todoist_task_id, content, project_id, priority,"
        " reason) VALUES (?, 'tarea-falsa', ?, 'proyecto-falso', 1, 'motivo inventado')",
        (deleted_at, content),
    )


def add_day(conn: sqlite3.Connection, day: str) -> None:
    """Add a plan, one plan item and a rating for `day`; their moments do not matter here."""
    moment = "2000-01-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO day_plans (day, confirmed_at, plan) VALUES (?, ?, '[]')", (day, moment)
    )
    conn.execute(
        "INSERT INTO plan_items (day, todoist_task_id, status) VALUES (?, 'tarea-falsa', 'done')",
        (day,),
    )
    conn.execute("INSERT INTO day_ratings (day, rating, rated_at) VALUES (?, 3, ?)", (day, moment))


def add_system_notice(conn: sqlite3.Connection, detected_at: str) -> None:
    """Each row gets its own period, so `UNIQUE (kind, period)` never gets in the way."""
    (rows,) = conn.execute("SELECT COUNT(*) FROM system_notices").fetchone()
    conn.execute(
        "INSERT INTO system_notices (kind, period, detected_at) VALUES ('api_down', ?, ?)",
        (f"2000-01-{rows + 1:02d}", detected_at),
    )


def add_usage(conn: sqlite3.Connection, day: str) -> None:
    conn.execute(
        "INSERT INTO model_usage (day, model, input_tokens, output_tokens, cache_read_tokens,"
        " cache_write_tokens, usd) VALUES (?, 'modelo-inventado', 1, 1, 0, 0, 0.0)",
        (day,),
    )


def column_values(conn: sqlite3.Connection, table: str, column: str) -> list[str]:
    return [row[0] for row in conn.execute(f"SELECT {column} FROM {table} ORDER BY {column}")]


@pytest.mark.parametrize(
    ("add", "table", "column", "cutoff", "second_before"),
    [
        (add_decision, "decisions", "created_at", "2026-04-16T10:00:00+00:00", "09:59:59"),
        (add_notice, "notices", "sent_at", "2025-07-15T10:00:00+00:00", "09:59:59"),
        (add_approval, "approvals", "created_at", "2026-06-15T10:00:00+00:00", "09:59:59"),
        (add_deleted_task, "deleted_tasks", "deleted_at", "2026-06-15T10:00:00+00:00", "09:59:59"),
        (
            add_system_notice,
            "system_notices",
            "detected_at",
            "2025-07-15T10:00:00+00:00",
            "09:59:59",
        ),
    ],
)
def test_cada_tabla_borra_lo_anterior_a_su_corte_y_guarda_la_frontera(
    conn, add, table, column, cutoff, second_before
):
    """Pilla una retención equivocada (90, 365, 30, 30, 365 días) o `<=` en vez de `<`."""
    before = cutoff.replace("10:00:00", second_before)
    add(conn, before)
    add(conn, cutoff)
    deleted = purge(conn, FixedClock(SUMMER_NOW))
    assert deleted[table] == 1
    assert column_values(conn, table, column) == [cutoff]


def test_el_corte_es_absoluto_aunque_cruce_un_cambio_de_hora(conn):
    """Pilla el corte de reloj de pared: desde invierno, 90 días de pared quedan 1 h antes."""
    now = datetime(2026, 11, 19, 9, 0, tzinfo=MADRID)
    add_decision(conn, "2026-08-21T07:30:00+00:00")
    add_decision(conn, "2026-08-21T08:00:00+00:00")
    assert purge(conn, FixedClock(now))["decisions"] == 1
    assert column_values(conn, "decisions", "created_at") == ["2026-08-21T08:00:00+00:00"]


@pytest.mark.parametrize(
    ("now", "deleted_day", "kept_day"),
    [
        (datetime(2026, 11, 19, 9, 0, tzinfo=MADRID), "2025-11-18", "2025-11-19"),
        (datetime(2028, 2, 29, 9, 0, tzinfo=MADRID), "2027-02-28", "2027-03-01"),
        (datetime(2026, 11, 19, 0, 30, tzinfo=MADRID), "2025-11-18", "2025-11-19"),
    ],
)
def test_las_tablas_por_dia_guardan_365_dias_locales(conn, now, deleted_day, kept_day):
    """Pilla el día en UTC (a las 00:30 de Madrid aún es el día anterior en UTC) y el 29-F."""
    for day in (deleted_day, kept_day):
        add_day(conn, day)
        add_usage(conn, day)
    deleted = purge(conn, FixedClock(now))
    for table in ("plan_items", "day_ratings", "day_plans", "model_usage"):
        assert deleted[table] == 1
        assert column_values(conn, table, "day") == [kept_day]


def test_los_cambios_de_memoria_no_se_purgan(conn):
    """Pilla que `changes` entre en la purga: se guarda siempre."""
    conn.execute(
        "INSERT INTO changes (created_at, file, source_message) VALUES (?, 'f.yaml', 'm')",
        ("2001-01-01T00:00:00+00:00",),
    )
    deleted = purge(conn, FixedClock(SUMMER_NOW))
    assert "changes" not in deleted
    assert column_values(conn, "changes", "created_at") == ["2001-01-01T00:00:00+00:00"]


def test_una_base_vacia_se_purga_sin_error(conn):
    """El caso vacío: todas las tablas con retención, a cero."""
    assert purge(conn, FixedClock(SUMMER_NOW)) == {
        "decisions": 0,
        "notices": 0,
        "approvals": 0,
        "deleted_tasks": 0,
        "system_notices": 0,
        "plan_items": 0,
        "day_ratings": 0,
        "day_plans": 0,
        "model_usage": 0,
    }
