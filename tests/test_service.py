"""Servicio: arranca, espera y para limpio (SQLite cerrada, log vaciado), con el reloj fijo."""

import asyncio
import os
import signal
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import pytest
from conftest import VALID_ENV, FixedClock, write_env
from heartbeat_data import FakeMessages, intentions, limits
from todoist_data import FakeTodoistApi

import jimic.cli
import jimic.service
from jimic.channel.bot import ChannelError
from jimic.config import load_settings
from jimic.heartbeat.decide import ModelApi
from jimic.logs import LOG_FILE
from jimic.memory import personal
from jimic.memory.database import load_migrations, open_database, schema_version
from jimic.paths import resolve_paths
from jimic.runtime import Runtime

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=ZoneInfo("Europe/Madrid"))
SIGNAL_WAIT = 10
"""Segundos como mucho para que el servicio arranque o pare tras la señal en los tests."""


def write_setup(env: dict[str, str] = VALID_ENV) -> None:
    """A valid `.env` and the two YAML templates, as `jimic config init` writes them."""
    write_env(env)
    personal.init(resolve_paths(os.environ).config_dir)


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


class FakeChannel:
    """A channel that records its events and checks that the database is still open on `stop`."""

    def __init__(self, conn: sqlite3.Connection, events: list[str], fail_start=False) -> None:
        self.conn = conn
        self.events = events
        self.fail_start = fail_start

    async def start(self) -> None:
        self.events.append("start")
        self.conn.execute("SELECT 1")
        if self.fail_start:
            raise ChannelError("no se pudo arrancar Telegram (InvalidToken)")

    async def stop(self) -> None:
        self.conn.execute("SELECT 1")  # la base sigue abierta: el canal para antes de cerrarla
        self.events.append("stop")


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
    assert schema_version(conn) == len(load_migrations())
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


def test_el_canal_arranca_tras_la_base_y_para_antes_de_cerrarla(opened):
    """Pilla un canal que arranca sin base o que para con la base ya cerrada: `FakeChannel` la
    usa en `start` y en `stop`. Con el bot de verdad no se prueba aquí: no se arranca."""
    paths = resolve_paths(os.environ)
    events: list[str] = []

    async def scenario():
        stop, ready = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(
            jimic.service.serve(
                paths, FixedClock(NOW), stop, ready, lambda conn: FakeChannel(conn, events)
            )
        )
        await asyncio.wait_for(ready.wait(), SIGNAL_WAIT)
        assert events == ["start"]
        stop.set()
        await asyncio.wait_for(task, SIGNAL_WAIT)

    asyncio.run(scenario())
    assert events == ["start", "stop"]
    assert_closed(opened[0])


def test_un_canal_que_no_arranca_levanta_channel_error_y_deja_todo_cerrado(opened):
    """Pilla un arranque fallido que deja la base abierta o `ready` puesto."""
    paths = resolve_paths(os.environ)
    events: list[str] = []
    ready = asyncio.Event()

    async def scenario():
        with pytest.raises(ChannelError, match="InvalidToken"):
            await jimic.service.serve(
                paths,
                FixedClock(NOW),
                asyncio.Event(),
                ready,
                lambda conn: FakeChannel(conn, events, fail_start=True),
            )

    asyncio.run(scenario())
    assert not ready.is_set()
    assert events == ["start", "stop"]
    assert_closed(opened[0])
    assert "stopped at" in (paths.logs_dir / LOG_FILE).read_text()


def test_jimic_run_con_un_canal_que_no_arranca_sale_con_2_sin_valores(
    monkeypatch, capsys, guard_signals
):
    """De punta a punta: el mensaje nombra el tipo de fallo y nunca el token."""
    write_setup()
    events: list[str] = []
    monkeypatch.setattr(
        jimic.service,
        "build_runtime",
        lambda settings, paths, clock, conn, its, lim: FakeChannel(conn, events, fail_start=True),
    )
    assert jimic.cli.main(["run"]) == 2
    err = capsys.readouterr().err
    assert err.endswith("jimic: no se pudo arrancar Telegram (InvalidToken)\n")
    assert VALID_ENV["TOKEN_TELEGRAM"] not in err


def test_jimic_run_arranca_y_para_con_sigterm(guard_signals, capsys, monkeypatch):
    """De punta a punta: la CLI valida el `.env`, arranca y para con SIGTERM saliendo con 0."""
    write_setup()
    events: list[str] = []
    monkeypatch.setattr(
        jimic.service,
        "build_runtime",
        lambda settings, paths, clock, conn, its, lim: FakeChannel(conn, events),
    )
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
    assert events == ["start", "stop"]
    assert VALID_ENV["TOKEN_TELEGRAM"] not in log + capsys.readouterr().err


def test_una_base_rota_sale_con_2_y_queda_en_el_log(capsys):
    """Pilla un traceback con salida 1 y un log que no dice por qué no arrancó."""
    write_setup()
    paths = resolve_paths(os.environ)
    paths.data_dir.mkdir(parents=True)
    paths.database.write_bytes(b"esto no es una base de datos SQLite, es texto inventado" * 20)
    assert jimic.cli.main(["run"]) == 2
    assert capsys.readouterr().err.endswith("jimic: file is not a database\n")
    log = (paths.logs_dir / LOG_FILE).read_text()
    assert "startup failed (DatabaseError)" in log


