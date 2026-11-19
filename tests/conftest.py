import os
from datetime import datetime
from pathlib import Path

import pytest

from jimic.paths import resolve_paths

SLOW_TIMEOUT = 600
"""Segundos por test `slow`: un tope contra un bucle sin fin, no una medida."""

REAL_HOME = Path(os.path.expanduser("~"))
"""El home real, leído al importar, antes de que ningún fixture lo cambie."""


VALID_ENV = {
    "ENTORNO": "prod",
    "ZONA_HORARIA": "Europe/Madrid",
    "MODELO_LATIDO": "modelo-latido-de-prueba",
    "MODELO_AGENTE": "modelo-agente-de-prueba",
    "CLAVE_ANTHROPIC": "test-clave-anthropic-123",
    "TOKEN_TELEGRAM": "test-token-telegram-456",
    "CHAT_TELEGRAM": "987654321",
    "TOKEN_TODOIST": "test-token-todoist-789",
    "RUTA_APEX": "/datos/inventados/apex.json",
}
"""Un `.env` válido de producción con valores inventados y fáciles de buscar en un texto."""


def write_env(values: dict[str, str]) -> Path:
    """Write `values` as the `.env` of the environment selected by `os.environ`."""
    paths = resolve_paths(os.environ)
    paths.config_dir.mkdir(parents=True)
    paths.env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    return paths.env_file


class FixedClock:
    """A clock that always says `moment`; tests move it by assigning `moment`."""

    def __init__(self, moment: datetime) -> None:
        if moment.utcoffset() is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")
        self.moment = moment

    def now(self) -> datetime:
        return self.moment


def pytest_collection_modifyitems(items):
    """Da a los tests `slow` su propio límite de tiempo, mayor que el de los demás."""
    for item in items:
        if item.get_closest_marker("slow") and not item.get_closest_marker("timeout"):
            item.add_marker(pytest.mark.timeout(SLOW_TIMEOUT))


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """Point HOME at a temporary folder and drop the variables that move the roots."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "JIMIC_ENTORNO"):
        monkeypatch.delenv(var, raising=False)
    return home
