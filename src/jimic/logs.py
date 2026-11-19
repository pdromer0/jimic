"""Logs: standard error plus a file in `<data root>/logs/`, rotated at midnight, 7 days kept.

Logs say what happened (intent, decision, IDs, times, errors) and never the content: no task
titles, event names or message text. Their timestamps come from the system, not the clock.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOGGER_NAME = "jimic"
LOG_FILE = "jimic.log"
DAYS_KEPT = 7
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


@contextmanager
def logging_to(logs_dir: Path) -> Generator[logging.Logger]:
    """Send the `jimic` loggers to stderr and the daily file; flush and close them on exit."""
    logs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    formatter = logging.Formatter(FORMAT)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        TimedRotatingFileHandler(
            logs_dir / LOG_FILE, when="midnight", backupCount=DAYS_KEPT, encoding="utf-8"
        ),
    ]
    logger = logging.getLogger(LOGGER_NAME)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    try:
        yield logger
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.flush()
            handler.close()
        logger.setLevel(previous_level)
