"""El ciclo de un latido: primera pasada, estado, decisión y segunda pasada (6.3 del diseño).

`decide` es un espía con tope: con la primera pasada parada no se llama, y tampoco se leen las
tareas ni APEX.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta

import pytest
from calendar_data import SECRET_TITLE, FakeGoogleService, calendar_item, paged, timed_event
from conftest import FixedClock
from heartbeat_data import (
    MADRID,
    SECRET_TEXT,
    SpyDecide,
    add_notice,
    all_day,
    calendar_with,
    event,
    intentions,
    limits,
    madrid,
    outgoing,
    sources,
    task,
)

from jimic.heartbeat.cycle import run_cycle
from jimic.heartbeat.limits import WaitingNotices
from jimic.memory.database import open_database
from jimic.tools.calendar import CalendarDay, GoogleCalendarReader
from jimic.tools.todoist import TaskList

MEETING = event(madrid("2030-01-07T10:00"), minutes=60)


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def cycle(conn, now, srcs=None, spy=None, lim=None, its=None, queue=None):
    moment = now if isinstance(now, datetime) else madrid(now)
    return asyncio.run(
        run_cycle(
            clock=FixedClock(moment),
            timezone=MADRID,
            limits=lim if lim is not None else limits(),
            intentions=its if its is not None else intentions(),
            sources=srcs if srcs is not None else sources(),
            conn=conn,
            queue=queue if queue is not None else WaitingNotices(),
            decide=spy if spy is not None else SpyDecide(),
        )
    )


def reads(srcs) -> tuple[int, int, int]:
    return srcs.calendar.reads, srcs.todoist.reader.reads, srcs.apex.reads


# --- La primera pasada corta el ciclo sin modelo ----------------------------------------------


def test_a_las_23_30_el_ciclo_acaba_sin_llamar_a_decide(conn):
    """Pilla un silencio que se mira después del modelo, o que lee las fuentes antes de parar."""
    spy, srcs = SpyDecide(), sources()
    result = cycle(conn, "2030-01-07T23:30", srcs, spy)
    assert result.stopped == "quiet_hours"
    assert spy.calls == 0
    assert reads(srcs) == (0, 0, 0)


@pytest.mark.parametrize("now", ["10:00:00", "10:30:00", "10:59:59"])
def test_durante_un_evento_no_se_llama_a_decide(conn, now):
    """Pilla una primera pasada que no mira los eventos en curso (`[inicio, fin)`)."""
    spy, srcs = SpyDecide(), sources(calendar_with(MEETING))
    result = cycle(conn, f"2030-01-07T{now}", srcs, spy)
    assert result.stopped == "in_event"
    assert spy.calls == 0
    assert reads(srcs) == (1, 0, 0)


def test_al_acabar_el_evento_ya_se_llama(conn):
    spy = SpyDecide()
    assert cycle(conn, "2030-01-07T11:00", sources(calendar_with(MEETING)), spy).stopped is None
    assert spy.calls == 1


def test_con_silent_during_events_false_durante_un_evento_si_se_llama(conn):
    """Lo que diga `limites.yaml` manda: Pedro puede quitar el silencio de los eventos."""
    spy = SpyDecide()
    lim = limits(silent_during_events=False)
    result = cycle(conn, "2030-01-07T10:30", sources(calendar_with(MEETING)), spy, lim=lim)
    assert result.stopped is None
    assert spy.calls == 1


def test_un_evento_de_todo_el_dia_no_esta_en_curso(conn):
    """Pilla que un evento de todo el día (un cumpleaños, unas vacaciones) silencie el día."""
    spy = SpyDecide()
    cycle(conn, "2030-01-07T10:30", sources(calendar_with(all_day(date(2030, 1, 7)))), spy)
    assert spy.calls == 1


def test_un_evento_rechazado_no_para_el_ciclo(conn):
    spy = SpyDecide()
    declined = event(madrid("2030-01-07T10:00"), declined=True)
    cycle(conn, "2030-01-07T10:30", sources(calendar_with(declined)), spy)
    assert spy.calls == 1


def test_un_evento_del_calendario_todoist_no_para_el_ciclo(conn):
    """Las tareas con hora que Todoist copia a su calendario no son eventos (1.5-e)."""
    service = FakeGoogleService(
        paged([calendar_item("propio", name="Personal"), calendar_item("t", name="Todoist")]),
        {
            "propio": paged([]),
            "t": paged(
                [timed_event("Gimnasio", "2030-01-07T10:00:00+01:00", "2030-01-07T11:00:00+01:00")]
            ),
        },
    )
    spy = SpyDecide()
    result = cycle(
        conn, "2030-01-07T10:30", sources(GoogleCalendarReader(lambda: service, MADRID)), spy
    )
    assert result.stopped is None
    assert spy.calls == 1
    assert spy.states[0].calendar.excluded_calendar_count == 1


def test_sin_calendario_no_se_para_y_el_estado_lo_dice(conn):
    """Sin calendario no se sabe si hay evento: sigue, y el modelo sabe que falta (1.7-c)."""
    spy = SpyDecide()
    cycle(conn, "2030-01-07T10:30", sources(CalendarDay.unavailable("auth")), spy)
    assert spy.calls == 1
    assert spy.states[0].sources()["calendar"] == {
        "available": False,
        "unavailable_reason": "auth",
    }


# --- El cupo ----------------------------------------------------------------------------------


def fill(conn, count: int) -> None:
    for n in range(count):
        add_notice(conn, f"otro_{n}", madrid(f"2030-01-07T{8 + n:02}:00"), n + 1)


def test_el_mensaje_proactivo_9_del_dia_se_bloquea(conn):
    """Con 8 enviados ni el cierre cabe: el ciclo acaba sin modelo."""
    fill(conn, 8)
    spy, srcs = SpyDecide(outgoing("cierre_del_dia")), sources()
    result = cycle(conn, "2030-01-07T21:30", srcs, spy)
    assert result.stopped == "daily_cap"
    assert spy.calls == 0
    assert reads(srcs) == (0, 0, 0)


def test_con_7_enviados_solo_cabe_el_cierre(conn):
    fill(conn, 7)
    gym = cycle(conn, "2030-01-07T20:00", spy=SpyDecide(outgoing("insistir_gimnasio")))
    close = cycle(conn, "2030-01-07T21:30", spy=SpyDecide(outgoing("cierre_del_dia")))
    assert [(v.action, v.reason) for v in gym.verdicts] == [("block", "close_slot")]
    assert [(v.action, v.reason) for v in close.verdicts] == [("send", "ok")]


def test_un_aviso_repetido_se_para_en_la_segunda_pasada(conn):
    add_notice(conn, "regar", madrid("2030-01-07T09:00"), 1)
    spy = SpyDecide(outgoing("regar"))
    result = cycle(conn, "2030-01-07T12:00", spy=spy)
    assert spy.calls == 1
    assert [(v.action, v.reason) for v in result.verdicts] == [("block", "repeated")]


# --- Un día entero de latidos -----------------------------------------------------------------


def heartbeats(day: date) -> list[datetime]:
    """Every 15 minutes of real time, from local midnight to the next one."""
    start = datetime.combine(day, time.min, tzinfo=MADRID).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=MADRID).astimezone(UTC)
    beats = []
    while start < end:
        beats.append(start.astimezone(MADRID))
        start += timedelta(minutes=15)
    return beats


@pytest.mark.parametrize(
    ("day", "beats"),
    [(date(2030, 1, 7), 96), (date(2030, 3, 31), 92), (date(2030, 10, 27), 100)],
)
def test_de_los_latidos_de_un_dia_64_llaman_a_decide(conn, day, beats):
    """De 7:00 a 23:00 hay 64 latidos, también los días del cambio de hora (23 y 25 h): pilla
    un borde del silencio desplazado o una hora local mal calculada."""
    spy = SpyDecide()
    moments = heartbeats(day)
    for moment in moments:
        cycle(conn, moment, spy=spy)
    assert len(moments) == beats
    assert spy.calls == 64
    assert spy.states[0].now.time() == time(7, 0)
    assert spy.states[-1].now.time() == time(22, 45)


# --- La cola de agrupar -----------------------------------------------------------------------


def test_lo_que_espera_se_descarta_al_entrar_en_silencio(conn, caplog):
    """Pilla un aviso que sobrevive a la noche y sale a las 7:00 con datos de ayer."""
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T22:40"), 1)
    queue = WaitingNotices()
    waiting = cycle(conn, "2030-01-07T22:45", spy=SpyDecide(outgoing("basura")), queue=queue)
    assert [v.action for v in waiting.verdicts] == ["wait"]
    with caplog.at_level(logging.INFO, logger="jimic"):
        result = cycle(conn, "2030-01-07T23:00", queue=queue)
    assert (result.stopped, result.dropped) == ("quiet_hours", 1)
    assert queue.items == ()
    assert "dropped 1 waiting notices (quiet_hours)" in caplog.text
    assert SECRET_TEXT not in caplog.text


def test_sin_horas_de_silencio_lo_de_ayer_se_descarta_al_cambiar_de_dia(conn):
    lim = limits(quiet_hours={"from": "07:00", "to": "07:00"})
    queue = WaitingNotices()
    queue.add(outgoing("basura"), date(2030, 1, 7))
    result = cycle(conn, "2030-01-08T00:15", lim=lim, queue=queue)
    assert (result.stopped, result.dropped) == (None, 1)
    assert queue.items == ()


def test_sin_propuesta_la_segunda_pasada_suelta_la_cola(conn):
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T10:00"), 1)
    queue = WaitingNotices()
    cycle(conn, "2030-01-07T10:15", spy=SpyDecide(outgoing("basura")), queue=queue)
    result = cycle(conn, "2030-01-07T10:30", spy=SpyDecide(None), queue=queue)
    assert [(v.action, v.reason, v.intents) for v in result.verdicts] == [
        ("send", "released", ("basura",))
    ]


# --- Privacidad -------------------------------------------------------------------------------


def test_ni_los_logs_ni_el_repr_llevan_titulos_textos_ni_nombres_de_calendarios(conn, caplog):
    secret_calendar = "Calendario-de-Pedro-inventado-4410"
    service = FakeGoogleService(
        paged([calendar_item("propio", name=secret_calendar), calendar_item("t", name="Todoist")]),
        {
            "propio": paged(
                [
                    timed_event(
                        SECRET_TITLE, "2030-01-07T12:00:00+01:00", "2030-01-07T13:00:00+01:00"
                    )
                ]
            )
        },
    )
    srcs = sources(
        GoogleCalendarReader(lambda: service, MADRID),
        TaskList(available=True, tasks=[task("t1")]),
    )
    with caplog.at_level(logging.DEBUG, logger="jimic"):
        result = cycle(conn, "2030-01-07T10:30", srcs, SpyDecide(outgoing("insistir_gimnasio")))
        silent = cycle(conn, "2030-01-07T23:30", srcs, SpyDecide())
    assert result.state is not None and SECRET_TITLE in result.state.to_json()
    assert silent.stopped == "quiet_hours"
    for secret in (SECRET_TITLE, SECRET_TEXT, secret_calendar, "Todoist"):
        assert secret not in caplog.text
        assert secret not in repr(result)
        assert secret not in repr(silent)
