"""`jimic telegram demo`: se niega fuera de `dev`, manda el plan inventado y no crea nada."""

import asyncio
import os
import signal
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import VALID_ENV, FixedClock, write_env

import jimic.cli
import jimic.service
from jimic.channel import demo
from jimic.channel.message import InteractiveMessage, Item, render
from jimic.config import Settings, load_settings
from jimic.logs import LOG_FILE
from jimic.memory.database import open_database
from jimic.paths import resolve_paths

NOW = datetime(2026, 11, 21, 19, 0, tzinfo=ZoneInfo("Europe/Madrid"))
DEV_ENV = {**VALID_ENV, "ENTORNO": "dev", "PROYECTO_PRUEBAS_TODOIST": "proyecto-pruebas-123"}


class RecordingChannel:
    """Stands in for `TelegramChannel`: on `start` it calls `on_started` with itself, and it
    records what the demo sends."""

    def __init__(self, handlers, on_started) -> None:
        self.handlers = handlers
        self.on_started = on_started
        self.sent: list[tuple[str, InteractiveMessage, datetime]] = []
        self.stopped = False

    async def start(self) -> None:
        await self.on_started(self)

    async def stop(self) -> None:
        self.stopped = True

    async def send(self, kind, message, expires_at) -> int:
        self.sent.append((kind, message, expires_at))
        return 1


@pytest.fixture
def built(monkeypatch):
    """Replace `build_channel` in the demo; list every channel it builds."""
    channels: list[RecordingChannel] = []

    def fake_build(settings, conn, clock, handlers=None, on_started=None):
        channel = RecordingChannel(handlers, on_started)
        channels.append(channel)
        return channel

    monkeypatch.setattr(demo, "build_channel", fake_build)
    return channels


def use_env(monkeypatch, values, environment):
    monkeypatch.setenv("JIMIC_ENTORNO", environment)
    write_env(values)


def test_la_demo_se_niega_con_prod_antes_de_leer_el_env_o_construir_nada(built):
    """Pilla una demo permitida en `prod`. No hay `.env`: si leyera la configuración antes de
    comprobar el entorno, el error sería otro (falta el fichero), no el de la demo."""
    paths = resolve_paths(os.environ)
    assert paths.environment == "prod"
    with pytest.raises(demo.DemoError, match=r"solo corre con ENTORNO=dev.*prod"):
        demo.run(os.environ)
    assert built == []
    assert not paths.data_dir.exists()


def test_la_demo_se_niega_aunque_el_env_diga_dev_si_la_copia_es_prod(built):
    """El entorno lo elige `JIMIC_ENTORNO` (donde está la copia), no lo que diga un `.env`."""
    write_env(DEV_ENV)
    with pytest.raises(demo.DemoError):
        demo.run(os.environ)
    assert built == []


def test_la_cli_se_niega_con_prod_saliendo_con_2_y_un_mensaje_claro(built, capsys):
    assert jimic.cli.main(["telegram", "demo"]) == 2
    assert capsys.readouterr().err == (
        "jimic: la demo solo corre con ENTORNO=dev, y esta copia es prod:"
        " lánzala con JIMIC_ENTORNO=dev\n"
    )
    assert built == []


def demo_settings():
    return Settings.model_validate(DEV_ENV)


def test_la_demo_ensena_una_propuesta_con_sus_filas_y_la_global_bajo_una_respuesta():
    """La forma que Pedro verá en el móvil (la captura de la 1.11): las etiquetas del formato
    del plan, la fila global sin el borrado y el texto de la respuesta encima."""
    (kind, proposal), (undo_kind, undo) = demo.demo_messages(demo_settings())
    assert (kind, undo_kind) == ("plan", "undo")
    view = render(proposal, approval_id=1)
    assert [[b.label for b in row] for row in view.keyboard] == [
        ["1 ✅", "1 ⏭"],
        ["2 🔄 A las 9:50", "2 🆗 Déjalo"],
        ["3 🗑️ Bórrala", "3 🆗 Déjala"],
        ["✅ Créalo todo", "⏭ Nada"],
    ]
    assert view.text.startswith(f"{demo.ANSWER}\n\n<b>Te propongo</b>")
    assert [[b.label for b in row] for row in render(undo, 2).keyboard] == [["1 ↩️ Deshacer"]]


@pytest.mark.parametrize(
    ("index", "option", "result"),
    [
        (0, "ok", "✓ creada"),
        (0, "no", "⏭ descartada"),
        (1, "ok", "✓ movida"),
        (2, "del", "✓ borrada"),
    ],
)
def test_los_toques_de_la_demo_solo_marcan_el_resultado(index, option, result):
    (_, proposal), _ = demo.demo_messages(demo_settings())
    assert asyncio.run(demo.demo_plan(proposal.items[index], option, 1)) == result


def test_con_dev_la_demo_arranca_el_canal_manda_el_plan_y_para_con_sigterm(
    monkeypatch, built, capsys
):
    """De punta a punta con el servicio de verdad y el canal doble: el plan sale cuando el canal
    ya escucha, caduca a la hora, y Ctrl+C (SIGTERM) lo para limpio."""
    use_env(monkeypatch, DEV_ENV, "dev")
    paths = resolve_paths(os.environ)
    log_file = paths.logs_dir / LOG_FILE

    def stop_when_sent():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if built and built[0].sent:
                break
            time.sleep(0.05)
        signal.raise_signal(signal.SIGTERM)

    previous = signal.signal(signal.SIGTERM, lambda signum, frame: pytest.fail("sin manejador"))
    stopper = threading.Thread(target=stop_when_sent)
    stopper.start()
    try:
        demo.run(os.environ)
    finally:
        stopper.join()
        signal.signal(signal.SIGTERM, previous)
    (channel,) = built
    (kind, message, expires_at), (undo_kind, _, _) = channel.sent
    assert (kind, undo_kind) == ("plan", "undo")
    assert message == demo.demo_messages(demo_settings())[0][1]
    assert channel.stopped
    assert list(channel.handlers) == ["plan", "undo"]
    # `expires_at` viene del reloj del sistema: una hora después de enviar, con holgura.
    assert (
        timedelta(minutes=59) < expires_at - datetime.now(expires_at.tzinfo) <= timedelta(hours=1)
    )
    assert "stopped at" in log_file.read_text()


def test_la_factoria_de_la_demo_pasa_su_manejador_y_envia_el_plan_al_empezar(monkeypatch, built):
    """Sin servicio: la factoría construye el canal con el manejador de la demo y su `on_started`
    envía el plan con la caducidad de una hora desde el reloj inyectado."""
    use_env(monkeypatch, DEV_ENV, "dev")
    settings = load_settings(resolve_paths(os.environ))
    conn = open_database(resolve_paths(os.environ).database)
    try:
        channel = demo.demo_factory(settings, FixedClock(NOW))(conn)
    finally:
        conn.close()
    assert channel is built[0]
    asyncio.run(channel.start())
    assert built[0].sent == [
        (kind, message, NOW + timedelta(hours=1)) for kind, message in demo.demo_messages(settings)
    ]
    assert isinstance(built[0].sent[0][1].items[0], Item)
