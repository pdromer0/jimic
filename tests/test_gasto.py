"""El contador de gasto: el coste de cada llamada con la caché, el salto de 100 000 y las sumas.

Los valores esperados son los del plan, calculados a mano con código independiente: llamada tipo
de 2000 tokens fijos cacheados, 2500 del estado y 100 de salida.
"""

from datetime import date
from decimal import Decimal

import pytest

from jimic.memory.database import open_database
from jimic.memory.usage import (
    PRICES,
    TokenUsage,
    UnpricedError,
    cost,
    record,
    spent_in_month,
    spent_on,
)

HAIKU = "claude-haiku-5-5"
FIRST_1H = TokenUsage(input_tokens=2500, output_tokens=100, cache_write_1h_tokens=2000)
FIRST_5M = TokenUsage(input_tokens=2500, output_tokens=100, cache_write_5m_tokens=2000)
CACHED = TokenUsage(input_tokens=2500, output_tokens=100, cache_read_tokens=2000)
NO_CACHE = TokenUsage(input_tokens=4500, output_tokens=100)


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.sqlite3")
    yield connection
    connection.close()


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (FIRST_1H, "0.0007"),
        (FIRST_5M, "0.00055"),
        (CACHED, "0.00032"),
        (NO_CACHE, "0.0005"),
    ],
)
def test_coste_de_la_llamada_tipo(usage, expected):
    """Pilla un precio de la tabla cambiado, la caché cobrada como entrada o el TTL confundido."""
    assert cost(HAIKU, usage) == Decimal(expected)


def test_la_lectura_de_cache_se_cobra_a_su_precio_y_no_gratis():
    """Pilla un contador que ignora la lectura de caché: saldría 0,0003, no 0,00032."""
    assert cost(HAIKU, CACHED) - cost(HAIKU, TokenUsage(2500, 100)) == Decimal("0.00002")


@pytest.mark.parametrize(
    ("input_tokens", "expected"),
    [(100_000, "0.0101"), (100_001, "0.0505005")],
)
def test_por_encima_de_100_000_tokens_de_prompt_todo_cuesta_cinco_veces(input_tokens, expected):
    """El borde: 100 000 tokens de entrada (más 200 de salida) aún van a la tarifa normal."""
    assert cost(HAIKU, TokenUsage(input_tokens, 200)) == Decimal(expected)


def test_el_salto_de_100_000_cuenta_tambien_la_cache():
    """Pilla un tramo que solo mira `input_tokens`: 2500 + 97 501 de caché pasan de 100 000."""
    usage = TokenUsage(input_tokens=2500, output_tokens=0, cache_read_tokens=97_501)
    assert cost(HAIKU, usage) == 5 * (2500 * Decimal("0.10") + 97_501 * Decimal("0.01")) / 10**6


def test_un_modelo_sin_precio_no_se_cuenta_en_silencio():
    with pytest.raises(UnpricedError, match="modelo-sin-precio"):
        cost("modelo-sin-precio", NO_CACHE)


def test_sonnet_aun_no_tiene_precio_de_cache():
    """La 1.10 confirma su caché: hasta entonces, usarla es un error, no un cero."""
    assert PRICES["claude-sonnet-5-5"].cache_read is None
    with pytest.raises(UnpricedError):
        cost("claude-sonnet-5-5", CACHED)


def test_un_dia_de_60_llamadas_con_la_reunion_escribe_la_cache_dos_veces(conn):
    """El día de ejemplo con caché de 1 h: el hueco de 9:45 a 11:00 (75 min) la deja caducar."""
    day = date(2030, 1, 7)
    for usage in [FIRST_1H, *[CACHED] * 29, FIRST_1H, *[CACHED] * 29]:
        record(conn, day, HAIKU, usage)
    assert spent_on(conn, day) == pytest.approx(0.01996, abs=1e-12)
    (rows,) = conn.execute("SELECT COUNT(*) FROM model_usage").fetchone()
    assert rows == 60


def test_la_fila_guarda_los_tokens_y_los_dolares_sin_contenido(conn):
    usd = record(conn, date(2030, 1, 7), HAIKU, FIRST_1H)
    row = conn.execute("SELECT * FROM model_usage").fetchone()
    assert row[1:] == ("2030-01-07", HAIKU, 2500, 100, 0, 2000, float(usd))


def test_gastado_hoy_y_en_el_mes_solo_cuentan_su_periodo(conn):
    """Pilla un mes que empieza en otro sitio que el día 1 o un día que suma otros días."""
    for day in (date(2030, 1, 31), date(2030, 2, 1), date(2030, 2, 1), date(2030, 2, 28)):
        record(conn, day, HAIKU, NO_CACHE)
    assert spent_on(conn, date(2030, 2, 1)) == pytest.approx(0.001, abs=1e-12)
    assert spent_in_month(conn, date(2030, 2, 15)) == pytest.approx(0.0015, abs=1e-12)
    assert spent_in_month(conn, date(2030, 1, 1)) == pytest.approx(0.0005, abs=1e-12)
    assert spent_on(conn, date(2030, 3, 1)) == 0
