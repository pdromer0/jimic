import os
from datetime import datetime
from pathlib import Path

import pytest

SLOW_TIMEOUT = 600
"""Segundos por test `slow`: un tope contra un bucle sin fin, no una medida."""

REAL_HOME = Path(os.path.expanduser("~"))
"""El home real, leído al importar, antes de que ningún fixture lo cambie."""


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
