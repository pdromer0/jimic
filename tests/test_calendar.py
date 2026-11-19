"""El lector de Calendar: solo lectura, máscara mínima, cambios de hora y fallos con código."""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httplib2
import pytest
from calendar_data import (
    MAX_CALLS,
    SECRET_TITLE,
    FakeGoogleService,
    FixedCalendarReader,
    RunawayDouble,
    all_day_event,
    calendar_item,
    paged,
    timed_event,
)
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpMockSequence

from jimic.tools.calendar import (
    CalendarDay,
    CalendarEvent,
    CalendarReader,
    GoogleCalendarReader,
)
from jimic.tools.failures import ToolFailure

LOGGER = "jimic.tools.calendar"
MADRID = ZoneInfo("Europe/Madrid")
MONDAY = date(2030, 1, 7)

# Escritas a mano, no importadas: si la constante del código cambia, el test lo nota.
EVENT_MASK = (
    "nextPageToken,items(summary,start,end,status,organizer/self,attendees(self,responseStatus))"
)
CALENDAR_MASK = "nextPageToken,items(id,selected,summary)"


def make_reader(service: Any) -> GoogleCalendarReader:
    return GoogleCalendarReader(lambda: service, MADRID)


def read(service: Any, day: date = MONDAY) -> CalendarDay:
    return asyncio.run(make_reader(service).read_day(day))


def one_calendar(*events: dict[str, Any]) -> FakeGoogleService:
    return FakeGoogleService(paged([calendar_item("cal-1")]), {"cal-1": paged(list(events))})


def public_members(cls: type) -> set[str]:
    return {name for name in dir(cls) if not name.startswith("_")}


def http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), SECRET_TITLE.encode())


# --- Solo lectura ----------------------------------------------------------------------------


def test_el_lector_no_tiene_ninguna_funcion_que_escriba():
    """Pilla un método de escritura en la interfaz, en el lector de Google o en el doble."""
    assert public_members(CalendarReader) == {"read_day"}
    assert public_members(GoogleCalendarReader) == {"read_day"}
    assert public_members(FixedCalendarReader) == {"read_day"}


def test_el_lector_solo_pide_listados():
    """Pilla una petición que no sea un `list`: ni insert, ni patch, ni delete."""
    service = one_calendar(
        timed_event("Reunión", "2030-01-07T10:00:00+01:00", "2030-01-07T11:00:00+01:00")
    )
    read(service)
    assert {kind for kind, _ in service.requests} == {"calendarList", "events"}


def test_el_doble_de_datos_fijos_devuelve_el_dia_que_se_le_da():
    day = CalendarDay(available=False, unavailable_reason="auth")
    assert asyncio.run(FixedCalendarReader(day).read_day(MONDAY)) is day


# --- La máscara: lo que no se pide no se descarga --------------------------------------------


def test_la_mascara_de_los_eventos_es_exacta_y_no_pide_descripcion():
    """Pilla un campo de más (descripción, ubicación, correos) en la petición de eventos."""
    service = one_calendar()
    read(service)
    (arguments,) = service.requests_of("events")
    assert arguments["fields"] == EVENT_MASK
    for forbidden in ("description", "location", "email", "displayName", "attachments"):
        assert forbidden not in arguments["fields"]


def test_la_mascara_de_los_calendarios_pide_solo_id_visible_y_nombre():
    """Pilla un campo de más en la lista de calendarios: el nombre, solo para saltar el de
    Todoist (1.5-e)."""
    service = one_calendar()
    read(service)
    (arguments,) = service.requests_of("calendarList")
    assert arguments["fields"] == CALENDAR_MASK


def test_la_peticion_pide_eventos_sueltos_ordenados_y_del_dia():
    """Pilla `singleEvents` o `orderBy` perdidos, o un día mal acotado."""
    service = one_calendar()
    read(service)
    (arguments,) = service.requests_of("events")
    assert arguments["calendarId"] == "cal-1"
    assert arguments["singleEvents"] is True
    assert arguments["orderBy"] == "startTime"
    assert arguments["timeMin"] == "2030-01-07T00:00:00+01:00"
    assert arguments["timeMax"] == "2030-01-08T00:00:00+01:00"


