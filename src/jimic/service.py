"""The service: start, wait until told to stop, and stop cleanly.

`run` is what `jimic run` calls: it finds the folders, validates the `.env`, and runs the
`asyncio` loop until SIGINT or SIGTERM. `serve` is the body, stopped by an event, which is how
tests drive it. Stopping closes SQLite and flushes the logs.
"""

import asyncio
import logging
import signal
from collections.abc import Mapping

from jimic.clock import Clock, SystemClock
from jimic.config import load_settings
from jimic.logs import logging_to
from jimic.memory.database import open_database
from jimic.paths import Paths, resolve_paths

logger = logging.getLogger(__name__)

STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)


async def serve(
    paths: Paths, clock: Clock, stop: asyncio.Event, ready: asyncio.Event | None = None
) -> None:
    """Open the logs and the database, set `ready`, and wait for `stop`; then close both."""
    with logging_to(paths.logs_dir):
        logger.info("started at %s (%s)", clock.now().isoformat(), paths.environment)
        conn = open_database(paths.database)
        try:
            if ready is not None:
                ready.set()
            await stop.wait()
        finally:
            conn.close()
            logger.info("stopped at %s", clock.now().isoformat())


async def serve_until_signal(
    paths: Paths, clock: Clock, ready: asyncio.Event | None = None
) -> None:
    """Run `serve` until SIGINT or SIGTERM; the handlers are removed on the way out."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in STOP_SIGNALS:
        loop.add_signal_handler(sig, stop.set)
    try:
        await serve(paths, clock, stop, ready)
    finally:
        for sig in STOP_SIGNALS:
            loop.remove_signal_handler(sig)


def run(environ: Mapping[str, str]) -> None:
    """Validate the setup and run the service until a stop signal.

    Raises `PathsError`, `ConfigError` or `MigrationError` with a message for the user.
    """
    paths = resolve_paths(environ)
    settings = load_settings(paths)
    asyncio.run(serve_until_signal(paths, SystemClock(settings.timezone)))
