"""El canal de Telegram con la biblioteca de verdad y la red de mentira: ni red ni *polling*."""

import asyncio
import json
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import FixedClock
from telegram.error import InvalidToken
from telegram.ext import Application, MessageHandler, filters
from telegram_data import (
    CHAT_ID,
    OTHER_CHAT_ID,
    TOKEN,
    FakeRequest,
    callback_update,
    inline_callback_update,
    photo_update,
    text_update,
    to_update,
)

from jimic.channel import bot as bot_module
from jimic.channel import store
from jimic.channel.bot import (
    ALREADY_RESOLVED,
    COULD_NOT_REPLY,
    IN_PROGRESS,
    NOT_YET,
    ChannelError,
    Incoming,
    Outgoing,
    Reply,
    SendError,
    TelegramChannel,
    build_application,
)
from jimic.channel.message import (
    GlobalOption,
    InteractiveMessage,
    Item,
    Option,
    claim,
)
from jimic.memory.database import open_database

NOW = datetime(2026, 11, 21, 19, 0, tzinfo=ZoneInfo("Europe/Madrid"))
EXPIRES = NOW + timedelta(hours=1)
SECRET_TITLE = "Título secreto 17:30"
SECRET_TEXT = "texto secreto de Pedro"


def make_message() -> InteractiveMessage:
    options = (Option(key="y", label="Sí"), Option(key="n", label="No", approves=False))
    return InteractiveMessage(
        header="Plan de prueba",
        items=(
            Item(title="Gimnasio 13:00", options=options),
            Item(title=SECRET_TITLE, options=options),
        ),
        global_options=(GlobalOption(key="all", label="Créalo todo", option="y"),),
    )


async def handler_ok(item: Item, option: str, approval_id: int) -> str:
    return "✓ creada" if option == "y" else "✗ descartada"


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


@pytest.fixture
def clock():
    return FixedClock(NOW)


@pytest.fixture
def request_double():
    return FakeRequest()


@pytest.fixture
def make_channel(conn, clock, request_double):
    def build(**kwargs) -> tuple[TelegramChannel, Application]:
        application = build_application(TOKEN, request_double)
        channel = TelegramChannel(
            application, CHAT_ID, conn, clock, handlers={"demo": handler_ok}, **kwargs
        )
        return channel, application

    return build


MAX_SPINS = 1000
"""Loop turns `drive` waits for the turns of the conversation before giving up."""


async def settle() -> None:
    """Wait until the tasks the channel started (the turns of the conversation) have ended."""
    for _ in range(MAX_SPINS):
        if len(asyncio.all_tasks()) == 1:
            return
        await asyncio.sleep(0)
    raise AssertionError("a turn of the conversation never ended")


def drive(application, *updates, before=None):
    """Initialize the application (no polling), run `before(channel)`, feed it the updates and
    wait for the turns they start."""

    async def scenario():
        async with application:
            if before is not None:
                await before()
            for update in updates:
                await application.process_update(to_update(update, application.bot))
            await settle()

    asyncio.run(scenario())


def send_and_drive(channel, application, *updates):
    ids: list[int] = []

    async def before():
        ids.append(await channel.send("demo", make_message(), EXPIRES))

    drive(application, *updates, before=before)
    return ids[0]


def keyboard_rows(params) -> list[list[str]]:
    markup = params["reply_markup"]
    return [[button["text"] for button in row] for row in markup["inline_keyboard"]]


def test_enviar_manda_html_sin_vista_previa_con_filas_numeradas_y_la_global(
    make_channel, request_double, conn
):
    """El mensaje sale con `sendMessage`, HTML, sin vista previa, una fila por elemento y la
    global; queda guardado en `approvals`, con el título solo en el texto y no en los botones."""
    channel, application = make_channel()
    approval_id = send_and_drive(channel, application)
    assert request_double.methods() == ["sendMessage"]
    (params,) = request_double.params("sendMessage")
    assert params["chat_id"] == CHAT_ID
    assert params["parse_mode"] == "HTML"
    assert params["link_preview_options"]["is_disabled"] is True
    assert params["text"].startswith("<b>Plan de prueba</b>")
    assert keyboard_rows(params) == [["1 Sí", "1 No"], ["2 Sí", "2 No"], ["Créalo todo"]]
    data = [b["callback_data"] for row in params["reply_markup"]["inline_keyboard"] for b in row]
    assert data == [
        f"{approval_id}:1:y",
        f"{approval_id}:1:n",
        f"{approval_id}:2:y",
        f"{approval_id}:2:n",
        f"{approval_id}:g:all",
    ]
    assert store.load(conn, approval_id) is not None