@pytest.mark.parametrize(
    ("day", "time_min", "time_max", "hours"),
    [
        (date(2030, 3, 31), "2030-03-31T00:00:00+01:00", "2030-04-01T00:00:00+02:00", 23),
        (date(2030, 10, 27), "2030-10-27T00:00:00+02:00", "2030-10-28T00:00:00+01:00", 25),
    ],
)
def test_los_dias_del_cambio_de_hora_se_acotan_de_medianoche_a_medianoche(
    day, time_min, time_max, hours
):
    """Pilla sumar 24 h en vez de un día de calendario: el día dura 23 o 25 horas."""
    service = one_calendar()
    read(service, day)
    (arguments,) = service.requests_of("events")
    assert (arguments["timeMin"], arguments["timeMax"]) == (time_min, time_max)
    span = datetime.fromisoformat(time_max) - datetime.fromisoformat(time_min)
    assert span == timedelta(hours=hours)


def test_con_el_cliente_real_la_url_lleva_la_mascara_sin_descripcion():
    """Pilla un fallo que el doble no ve: la petición real, con el cliente de Google sin red."""
    http = HttpMockSequence(
        [
            ({"status": "200"}, json.dumps({"items": [calendar_item("cal-1")]})),
            ({"status": "200"}, json.dumps({"items": []})),
        ]
    )
    service = build("calendar", "v3", http=http, static_discovery=True)
    day = read(service)
    assert day.available
    calendars_uri, events_uri = (call[0] for call in http.request_sequence)
    assert parse_qs(urlparse(calendars_uri).query)["fields"] == [CALENDAR_MASK]
    query = parse_qs(urlparse(events_uri).query)
    assert query["fields"] == [EVENT_MASK]
    assert query["singleEvents"] == ["true"]
    assert query["orderBy"] == ["startTime"]
    assert "description" not in events_uri


# --- Qué calendarios --------------------------------------------------------------------------


def test_solo_se_leen_los_calendarios_marcados_como_visibles():
    """Pilla leer uno no visible: `selected` ausente o falso no cuenta."""
    service = FakeGoogleService(
        paged([calendar_item("visible", True), calendar_item("falso", False)]),
        {"visible": paged([])},
    )
    day = read(service)
    assert [a["calendarId"] for a in service.requests_of("events")] == ["visible"]
    assert day.calendar_count == 1


def test_un_calendario_sin_el_campo_selected_no_es_visible():
    """Google no manda `selected` en los no visibles: «ausente» es «no visible»."""
    service = FakeGoogleService(
        paged([calendar_item("visible", True), calendar_item("sin-campo", None)]),
        {"visible": paged([])},
    )
    read(service)
    assert [a["calendarId"] for a in service.requests_of("events")] == ["visible"]


def test_sin_calendarios_visibles_el_dia_esta_disponible_y_vacio():
    """El lector no lo trata como fallo (lo hace el contrato): cero calendarios, cero eventos."""
    service = FakeGoogleService(paged([calendar_item("oculto", None)]))
    day = read(service)
    assert day.available
    assert day.calendar_count == 0
    assert day.events == []
    assert service.requests_of("events") == []


def test_los_calendarios_en_varias_paginas_se_recorren_todos():
    service = FakeGoogleService(
        paged([calendar_item("uno")], [calendar_item("dos")]),
        {"uno": paged([]), "dos": paged([])},
    )
    day = read(service)
    assert day.calendar_count == 2
    assert service.requests_of("calendarList")[1]["pageToken"] == "token-1"


# --- El calendario «Todoist» (1.5-e) ---------------------------------------------------------

TODOIST_NAME = "Todoist"


def with_todoist_calendar(**todoist: Any) -> FakeGoogleService:
    """A visible calendar of Pedro's and a visible «Todoist» one, each with one event."""
    return FakeGoogleService(
        paged([calendar_item("propio", name="Personal"), calendar_item("de-todoist", **todoist)]),
        {
            "propio": paged(
                [timed_event("Reunión", "2030-01-07T10:00:00+01:00", "2030-01-07T11:00:00+01:00")]
            ),
            "de-todoist": paged(
                [timed_event("Gimnasio", "2030-01-07T12:00:00+01:00", "2030-01-07T13:00:00+01:00")]
            ),
        },
    )


def test_el_calendario_todoist_no_aporta_eventos_y_se_cuenta_aparte():
    """Pilla leer el calendario que llena la integración de Todoist: sus tareas saldrían dos
    veces, y como eventos."""
    service = with_todoist_calendar(name=TODOIST_NAME)
    day = read(service)
    assert [a["calendarId"] for a in service.requests_of("events")] == ["propio"]
    assert [event.title for event in day.events] == ["Reunión"]
    assert day.calendar_count == 1
    assert day.excluded_calendar_count == 1


