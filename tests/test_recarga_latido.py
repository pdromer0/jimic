"""La recarga en caliente en el latido (tarea 1.12) y la ventana `from == to` de una intención.

Al empezar cada latido, si cambió un YAML a mano, se recarga y se valida. Uno que no vale deja la
última versión buena y manda `config_invalid`, uno al día. El aviso `memory_clipped` lo registra
la conversación y lo manda el latido.
"""

import asyncio

import pytest
from conftest import FixedClock
from heartbeat_data import (
    CLOSE,
    MADRID,
    PLAN,
    FakeMessages,
    FakeSender,
    day_state,
    intentions,
    limits,
    madrid,
    sources,
)
from memory_data import DELETIONS_LINE, write_personal

from jimic.heartbeat.beat import CONFIG_INVALID_TEXT, MEMORY_CLIPPED_TEXT, Heartbeat
from jimic.heartbeat.decide import HaikuDecider, fixed_block
from jimic.memory import system_notices
from jimic.memory.database import open_database
from jimic.memory.live import LiveConfig
from jimic.memory.personal import template

MODEL = "claude-haiku-5-5"


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


class World:
    def __init__(self, conn, tmp_path):
        self.conn = conn
        self.clock = FixedClock(madrid("2030-01-07T09:00"))
        self.config_dir = write_personal(tmp_path / "config")
        self.live = LiveConfig.load(self.config_dir)
        self.sender = FakeSender()
        self.heartbeat = Heartbeat(
            clock=self.clock,
            timezone=MADRID,
            limits=limits(),
            intentions=intentions(PLAN, CLOSE),
            sources=sources(),
            conn=conn,
            decider=HaikuDecider(FakeMessages().create, MODEL, intentions(), self.live),
            sender=self.sender,
            config=self.live,
        )

    def at(self, moment: str) -> None:
        self.clock.moment = madrid(moment)
        asyncio.run(self.heartbeat.beat())

    def set_deletions(self, value) -> None:
        text = template("limites.yaml").replace(DELETIONS_LINE, f"max_deletions_per_day: {value}")
        (self.config_dir / "limites.yaml").write_text(text, encoding="utf-8")


def test_una_edicion_a_mano_valida_llega_al_latido_siguiente(conn, tmp_path):
    w = World(conn, tmp_path)
    w.set_deletions(2)
    w.at("2030-01-07T09:15")
    assert w.live.limits.max_deletions_per_day == 2
    assert w.sender.texts == []


def test_una_edicion_invalida_mantiene_la_ultima_buena_y_avisa_una_vez_al_dia(conn, tmp_path):
    w = World(conn, tmp_path)
    w.set_deletions("cinco")
    for moment in ("2030-01-07T09:15", "2030-01-07T09:30", "2030-01-07T12:00"):
        w.at(moment)
    assert w.live.limits.max_deletions_per_day == 5
    expected = CONFIG_INVALID_TEXT.format(files="limites.yaml")
    assert w.sender.texts == [expected]
    assert "jimic config check" in expected
    w.at("2030-01-08T09:00")  # sigue inválido al día siguiente: otro aviso
    assert w.sender.texts == [expected, expected]


def test_si_vuelve_a_valer_deja_de_avisar(conn, tmp_path):
    w = World(conn, tmp_path)
    w.set_deletions("cinco")
    w.at("2030-01-07T09:15")
    w.set_deletions(4)
    w.at("2030-01-08T09:00")
    assert w.live.limits.max_deletions_per_day == 4
    assert len(w.sender.texts) == 1


def test_el_aviso_de_un_fichero_recortado_lo_manda_el_latido(conn, tmp_path):
    w = World(conn, tmp_path)
    now = madrid("2030-01-07T09:00")
    system_notices.detect(conn, "memory_clipped", now.date(), now, "rutina,preferencias")
    w.at("2030-01-07T09:15")
    assert w.sender.texts == [MEMORY_CLIPPED_TEXT.format(files="rutina.md y preferencias.md")]


WHOLE_DAY = PLAN | {"window": {"from": "09:00", "to": "09:00"}}


def test_una_ventana_from_igual_a_to_es_el_dia_entero_y_no_es_pista(conn):
    """El pendiente de la 1.7 (ajuste 7): como `quiet_hours` en la decisión 80, el día entero;
    para el modelo, lo mismo que no tener ventana."""
    moment = madrid("2030-01-07T09:00")
    its = intentions(WHOLE_DAY)
    assert '"window"' not in fixed_block(its, moment)
    (active,) = day_state(conn, moment, its).intentions
    assert active.window is None
    assert '"window"' in fixed_block(intentions(PLAN), moment)
    (normal,) = day_state(conn, moment, intentions(PLAN)).intentions
    assert normal.window == {"from": "07:30", "to": "09:30"}
