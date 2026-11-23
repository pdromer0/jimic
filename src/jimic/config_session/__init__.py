"""The configuration session: Claude Code opened in the config root with limits of its own.

`jimic config sesion` writes `<config>/.claude/` from the templates of this package every time
and opens Claude Code there (decision 1.18-b). The session can read and edit the personal files
and run `jimic config check`; it cannot read the secrets of either copy, push, use `gh` or reach
the network. Two layers keep the secrets out (decision 1.18-a): the `deny` rules stop Claude
Code's own tools, and the sandbox (bubblewrap) stops the shell commands and their children.

What Claude reads goes into the conversation, and so to the API: the secrets of the other copy
are denied too, even with the network closed.
"""

import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from importlib.resources import files
from pathlib import Path
from typing import NoReturn

from jimic.files import write_private
from jimic.paths import ENVIRONMENT_VAR, ROOT_NAMES, resolve_paths

SECRET_NAMES = (".env", ".env.*", "google_cliente.json", "google_token.json")
"""The files of each config root the session never reads: the `.env` (and any variant of it)
and the two Google credentials. A name with `*` is a pattern, only for the `deny` rules."""

SANDBOX_PACKAGES = {"bwrap": "bubblewrap", "socat": "socat"}
"""What the sandbox of Claude Code needs on Linux, by command and by Debian/Ubuntu package."""

REQUIRED_COMMANDS = (*SANDBOX_PACKAGES, "claude")

SKILL_NAME = "configurar-jimic"

SETTINGS_SCHEMA = "https://json.schemastore.org/claude-code-settings.json"

Launcher = Callable[[Path, Sequence[str]], None]
"""Opens Claude Code: the folder to work in and the command line."""


class SessionError(Exception):
    """The session cannot be opened; the message says what to install or what is missing."""


def secret_files(environ: Mapping[str, str]) -> list[Path]:
    """The secrets of both copies, under the roots `resolve_paths` gives for each environment."""
    roots = [
        resolve_paths({**environ, ENVIRONMENT_VAR: environment}).config_dir
        for environment in ROOT_NAMES
    ]
    return [root / name for root in roots for name in SECRET_NAMES]


def build_settings(environ: Mapping[str, str], jimic: Path) -> dict[str, object]:
    """The `settings.json` of the session for the copy that `environ` picks.

    `jimic` is the absolute path of the `jimic` executable: inside the sandbox, `uv run` cannot
    write its cache. Paths in the rules are absolute (`//…` in a permission rule), so they hold
    wherever `claude` is opened.
    """
    paths = resolve_paths(environ)
    secrets = secret_files(environ)
    deny = [f"{tool}(/{path})" for path in secrets for tool in ("Read", "Edit")]
    deny += ["WebFetch", "WebSearch", "Bash(git push *)", "Bash(gh *)"]
    return {
        "$schema": SETTINGS_SCHEMA,
        "permissions": {"allow": [f"Bash({jimic} config check)"], "deny": deny},
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {"denyRead": [str(path) for path in secrets if "*" not in path.name]},
            # `strictAllowlist` is only honoured from `--settings`: `run` opens Claude Code so.
            "network": {"allowedDomains": [], "strictAllowlist": True},
        },
        "env": {ENVIRONMENT_VAR: paths.environment, "PYTHONDONTWRITEBYTECODE": "1"},
    }


def template(name: str) -> str:
    return files("jimic.config_session").joinpath(name).read_text(encoding="utf-8")


def settings_file(config_dir: Path) -> Path:
    return config_dir / ".claude" / "settings.json"


def memory_file(config_dir: Path) -> Path:
    return config_dir / ".claude" / "CLAUDE.md"


def skill_file(config_dir: Path) -> Path:
    return config_dir / ".claude" / "skills" / SKILL_NAME / "SKILL.md"


def write_session(environ: Mapping[str, str], jimic: Path) -> Path:
    """Write the settings, the `CLAUDE.md` and the skill, always from the templates.

    Each file is replaced atomically (0600), so a change by hand is lost and a new template
    arrives at the next launch. Return the path of the settings file.
    """
    config_dir = resolve_paths(environ).config_dir
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = config_dir
    for part in (".claude", "skills", SKILL_NAME):
        folder = folder / part
        folder.mkdir(mode=0o700, exist_ok=True)
    settings = json.dumps(build_settings(environ, jimic), indent=2, ensure_ascii=False) + "\n"
    write_private(settings_file(config_dir), settings)
    for path, name in (
        (memory_file(config_dir), "claude.md"),
        (skill_file(config_dir), "skill.md"),
    ):
        write_private(path, template(name).replace("{jimic}", str(jimic)))
    return settings_file(config_dir)


def missing_commands(which: Callable[[str], str | None]) -> list[str]:
    return [command for command in REQUIRED_COMMANDS if which(command) is None]


def describe_missing(missing: Sequence[str]) -> str:
    parts = []
    packages = [SANDBOX_PACKAGES[command] for command in missing if command in SANDBOX_PACKAGES]
    if packages:
        parts.append(
            f"instala {' y '.join(packages)} (sudo apt install {' '.join(packages)}),"
            " que necesita el sandbox de la sesión"
        )
    if "claude" in missing:
        parts.append("instala Claude Code (no hay `claude` en el PATH)")
    return "no se abre la sesión de configuración: " + "; ".join(parts)


def default_jimic() -> Path:
    """The `jimic` next to the running Python: the one of the virtual environment.

    The link is not resolved: `.venv/bin/python` points at the base interpreter, whose folder
    has no `jimic`.
    """
    return Path(sys.executable).with_name("jimic")


def find_command(name: str) -> str | None:
    return shutil.which(name)


def exec_claude(cwd: Path, argv: Sequence[str]) -> NoReturn:
    """Replace this process with Claude Code, working in `cwd`."""
    os.chdir(cwd)
    os.execvp(argv[0], list(argv))


def run(
    environ: Mapping[str, str],
    *,
    which: Callable[[str], str | None] | None = None,
    launch: Launcher | None = None,
    jimic: Path | None = None,
) -> None:
    """Check the dependencies, write the session files and open Claude Code in the config root.

    Nothing is written or opened if a command is missing. Claude Code gets the settings file
    with `--settings` too: only from there does it honour `strictAllowlist` (the network closed
    without asking) and keep `allowUnsandboxedCommands` fixed.
    """
    config_dir = resolve_paths(environ).config_dir
    missing = missing_commands(which or find_command)
    if missing:
        raise SessionError(describe_missing(missing))
    executable = jimic or default_jimic()
    if not executable.is_file():
        raise SessionError(f"no está el ejecutable de jimic en {executable}")
    settings = write_session(environ, executable)
    (launch or exec_claude)(config_dir, ["claude", "--settings", str(settings)])