@pytest.mark.parametrize("name", ["todoist", "Todoist ", "Todoist (trabajo)", "Mi Todoist"])
def test_solo_el_nombre_exacto_es_el_de_todoist(name):
    """Pilla comparar sin distinguir mayúsculas o por contener: otro calendario se perdería."""
    day = read(with_todoist_calendar(name=name))
    assert day.calendar_count == 2
    assert day.excluded_calendar_count == 0


def test_un_calendario_todoist_no_visible_ni_se_lee_ni_se_cuenta():
    day = read(with_todoist_calendar(name=TODOIST_NAME, selected=None))
    assert day.calendar_count == 1
    assert day.excluded_calendar_count == 0


def test_un_nombre_de_calendario_que_no_es_texto_es_formato_invalido():
    day = read(with_todoist_calendar(name=5))
    assert day.unavailable_reason == "invalid_format"


def test_el_nombre_raro_de_un_calendario_oculto_no_tumba_el_dia():
    """Pilla mirar el nombre antes que `selected`: un calendario que no se lee no puede romper."""
    day = read(with_todoist_calendar(name=5, selected=None))
    assert day.available
    assert day.calendar_count == 1


def test_el_nombre_de_los_calendarios_no_sale_del_lector(caplog):
    """Pilla un nombre de calendario en el `repr`, el JSON o los logs: solo se compara."""
    secret = "Calendario-de-Pedro-secreto-4410"
    service = FakeGoogleService(
        paged([calendar_item("propio", name=secret), calendar_item("t", name=TODOIST_NAME)]),
        {"propio": paged([])},
    )
    failing = FakeGoogleService(
        paged([calendar_item("propio", name=secret), calendar_item("t", name=TODOIST_NAME)]),
        {"propio": paged([])},
        error=http_error(500),
    )
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        day = read(service)
        broken = read(failing)
    assert day.excluded_calendar_count == 1
    for name in (secret, TODOIST_NAME):
        assert name not in repr(day)
        assert name not in day.to_json()
        assert name not in repr(broken)
        assert name not in caplog.text


# --- Los eventos ------------------------------------------------------------------------------


def test_un_evento_con_hora_llega_en_la_zona_de_la_configuracion():
    """Pilla no convertir la zona: 09:00 UTC es 10:00 en Madrid en enero y 11:00 en julio."""
    service = one_calendar(
        timed_event("Reunión", "2030-01-07T09:00:00Z", "2030-01-07T10:00:00Z"),
    )
    (event,) = read(service).events
    assert event.start == datetime(2030, 1, 7, 10, 0, tzinfo=MADRID)
    assert event.start.utcoffset() == timedelta(hours=1)  # type: ignore[union-attr] # es un datetime
    assert event.end == datetime(2030, 1, 7, 11, 0, tzinfo=MADRID)
    assert not event.all_day


def test_un_evento_con_otro_desfase_se_pasa_a_madrid():
    service = one_calendar(
        timed_event("Llamada", "2030-07-01T10:00:00-05:00", "2030-07-01T11:00:00-05:00"),
    )
    (event,) = read(service, date(2030, 7, 1)).events
    assert event.start == datetime(2030, 7, 1, 17, 0, tzinfo=MADRID)
    assert event.start.utcoffset() == timedelta(hours=2)  # type: ignore[union-attr] # es un datetime


def test_un_evento_sin_desfase_usa_su_zona():
    """Google permite la hora sin desfase si el evento dice su `timeZone`."""
    raw = timed_event("Clase", "2030-01-07T09:00:00", "2030-01-07T10:00:00")
    raw["start"]["timeZone"] = "UTC"
    raw["end"]["timeZone"] = "UTC"
    (event,) = read(one_calendar(raw)).events
    assert event.start == datetime(2030, 1, 7, 10, 0, tzinfo=MADRID)


def test_un_evento_de_todo_el_dia_llega_como_fecha_y_marcado():
    """Pilla un evento de todo el día convertido en uno con horas."""
    service = one_calendar(all_day_event("Festivo", "2030-01-07", "2030-01-08"))
    (event,) = read(service).events
    assert event.all_day
    assert event.start == date(2030, 1, 7)
    assert not isinstance(event.start, datetime)
    assert event.end == date(2030, 1, 8)
    assert not isinstance(event.end, datetime)