# --- Lo que arranca `jimic run` (tarea 1.8) ------------------------------------------------------


def test_jimic_run_no_arranca_con_un_limites_yaml_no_valido(capsys):
    """Pilla un servicio que arranca con la zona protegida rota (y así sin límites que valgan).
    El informe es el de `jimic config check` y no enseña el contenido."""
    write_setup()
    config_dir = resolve_paths(os.environ).config_dir
    limits_file = config_dir / "limites.yaml"
    text = limits_file.read_text().replace("max_messages_per_day: 8", "max_messages_per_day: ocho")
    limits_file.write_text(text)
    assert jimic.cli.main(["run"]) == 2
    err = capsys.readouterr().err
    assert "limites.yaml: no es válido" in err
    assert "max_messages_per_day: debe ser un número entero" in err
    assert "ocho" not in err
    assert not resolve_paths(os.environ).database.exists()


def test_jimic_run_no_arranca_sin_los_yaml_pero_si_sin_los_markdown(capsys, monkeypatch):
    """Los Markdown los crea la puesta en marcha (1.13): no impiden arrancar."""
    write_env(VALID_ENV)
    assert jimic.cli.main(["run"]) == 2
    assert "no existe: créalo con `jimic config init`" in capsys.readouterr().err
    personal.init(resolve_paths(os.environ).config_dir)
    monkeypatch.setattr(
        jimic.service,
        "build_runtime",
        lambda settings, paths, clock, conn, its, lim: FakeChannel(conn, [], fail_start=True),
    )
    assert jimic.cli.main(["run"]) == 2  # llega hasta el canal: los ficheros ya valen
    assert "no se pudo arrancar Telegram" in capsys.readouterr().err


def test_jimic_run_no_arranca_con_un_modelo_sin_precio(capsys):
    """Nada se gasta sin contarlo."""
    write_setup({**VALID_ENV, "MODELO_LATIDO": "modelo-sin-precio"})
    assert jimic.cli.main(["run"]) == 2
    assert capsys.readouterr().err == (
        "jimic: el modelo modelo-sin-precio no tiene precio en jimic.memory.usage\n"
    )


class FakeRuntimeChannel:
    """The channel as the runtime uses it: start, stop, send and schedule, all recorded."""

    def __init__(self, events: list, fail_start: bool = False) -> None:
        self.events = events
        self.fail_start = fail_start
        self.scheduled: list[tuple] = []

    async def start(self) -> None:
        self.events.append("channel start")
        if self.fail_start:
            raise ChannelError("no se pudo arrancar Telegram (InvalidToken)")

    async def stop(self) -> None:
        self.events.append("channel stop")

    async def send_text(self, text: str) -> int:
        raise AssertionError("the runtime test sends nothing")

    def schedule_every(self, task, interval, first, name) -> None:
        self.scheduled.append((name, interval, first))

    def schedule_daily(self, task, at, name) -> None:
        self.scheduled.append((name, at))


def runtime_with(conn, events, fail_start=False):
    write_env(VALID_ENV)
    paths = resolve_paths(os.environ)
    settings = load_settings(paths)
    channel = FakeRuntimeChannel(events, fail_start)
    todoist = FakeTodoistApi()

    async def close_model() -> None:
        events.append("model close")

    runtime = Runtime(
        settings,
        paths,
        FixedClock(NOW.replace(minute=7)),
        conn,
        intentions(),
        limits(),
        channel_factory=lambda settings, conn, clock: channel,  # pyright: ignore[reportArgumentType] - a double of the channel
        model_factory=lambda settings: ModelApi(FakeMessages().create, close_model),
        todoist_api=todoist,
    )
    return runtime, channel, todoist


def test_el_runtime_programa_el_latido_alineado_y_la_purga_y_para_en_orden(tmp_path):
    """Cada 15 min desde el siguiente cuarto (9:07 → 9:15) y la purga a las 4:00 de Madrid. Al
    parar: el canal primero (ya no hay latidos), después el modelo y las fuentes."""
    conn = open_database(tmp_path / "jimic.db")
    events: list[str] = []
    runtime, channel, todoist = runtime_with(conn, events)
    madrid = ZoneInfo("Europe/Madrid")

    async def scenario():
        await runtime.start()
        assert not todoist.closed
        await runtime.stop()

    try:
        asyncio.run(scenario())
    finally:
        conn.close()
    assert channel.scheduled == [
        ("latido", timedelta(minutes=15), datetime(2026, 11, 19, 9, 15, tzinfo=madrid)),
        ("purga", dt_time(4, 0, tzinfo=madrid)),
    ]
    assert events == ["channel start", "channel stop", "model close"]
    assert todoist.closed
    assert runtime.heartbeat is not None


def test_si_el_canal_no_arranca_el_runtime_cierra_el_modelo_y_las_fuentes(tmp_path):
    conn = open_database(tmp_path / "jimic.db")
    events: list[str] = []
    runtime, channel, todoist = runtime_with(conn, events, fail_start=True)
    try:
        with pytest.raises(ChannelError):
            asyncio.run(runtime.start())
    finally:
        conn.close()
    assert events == ["channel start", "model close"]
    assert todoist.closed
    assert channel.scheduled == []