def test_un_toque_edita_el_mismo_mensaje_y_no_envia_otro(make_channel, request_double):
    """Pilla un envío en vez de una edición: se mira el método de la API, no solo el texto."""
    channel, application = make_channel()
    approval_id = send_and_drive(channel, application)
    request_double.calls.clear()
    drive(application, callback_update(f"{approval_id}:1:y", message_id=77))
    assert request_double.methods() == ["editMessageText", "answerCallbackQuery"]
    (params,) = request_double.params("editMessageText")
    assert (params["chat_id"], params["message_id"]) == (CHAT_ID, 77)
    assert params["parse_mode"] == "HTML"
    assert params["link_preview_options"]["is_disabled"] is True
    assert "1. Gimnasio 13:00 — ✓ creada" in params["text"]
    assert keyboard_rows(params) == [["2 Sí", "2 No"], ["Créalo todo"]]


def test_un_toque_de_otro_chat_se_ignora_sin_responder_ni_editar(
    make_channel, request_double, caplog
):
    """Pilla un filtro de chat que falta en los toques: ni `editMessageText` ni
    `answerCallbackQuery`, y el log dice solo «unknown chat», sin ID ni datos del toque."""
    channel, application = make_channel()
    approval_id = send_and_drive(channel, application)
    request_double.calls.clear()
    with caplog.at_level(logging.INFO):
        drive(application, callback_update(f"{approval_id}:1:y", chat_id=OTHER_CHAT_ID))
    assert request_double.methods() == []
    assert "unknown chat" in caplog.text
    assert str(OTHER_CHAT_ID) not in caplog.text
    assert f"{approval_id}:1:y" not in caplog.text


def test_un_toque_de_un_mensaje_en_linea_se_ignora(make_channel, request_double, caplog):
    """Sin `message` no hay chat que comprobar: no puede ser el de Pedro."""
    channel, application = make_channel()
    approval_id = send_and_drive(channel, application)
    request_double.calls.clear()
    with caplog.at_level(logging.INFO):
        drive(application, inline_callback_update(f"{approval_id}:1:y"))
    assert request_double.methods() == []
    assert "unknown chat" in caplog.text


def test_un_mensaje_de_otro_chat_se_ignora_sin_responder(make_channel, request_double, caplog):
    channel, application = make_channel()
    with caplog.at_level(logging.INFO):
        drive(application, text_update(SECRET_TEXT, chat_id=OTHER_CHAT_ID))
    assert request_double.methods() == []
    assert "unknown chat" in caplog.text
    assert SECRET_TEXT not in caplog.text
    assert str(OTHER_CHAT_ID) not in caplog.text


@pytest.mark.parametrize("text", [SECRET_TEXT, "/plan", "/start"])
def test_sin_conversacion_un_texto_recibe_la_frase_fija(make_channel, request_double, text):
    """La demo: también un `/comando` recibe lo mismo. La respuesta es inmediata: sin
    «escribiendo…», que no hace falta."""
    channel, application = make_channel()
    drive(application, text_update(text))
    assert request_double.methods() == ["sendMessage"]
    (params,) = request_double.params("sendMessage")
    assert params["text"] == NOT_YET
    assert params["chat_id"] == CHAT_ID
    assert params["parse_mode"] == "HTML"


def test_el_texto_de_pedro_va_al_manejador_inyectado(make_channel, request_double):
    received: list[str] = []

    async def on_text(incoming: Incoming) -> str:
        text = incoming.text
        received.append(text)
        return "respuesta <inventada> & sin escapar"

    channel, application = make_channel(on_text=on_text)
    drive(application, text_update("hola"))
    assert received == ["hola"]
    (params,) = request_double.params("sendMessage")
    assert params["text"] == "respuesta &lt;inventada&gt; &amp; sin escapar"
    assert params["parse_mode"] == "HTML"
    assert params["link_preview_options"] == {"is_disabled": True}