def test_el_evento_rechazado_llega_marcado_y_no_se_descarta():
    """Pilla un rechazo sin marcar: lo decide quien lo use (la 1.7)."""
    declined = timed_event(
        "Rechazado",
        "2030-01-07T10:00:00+01:00",
        "2030-01-07T11:00:00+01:00",
        attendees=[
            {"self": True, "responseStatus": "declined"},
            {"responseStatus": "accepted"},
        ],
    )
    accepted = timed_event(
        "Aceptado",
        "2030-01-07T12:00:00+01:00",
        "2030-01-07T13:00:00+01:00",
        attendees=[{"self": True, "responseStatus": "accepted"}],
    )
    pending = timed_event("Sin responder", "2030-01-07T14:00:00+01:00", "2030-01-07T15:00:00+01:00")
    events = read(one_calendar(declined, accepted, pending)).events
    assert [(e.title, e.declined) for e in events] == [
        ("Rechazado", True),
        ("Aceptado", False),
        ("Sin responder", False),
    ]


def test_que_otro_asistente_rechace_no_marca_el_evento():
    """Pilla confundir «alguien rechazó» con «Pedro rechazó»."""
    raw = timed_event(
        "Equipo",
        "2030-01-07T10:00:00+01:00",
        "2030-01-07T11:00:00+01:00",
        attendees=[{"responseStatus": "declined"}, {"self": True, "responseStatus": "accepted"}],
    )
    (event,) = read(one_calendar(raw)).events
    assert not event.declined


def test_organizer_is_me_sale_de_organizer_self():
    mine = timed_event(
        "Mío", "2030-01-07T10:00:00+01:00", "2030-01-07T11:00:00+01:00", organizer={"self": True}
    )
    other = timed_event("Ajeno", "2030-01-07T12:00:00+01:00", "2030-01-07T13:00:00+01:00")
    events = read(one_calendar(mine, other)).events
    assert [(e.title, e.organizer_is_me) for e in events] == [("Mío", True), ("Ajeno", False)]


def test_un_evento_cancelado_se_descarta_aunque_llegue_sin_horas():
    """Pilla un cancelado que se cuela, o que rompe el formato por no traer `start`."""
    cancelled_without_times = {"status": "cancelled"}
    cancelled = timed_event(
        "Cancelado", "2030-01-07T10:00:00+01:00", "2030-01-07T11:00:00+01:00", status="cancelled"
    )
    alive = timed_event(
        "Vivo", "2030-01-07T12:00:00+01:00", "2030-01-07T13:00:00+01:00", status="confirmed"
    )
    day = read(one_calendar(cancelled_without_times, cancelled, alive))
    assert day.available
    assert [e.title for e in day.events] == ["Vivo"]


def test_un_evento_sin_titulo_llega_con_titulo_vacio():
    service = one_calendar(
        timed_event(None, "2030-01-07T10:00:00+01:00", "2030-01-07T11:00:00+01:00")
    )
    (event,) = read(service).events
    assert event.title == ""


def test_los_eventos_de_varios_calendarios_se_ordenan_por_inicio_y_los_de_todo_el_dia_primero():
    """Pilla el orden por calendario en vez de por hora; los empates conservan su orden."""
    service = FakeGoogleService(
        paged([calendar_item("a"), calendar_item("b")]),
        {
            "a": paged(
                [
                    timed_event(
                        "A 11:00", "2030-01-07T11:00:00+01:00", "2030-01-07T12:00:00+01:00"
                    ),
                    timed_event(
                        "A 15:00", "2030-01-07T15:00:00+01:00", "2030-01-07T16:00:00+01:00"
                    ),
                ]
            ),
            "b": paged(
                [
                    all_day_event("B todo el día", "2030-01-07", "2030-01-08"),
                    timed_event(
                        "B 11:00", "2030-01-07T11:00:00+01:00", "2030-01-07T11:30:00+01:00"
                    ),
                    timed_event(
                        "B 09:00", "2030-01-07T09:00:00+01:00", "2030-01-07T10:00:00+01:00"
                    ),
                ]
            ),
        },
    )
    titles = [e.title for e in read(service).events]
    assert titles == ["B todo el día", "B 09:00", "A 11:00", "B 11:00", "A 15:00"]


def test_los_eventos_en_varias_paginas_se_recorren_todos():
    service = FakeGoogleService(
        paged([calendar_item("cal-1")]),
        {
            "cal-1": paged(
                [timed_event("Uno", "2030-01-07T10:00:00+01:00", "2030-01-07T11:00:00+01:00")],
                [timed_event("Dos", "2030-01-07T12:00:00+01:00", "2030-01-07T13:00:00+01:00")],
            )
        },
    )
    assert [e.title for e in read(service).events] == ["Uno", "Dos"]
    assert service.requests_of("events")[1]["pageToken"] == "token-1"


