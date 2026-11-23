"""Lo que hace un toque, sin Telegram: manejador inyectado, caducidad y estado guardado."""

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import FixedClock

from jimic.channel import store
from jimic.channel.message import (
    GlobalOption,
    InteractiveMessage,
    Item,
    Option,
    Tap,
)
from jimic.channel.taps import Answer, Declined, Failed, Followup, Renew, process_tap, sweep
from jimic.memory.database import open_database

NOW = datetime(2026, 11, 21, 19, 0, tzinfo=ZoneInfo("Europe/Madrid"))
EXPIRES = NOW + timedelta(hours=1)
MAX_CALLS = 20
"""Tope del doble: un manejador al que el código llama en bucle falla con un error claro."""

SECRET_TITLE = "Título secreto 17:30"


class RecordingHandler:
    """A handler that records what it was called with and answers `✓`/`✗`; it can fail."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_on = fail_on or set()

    async def __call__(self, item: Item, option: str, approval_id: int) -> str:
        self.calls.append((item.title, option))
        if len(self.calls) > MAX_CALLS:
            raise AssertionError("el manejador se ha llamado en bucle")
        if item.title in self.fail_on:
            raise RuntimeError(f"falla con {item.title}")
        return "✓ creada" if option == "y" else "✗ descartada"


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


@pytest.fixture
def clock():
    return FixedClock(NOW)


def make_message() -> InteractiveMessage:
    options = (Option(key="y", label="Sí"), Option(key="n", label="No", approves=False))
    return InteractiveMessage(
        header="Plan de prueba",
        items=(
            Item(title="Gimnasio 13:00", options=options),
            Item(title=SECRET_TITLE, options=options),
            Item(title="Leer 21:00", options=options),
        ),
        global_options=(
            GlobalOption(key="all", label="Créalo todo", option="y"),
            GlobalOption(key="none", label="Nada", option="n"),
        ),
    )


@pytest.fixture
def approval_id(conn, clock):
    return store.create(conn, clock, "demo", make_message(), EXPIRES)


def run(conn, clock, handler, tap, kind="demo"):
    return asyncio.run(process_tap(conn, clock, {kind: handler}, tap))


def stored(conn, approval_id):
    approval = store.load(conn, approval_id)
    assert approval is not None
    return approval


def test_un_toque_llama_al_manejador_resuelve_el_elemento_y_quita_su_fila(conn, clock, approval_id):
    """Pilla un toque que no llama al manejador, no guarda el resultado o deja la fila."""
    handler = RecordingHandler()
    result = run(conn, clock, handler, Tap(approval_id, 1, "y"))
    assert handler.calls == [("Gimnasio 13:00", "y")]
    assert result.code == "resolved"
    assert result.view is not None
    assert "1. Gimnasio 13:00 — ✓ creada" in result.view.text
    assert [[b.label for b in row] for row in result.view.keyboard] == [
        ["2 Sí", "2 No"],
        ["3 Sí", "3 No"],
        ["Créalo todo", "Nada"],
    ]
    approval = stored(conn, approval_id)
    assert approval.status == "pending"
    assert approval.message.items[0].result == "✓ creada"


def test_el_segundo_toque_sobre_lo_resuelto_no_hace_nada(conn, clock, approval_id):
    """No llama otra vez al manejador y no pide editar el mensaje."""
    handler = RecordingHandler()
    run(conn, clock, handler, Tap(approval_id, 1, "y"))
    again = run(conn, clock, handler, Tap(approval_id, 1, "n"))
    assert again.view is None
    assert again.code == "nothing_to_do"
    assert len(handler.calls) == 1
    assert stored(conn, approval_id).message.items[0].chosen == "y"


def test_creelo_todo_resuelve_lo_pendiente_y_no_toca_lo_ya_resuelto(conn, clock, approval_id):
    """El manejador no se llama para lo que ya estaba resuelto; al final no queda teclado."""
    handler = RecordingHandler()
    run(conn, clock, handler, Tap(approval_id, 2, "n"))
    result = run(conn, clock, handler, Tap(approval_id, None, "all"))
    assert handler.calls == [(SECRET_TITLE, "n"), ("Gimnasio 13:00", "y"), ("Leer 21:00", "y")]
    assert result.view is not None
    assert result.view.keyboard == ()
    assert f"2. {SECRET_TITLE} — ✗ descartada" in result.view.text
    approval = stored(conn, approval_id)
    assert approval.status == "approved"


def test_todo_con_noes_queda_rechazado(conn, clock, approval_id):
    """[Nada] resuelve todo con una opción que no aprueba: la fila queda `rejected`."""
    run(conn, clock, RecordingHandler(), Tap(approval_id, None, "none"))
    assert stored(conn, approval_id).status == "rejected"


def test_un_manejador_que_falla_deja_ese_elemento_pendiente_sin_contenido_en_el_log(
    conn, clock, approval_id, caplog
):
    """Pilla un fallo que resuelve el elemento igualmente o que escribe el título en el log."""
    handler = RecordingHandler(fail_on={SECRET_TITLE})
    with caplog.at_level(logging.INFO):
        result = run(conn, clock, handler, Tap(approval_id, None, "all"))
    assert result.code == "partially_resolved"
    assert result.view is not None
    assert f"2. {SECRET_TITLE} — falló (unexpected:RuntimeError)\n3. Leer 21:00 — ✓ creada" in (
        result.view.text
    )
    assert "1. Gimnasio 13:00 — ✓ creada" in result.view.text
    assert [[b.label for b in row] for row in result.view.keyboard] == [
        ["2 Sí", "2 No"],
        ["Créalo todo", "Nada"],
    ]
    assert "RuntimeError" in caplog.text
    assert SECRET_TITLE not in caplog.text
    assert stored(conn, approval_id).status == "pending"


def test_si_el_unico_manejador_falla_el_elemento_sigue_pendiente_con_su_nota(
    conn, clock, approval_id
):
    """Pilla un fallo que deja el elemento «en curso» (sin fila: no se podría reintentar) o que
    lo resuelve. Con su nota y su fila, Pedro puede volver a intentarlo."""
    handler = RecordingHandler(fail_on={"Gimnasio 13:00"})
    result = run(conn, clock, handler, Tap(approval_id, 1, "y"))
    assert result.code == "handler_failed"
    item = stored(conn, approval_id).message.items[0]
    assert (item.result, item.running, item.note) == (
        None,
        False,
        "falló (unexpected:RuntimeError)",
    )
    assert result.view is not None
    assert result.view.keyboard[0][0].label == "1 Sí"
    handler.fail_on = set()
    again = run(conn, clock, handler, Tap(approval_id, 1, "y"))
    assert again.code == "resolved"
    assert stored(conn, approval_id).message.items[0].note is None


def test_un_toque_caducado_marca_caducado_y_quita_el_teclado(conn, clock, approval_id):
    """El borde: justo en `expires_at` ya caduca. No llama al manejador."""
    clock.moment = EXPIRES
    handler = RecordingHandler()
    result = run(conn, clock, handler, Tap(approval_id, 1, "y"))
    assert handler.calls == []
    assert result.code == "expired"
    assert result.view is not None
    assert result.view.keyboard == ()
    assert result.view.text.count("— caducado") == 3
    assert stored(conn, approval_id).status == "expired"


def test_un_segundo_toque_caducado_vuelve_a_quitar_el_teclado(conn, clock, approval_id):
    """Si la primera edición falló y el teclado sigue en el móvil, el siguiente toque lo quita."""
    clock.moment = EXPIRES + timedelta(minutes=5)
    run(conn, clock, RecordingHandler(), Tap(approval_id, 1, "y"))
    again = run(conn, clock, RecordingHandler(), Tap(approval_id, 2, "y"))
    assert again.code == "expired"
    assert again.view is not None
    assert again.view.keyboard == ()


def test_un_segundo_antes_de_caducar_aun_se_atiende(conn, clock, approval_id):
    clock.moment = EXPIRES - timedelta(seconds=1)
    assert run(conn, clock, RecordingHandler(), Tap(approval_id, 1, "y")).code == "resolved"


def test_caducar_no_toca_lo_ya_resuelto(conn, clock, approval_id):
    run(conn, clock, RecordingHandler(), Tap(approval_id, 1, "y"))
    clock.moment = EXPIRES
    result = run(conn, clock, RecordingHandler(), Tap(approval_id, 2, "y"))
    assert result.view is not None
    assert "1. Gimnasio 13:00 — ✓ creada" in result.view.text
    assert "2. " + SECRET_TITLE + " — caducado" in result.view.text


def test_un_id_inexistente_un_kind_sin_manejador_y_un_payload_roto_no_cambian_nada(
    conn, clock, approval_id
):
    """Ninguno lanza: el chat de Pedro recibe un toque de nada y el mensaje se queda."""
    handler = RecordingHandler()
    assert run(conn, clock, handler, Tap(999, 1, "y")).code == "unknown_approval"
    assert run(conn, clock, handler, Tap(approval_id, 1, "y"), kind="otro").code == "no_handler"
    conn.execute("UPDATE approvals SET payload = '{}' WHERE id = ?", (approval_id,))
    assert run(conn, clock, handler, Tap(approval_id, 1, "y")).code == "invalid_payload"
    assert handler.calls == []


def test_tras_resolver_todo_un_toque_viejo_no_hace_nada(conn, clock, approval_id):
    """La fila ya salió de `pending`: el toque sobre un teclado viejo no cambia el mensaje."""
    run(conn, clock, RecordingHandler(), Tap(approval_id, None, "all"))
    result = run(conn, clock, RecordingHandler(), Tap(approval_id, 1, "n"))
    assert (result.code, result.view) == ("already_resolved", None)


# --- Reclamar antes del efecto, renovar y barrer (tarea 1.11) --------------------------------


class GatedHandler:
    """A handler that waits for `gate` before answering: an effect under way."""

    def __init__(self) -> None:
        self.calls = 0
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def __call__(self, item: Item, option: str, approval_id: int) -> str:
        self.calls += 1
        if self.calls > MAX_CALLS:
            raise AssertionError("el manejador se ha llamado en bucle")
        self.started.set()
        await self.gate.wait()
        return "✓ creada"


def test_dos_toques_simultaneos_ejecutan_una_sola_vez(conn, clock, approval_id):
    """Pilla el toque doble (versión incorrecta 7): sin reclamar antes del efecto, el segundo
    toque llega mientras el primero espera a Todoist y vuelve a ejecutar."""
    handler = GatedHandler()

    async def scenario():
        first = asyncio.create_task(
            process_tap(conn, clock, {"demo": handler}, Tap(approval_id, 1, "y"))
        )
        await handler.started.wait()
        second = await process_tap(conn, clock, {"demo": handler}, Tap(approval_id, 1, "y"))
        global_tap = asyncio.create_task(
            process_tap(conn, clock, {"demo": handler}, Tap(approval_id, None, "all"))
        )
        await asyncio.sleep(0)
        handler.gate.set()
        return await first, second, await global_tap

    first, second, global_tap = asyncio.run(scenario())
    assert second.code == "in_progress"
    assert first.code == "resolved"
    # [Créalo todo] mientras el 1 estaba en curso: hace el 2 y el 3, nunca el 1 otra vez.
    assert global_tap.code == "resolved"
    assert handler.calls == 3
    assert [item.result for item in stored(conn, approval_id).message.items] == ["✓ creada"] * 3


def test_mientras_corre_el_elemento_queda_guardado_en_curso(conn, clock, approval_id):
    """El estado «en curso» se guarda antes del efecto: tras una caída, nadie lo repite solo."""
    handler = GatedHandler()

    async def scenario():
        task = asyncio.create_task(
            process_tap(conn, clock, {"demo": handler}, Tap(approval_id, 1, "y"))
        )
        await handler.started.wait()
        running = stored(conn, approval_id).message.items[0].running
        handler.gate.set()
        await task
        return running

    assert asyncio.run(scenario()) is True


def test_si_el_payload_cambio_entre_leer_y_reclamar_no_se_ejecuta(
    conn, clock, approval_id, monkeypatch
):
    """Pilla un reclamo sin comparar el payload: dos sobre lo mismo ejecutarían dos veces."""
    original = store.load

    def stale(connection, approval):
        loaded = original(connection, approval)
        # Otro toque reclamó en medio: el payload guardado ya no es el leído.
        connection.execute("UPDATE approvals SET payload = json_set(payload, '$.header', 'otro')")
        return loaded

    monkeypatch.setattr(store, "load", stale)
    handler = RecordingHandler()
    assert run(conn, clock, handler, Tap(approval_id, 1, "y")).code == "in_progress"
    assert handler.calls == []


def test_renovar_deja_el_elemento_pendiente_con_lo_nuevo(conn, clock, approval_id):
    """Pilla una renovación que resuelve el elemento (no volvería a preguntar)."""

    async def renew(item: Item, option: str, approval: int):
        return Renew(
            item.model_copy(update={"title": "Título nuevo", "note": "ha cambiado: ¿la borro así?"})
        )

    result = asyncio.run(process_tap(conn, clock, {"demo": renew}, Tap(approval_id, 1, "y")))
    assert result.code == "renewed"
    item = stored(conn, approval_id).message.items[0]
    assert (item.title, item.note, item.pending) == (
        "Título nuevo",
        "ha cambiado: ¿la borro así?",
        True,
    )
    assert stored(conn, approval_id).status == "pending"


def test_un_fallo_con_codigo_se_ve_en_la_nota(conn, clock, approval_id):
    async def failing(item: Item, option: str, approval: int):
        return Failed("timeout")

    asyncio.run(process_tap(conn, clock, {"demo": failing}, Tap(approval_id, 1, "y")))
    assert stored(conn, approval_id).message.items[0].note == "falló (timeout)"


def test_el_manejador_recibe_el_id_de_su_aprobacion(conn, clock, approval_id):
    seen = []

    async def handler(item: Item, option: str, approval: int):
        seen.append(approval)
        return "ok"

    asyncio.run(process_tap(conn, clock, {"demo": handler}, Tap(approval_id, 1, "y")))
    assert seen == [approval_id]


def test_el_barrido_caduca_lo_vencido_y_dice_que_editar(conn, clock, approval_id):
    """Pilla una propuesta sin toque que conserva sus botones tras caducar (pendiente de la 1.4)."""
    store.set_message_id(conn, approval_id, 77)
    assert sweep(conn, clock) == []
    clock.moment = EXPIRES
    (swept,) = sweep(conn, clock)
    assert (swept.approval_id, swept.telegram_message_id) == (approval_id, 77)
    assert swept.view is not None and swept.view.keyboard == ()
    assert "— caducado" in swept.view.text
    assert stored(conn, approval_id).status == "expired"
    assert sweep(conn, clock) == []


def test_el_barrido_solo_marca_lo_que_no_tiene_mensaje_o_no_es_un_mensaje(conn, clock):
    """Las propuestas de la 1.10 (otro payload, sin mensaje) y un payload roto con mensaje."""
    for payload, message_id in (('{"items": []}', None), ('{"roto": true}', 5)):
        conn.execute(
            "INSERT INTO approvals (created_at, expires_at, kind, payload, telegram_message_id)"
            " VALUES ('2026-11-21T17:00:00+00:00', '2026-11-21T18:00:00+00:00', 'plan', ?, ?)",
            (payload, message_id),
        )
    clock.moment = EXPIRES
    swept = sweep(conn, clock)
    assert [(s.telegram_message_id, s.view) for s in swept] == [(None, None), (5, None)]
    statuses = [row[0] for row in conn.execute("SELECT status FROM approvals ORDER BY id")]
    assert statuses == ["expired", "expired"]


# --- Declined (tarea 1.12) ----------------------------------------------------------------------


def test_un_declined_resuelve_como_un_no_y_la_aprobacion_queda_rechazada(conn, clock):
    """Ajuste 11: mirado y no hecho, para siempre ([Guardar] sobre un fichero que cambió)."""

    async def declines(item, option, approval_id):
        return Declined("ha cambiado: no lo guardo")

    message = InteractiveMessage(header="H", items=(make_message().items[0],))
    approval = store.create(conn, clock, "demo", message, EXPIRES)
    result = run(conn, clock, declines, Tap(approval, 1, "y"))
    assert result.code == "resolved"
    item = stored(conn, approval).message.items[0]
    assert (item.result, item.chosen) == ("ha cambiado: no lo guardo", "n")
    assert stored(conn, approval).status == "rejected"


def test_un_declined_sin_opcion_de_no_queda_pendiente_con_su_nota(conn, clock):
    async def declines(item, option, approval_id):
        return Declined("no se pudo")

    only_yes = Item(title="Solo sí", options=(Option(key="y", label="Sí"),))
    approval = store.create(
        conn, clock, "demo", InteractiveMessage(header="H", items=(only_yes,)), EXPIRES
    )
    run(conn, clock, declines, Tap(approval, 1, "y"))
    item = stored(conn, approval).message.items[0]
    assert item.pending and item.note == "no se pudo"
    assert stored(conn, approval).status == "pending"


# --- Answer: resuelto y una respuesta tras el toque (tarea 1.15) ------------------------------


def test_answer_con_texto_resuelve_y_envia_el_texto(conn, clock, approval_id):
    async def answers(item, option, approval_id):
        return Answer("🏃 a por ello", "¡Vamos! 💪")

    result = run(conn, clock, answers, Tap(approval_id, 1, "y"))
    assert result.code == "resolved" and result.send == ("¡Vamos! 💪",)
    assert result.messages == ()
    stored = store.load(conn, approval_id)
    assert stored is not None and stored.message.items[0].result == "🏃 a por ello"


def test_answer_con_mensaje_lo_deja_para_el_canal(conn, clock, approval_id):
    followup = Followup("key_undo", make_message(), EXPIRES, "¡Hecho! 💪")

    async def answers(item, option, approval_id):
        return Answer("✅ hecho", followup)

    result = run(conn, clock, answers, Tap(approval_id, 1, "y"))
    assert result.send == () and result.messages == (followup,)


def test_answer_con_then_deja_el_elemento_pendiente_con_sus_opciones_y_la_nota(
    conn, clock, approval_id
):
    """Tarea 1.15: [Voy ahora] deja [Ya entrené] y [Hoy no]. Solo cambian las opciones y la
    nota, sobre el elemento guardado (revisión propia)."""

    async def answers(item, option, approval_id):
        then = item.model_copy(update={"title": "título viejo", "options": item.options[:1]})
        return Answer("🏃 a por ello", "¡Vamos! 💪", then=then)

    result = run(conn, clock, answers, Tap(approval_id, 1, "y"))
    assert result.code == "resolved" and not result.closed
    stored = store.load(conn, approval_id)
    assert stored is not None
    item = stored.message.items[0]
    assert item.pending and [o.key for o in item.options] == ["y"]
    assert item.note == "🏃 a por ello" and item.title == "Gimnasio 13:00"
