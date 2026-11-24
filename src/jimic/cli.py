"""Command-line interface of jimic."""

import argparse
import os
import sqlite3
import sys
from collections.abc import Callable
from importlib.metadata import version

from jimic import config_session, service
from jimic.channel import demo
from jimic.channel.bot import ChannelError
from jimic.config import ConfigError
from jimic.doctor import diagnose, render
from jimic.evals import command as evals
from jimic.evals.scenarios import EvalError
from jimic.memory import personal
from jimic.memory.database import MigrationError
from jimic.memory.personal import PersonalFilesInvalid
from jimic.memory.usage import UnpricedError
from jimic.paths import PathsError, resolve_paths
from jimic.tools import google_auth

SETUP_ERROR = 2
"""Exit status when the service cannot start because of its folders, `.env`, personal files or
database.

The messages of these errors name keys, paths and SQLite failures, never values."""

SETUP_ERRORS = (
    PathsError,
    ConfigError,
    PersonalFilesInvalid,
    UnpricedError,
    MigrationError,
    ChannelError,
    config_session.SessionError,
    demo.DemoError,
    EvalError,
    sqlite3.Error,
    OSError,
)
"""What `jimic run`, `jimic config sesion`, `jimic telegram demo` and `jimic evaluar` turn into
a message and `SETUP_ERROR`."""

CHECK_FAILED = 1
"""Exit status of `jimic config check` when a personal file is invalid or a YAML one is missing."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jimic")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('jimic')}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor", help="comprueba si el entorno está listo")
    commands.add_parser("run", help="arranca el servicio hasta SIGINT o SIGTERM")
    config = commands.add_parser("config", help="ficheros personales")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("check", help="dice dónde falla cada fichero, sin enseñarlo")
    config_commands.add_parser("init", help="crea los YAML que faltan; nunca sobrescribe")
    config_commands.add_parser(
        "sesion", help="abre Claude Code en la carpeta de configuración, con sus límites"
    )
    google = commands.add_parser("google", help="permiso de Google")
    google_commands = google.add_subparsers(dest="google_command", required=True)
    google_commands.add_parser(
        "autorizar", help="pide el permiso de solo lectura del calendario (una vez por copia)"
    )
    telegram = commands.add_parser("telegram", help="el bot de Telegram")
    telegram_commands = telegram.add_subparsers(dest="telegram_command", required=True)
    telegram_commands.add_parser(
        "demo", help="plan inventado en el bot de pruebas (solo dev; Ctrl+C para parar)"
    )
    evaluate = commands.add_parser("evaluar", help="evaluaciones del juicio del modelo (solo dev)")
    evaluate_commands = evaluate.add_subparsers(dest="eval_command", required=True)
    heartbeat = evaluate_commands.add_parser(
        "latido", help="los escenarios de evals/latido.yaml contra la API real (cuesta dinero)"
    )
    heartbeat.add_argument(
        "--escenario", action="append", dest="scenarios", metavar="ID", help="solo este (repetible)"
    )
    heartbeat.add_argument(
        "--pasadas", type=int, default=evals.DEFAULT_PASSES, dest="passes", help="por escenario"
    )
    heartbeat.add_argument(
        "--comparar-con",
        dest="compare_to",
        metavar="INFORME",
        help="el informe con el que comparar (sin él, el último completo)",
    )
    plan = evaluate_commands.add_parser(
        "plan", help="los escenarios de evals/plan.yaml con el agente real (cuesta dinero)"
    )
    plan.add_argument(
        "--escenario", action="append", dest="scenarios", metavar="ID", help="solo este (repetible)"
    )
    plan.add_argument(
        "--pasadas", type=int, default=evals.DEFAULT_PASSES, dest="passes", help="por escenario"
    )
    plan.add_argument(
        "--comparar-con",
        dest="compare_to",
        metavar="INFORME",
        help="el informe con el que comparar (sin él, el último completo)",
    )
    return parser


def google_command() -> int:
    """Run `jimic google autorizar` for the copy that `JIMIC_ENTORNO` picks."""
    try:
        paths = resolve_paths(os.environ)
    except PathsError as error:
        print(f"jimic: {error}", file=sys.stderr)
        return SETUP_ERROR
    try:
        google_auth.authorize(paths)
    except google_auth.GoogleAuthError as error:
        print(f"jimic: {error}", file=sys.stderr)
        return SETUP_ERROR
    print("Permiso guardado: JimiC puede leer el calendario, y solo leerlo.")
    return 0


def config_command(command: str) -> int:
    """Run `jimic config check`, `init` or `sesion` in the config root of the environment."""
    if command == "sesion":
        return setup_errors_as_exit(lambda: config_session.run(os.environ))
    try:
        config_dir = resolve_paths(os.environ).config_dir
    except PathsError as error:
        print(f"jimic: {error}", file=sys.stderr)
        return SETUP_ERROR
    if command == "check":
        reports = personal.check(config_dir)
        print(personal.render_check(config_dir, reports))
        return CHECK_FAILED if personal.has_errors(reports) else 0
    try:
        results = personal.init(config_dir)
    except OSError as error:
        print(f"jimic: no se pueden crear los ficheros ({type(error).__name__})", file=sys.stderr)
        return SETUP_ERROR
    print(personal.render_init(config_dir, results))
    return 0


def setup_errors_as_exit(action: Callable[[], None]) -> int:
    """Run `action`; a setup error is printed as `jimic: <message>` and gives `SETUP_ERROR`."""
    try:
        action()
    except SETUP_ERRORS as error:
        print(f"jimic: {error}", file=sys.stderr)
        return SETUP_ERROR
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        print(render(diagnose()))
    elif args.command == "config":
        return config_command(args.config_command)
    elif args.command == "google":
        return google_command()
    elif args.command == "evaluar":
        if args.eval_command == "plan":
            return setup_errors_as_exit(
                lambda: print(
                    evals.run_plan(
                        os.environ, args.scenarios, args.passes, compare_to=args.compare_to
                    ).text
                )
            )
        return setup_errors_as_exit(
            lambda: print(
                evals.run(os.environ, args.scenarios, args.passes, compare_to=args.compare_to).text
            )
        )
    elif args.command == "telegram":
        return setup_errors_as_exit(lambda: demo.run(os.environ))
    elif args.command == "run":
        return setup_errors_as_exit(lambda: service.run(os.environ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
