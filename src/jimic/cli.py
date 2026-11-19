"""Command-line interface of jimic."""

import argparse
import os
import sqlite3
import sys
from importlib.metadata import version

from jimic import service
from jimic.config import ConfigError
from jimic.doctor import diagnose, render
from jimic.memory.database import MigrationError
from jimic.paths import PathsError

SETUP_ERROR = 2
"""Exit status when the service cannot start because of its folders, `.env` or database.

The messages of these errors name keys, paths and SQLite failures, never values."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jimic")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('jimic')}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor", help="comprueba si el entorno está listo")
    commands.add_parser("run", help="arranca el servicio hasta SIGINT o SIGTERM")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        print(render(diagnose()))
    elif args.command == "run":
        try:
            service.run(os.environ)
        except (PathsError, ConfigError, MigrationError, sqlite3.Error, OSError) as error:
            print(f"jimic: {error}", file=sys.stderr)
            return SETUP_ERROR
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
