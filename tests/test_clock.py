"""Reloj: hora consciente de la zona, momentos en UTC para la base y nadie más lee la hora."""

import ast
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import FixedClock

import jimic
from jimic.clock import SystemClock, to_db

MADRID = ZoneInfo("Europe/Madrid")
PACKAGE_DIR = Path(jimic.__file__).parent

SYSTEM_TIME_CALLS = {
    "datetime.now",
    "datetime.utcnow",
    "datetime.today",
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "datetime.datetime.today",
    "date.today",
    "datetime.date.today",
    "time.time",
    "time.time_ns",
}
"""Llamadas que leen el reloj del sistema. `clock.now()` no está: es lo que se debe usar."""


def system_time_calls(source: str) -> list[str]:
    """Return the dotted names of the calls in `source` that read the system clock."""

    def dotted(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = dotted(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            if name in SYSTEM_TIME_CALLS:
                found.append(name)
    return found


def test_el_reloj_del_sistema_da_la_hora_en_su_zona():
    """Pilla un `now()` sin zona o en UTC: la hora sale con la zona de la configuración."""
    now = SystemClock(MADRID).now()
    assert now.tzinfo is MADRID
    assert now.utcoffset() is not None


def test_to_db_pasa_a_utc_y_trunca_a_segundos():
    """Pilla que se guarde la hora local con su desfase o con microsegundos."""
    moment = datetime(2026, 11, 19, 9, 0, 0, 500_000, tzinfo=MADRID)
    assert to_db(moment) == "2026-11-19T08:00:00+00:00"


def test_to_db_rechaza_un_datetime_sin_zona():
    """Pilla un momento ambiguo guardado como si fuera UTC."""
    with pytest.raises(ValueError):
        to_db(datetime(2026, 11, 19, 9, 0))


def test_en_utc_el_orden_del_texto_es_el_orden_real():
    """Pilla comparar texto con desfases distintos: 02:30+02:00 va antes que 02:10+01:00."""
    earlier = datetime.fromisoformat("2026-10-25T02:30:00+02:00")
    later = datetime.fromisoformat("2026-10-25T02:10:00+01:00")
    assert earlier < later
    assert not earlier.isoformat() < later.isoformat()
    assert to_db(earlier) < to_db(later)


def test_el_detector_ve_el_reloj_del_sistema_y_deja_pasar_el_inyectado():
    """Sin esto, el test del paquete podría pasar porque el detector no detecta nada."""
    source = (
        "import time\nfrom datetime import date, datetime\n"
        "a = datetime.now(tz)\nb = date.today()\nc = time.time()\nd = self._clock.now()\n"
        "e = clock.now()\n"
    )
    assert system_time_calls(source) == ["datetime.now", "date.today", "time.time"]


def test_solo_clock_lee_el_reloj_del_sistema():
    """Pilla un `datetime.now()` fuera de `clock.py`: ese código no se puede probar con el doble."""
    files = sorted(PACKAGE_DIR.rglob("*.py"))
    assert PACKAGE_DIR / "clock.py" in files
    offenders = {
        str(path.relative_to(PACKAGE_DIR)): calls
        for path in files
        if path.name != "clock.py" and (calls := system_time_calls(path.read_text()))
    }
    assert offenders == {}


def test_el_reloj_fijo_de_los_tests_exige_zona():
    """El doble rechaza un momento sin zona, como el reloj real nunca lo daría."""
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 1, 1))
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    assert FixedClock(moment).now() is moment
