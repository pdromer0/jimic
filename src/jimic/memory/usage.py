"""The spend counter (10.3 of the design): one row per model call, tokens and dollars only.

Prices live here, with the day they were seen (annex B of the design). The heartbeat model must
have a price: `jimic run` refuses to start otherwise, so nothing is spent without counting it.
Amounts are computed with `Decimal` and stored as `REAL`; sums come back as `float`.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

PRICES_SEEN = date(2026, 11, 19)
"""When the prices below were read (Anthropic's pricing page; published on 7-oct-2026)."""

PER_TOKEN = Decimal(1_000_000)
"""Prices are in dollars per million tokens."""


@dataclass(frozen=True)
class Price:
    """Dollars per million tokens. A `None` price is one not confirmed yet: using it raises."""

    input: Decimal
    output: Decimal
    cache_read: Decimal | None = None
    cache_write_5m: Decimal | None = None
    cache_write_1h: Decimal | None = None
    long_prompt: int | None = None
    """Above this many prompt tokens (input plus cache), every price is multiplied..."""
    long_factor: Decimal = Decimal(1)
    """... by this."""


PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(
        input=Decimal("0.10"),
        output=Decimal("0.50"),
        cache_read=Decimal("0.01"),
        cache_write_5m=Decimal("0.125"),
        cache_write_1h=Decimal("0.20"),
        long_prompt=100_000,
        long_factor=Decimal(5),
    ),
    # For task 1.10: its cache prices and long prompts are confirmed there.
    "claude-sonnet-5-5": Price(input=Decimal(2), output=Decimal(10)),
}


class UnpricedError(Exception):
    """A model or a kind of token without a price; the message names it, never content."""


@dataclass(frozen=True)
class TokenUsage:
    """What one call used, as the API reports it (cache writes split by their TTL)."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0

    @property
    def cache_write_tokens(self) -> int:
        return self.cache_write_5m_tokens + self.cache_write_1h_tokens

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


def price_of(model: str) -> Price:
    try:
        return PRICES[model]
    except KeyError:
        raise UnpricedError(f"el modelo {model} no tiene precio en jimic.memory.usage") from None


def _priced(price: Decimal | None, tokens: int, what: str) -> Decimal:
    if tokens == 0:
        return Decimal(0)
    if price is None:
        raise UnpricedError(f"sin precio para {what}")
    return price * tokens


def cost(model: str, usage: TokenUsage) -> Decimal:
    """Dollars of one call. Above `long_prompt` prompt tokens, everything costs `long_factor`
    times as much, output included."""
    price = price_of(model)
    total = (
        price.input * usage.input_tokens
        + price.output * usage.output_tokens
        + _priced(price.cache_read, usage.cache_read_tokens, "lectura de caché")
        + _priced(price.cache_write_5m, usage.cache_write_5m_tokens, "escritura de caché (5 min)")
        + _priced(price.cache_write_1h, usage.cache_write_1h_tokens, "escritura de caché (1 h)")
    )
    if price.long_prompt is not None and usage.prompt_tokens > price.long_prompt:
        total *= price.long_factor
    return total / PER_TOKEN


def record(conn: sqlite3.Connection, day: date, model: str, usage: TokenUsage) -> Decimal:
    """Add the row of one call (its local day) and return its dollars."""
    usd = cost(model, usage)
    conn.execute(
        "INSERT INTO model_usage (day, model, input_tokens, output_tokens, cache_read_tokens,"
        " cache_write_tokens, usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            day.isoformat(),
            model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
            float(usd),
        ),
    )
    return usd


def spent_on(conn: sqlite3.Connection, day: date) -> float:
    """Dollars spent on the local `day` (task 1.10 uses it for `daily_cap_usd`)."""
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(usd), 0) FROM model_usage WHERE day = ?", (day.isoformat(),)
    ).fetchone()
    return total


def spent_in_month(conn: sqlite3.Connection, day: date) -> float:
    """Dollars spent from the first day of the month of `day` (local) to its end."""
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(usd), 0) FROM model_usage WHERE substr(day, 1, 7) = ?",
        (day.strftime("%Y-%m"),),
    ).fetchone()
    return total
