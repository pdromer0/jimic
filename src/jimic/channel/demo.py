"""`jimic telegram demo`: an invented proposal in the test bot, to see the buttons on the phone.

Only with `ENTORNO=dev`: the demo refuses before it reads the `.env` or builds anything. It
sends two messages, as the assistant of task 1.11 would: a proposal with a create, a move and a
delete under an answer, with the global row `[✅ Créalo todo] [⏭ Nada]`, and what was done at
once with its [Deshacer]. The taps edit the messages and touch nothing in Todoist: the handlers
only say what would have happened. A text from Pedro gets the fixed phrase. It is a process that
does not end (stop it with Ctrl+C): **Pedro launches it, never the agent**.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from jimic.channel.bot import TelegramChannel, build_channel
from jimic.channel.message import InteractiveMessage, Item
from jimic.channel.taps import HandlerOutcome
from jimic.clock import Clock, SystemClock
from jimic.config import Settings, load_settings
from jimic.execution.plan_message import PLAN_KIND, UNDO_KIND, plan_message, undo_message
from jimic.paths import Paths, resolve_paths
from jimic.service import ChannelFactory, serve_until_signal

logger = logging.getLogger(__name__)

TTL = timedelta(hours=1)
"""How long the demo waits for its buttons: it only has to outlast a screenshot."""
DAY = date(2030, 1, 7)
"""The invented day of the demo: «hoy» in what it shows."""
ANSWER = "Te propongo meter el gimnasio, mover el webinar y quitar un duplicado (inventado)."
RESULTS = {
    ("create", "ok"): "✓ creada",
    ("create", "no"): "⏭ descartada",
    ("move", "ok"): "✓ movida",
    ("move", "no"): "🆗 se queda",
    ("delete", "del"): "✓ borrada",
    ("delete", "keep"): "🆗 se queda",
}


class DemoError(Exception):
    """The demo cannot run here; the message says why."""


def _snapshot(title: str, due: str) -> dict[str, Any]:
    return {"title": title, "due": due, "project_id": "demo", "is_recurring": False}


ENTRIES: list[dict[str, Any]] = [
    {
        "action": "create",
        "title": "🏋️ Gimnasio",
        "due": "2030-01-07T12:00",
        "duration_minutes": 75,
        "reason": "inventado",
    },
    {
        "action": "move",
        "task_id": "demo-1",
        "due": "2030-01-07T09:50",
        "reason": "inventado",
        "snapshot": _snapshot("Webinar", "2030-01-07T10:00:00+01:00"),
    },
    {
        "action": "delete",
        "task_id": "demo-2",
        "reason": "inventado",
        "snapshot": _snapshot("Andar 30 min", "2030-01-06"),
    },
]


def demo_messages(settings: Settings) -> list[tuple[str, InteractiveMessage]]:
    """The invented proposal and the invented [Deshacer]: nothing of them is real."""
    proposal = plan_message(ENTRIES, DAY, settings.timezone).model_copy(update={"body": ANSWER})
    undo = undo_message("➕ 🧘 Estiramientos · hoy 21:30", {"action": "create", "task_id": "x"})
    undo = undo.model_copy(update={"body": "Apuntado (inventado)."})
    return [(PLAN_KIND, proposal), (UNDO_KIND, undo)]


async def demo_plan(item: Item, option: str, approval_id: int) -> HandlerOutcome:
    """Only marks the result: the demo touches nothing."""
    action = str((item.data or {}).get("action"))
    return RESULTS.get((action, option), "hecho")


async def demo_undo(item: Item, option: str, approval_id: int) -> HandlerOutcome:
    return "↩️ deshecho"


HANDLERS = {PLAN_KIND: demo_plan, UNDO_KIND: demo_undo}


def refuse_unless_dev(paths: Paths) -> None:
    """Raise `DemoError` unless the copy is `dev` (`JIMIC_ENTORNO=dev`)."""
    if paths.environment != "dev":
        raise DemoError(
            f"la demo solo corre con ENTORNO=dev, y esta copia es {paths.environment}:"
            " lánzala con JIMIC_ENTORNO=dev"
        )


def demo_factory(settings: Settings, clock: Clock) -> ChannelFactory:
    """The channel of the demo: it sends its messages as soon as it is listening."""

    async def send_demo(channel: TelegramChannel) -> None:
        for kind, message in demo_messages(settings):
            await channel.send(kind, message, clock.now() + TTL)
        logger.info("demo sent; press Ctrl+C to stop")

    def factory(conn: sqlite3.Connection) -> TelegramChannel:
        return build_channel(settings, conn, clock, handlers=HANDLERS, on_started=send_demo)

    return factory


def run(environ: Mapping[str, str]) -> None:
    """Run the demo until SIGINT or SIGTERM.

    Raises `DemoError` outside `dev` (before anything else), and `PathsError`, `ConfigError`,
    `MigrationError` or `ChannelError` like `jimic run`.
    """
    paths = resolve_paths(environ)
    refuse_unless_dev(paths)
    settings = load_settings(paths)
    clock = SystemClock(settings.timezone)
    asyncio.run(serve_until_signal(paths, clock, channel_factory=demo_factory(settings, clock)))