def test_algo_que_no_es_texto_no_recibe_respuesta(make_channel, request_double):
    channel, application = make_channel()
    drive(application, photo_update())
    assert request_double.methods() == []


def test_el_segundo_toque_sobre_lo_resuelto_no_edita_y_responde_al_toque(
    make_channel, request_double
):
    """Se responde para que Telegram pare el reloj del botón, pero el mensaje no cambia."""
    channel, application = make_channel()
    approval_id = send_and_drive(channel, application)
    drive(application, callback_update(f"{approval_id}:1:y"))
    request_double.calls.clear()
    drive(application, callback_update(f"{approval_id}:1:n", update_id=2))
    assert request_double.methods() == ["answerCallbackQuery"]
    assert request_double.params("answerCallbackQuery")[0]["text"] == ALREADY_RESOLVED


def test_un_toque_caducado_quita_el_teclado_con_un_teclado_vacio(
    make_channel, request_double, clock
):
    """El teclado se quita enviándolo vacío: pilla un `reply_markup` que se omite y lo deja."""
    channel, application = make_channel()
    approval_id = send_and_drive(channel, application)
    clock.moment = EXPIRES
    request_double.calls.clear()
    drive(application, callback_update(f"{approval_id}:2:y"))
    (params,) = request_double.params("editMessageText")
    assert params["reply_markup"] == {"inline_keyboard": []}
    assert params["text"].count("— caducado") == 2


def test_un_message_is_not_modified_no_es_un_error(make_channel, request_double, caplog):
    channel, application = make_channel()
    approval_id = send_and_drive(channel, application)
    request_double.failures["editMessageText"] = (
        400,
        "Bad Request: message is not modified: specified new message content and reply markup"
        " are exactly the same as a current content and reply markup of the message",
    )
    with caplog.at_level(logging.INFO):
        drive(application, callback_update(f"{approval_id}:1:y"))
    assert "edit failed" not in caplog.text
    assert "answerCallbackQuery" in request_double.methods()


def test_un_fallo_al_editar_se_registra_como_codigo_y_aun_asi_se_responde(
    make_channel, request_double, caplog
):
    """Sin contenido en el log (ni el título ni el texto del error) y sin reintentos."""
    channel, application = make_channel()
    approval_id = send_and_drive(channel, application)
    request_double.failures["editMessageText"] = (400, f"Bad Request: {SECRET_TITLE}")
    request_double.calls.clear()
    with caplog.at_level(logging.INFO):
        drive(application, callback_update(f"{approval_id}:1:y"))
    assert "telegram edit failed (BadRequest)" in caplog.text
    assert SECRET_TITLE not in caplog.text
    assert request_double.methods() == ["editMessageText", "answerCallbackQuery"]


def test_un_fallo_al_enviar_levanta_send_error_y_no_deja_aprobacion(
    make_channel, request_double, conn
):
    """Nadie podría tocar un mensaje que no llegó: la fila se borra. Un solo intento."""
    channel, application = make_channel()
    request_double.failures["sendMessage"] = (403, "Forbidden: bot was blocked by the user")

    async def before():
        with pytest.raises(SendError) as error:
            await channel.send("demo", make_message(), EXPIRES)
        assert str(error.value) == "Forbidden"

    drive(application, before=before)
    assert request_double.methods() == ["sendMessage"]
    assert conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)


def test_un_error_inesperado_en_un_manejador_registra_solo_su_tipo(
    make_channel, request_double, caplog
):
    """El manejador de errores propio: sin traceback ni mensaje (podrían llevar contenido)."""

    async def broken(update, context):
        raise RuntimeError(SECRET_TEXT)

    channel, application = make_channel()
    application.add_handler(MessageHandler(filters.ALL, broken), group=1)
    with caplog.at_level(logging.INFO):
        drive(application, text_update("hola"))
    assert "unhandled error in telegram handler (RuntimeError)" in caplog.text
    assert SECRET_TEXT not in caplog.text
    assert not any(record.exc_info for record in caplog.records)