# --- Paginación con tope ----------------------------------------------------------------------


def pages_of_events(count: int) -> list[dict[str, Any]]:
    return paged(*([[]] * count))


def test_diez_paginas_de_eventos_se_leen():
    """El tope es 10: con diez páginas todo va bien."""
    service = FakeGoogleService(paged([calendar_item("cal-1")]), {"cal-1": pages_of_events(10)})
    assert read(service).available
    assert len(service.requests_of("events")) == 10


def test_once_paginas_de_eventos_son_un_error_claro_y_no_un_bucle():
    """Pilla la paginación sin tope: la página 11 no se pide."""
    service = FakeGoogleService(paged([calendar_item("cal-1")]), {"cal-1": pages_of_events(11)})
    day = read(service)
    assert day.unavailable_reason == "too_many_pages"
    assert len(service.requests_of("events")) == 10


def test_once_paginas_de_calendarios_son_un_error_claro():
    service = FakeGoogleService(paged(*([[calendar_item("x", None)]] * 11)))
    assert read(service).unavailable_reason == "too_many_pages"
    assert len(service.requests_of("calendarList")) == 10


def test_el_doble_lanza_un_error_claro_si_se_le_llama_en_bucle():
    """El tope del doble: el error no es una `Exception`, así que el lector no lo disimula."""
    service = FakeGoogleService(paged([]))
    with pytest.raises(RunawayDouble, match=str(MAX_CALLS)):
        for _ in range(MAX_CALLS + 1):
            service.calendarList().list().execute()


# --- Fallos con código ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (http_error(401), "auth"),
        (http_error(403), "http_403"),
        (http_error(503), "http_503"),
        (RefreshError("invalid_grant"), "auth"),
        (RefreshError("servidor ocupado", retryable=True), "network"),
        (ToolFailure("auth"), "auth"),
        (TimeoutError("timed out"), "timeout"),
        (ConnectionError("sin red"), "network"),
        (TransportError("sin red"), "network"),
        (httplib2.ServerNotFoundError("sin dns"), "network"),
        (RuntimeError(SECRET_TITLE), "unexpected:RuntimeError"),
    ],
)
def test_cada_fallo_da_su_codigo_y_el_lector_no_lanza(error, code):
    """Pilla un fallo que lance al servicio o un código que dependa del mensaje."""
    day = read(FakeGoogleService(paged([]), error=error))
    assert not day.available
    assert day.unavailable_reason == code
    assert day.events == []


def test_un_fallo_al_conectar_tampoco_lanza():
    def connect():
        raise ToolFailure("auth")

    day = asyncio.run(GoogleCalendarReader(connect, MADRID).read_day(MONDAY))
    assert day.unavailable_reason == "auth"


@pytest.mark.parametrize(
    "raw",
    [
        "no es un diccionario",
        {"start": {"dateTime": "2030-01-07T10:00:00"}, "end": {"dateTime": "2030-01-07T11:00:00"}},
        {"start": {"dateTime": "ayer"}, "end": {"dateTime": "2030-01-07T11:00:00Z"}},
        {"start": {"date": "2030-13-45"}, "end": {"date": "2030-01-08"}},
        {"start": {"date": "2030-01-07"}, "end": {"dateTime": "2030-01-07T11:00:00Z"}},
        {"start": {}, "end": {}},
        {"summary": 5, "start": {"date": "2030-01-07"}, "end": {"date": "2030-01-08"}},
    ],
)
def test_un_evento_que_no_tiene_la_forma_pedida_es_invalid_format(raw):
    """Pilla aceptar en silencio algo que no es lo que pide la máscara."""
    day = read(one_calendar(raw))
    assert day.unavailable_reason == "invalid_format"


def test_si_falla_un_calendario_falla_el_dia_entero():
    """Un día con eventos que faltan parecería libre: es todo o nada."""
    service = FakeGoogleService(
        paged([calendar_item("bueno"), calendar_item("malo")]),
        {
            "bueno": paged(
                [timed_event("Ok", "2030-01-07T10:00:00+01:00", "2030-01-07T11:00:00+01:00")]
            ),
            "malo": [{"items": ["roto"]}],
        },
    )
    day = read(service)
    assert not day.available
    assert day.events == []


