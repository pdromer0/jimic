"""The service: start, wait until told to stop, and stop cleanly.

`run` is what `jimic run` calls: it finds the folders, validates the `.env` and the two YAML
personal files, checks that the heartbeat model has a price, and runs the `asyncio` loop until
SIGINT or SIGTERM. `serve` is the body, stopped by an event, which is how tests drive it. What
runs inside (`jimic.runtime`: the sources, the model, Telegram, the heartbeat) starts after the
database and stops before it closes, inside this same loop. Stopping closes SQLite and flushes
the logs.
"""

import asyncio
import logging
import signal
import sqlite3
from collections.abc import Callable, Mapping
from typing import Protocol

from jimic.clock import Clock, SystemClock
from jimic.config import load_settings
from jimic.logs import logging_to
from jimic.memory.database import open_database
from jimic.memory.personal import load_personal
from jimic.memory.usage import price_of
from jimic.paths import Paths, resolve_paths
from jimic.runtime import Runtime

logger = logging.getLogger(__name__)

STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class Channel(Protocol):
    """What the service runs inside: start it, and stop it (safe to repeat). The Telegram
    channel of the demo, or the whole runtime of `jimic run`."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


type ChannelFactory = Callable[[sqlite3.Connection], Channel]
"""Builds the channel once the database is open; nothing connects until `start`."""


async def serve(
    paths: Paths,
    clock: Clock,
    stop: asyncio.Event,
    ready: asyncio.Event | None = None,
    channel_factory: ChannelFactory | None = None,
) -> None:
    """Open the logs and the database, start the channel, set `ready`, and wait for `stop`;
    then stop the channel and close the database. A channel that cannot start raises
    `ChannelError`."""
    # Created here, private: `mkdir(mode=...)` only applies the mode to the last folder it makes.
    paths.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with logging_to(paths.logs_dir):
        logger.info("started at %s (%s)", clock.now().isoformat(), paths.environment)
        try:
            conn = open_database(paths.database)
        except Exception as error:
            logger.error("startup failed (%s)", type(error).__name__)
            raise
        channel = None
        try:
            if channel_factory is not None:
                channel = channel_factory(conn)
                await channel.start()
            if ready is not None:
                ready.set()
            await stop.wait()
        finally:
            try:
                if channel is not None:
                    await channel.stop()
            finally:
                conn.close()
                logger.info("stopped at %s", clock.now().isoformat())


async def serve_until_signal(
    paths: Paths,
    clock: Clock,
    ready: asyncio.Event | None = None,
    channel_factory: ChannelFactory | None = None,
) -> None:
    """Run `serve` until SIGINT or SIGTERM; the handlers are removed on the way out."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in STOP_SIGNALS:
        loop.add_signal_handler(sig, stop.set)
    try:
        await serve(paths, clock, stop, ready, channel_factory)
    finally:
        for sig in STOP_SIGNALS:
            loop.remove_signal_handler(sig)


build_runtime = Runtime
"""What `run` builds inside the service; the tests replace it."""


def run(environ: Mapping[str, str]) -> None:
    """Validate the setup and run the service until a stop signal.

    Raises `PathsError`, `ConfigError`, `PersonalFilesInvalid`, `UnpricedError`,
    `MigrationError` or `ChannelError` with a message for the user.
    """
    paths = resolve_paths(environ)
    settings = load_settings(paths)
    intentions, limits = load_personal(paths.config_dir)
    price_of(settings.heartbeat_model)  # nothing is spent without counting it
    clock = SystemClock(settings.timezone)

    def channel_factory(conn: sqlite3.Connection) -> Channel:
        return build_runtime(settings, paths, clock, conn, intentions, limits)

    asyncio.run(serve_until_signal(paths, clock, channel_factory=channel_factory))