def test_si_la_conversacion_falla_pedro_recibe_una_frase_fija(make_channel, request_double, caplog):
    async def on_text(incoming: Incoming) -> str:
        raise RuntimeError(SECRET_TEXT)

    channel, application = make_channel(on_text=on_text)
    with caplog.at_level(logging.INFO):
        drive(application, text_update("hola"))
    assert request_double.params("sendMessage")[0]["text"] == COULD_NOT_REPLY
    assert "text handler failed (RuntimeError)" in caplog.text
    assert SECRET_TEXT not in caplog.text


def test_mientras_el_agente_piensa_un_toque_se_atiende_y_el_texto_sale_despues(
    make_channel, request_double
):
    """Ajuste 1 del chat: el manejador de texto no bloquea Telegram. Con el agente «pensando»,
    un toque de botón se resuelve y se edita; la respuesta sale cuando el agente acaba."""
    thinking, started = asyncio.Event(), asyncio.Event()
    order: list[str] = []

    async def on_text(incoming: Incoming) -> str:
        started.set()
        await thinking.wait()
        order.append("respuesta")
        return "hecho"

    channel, application = make_channel(on_text=on_text, typing_every=0.001)

    async def scenario():
        async with application:
            approval = await channel.send("demo", make_message(), EXPIRES)
            await application.process_update(to_update(text_update("hola"), application.bot))
            await started.wait()  # the agent is thinking now
            await application.process_update(
                to_update(callback_update(f"{approval}:1:y", update_id=2), application.bot)
            )
            order.append("toque")
            assert "editMessageText" in request_double.methods()
            thinking.set()
            await settle()

    asyncio.run(scenario())
    assert order == ["toque", "respuesta"]
    assert request_double.params("sendMessage")[-1]["text"] == "hecho"
    typing = request_double.params("sendChatAction")[0]
    assert (typing["chat_id"], typing["action"]) == (CHAT_ID, "typing")


