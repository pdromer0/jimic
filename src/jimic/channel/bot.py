"""The Telegram channel: a thin adapter over `python-telegram-bot` that only listens to Pedro.

What decides things lives elsewhere (`message` renders, `taps` applies a tap); this module only
filters by chat, sends, edits, answers and starts or stops the polling inside the loop that
`service.py` already runs. Nothing it logs is content: no text, titles, `chat_id` or token, only
codes (the name of an exception type), ids and counts.

Rules (1.4-a): whatever comes from another chat is ignored in silence, with no reply and no
`answerCallbackQuery`; the log says only «unknown chat».
"""

import logging
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.request import BaseRequest

from jimic.channel import store
from jimic.channel.message import InteractiveMessage, View, parse_callback, render
from jimic.channel.taps import TapHandler, process_tap
from jimic.clock import Clock
from jimic.config import Settings

logger = logging.getLogger(__name__)

NOT_YET = "Aún no sé conversar: llego en la 1.10"
"""What Pedro's text gets until the conversation arrives (task 1.10)."""
ALREADY_RESOLVED = "Eso ya estaba resuelto"
COULD_NOT = "No he podido hacerlo"
ALLOWED_UPDATES = ("message", "callback_query")
NOT_MODIFIED = "message is not modified"

type TextHandler = Callable[[str], Awaitable[str]]
"""Called with the text Pedro wrote; returns the reply, as plain text."""
type OnStarted = Callable[["TelegramChannel"], Awaitable[None]]


class ChannelError(Exception):
    """The channel could not start; the message says why without values."""


class SendError(ChannelError):
    """A message could not be sent; `code` is the name of the failure, never content."""


async def reply_not_yet(text: str) -> str:
    return NOT_YET


def build_application(token: str, request: BaseRequest | None = None) -> Application:
    """The `Application` for `token`. A `request` replaces the HTTP client (the tests' double).

    `httpx` writes every request, with the token in its URL, at INFO, and `telegram` writes the
    parameters of every call (the text) at DEBUG: both are kept at WARNING.
    """
    for name in ("telegram", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    builder = Application.builder().token(token)
    if request is not None:
        builder = builder.request(request).get_updates_request(request)
    return builder.build()


def _code(error: BaseException) -> str:
    return type(error).__name__


def _markup(view: View) -> InlineKeyboardMarkup | None:
    if not view.keyboard:
        return None
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(b.label, callback_data=b.data) for b in row]
            for row in view.keyboard
        ]
    )


# PTB writes an empty `InlineKeyboardMarkup` as `{}`, and the Bot API says `inline_keyboard` is
# required: the empty list that takes the buttons off a message goes explicitly.
NO_KEYBOARD = {"reply_markup": {"inline_keyboard": []}}


