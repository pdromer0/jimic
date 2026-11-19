"""Where jimic keeps its files: the config root (personal files, `.env`) and the data root.

The roots follow the XDG base directories. `JIMIC_ENTORNO` picks the copy: `prod` (or unset)
uses `jimic`, `dev` uses `jimic-dev`. That one variable moves both roots.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENVIRONMENT_VAR = "JIMIC_ENTORNO"
ROOT_NAMES = {"prod": "jimic", "dev": "jimic-dev"}


class PathsError(Exception):
    """The environment does not say where the roots are; the message is shown to the user."""


@dataclass(frozen=True)
class Paths:
    environment: str
    config_dir: Path
    data_dir: Path

    @property
    def env_file(self) -> Path:
        return self.config_dir / ".env"

    @property
    def google_client_file(self) -> Path:
        """The OAuth client of the copy (a desktop client), put there by hand."""
        return self.config_dir / "google_cliente.json"

    @property
    def google_token_file(self) -> Path:
        """The user's permission for Google, written by `jimic google autorizar` (0600)."""
        return self.config_dir / "google_token.json"

    @property
    def database(self) -> Path:
        return self.data_dir / "jimic.sqlite3"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


def _xdg_base(environ: Mapping[str, str], var: str, home: Path, default: str) -> Path:
    """Return `$var` if it is an absolute path; otherwise `home/default`, as the XDG spec says."""
    value = environ.get(var, "")
    if value and Path(value).is_absolute():
        return Path(value)
    return home / default


def resolve_paths(environ: Mapping[str, str]) -> Paths:
    """Return the roots for the environment given (usually `os.environ`)."""
    environment = environ.get(ENVIRONMENT_VAR) or "prod"
    if environment not in ROOT_NAMES:
        allowed = ", ".join(sorted(ROOT_NAMES))
        raise PathsError(f"{ENVIRONMENT_VAR} debe ser uno de: {allowed}")
    home_value = environ.get("HOME", "")
    if not home_value or not Path(home_value).is_absolute():
        raise PathsError("HOME no es una ruta absoluta")
    home = Path(home_value)
    name = ROOT_NAMES[environment]
    return Paths(
        environment=environment,
        config_dir=_xdg_base(environ, "XDG_CONFIG_HOME", home, ".config") / name,
        data_dir=_xdg_base(environ, "XDG_DATA_HOME", home, ".local/share") / name,
    )