def test_una_respuesta_larga_se_parte_en_mensajes_que_caben(make_channel, request_double):
    async def on_text(incoming: Incoming) -> str:
        return "\n".join(["línea & más " + "x" * 90] * 60)

    channel, application = make_channel(on_text=on_text)
    drive(application, text_update("hola"))
    texts = [params["text"] for params in request_double.params("sendMessage")]
    assert len(texts) == 2
    assert all(len(text.encode("utf-16-le")) // 2 <= 4096 for text in texts)
    assert "&amp;" in texts[0] and "& " not in texts[0]


def test_al_parar_se_cancela_el_turno_que_sigue_pensando(make_channel, request_double):
    never = asyncio.Event()

    async def on_text(incoming: Incoming) -> str:
        await never.wait()
        return "nunca"

    channel, application = make_channel(on_text=on_text, typing_every=0.001)

    async def scenario():
        await application.initialize()
        await application.process_update(to_update(text_update("hola"), application.bot))
        await asyncio.sleep(0)
        await channel.stop()
        await settle()

    asyncio.run(scenario())
    assert request_double.params("sendMessage") == []


def test_el_log_de_todo_un_recorrido_no_lleva_contenido_ni_chat_ni_token(
    make_channel, request_double, caplog
):
    channel, application = make_channel()
    with caplog.at_level(logging.DEBUG):
        approval_id = send_and_drive(
            channel,
            application,
            text_update(SECRET_TEXT),
        )
        drive(application, callback_update(f"{approval_id}:2:y", update_id=2))
        drive(application, callback_update(f"{approval_id}:1:y", chat_id=OTHER_CHAT_ID))
    for secret in (SECRET_TITLE, SECRET_TEXT, TOKEN, str(CHAT_ID), str(OTHER_CHAT_ID)):
        assert secret not in caplog.text


def test_un_texto_del_latido_va_sin_formato_ni_teclado_y_devuelve_su_id(
    make_channel, request_double
):
    """Lo que escribe el modelo va tal cual: con HTML, un `<` lo rompería."""
    channel, application = make_channel()
    ids: list[int] = []

    async def before():
        ids.append(await channel.send_text("a < b & c"))

    drive(application, before=before)
    assert ids == [10]
    (params,) = request_double.params("sendMessage")
    assert params["text"] == "a < b & c"
    assert "parse_mode" not in params and "reply_markup" not in params
    assert request_double.methods() == ["sendMessage"]


def test_un_texto_que_telegram_rechaza_levanta_send_error(make_channel, request_double):
    channel, application = make_channel()
    request_double.failures["sendMessage"] = (403, "Forbidden: bot was blocked by the user")

    async def before():
        with pytest.raises(SendError):
            await channel.send_text("hola")

    drive(application, before=before)


def test_los_trabajos_no_se_solapan_sin_registro_ni_se_ponen_al_dia(make_channel):
    """Leído del `JobQueue` de verdad, sin arrancarlo: cada 15 min desde el primero alineado,
    sin acumular lo perdido y con un margen de un minuto."""
    channel, application = make_channel()
    first = datetime(2026, 11, 21, 19, 15, tzinfo=ZoneInfo("Europe/Madrid"))

    async def nothing() -> None:
        return None

    async def before():
        every = channel.schedule_every(nothing, timedelta(minutes=15), first, "latido")
        daily = channel.schedule_daily(
            nothing, time(4, 0, tzinfo=ZoneInfo("Europe/Madrid")), "purga"
        )
        for job in (every, daily):
            assert job.job.max_instances == 2
            assert job.job.coalesce is True
            assert job.job.misfire_grace_time == 60
        assert every.job.trigger.interval == timedelta(minutes=15)
        assert every.job.trigger.start_date == first
        assert [job.name for job in application.job_queue.jobs()] == ["latido", "purga"]

    drive(application, before=before)


def test_build_application_deja_httpx_en_warning():
    """A INFO, `httpx` escribiría cada URL con el token; a DEBUG, `telegram` los parámetros."""
    for name in ("telegram", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    build_application(TOKEN, FakeRequest())
    for name in ("telegram", "httpx", "httpcore"):
        assert logging.getLogger(name).level == logging.WARNING


class FakeUpdater:
    def __init__(self, log: list[str], fail_start: Exception | None = None) -> None:
        self.log = log
        self.fail_start = fail_start
        self.polling_kwargs: dict = {}

    async def start_polling(self, **kwargs) -> None:
        self.log.append("start_polling")
        self.polling_kwargs = kwargs
        if self.fail_start is not None:
            raise self.fail_start

    async def stop(self) -> None:
        self.log.append("updater.stop")


class FakeApplication:
    """Records the order of the lifecycle; `stop` of one that never started raises like PTB."""

    def __init__(self, fail_initialize: Exception | None = None, fail_start=None) -> None:
        self.log: list[str] = []
        self.updater = FakeUpdater(self.log, fail_start)
        self.bot = object()
        self.handlers: list[object] = []
        self.fail_initialize = fail_initialize
        self.started = False

    def add_handler(self, handler) -> None:
        self.handlers.append(handler)

    def add_error_handler(self, handler) -> None:
        self.handlers.append(handler)

    async def initialize(self) -> None:
        self.log.append("initialize")
        if self.fail_initialize is not None:
            raise self.fail_initialize

    async def start(self) -> None:
        self.log.append("start")
        self.started = True

    async def stop(self) -> None:
        if not self.started:
            raise RuntimeError("This Application is not running!")
        self.log.append("stop")
        self.started = False

    async def shutdown(self) -> None:
        self.log.append("shutdown")


def fake_channel(app, conn, clock, **kwargs) -> TelegramChannel:
    return TelegramChannel(app, CHAT_ID, conn, clock, **kwargs)


def test_arranca_y_para_en_orden_con_dobles_sin_arrancar_el_bot(conn, clock):
    """`initialize → start → start_polling` y, al parar, al revés; el *polling* pide solo
    mensajes y toques; `on_started` corre cuando ya está escuchando."""
    app = FakeApplication()
    seen: list[str] = []

    async def on_started(channel):
        seen.append(",".join(app.log))

    channel = fake_channel(app, conn, clock, on_started=on_started)

    async def scenario():
        await channel.start()
        await channel.stop()

    asyncio.run(scenario())
    assert app.log == ["initialize", "start", "start_polling", "updater.stop", "stop", "shutdown"]
    assert seen == ["initialize,start,start_polling"]
    assert app.updater.polling_kwargs["allowed_updates"] == ["message", "callback_query"]
    assert "drop_pending_updates" not in app.updater.polling_kwargs


def test_parar_un_canal_que_no_arranco_no_falla(conn, clock):
    """Cada paso corre aunque otro falle: el `RuntimeError` de uno que no estaba en marcha no
    impide el cierre del resto."""
    app = FakeApplication()
    asyncio.run(fake_channel(app, conn, clock).stop())
    assert app.log == ["updater.stop", "shutdown"]


def test_un_token_malo_al_arrancar_es_channel_error_sin_valores_y_cierra(conn, clock, caplog):
    app = FakeApplication(fail_initialize=InvalidToken(f"The token `{TOKEN}` was rejected"))
    with caplog.at_level(logging.INFO), pytest.raises(ChannelError) as error:
        asyncio.run(fake_channel(app, conn, clock).start())
    assert str(error.value) == "no se pudo arrancar Telegram (InvalidToken)"
    assert TOKEN not in str(error.value) + caplog.text
    assert app.log == ["initialize", "updater.stop", "shutdown"]


def test_el_modulo_expone_el_gestor_de_errores_y_sin_concurrencia():
    """Los toques se procesan de uno en uno: depende del valor por defecto de la biblioteca."""
    application = build_application(TOKEN, FakeRequest())
    assert application.concurrent_updates == 1
    assert bot_module.ALLOWED_UPDATES == ("message", "callback_query")


def test_un_fallo_parcial_de_creelo_todo_avisa_en_el_toque(make_channel, request_double):
    """Pilla un [Créalo todo] que deja un elemento sin resolver sin decírselo a Pedro."""

    async def flaky(item: Item, option: str, approval_id: int) -> str:
        if item.title == SECRET_TITLE:
            raise RuntimeError("falla")
        return "✓ creada"

    channel, application = make_channel()
    channel._handlers["demo"] = flaky
    approval_id = send_and_drive(channel, application)
    request_double.calls.clear()
    drive(application, callback_update(f"{approval_id}:g:all"))
    assert request_double.methods() == ["editMessageText", "answerCallbackQuery"]
    assert request_double.params("answerCallbackQuery")[0]["text"] == bot_module.COULD_NOT


def test_parar_sigue_aunque_un_paso_falle_con_cualquier_error(conn, clock):
    """Un `OSError` al cerrar el *updater* no impide parar la aplicación ni cerrarla."""
    app = FakeApplication()

    async def broken_stop() -> None:
        raise OSError("disco")

    app.updater.stop = broken_stop
    asyncio.run(fake_channel(app, conn, clock).stop())
    assert app.log == ["shutdown"]


def test_un_mensaje_que_llega_mientras_para_no_abre_un_turno(make_channel, request_double):
    """Hallazgo 1 de la revisión: lo que la aplicación procesa al pararse no empieza un turno
    que nadie cancelaría."""
    received: list[str] = []

    async def on_text(incoming: Incoming) -> str:
        text = incoming.text
        received.append(text)
        return "nunca"

    channel, application = make_channel(on_text=on_text)

    async def scenario():
        await application.initialize()
        await channel.stop()
        await application.initialize()
        await application.process_update(to_update(text_update("hola"), application.bot))
        await settle()
        await application.shutdown()

    asyncio.run(scenario())
    assert received == []
    assert request_double.params("sendMessage") == []


def test_dos_mensajes_seguidos_se_contestan_en_el_orden_de_llegada(make_channel, request_double):
    """Ajuste de la revisión del chat: el «escribiendo…» del primero tarda más que el del
    segundo. Pilla un turno que espera a esa llamada de red antes de ponerse en la cola: el
    segundo se colaría, y su hora de llegada se retrasaría."""
    lock = asyncio.Lock()
    arrived: list[str] = []

    async def on_text(incoming: Incoming) -> str:
        text = incoming.text
        arrived.append(text)  # lo que anota la conversación al llegar
        async with lock:  # el turno, como en la conversación
            await asyncio.sleep(0)
            return f"respuesta a {text}"

    slow = asyncio.Event()
    request_double.chat_action_gates[0] = slow
    channel, application = make_channel(on_text=on_text)

    async def scenario():
        async with application:
            await application.process_update(to_update(text_update("uno"), application.bot))
            await application.process_update(
                to_update(text_update("dos", update_id=2), application.bot)
            )
            for _ in range(MAX_SPINS):
                if len(request_double.params("sendMessage")) == 2:
                    break
                await asyncio.sleep(0)
            slow.set()
            await settle()

    asyncio.run(scenario())
    assert arrived == ["uno", "dos"]
    assert [p["text"] for p in request_double.params("sendMessage")] == [
        "respuesta a uno",
        "respuesta a dos",
    ]


# --- El último mensaje, la respuesta citada, las respuestas con botones y el barrido (1.11) ---


def test_enviar_guarda_el_id_del_mensaje_y_lo_recuerda_como_el_ultimo(
    make_channel, request_double, conn
):
    """Pilla un mensaje que no se puede editar sin un toque (el «sí», el barrido)."""
    channel, application = make_channel()
    approval_id = send_and_drive(channel, application)
    approval = store.load(conn, approval_id)
    assert approval is not None and approval.telegram_message_id == 10
    assert channel.last_sent == 10


def test_el_ultimo_mensaje_cuenta_tambien_el_texto_y_las_respuestas(make_channel, request_double):
    """Pilla un «último mensaje» que solo mira lo interactivo: un aviso del latido o una
    respuesta de la conversación después del borrado no lo dejarían atrás (decisión 1.11-d)."""
    seen: list[Incoming] = []

    async def on_text(incoming: Incoming) -> str:
        seen.append(incoming)
        return "una respuesta"

    channel, application = make_channel(on_text=on_text)

    async def scenario():
        async with application:
            await channel.send("demo", make_message(), EXPIRES)  # id 10
            await channel.send_text("un aviso del latido")  # id 11
            for number, text in enumerate(("hola", "otra"), start=1):
                update = to_update(text_update(text, update_id=number), application.bot)
                await application.process_update(update)
                await channel.drain()

    asyncio.run(scenario())
    # Lo que vale es lo último enviado cuando llegó cada mensaje: tras «hola», su respuesta (12).
    assert [incoming.last_sent for incoming in seen] == [11, 12]
    assert channel.last_sent == 13


def test_la_respuesta_citada_llega_con_su_id(make_channel):
    seen: list[Incoming] = []

    async def on_text(incoming: Incoming) -> str:
        seen.append(incoming)
        return "vale"

    channel, application = make_channel(on_text=on_text)
    drive(application, text_update("sí", reply_to=33))
    assert [(incoming.text, incoming.reply_to) for incoming in seen] == [("sí", 33)]


def test_una_respuesta_con_mensaje_sale_en_uno_con_el_texto_como_cuerpo(
    make_channel, request_double, conn
):
    """Pilla los botones en un mensaje aparte de la respuesta (decisión 1.11-a)."""

    async def on_text(incoming: Incoming) -> Reply:
        return Reply("Te dejo <esto>", Outgoing("demo", make_message(), EXPIRES))

    channel, application = make_channel(on_text=on_text)
    drive(application, text_update("hola"))
    (params,) = request_double.params("sendMessage")
    assert params["text"].startswith("Te dejo &lt;esto&gt;\n\n<b>Plan de prueba</b>")
    assert keyboard_rows(params)[0] == ["1 Sí", "1 No"]
    (approval,) = conn.execute("SELECT kind, telegram_message_id FROM approvals").fetchall()
    assert approval == ("demo", 10)


def test_si_no_caben_juntos_el_texto_sale_antes_y_la_propuesta_sin_cuerpo(
    make_channel, request_double
):
    async def on_text(incoming: Incoming) -> Reply:
        return Reply("x" * 4000, Outgoing("demo", make_message(), EXPIRES))

    channel, application = make_channel(on_text=on_text)
    drive(application, text_update("hola"))
    first, second = request_double.params("sendMessage")
    assert first["text"] == "x" * 4000 and "reply_markup" not in first
    assert second["text"].startswith("<b>Plan de prueba</b>")


def test_si_telegram_rechaza_lo_hecho_sale_su_texto_sin_boton(make_channel, request_double, conn):
    """Lo ya hecho al momento no se pierde de vista si su [Deshacer] no puede salir."""
    calls = {"n": 0}
    original = request_double.do_request

    async def first_fails(url, method, request_data=None, **timeouts):
        if url.endswith("/sendMessage"):
            calls["n"] += 1
            if calls["n"] == 1:
                body = {"ok": False, "error_code": 400, "description": "Bad Request: inventado"}
                return 400, json.dumps(body).encode()
        return await original(url, method, request_data, **timeouts)

    request_double.do_request = first_fails

    async def on_text(incoming: Incoming) -> Reply:
        return Reply("Hecho", Outgoing("undo", make_message(), EXPIRES, if_unsent="✅ Hecho: x"))

    channel, application = make_channel(on_text=on_text)
    drive(application, text_update("hola"))
    texts = [params["text"] for params in request_double.params("sendMessage")]
    assert texts[-1] == "✅ Hecho: x"
    assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone() == (0,)


def test_refresh_edita_el_mensaje_guardado_sin_toque(make_channel, request_double, conn):
    channel, application = make_channel()
    approval_id = send_and_drive(channel, application)
    request_double.calls.clear()

    async def before():
        await channel.refresh(approval_id)

    drive(application, before=before)
    (params,) = request_double.params("editMessageText")
    assert params["message_id"] == 10


def test_el_barrido_edita_y_quita_los_botones_de_lo_caducado(make_channel, request_double, clock):
    """Pilla una propuesta sin toque que conserva sus botones (1.11-c)."""
    channel, application = make_channel()
    send_and_drive(channel, application)
    request_double.calls.clear()
    clock.moment = EXPIRES

    async def before():
        await channel.sweep()

    drive(application, before=before)
    (params,) = request_double.params("editMessageText")
    assert params["message_id"] == 10
    assert params["reply_markup"] == {"inline_keyboard": []}
    assert "— caducado" in params["text"]


def test_un_toque_sobre_lo_que_esta_en_curso_avisa(make_channel, request_double, conn):
    channel, application = make_channel()
    approval_id = send_and_drive(channel, application)
    approval = store.load(conn, approval_id)
    assert approval is not None
    store.save(conn, approval_id, claim(approval.message, {0}), None, NOW)
    request_double.calls.clear()
    drive(application, callback_update(f"{approval_id}:1:y"))
    assert request_double.methods() == ["answerCallbackQuery"]
    assert request_double.params("answerCallbackQuery")[0]["text"] == IN_PROGRESS


def test_si_pedro_escribio_despues_del_ultimo_envio_el_si_no_responde_a_ese(make_channel):
    """Hallazgo 1 de la revisión: Pedro escribe «no, mejor borra Y» tras la propuesta A y,
    antes de que salga la respuesta, «sí». Ese «sí» no puede confirmar el borrado de A."""
    seen: list[Incoming] = []
    gate = asyncio.Event()

    async def on_text(incoming: Incoming) -> str:
        seen.append(incoming)
        if incoming.text != "sí":
            await gate.wait()
        return "respuesta"

    channel, application = make_channel(on_text=on_text)

    async def scenario():
        async with application:
            await channel.send("demo", make_message(), EXPIRES)  # A, id 10
            for number, text in enumerate(("no, mejor borra Y", "sí"), start=1):
                update = to_update(text_update(text, update_id=number), application.bot)
                await application.process_update(update)
            gate.set()
            await channel.drain()

    asyncio.run(scenario())
    assert [incoming.last_sent for incoming in seen] == [10, None]