class TelegramChannel:
    """Pedro's chat: send interactive messages, edit them on every tap, answer his text."""

    def __init__(
        self,
        application: Application,
        chat_id: int,
        conn: sqlite3.Connection,
        clock: Clock,
        handlers: Mapping[str, TapHandler] | None = None,
        on_text: TextHandler = reply_not_yet,
        on_started: OnStarted | None = None,
    ) -> None:
        self._app = application
        self._chat_id = chat_id
        self._conn = conn
        self._clock = clock
        self._handlers = dict(handlers or {})
        self._on_text = on_text
        self._on_started = on_started
        self._running = False
        application.add_handler(CallbackQueryHandler(self._handle_tap))
        application.add_handler(MessageHandler(filters.ALL, self._handle_message))
        application.add_error_handler(self._handle_error)

    async def start(self) -> None:
        """Initialize, start and begin polling. Raises `ChannelError` if Telegram says no."""
        updater = self._app.updater
        assert updater is not None
        try:
            await self._app.initialize()
            await self._app.start()
            await updater.start_polling(
                allowed_updates=list(ALLOWED_UPDATES), error_callback=self._polling_failed
            )
        except TelegramError as error:
            logger.error("telegram start failed (%s)", _code(error))
            await self.stop()
            raise ChannelError(f"no se pudo arrancar Telegram ({_code(error)})") from None
        self._running = True
        logger.info("telegram polling started")
        if self._on_started is not None:
            await self._on_started(self)

    async def stop(self) -> None:
        """Stop polling and shut the application down; every step runs even if one fails."""
        updater = self._app.updater
        assert updater is not None
        steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
            ("updater", updater.stop),
            ("application", self._app.stop),
            ("shutdown", self._app.shutdown),
        ]
        for name, step in steps:
            try:
                await step()
            except (TelegramError, RuntimeError) as error:
                # RuntimeError: a step that was never started has nothing to stop.
                logger.debug("telegram stop of %s skipped (%s)", name, _code(error))
        if self._running:
            logger.info("telegram polling stopped")
        self._running = False

    async def send(self, kind: str, message: InteractiveMessage, expires_at: datetime) -> int:
        """Save `message` as a pending approval of `kind` and send it; return the approval id.

        Raises `MessageTooLong` if it cannot fit, and `SendError` if Telegram refuses it (the
        approval is dropped: nobody could tap it).
        """
        approval_id = store.create(self._conn, self._clock, kind, message, expires_at)
        view = render(message, approval_id)
        try:
            await self._app.bot.send_message(
                chat_id=self._chat_id,
                text=view.text,
                parse_mode=ParseMode.HTML,
                reply_markup=_markup(view),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except TelegramError as error:
            store.discard(self._conn, approval_id)
            logger.error("telegram send failed (%s)", _code(error))
            raise SendError(_code(error)) from None
        logger.info("interactive message sent for approval %d", approval_id)
        return approval_id

    def _is_pedro(self, chat_id: int | None) -> bool:
        return chat_id == self._chat_id

    async def _handle_tap(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        # An inline message has no `message`: it cannot be Pedro's chat.
        if query is None or query.message is None or not self._is_pedro(query.message.chat.id):
            logger.info("unknown chat")
            return
        tap = parse_callback(query.data or "")
        if tap is None:
            logger.info("tap ignored (bad_data)")
            await self._answer(query.id)
            return
        result = await process_tap(self._conn, self._clock, self._handlers, tap)
        logger.info("tap: %s", result.code)
        if result.view is not None:
            await self._edit(query.message.message_id, result.view)
        await self._answer(query.id, self._toast(result.code))

    @staticmethod
    def _toast(code: str) -> str | None:
        if code in ("nothing_to_do", "already_resolved"):
            return ALREADY_RESOLVED
        if code == "handler_failed":
            return COULD_NOT
        return None

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if chat is None or not self._is_pedro(chat.id):
            logger.info("unknown chat")
            return
        message = update.effective_message
        if message is None or message.text is None:
            logger.info("message ignored (not_text)")
            return
        reply = await self._on_text(message.text)
        try:
            await self._app.bot.send_message(chat_id=self._chat_id, text=reply)
        except TelegramError as error:
            logger.error("telegram send failed (%s)", _code(error))

    async def _edit(self, message_id: int, view: View) -> None:
        try:
            await self._app.bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=message_id,
                text=view.text,
                parse_mode=ParseMode.HTML,
                reply_markup=_markup(view),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                api_kwargs=None if view.keyboard else NO_KEYBOARD,
            )
        except BadRequest as error:
            if NOT_MODIFIED not in error.message.lower():
                logger.error("telegram edit failed (%s)", _code(error))
        except TelegramError as error:
            logger.error("telegram edit failed (%s)", _code(error))

    async def _answer(self, query_id: str, text: str | None = None) -> None:
        try:
            await self._app.bot.answer_callback_query(query_id, text=text)
        except TelegramError as error:
            logger.error("telegram answer failed (%s)", _code(error))

    def _polling_failed(self, error: TelegramError) -> None:
        logger.warning("telegram polling failed (%s)", _code(error))

    async def _handle_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        # The default handler logs the traceback, and an error from the network can carry the URL
        # with the token: only the type goes to the log.
        logger.error(
            "unhandled error in telegram handler (%s)",
            _code(context.error) if context.error else "unknown",
        )


def build_channel(
    settings: Settings,
    conn: sqlite3.Connection,
    clock: Clock,
    handlers: Mapping[str, TapHandler] | None = None,
    on_started: OnStarted | None = None,
) -> TelegramChannel:
    """The channel of the bot in `settings`, ready to `start` (nothing connects before that)."""
    application = build_application(settings.telegram_token.get_secret_value())
    return TelegramChannel(
        application,
        settings.telegram_chat_id,
        conn,
        clock,
        handlers=handlers,
        on_started=on_started,
    )
