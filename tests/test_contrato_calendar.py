"""Contrato de Calendar contra la API real (decisión 1.5-c): lento, a mano y de solo lectura.

Solo corre con `JIMIC_CONTRATO=1`, con el permiso de `jimic-dev` (que da Pedro con
`JIMIC_ENTORNO=dev uv run jimic google autorizar`; el agente no ve ni el enlace ni el token).

Mitigaciones: ningún test enseña contenido real. Solo se comprueban la forma, los campos
presentes y los recuentos; **nunca** títulos ni horas. Y lo que se mira de las respuestas de
Google son los *nombres* de sus claves, para comprobar que la máscara `fields` se cumple.
"""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import httplib2
import pytest
from conftest import CONTRACT

from jimic.clock import SystemClock
from jimic.tools.calendar import build_calendar

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CONTRACT, reason="contrato: solo con JIMIC_CONTRATO=1"),
]

DAYS = 8
"""El día de hoy y los 7 siguientes."""

# Las claves que la máscara deja pasar, a mano (no importadas: si la máscara cambia, el
# contrato lo nota).
LIST_KEYS = {"items", "nextPageToken"}
CALENDAR_KEYS = {"id", "selected"}
EVENT_KEYS = {"summary", "start", "end", "status", "organizer", "attendees"}
MOMENT_KEYS = {"date", "dateTime", "timeZone"}
ORGANIZER_KEYS = {"self"}
ATTENDEE_KEYS = {"self", "responseStatus"}


def unexpected_keys(responses: list[tuple[str, Any]]) -> tuple[set[str], int]:
    """The names of the keys Google sent that the mask does not ask for, and how many items
    were looked at. Only names: a value never goes into a message."""
    extra: set[str] = set()
    looked_at = 0
    for uri, body in responses:
        assert isinstance(body, dict), "una respuesta de Google no es un objeto JSON"
        extra |= set(body) - LIST_KEYS
        is_events = "/events" in uri
        for item in body.get("items", []):
            looked_at += 1
            if not is_events:
                extra |= set(item) - CALENDAR_KEYS
                continue
            extra |= set(item) - EVENT_KEYS
            for moment in ("start", "end"):
                extra |= set(item.get(moment, {})) - MOMENT_KEYS
            extra |= set(item.get("organizer", {})) - ORGANIZER_KEYS
            for attendee in item.get("attendees", []):
                extra |= set(attendee) - ATTENDEE_KEYS
    return extra, looked_at


def test_contrato_de_calendar(contract_env, monkeypatch):
    paths, settings = contract_env
    responses: list[tuple[str, Any]] = []
    real_request = httplib2.Http.request

    def spy(self, uri, *args, **kwargs):
        response, content = real_request(self, uri, *args, **kwargs)
        if "/calendar/v3/" in uri:
            try:
                responses.append((uri, json.loads(content)))
            except ValueError:
                responses.append((uri, None))
        return response, content

    monkeypatch.setattr(httplib2.Http, "request", spy)
    reader = build_calendar(paths, settings.timezone)
    today = SystemClock(settings.timezone).now().date()

    async def read_all():
        return [await reader.read_day(today + timedelta(days=n)) for n in range(DAYS)]

    days = asyncio.run(read_all())

    reasons = [d.unavailable_reason for d in days if not d.available]
    assert not reasons, (
        f"Calendar no disponible ({sorted(set(map(str, reasons)))}); si es `auth`, "
        "pide a Pedro: JIMIC_ENTORNO=dev uv run jimic google autorizar"
    )

    # Al menos un calendario visible, contado sin nombres ni IDs: con cero, el contrato falla.
    counts = {d.calendar_count for d in days}
    assert min(counts) >= 1, f"calendarios visibles por día: {sorted(counts)}"

    # La forma de cada evento: tipos y coherencia, nunca el contenido.
    events = [e for d in days for e in d.events]
    for event in events:
        assert isinstance(event.title, str)
        assert isinstance(event.all_day, bool)
        assert isinstance(event.organizer_is_me, bool)
        assert isinstance(event.declined, bool)
        assert isinstance(event.start, datetime) is not event.all_day
        assert isinstance(event.end, datetime) is not event.all_day
        if isinstance(event.start, datetime) and isinstance(event.end, datetime):
            assert event.start.tzinfo is not None and event.end.tzinfo is not None
            assert event.start <= event.end

    # Solo llegan los campos de la máscara. Un contrato que no mira nada no prueba nada: se
    # exige haber visto respuestas de las dos listas y al menos un calendario.
    assert any("/events" in uri for uri, _ in responses), "no se vio ninguna lista de eventos"
    assert any("/calendarList" in uri for uri, _ in responses), "no se vio la lista de calendarios"
    extra, looked_at = unexpected_keys(responses)
    assert looked_at >= 1
    assert not extra, f"Google envió claves fuera de la máscara: {sorted(extra)}"

    print(
        f"\nrecuentos: calendarios visibles={min(counts)}, eventos en {DAYS} días={len(events)}, "
        f"elementos revisados={looked_at}, respuestas={len(responses)}"
    )
