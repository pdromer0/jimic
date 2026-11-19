"""Servicio: arranca, espera y para limpio (SQLite cerrada, log vaciado), con el reloj fijo."""

import asyncio
import os
import signal
import sqlite3
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import VALID_ENV, FixedClock, write_env

import jimic.cli
import jimic.service
from jimic.logs import LOG_FILE
from jimic.memory.database import open_database, schema_version
from jimic.paths import resolve_paths

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=ZoneInfo("Europe/Madrid"))
SIGNAL_WAIT = 10
"""Segundos como mucho para que el servicio arranque o pare tras la señal en los tests."""


class GuardSignalError(Exception):
    """The test's own handler ran: the service did not install its signal handler."""


@pytest.fixture
def guard_signals():
    """Install a handler that raises on SIGINT/SIGTERM, so a missing one fails the test.

    Without it, a SIGTERM the service ignores would kill pytest instead of failing a test.
    """

    def guard(signum, frame):
        raise GuardSignalError(signal.Signals(signum).name)

    previous = {sig: signal.signal(sig, guard) for sig in jimic.service.STOP_SIGNALS}
    yield
    for sig, handler in previous.items():
        signal.signal(sig, handler)


@pytest.fixture
def opened(monkeypatch):
    """Record every connection the service opens, to check later that it was closed."""
    connections: list[sqlite3.Connection] = []

    def recording_open(path):
        conn = open_database(path)
        connections.append(conn)
        return conn

    monkeypatch.setattr(jimic.service, "open_database", recording_open)
    return connections


def assert_closed(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_arranca_y_para_con_el_evento(opened):
    """Pilla una parada sucia: la base queda abierta o el log sin la línea de parada."""
    paths = resolve_paths(os.environ)

    async def scenario():
        stop, ready = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(jimic.service.serve(paths, FixedClock(NOW), stop, ready))
        await asyncio.wait_for(ready.wait(), SIGNAL_WAIT)
        assert not task.done()
        stop.set()
        await asyncio.wait_for(task, SIGNAL_WAIT)

    asyncio.run(scenario())
    assert len(opened) == 1
    assert_closed(opened[0])
    log = (paths.logs_dir / LOG_FILE).read_text()
    assert "started at 2026-11-19T09:00:00+01:00 (prod)" in log
    assert "stopped at 2026-11-19T09:00:00+01:00" in log
    conn = open_database(paths.database)
    assert schema_version(conn) == 1
    conn.close()


def test_la_raiz_de_datos_es_privada(opened):
    """Pilla la raíz de datos creada con la umask (0755) al crear `logs/` debajo con `parents`."""
    paths = resolve_paths(os.environ)

    async def scenario():
        stop = asyncio.Event()
        stop.set()
        await jimic.service.serve(paths, FixedClock(NOW), stop)

    asyncio.run(scenario())
    assert paths.data_dir.stat().st_mode & 0o777 == 0o700
    assert paths.logs_dir.stat().st_mode & 0o777 == 0o700


def test_el_evento_puesto_antes_de_arrancar_para_enseguida(opened):
    """El caso límite: la parada llega antes que el arranque y aun así cierra todo."""
    paths = resolve_paths(os.environ)

    async def scenario():
        stop = asyncio.Event()
        stop.set()
        await asyncio.wait_for(jimic.service.serve(paths, FixedClock(NOW), stop), SIGNAL_WAIT)

    asyncio.run(scenario())
    assert_closed(opened[0])
    assert "stopped at" in (paths.logs_dir / LOG_FILE).read_text()


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_sigint_y_sigterm_paran_limpio(guard_signals, opened, sig):
    """Pilla un servicio sin manejador de señales: el del test lanza y el test falla."""
    paths = resolve_paths(os.environ)

    async def scenario():
        ready = asyncio.Event()
        task = asyncio.create_task(jimic.service.serve_until_signal(paths, FixedClock(NOW), ready))
        await asyncio.wait_for(ready.wait(), SIGNAL_WAIT)
        signal.raise_signal(sig)
        await asyncio.wait_for(task, SIGNAL_WAIT)

    asyncio.run(scenario())
    assert_closed(opened[0])
    assert "stopped at" in (paths.logs_dir / LOG_FILE).read_text()


def test_jimic_run_sin_env_sale_con_2_y_dice_donde_lo_busca(capsys):
    """Pilla un traceback o una salida con 0 cuando falta la configuración."""
    paths = resolve_paths(os.environ)
    assert jimic.cli.main(["run"]) == 2
    err = capsys.readouterr().err
    assert err == f"jimic: no existe {paths.env_file}\n"
    assert not paths.data_dir.exists()


def test_jimic_run_arranca_y_para_con_sigterm(guard_signals, capsys):
    """De punta a punta: la CLI valida el `.env`, arranca y para con SIGTERM saliendo con 0."""
    write_env(VALID_ENV)
    paths = resolve_paths(os.environ)
    log_file = paths.logs_dir / LOG_FILE

    def stop_when_started():
        deadline = time.monotonic() + SIGNAL_WAIT
        while time.monotonic() < deadline:
            if log_file.exists() and "database open" in log_file.read_text():
                break
            time.sleep(0.05)
        signal.raise_signal(signal.SIGTERM)

    stopper = threading.Thread(target=stop_when_started)
    stopper.start()
    try:
        assert jimic.cli.main(["run"]) == 0
    finally:
        stopper.join()
    log = log_file.read_text()
    assert "started at" in log
    assert "stopped at" in log
    assert VALID_ENV["TOKEN_TELEGRAM"] not in log + capsys.readouterr().err
