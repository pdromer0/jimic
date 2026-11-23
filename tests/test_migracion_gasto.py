"""La migración 0008: el gasto de las filas de Sonnet ya guardadas pasa a contarse por tokens."""

import re
import sqlite3
from decimal import Decimal
from importlib.resources import files

import pytest

from jimic.memory.database import load_migrations, migrate
from jimic.memory.usage import PRICES, TokenUsage, cost

SONNET = "claude-sonnet-5-5"


def insert(conn, model, tokens, usd):
    conn.execute(
        "INSERT INTO model_usage (day, model, input_tokens, output_tokens, cache_read_tokens,"
        " cache_write_tokens, usd) VALUES ('2030-01-07', ?, ?, ?, ?, ?, ?)",
        (model, *tokens, usd),
    )


def usd_of(conn) -> list[float]:
    return [row[0] for row in conn.execute("SELECT usd FROM model_usage ORDER BY id")]


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "jimic.db", autocommit=True)
    migrate(connection, load_migrations()[:7])
    yield connection
    connection.close()


def test_las_filas_de_sonnet_con_tokens_valen_lo_que_dan_sus_tokens(conn):
    """Dos filas acumuladas como las del 23-nov (0,006 y 0,0075 en vez de 0,00602 y 0,00146)."""
    insert(conn, SONNET, (10, 100, 0, 2000), 0.00602)
    insert(conn, SONNET, (5, 50, 2000, 300), 0.00748)
    migrate(conn, load_migrations())
    assert usd_of(conn) == pytest.approx([0.00602, 0.00146])


def test_las_de_sonnet_sin_tokens_no_cambian(conn):
    """Sin tokens el `usd` es el tope de la ejecución. Pilla recalcular también esas (a 0)."""
    insert(conn, SONNET, (0, 0, 0, 0), 0.5)
    migrate(conn, load_migrations())
    assert usd_of(conn) == [0.5]


def test_las_de_haiku_y_otros_modelos_no_cambian(conn):
    """Haiku ya se contaba por tokens. Pilla una migración que no filtra por modelo."""
    insert(conn, "claude-haiku-5-5", (1000, 100, 0, 0), 7.77)
    insert(conn, "modelo-inventado", (1000, 100, 0, 0), 8.88)
    migrate(conn, load_migrations())
    assert usd_of(conn) == [7.77, 8.88]


def test_una_fila_de_sonnet_con_solo_cache_tambien_se_recalcula(conn):
    insert(conn, SONNET, (0, 0, 1_000_000, 0), 3.0)
    insert(conn, SONNET, (0, 0, 0, 1_000_000), 3.0)
    migrate(conn, load_migrations())
    assert usd_of(conn) == pytest.approx([0.10, 2.50])


def test_una_base_sin_filas_migra(conn):
    assert migrate(conn, load_migrations()) == 1


def test_los_precios_de_la_migracion_son_los_de_la_tabla():
    """Un millón de tokens de cada clase, con los números que lleva el SQL, frente a `cost`."""
    sql = files("jimic.memory").joinpath("migrations/0008_gasto_agente.sql").read_text("utf-8")
    prices = [
        Decimal(m)
        for m in re.findall(
            r"(\d+(?:\.\d+)?) \* (?:input|output|cache_read|cache_write)_tokens", sql
        )
    ]
    assert len(prices) == 4
    for index, price in enumerate(prices):
        usage = [0, 0, 0, 0]
        usage[index] = 1_000_000
        assert price == cost(SONNET, TokenUsage(*usage)), index
    assert prices[3] == PRICES[SONNET].cache_write_5m