# --- Una lectura cada vez ---------------------------------------------------------------------


def test_dos_lecturas_a_la_vez_no_comparten_el_cliente_al_mismo_tiempo():
    """Pilla un cliente HTTP compartido entre hilos: `httplib2` no lo soporta."""
    service = one_calendar(
        timed_event("Reunión", "2030-01-07T10:00:00+01:00", "2030-01-07T11:00:00+01:00")
    )
    service.delay = 0.03
    reader = make_reader(service)

    async def both():
        return await asyncio.gather(reader.read_day(MONDAY), reader.read_day(MONDAY))

    first, second = asyncio.run(both())
    assert first.available and second.available
    assert len(first.events) == len(second.events) == 1
    assert service.max_in_flight == 1
    assert len(service.requests) == 4  # las dos lecturas se hicieron enteras


# --- Avisos y logs ----------------------------------------------------------------------------


def make_flaky(errors: list[Exception | None]):
    """A reader whose Nth read fails with `errors[N]` (or works if it is `None`)."""
    calls = iter(errors)

    def connect():
        error = next(calls)
        if error is not None:
            raise error
        return one_calendar()

    return GoogleCalendarReader(connect, MADRID)


def warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_el_aviso_sale_una_sola_vez_mientras_siga_roto(caplog):
    """Pilla un aviso en cada lectura."""
    reader = make_flaky([ToolFailure("timeout")] * 3)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(3):
            asyncio.run(reader.read_day(MONDAY))
    assert warnings(caplog) == ["Calendar unavailable (timeout)"]


def test_si_se_recupera_y_vuelve_a_fallar_avisa_otra_vez(caplog):
    reader = make_flaky([ToolFailure("timeout"), None, ToolFailure("timeout")])
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(3):
            asyncio.run(reader.read_day(MONDAY))
    assert len(warnings(caplog)) == 2


def test_el_aviso_de_permiso_dice_que_comando_lanzar(caplog):
    """El aviso de `auth` nombra el comando; el de un fallo de red no."""
    with caplog.at_level(logging.INFO, logger=LOGGER):
        asyncio.run(make_flaky([ToolFailure("auth")]).read_day(MONDAY))
        asyncio.run(make_flaky([ToolFailure("timeout")]).read_day(MONDAY))
    auth, timeout = warnings(caplog)
    assert auth == "Calendar unavailable (auth); run `jimic google autorizar`"
    assert "autorizar" not in timeout


def test_un_calendario_que_funciona_no_deja_avisos(caplog):
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        read(one_calendar())
    assert caplog.records == []


def test_ningun_titulo_llega_a_los_logs_ni_a_los_codigos_ni_al_repr(caplog):
    """Pilla un título en un log, en un código de fallo o en el `repr` de un evento o un día."""
    working = one_calendar(
        timed_event(SECRET_TITLE, "2030-01-07T10:00:00+01:00", "2030-01-07T11:00:00+01:00")
    )
    failing = FakeGoogleService(paged([]), error=http_error(500))
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        day = read(working)
        broken = read(failing)
    assert day.events[0].title == SECRET_TITLE  # el título sí llega a quien lo usa
    assert SECRET_TITLE not in caplog.text
    assert SECRET_TITLE not in repr(day)
    assert SECRET_TITLE not in repr(day.events[0])
    assert SECRET_TITLE not in repr(broken)
    assert broken.unavailable_reason == "http_500"


def test_ni_los_ids_de_los_calendarios_llegan_a_los_logs(caplog):
    service = FakeGoogleService(
        paged([calendar_item("id-de-calendario-secreto-55")]),
        {"id-de-calendario-secreto-55": paged([])},
        error=None,
    )
    broken = FakeGoogleService(paged([]), error=http_error(500))
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        read(service)
        read(broken)
    assert "id-de-calendario-secreto-55" not in caplog.text


def test_el_dia_dice_su_forma_en_json_sin_campos_vacios():
    day = CalendarDay.unavailable("auth")
    assert json.loads(day.to_json()) == {
        "available": False,
        "unavailable_reason": "auth",
        "calendar_count": 0,
        "excluded_calendar_count": 0,
        "events": [],
    }


def test_un_evento_es_inmutable():
    event = CalendarEvent(
        title="x",
        start=date(2030, 1, 7),
        end=date(2030, 1, 8),
        all_day=True,
        organizer_is_me=False,
        declined=False,
    )
    with pytest.raises(ValueError):
        event.title = "y"
