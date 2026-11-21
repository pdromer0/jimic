"""`jimic telegram demo`: an invented plan in the test bot, to see the buttons on the phone.

Only with `ENTORNO=dev`: the demo refuses before it reads the `.env` or builds anything. It
sends a plan with 3 invented proposals, rows `n · Sí` / `n · No` and the global row
`[Créalo todo] [Nada]`. The taps edit the message and create nothing in Todoist. A text from
Pedro gets the fixed phrase. It is a process that does not end (stop it with Ctrl+C): **Pedro
launches it, never the agent**.
"""

import asyncio
import logging
from collections.abc import Mapping
from datetime import timedelta

from jimic.channel.bot import TelegramChannel, build_channel
from jimic.channel.message import GlobalOption, InteractiveMessage, Item, Option
from jimic.clock import Clock, SystemClock
from jimic.config import Settings, load_settings
from jimic.paths import Paths, resolve_paths
from jimic.service import ChannelFactory, serve_until_signal

logger = logging.getLogger(__name__)

KIND = "demo"
TTL = timedelta(hours=1)
"""How long the demo plan waits for its buttons: it only has to outlast a screenshot."""
CREATED = "✓ creada"
DISCARDED = "✗ descartada"


class DemoError(Exception):
    """The demo cannot run here; the message says why."""


def demo_message() -> InteractiveMessage:
    """The invented plan: nothing of it is real, and no tap creates anything."""
    options = (Option(key="y", label="Sí"), Option(key="n", label="No", approves=False))
    return InteractiveMessage(
        header="Plan de prueba (inventado)",
        items=tuple(
            Item(title=title, options=options)
            for title in (
                "Gimnasio 13:00",
                "Llamar a la tienda de bicis 17:30",
                "Leer 20 minutos 21:00",
            )
        ),
        global_options=(
            GlobalOption(key="all", label="Créalo todo", option="y"),
            GlobalOption(key="none", label="Nada", option="n"),
        ),
    )


async def demo_handler(item: Item, option: str) -> str:
    """Only marks the result: the demo creates nothing."""
    return CREATED if option == "y" else DISCARDED


def refuse_unless_dev(paths: Paths) -> None:
    """Raise `DemoError` unless the copy is `dev` (`JIMIC_ENTORNO=dev`)."""
    if paths.environment != "dev":
        raise DemoError(
            f"la demo solo corre con ENTORNO=dev, y esta copia es {paths.environment}:"
            " lánzala con JIMIC_ENTORNO=dev"
        )


def demo_factory(settings: Settings, clock: Clock) -> ChannelFactory:
    """The channel of the demo: it sends the plan as soon as it is listening."""

    async def send_plan(channel: TelegramChannel) -> None:
        await channel.send(KIND, demo_message(), clock.now() + TTL)
        logger.info("demo plan sent; press Ctrl+C to stop")

    def factory(conn):
        return build_channel(
            settings, conn, clock, handlers={KIND: demo_handler}, on_started=send_plan
        )

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
