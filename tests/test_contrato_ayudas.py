"""La ayuda del contrato de Calendar que busca claves fuera de la máscara.

El contrato real se salta sin `JIMIC_CONTRATO=1`; esta ayuda es lógica pura y se prueba siempre.
Si no detectara una clave de más, el contrato pasaría de forma vacía.
"""

from test_contrato_calendar import unexpected_keys

CALENDARS = "https://www.googleapis.com/calendar/v3/users/me/calendarList?fields=x"
EVENTS = "https://www.googleapis.com/calendar/v3/calendars/c1/events?fields=x"


def clean_event() -> dict:
    return {
        "summary": "x",
        "start": {"dateTime": "d", "timeZone": "z"},
        "end": {"date": "d"},
        "status": "confirmed",
        "organizer": {"self": True},
        "attendees": [{"self": True, "responseStatus": "declined"}],
    }


def test_una_respuesta_dentro_de_la_mascara_no_tiene_claves_de_mas():
    responses = [
        (CALENDARS, {"items": [{"id": "a", "selected": True}, {"id": "b"}]}),
        (EVENTS, {"items": [clean_event()], "nextPageToken": "t"}),
    ]
    assert unexpected_keys(responses) == (set(), 3)


def test_detecta_una_clave_de_mas_en_el_evento_en_su_organizador_y_en_sus_asistentes():
    for place, event in [
        ("evento", {**clean_event(), "description": "d"}),
        ("organizador", {**clean_event(), "organizer": {"self": True, "email": "e"}}),
        ("asistente", {**clean_event(), "attendees": [{"self": True, "email": "e"}]}),
        ("hora", {**clean_event(), "start": {"dateTime": "d", "extra": 1}}),
    ]:
        extra, _ = unexpected_keys([(EVENTS, {"items": [event]})])
        assert extra, place


def test_detecta_una_clave_de_mas_en_los_calendarios_y_en_la_respuesta_entera():
    item = {"id": "a", "summary": "nombre", "description": "d", "backgroundColor": "#000"}
    extra, _ = unexpected_keys([(CALENDARS, {"items": [item]})])
    assert extra == {"description", "backgroundColor"}  # `summary` sí se pide (1.5-e)
    extra, _ = unexpected_keys([(EVENTS, {"items": [], "timeZone": "Europe/Madrid", "kind": "k"})])
    assert extra == {"timeZone", "kind"}


def test_solo_nombres_nunca_valores():
    extra, _ = unexpected_keys(
        [(EVENTS, {"items": [{**clean_event(), "description": "valor-privado"}]})]
    )
    assert extra == {"description"}
    assert "valor-privado" not in repr(extra)


def test_cuenta_los_elementos_revisados_para_que_el_contrato_no_pase_en_vacio():
    _, looked_at = unexpected_keys([(CALENDARS, {"items": []}), (EVENTS, {"items": []})])
    assert looked_at == 0
