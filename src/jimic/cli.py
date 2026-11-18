"""Command-line interface of jimic."""

import argparse
from importlib.metadata import version

from jimic.doctor import diagnose, render


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jimic")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('jimic')}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor", help="comprueba si el entorno está listo")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        print(render(diagnose()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
