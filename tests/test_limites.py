"""Los límites en dos pasadas: silencio, evento en curso, cupo con el hueco del cierre, tope por
intención, no repetir y agrupar (1.3 y 6.3 del diseño, decisiones 1.7-a y 1.7-b)."""

import asyncio
import logging
from datetime import date, time

import pytest
from heartbeat_data import (
    CLOSE,
    GYM,
    MADRID,
    MONDAY,
    PLAN,
    PLANTS,
    SECRET_TEXT,
    add_notice,
    calendar_with,
    event,
    intentions,
    limits,
    madrid,
    outgoing,
)

from jimic.clock import local_day_bounds
from jimic.heartbeat.limits import (
    Quota,
    WaitingNotices,
    event_in_progress,
    first_pass,
    in_window,
    second_pass,
)
from jimic.memory.database import open_database
from jimic.memory.notices import sent_between
from jimic.memory.schemas import Window


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def sent(conn, day: date = MONDAY):
    return sent_between(conn, *local_day_bounds(day, MADRID))


def fill(conn, count: int, *intents: str) -> None:
    """`count` proactive messages this Monday, one an hour from 8:00, with these intents."""
    names = list(intents) + [f"otro_{n}" for n in range(count - len(intents))]
    for n, name in enumerate(names):
        add_notice(conn, name, madrid(f"2030-01-07T{8 + n:02}:00"), n + 1)


def window(start: str, end: str) -> Window:
    return Window.model_validate({"from": start, "to": end})


def run(conn, now: str, proposal=None, queue=None, its=None, lim=None, day=MONDAY):
    queue = queue if queue is not None else WaitingNotices()
    return second_pass(
        madrid(now),
        proposal,
        its if its is not None else intentions(),
        lim if lim is not None else limits(),
        sent(conn, day),
        queue,
    )


def only(verdicts):
    (verdict,) = verdicts
    return verdict.action, verdict.reason, verdict.intents


# --- Horas de silencio ------------------------------------------------------------------------

NIGHT = window("23:00", "07:00")


@pytest.mark.parametrize(
    ("hour", "silent"),
    [("22:59", False), ("23:00", True), ("23:30", True), ("06:59", True), ("07:00", False)],
)
def test_bordes_de_las_horas_de_silencio(hour, silent):
    """Pilla un borde mal cerrado: la ventana es `[23:00, 07:00)`."""
    assert in_window(time.fromisoformat(hour), NIGHT) is silent


@pytest.mark.parametrize(
    ("hour", "silent"), [("12:59", False), ("13:00", True), ("14:59", True), ("15:00", False)]
)
def test_una_ventana_que_no_cruza_la_medianoche(hour, silent):
    assert in_window(time.fromisoformat(hour), window("13:00", "15:00")) is silent


def test_una_ventana_con_el_mismo_principio_y_fin_esta_vacia():
    assert not any(in_window(time(h), window("07:00", "07:00")) for h in range(24))


# --- Evento en curso --------------------------------------------------------------------------


def test_un_evento_con_hora_esta_en_curso_de_su_inicio_a_su_fin_sin_incluirlo():
    day = calendar_with(event(madrid("2030-01-07T10:00"), minutes=60))
    assert not event_in_progress(day, madrid("2030-01-07T09:59:59"))
    assert event_in_progress(day, madrid("2030-01-07T10:00"))
    assert event_in_progress(day, madrid("2030-01-07T10:59:59"))
    assert not event_in_progress(day, madrid("2030-01-07T11:00"))


def test_un_evento_rechazado_no_esta_en_curso():
    day = calendar_with(event(madrid("2030-01-07T10:00"), declined=True))
    assert not event_in_progress(day, madrid("2030-01-07T10:30"))


# --- Primera pasada ---------------------------------------------------------------------------


def first(now: str, quota: Quota, day=None, **changes):
    reads = []

    async def read_calendar():
        reads.append(1)
        return day if day is not None else calendar_with()

    result = asyncio.run(first_pass(madrid(now), limits(**changes), quota, read_calendar))
    return result.stop, len(reads)


FREE = Quota(max_messages=8, sent=0, close_pending=True)


def test_en_silencio_para_sin_leer_el_calendario():
    assert first("2030-01-07T23:30", FREE) == ("quiet_hours", 0)


def test_sin_sitio_ni_para_el_cierre_para_sin_leer_el_calendario():
    """Pilla parar antes de tiempo: con 7 de 8 aún cabe el cierre y la pasada sigue."""
    assert first("2030-01-07T12:00", Quota(8, 8, close_pending=True)) == ("daily_cap", 0)
    assert first("2030-01-07T12:00", Quota(8, 7, close_pending=True)) == (None, 1)


def test_con_tope_cero_la_primera_pasada_para_siempre():
    assert first("2030-01-07T12:00", Quota(0, 0, close_pending=True)) == ("daily_cap", 0)


# --- Cupo con el hueco del cierre (1.7-a) -----------------------------------------------------


def test_con_tope_8_el_octavo_que_no_es_cierre_se_bloquea(conn):
    """Pilla un cupo sin hueco: con 7 enviados, lo que no es cierre ya no cabe."""
    fill(conn, 7)
    assert only(run(conn, "2030-01-07T20:00", outgoing("insistir_gimnasio"))) == (
        "block",
        "close_slot",
        ("insistir_gimnasio",),
    )


def test_el_cierre_como_octavo_entra(conn):
    fill(conn, 7)
    assert only(run(conn, "2030-01-07T21:30", outgoing("cierre_del_dia"))) == (
        "send",
        "ok",
        ("cierre_del_dia",),
    )


def test_el_noveno_se_bloquea_siempre(conn):
    """Pilla un tope que deja pasar el noveno, sea cierre o no."""
    fill(conn, 8)
    assert only(run(conn, "2030-01-07T21:30", outgoing("cierre_del_dia")))[:2] == (
        "block",
        "daily_cap",
    )
    assert only(run(conn, "2030-01-07T21:30", outgoing("insistir_gimnasio")))[:2] == (
        "block",
        "daily_cap",
    )


def test_enviado_el_cierre_el_hueco_se_libera(conn):
    """Pilla un hueco que sigue reservado cuando el cierre ya salió: se perdería un mensaje."""
    fill(conn, 7, "cierre_del_dia")
    assert only(run(conn, "2030-01-07T22:30", outgoing("insistir_gimnasio")))[:2] == ("send", "ok")


def test_sin_cierre_activo_no_hay_hueco(conn):
    fill(conn, 7)
    its = intentions(PLAN, {**CLOSE, "enabled": False}, GYM)
    assert only(run(conn, "2030-01-07T20:00", outgoing("insistir_gimnasio"), its=its))[:2] == (
        "send",
        "ok",
    )


def test_la_cuenta_del_cupo_con_el_hueco():
    assert (Quota(8, 6, True).left_for_others, Quota(8, 6, True).close_slot_kept) == (1, True)
    assert (Quota(8, 7, True).left_for_others, Quota(8, 7, True).close_slot_kept) == (0, True)
    assert (Quota(8, 8, True).left_for_others, Quota(8, 8, True).close_slot_kept) == (0, False)
    assert (Quota(8, 7, False).left_for_others, Quota(8, 7, False).close_slot_kept) == (1, False)


# --- Tope por intención y no repetir ----------------------------------------------------------


def test_un_aviso_repetido_se_para_en_la_segunda_pasada(conn):
    """Pilla un `notice` que sale dos veces el mismo día."""
    add_notice(conn, "regar", madrid("2030-01-07T09:00"), 1)
    assert only(run(conn, "2030-01-07T12:00", outgoing("regar"))) == (
        "block",
        "repeated",
        ("regar",),
    )


@pytest.mark.parametrize("intent", ["plan_del_dia", "cierre_del_dia"])
def test_el_plan_y_el_cierre_salen_una_vez_al_dia(conn, intent):
    add_notice(conn, intent, madrid("2030-01-07T09:00"), 1)
    assert only(run(conn, "2030-01-07T12:00", outgoing(intent)))[:2] == ("block", "repeated")


def test_un_aviso_del_dia_anterior_no_cuenta_hoy(conn):
    add_notice(conn, "regar", madrid("2030-01-06T20:00"), 1)
    assert only(run(conn, "2030-01-07T12:00", outgoing("regar")))[:2] == ("send", "ok")


def test_una_key_task_se_para_en_su_tope(conn):
    """Pilla un tope de insistencia que deja pasar el cuarto aviso: `min(3, 3)`."""
    for n in range(2):
        add_notice(conn, "insistir_gimnasio", madrid(f"2030-01-07T{12 + n}:00"), n + 1)
    assert only(run(conn, "2030-01-07T14:00", outgoing("insistir_gimnasio")))[:2] == ("send", "ok")
    add_notice(conn, "insistir_gimnasio", madrid("2030-01-07T14:00"), 3)
    assert only(run(conn, "2030-01-07T15:00", outgoing("insistir_gimnasio")))[:2] == (
        "block",
        "insist_cap",
    )


@pytest.mark.parametrize(("max_per_day", "max_insist", "cap"), [(5, 2, 2), (1, 3, 1), (0, 3, 0)])
def test_el_tope_de_una_key_task_es_el_menor_de_los_dos(conn, max_per_day, max_insist, cap):
    its = intentions(PLAN, CLOSE, {**GYM, "max_per_day": max_per_day})
    lim = limits(max_insist_per_task=max_insist)
    for n in range(cap):
        add_notice(conn, "insistir_gimnasio", madrid(f"2030-01-07T{10 + n}:00"), n + 1)
    verdict = only(run(conn, "2030-01-07T18:00", outgoing("insistir_gimnasio"), its=its, lim=lim))
    assert verdict[:2] == ("block", "insist_cap")


def test_un_aviso_fuera_de_sus_dias_se_bloquea(conn):
    """El riego toca lunes y jueves: el martes, no."""
    assert only(run(conn, "2030-01-08T12:00", outgoing("regar"), day=date(2030, 1, 8)))[:2] == (
        "block",
        "not_today",
    )


def test_una_intencion_desconocida_o_desactivada_se_bloquea(conn):
    its = intentions(PLAN, CLOSE, {**PLANTS, "enabled": False})
    assert (
        only(run(conn, "2030-01-07T12:00", outgoing("inventada"), its=its))[1] == "unknown_intent"
    )
    assert only(run(conn, "2030-01-07T12:00", outgoing("regar"), its=its))[1] == "disabled"


def test_sin_propuesta_y_sin_cola_no_hay_veredictos(conn):
    assert run(conn, "2030-01-07T12:00") == []


# --- Agrupar (1.7-b) --------------------------------------------------------------------------


def test_un_aviso_a_los_15_minutos_del_ultimo_proactivo_espera(conn):
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T10:00"), 1)
    queue = WaitingNotices()
    assert only(run(conn, "2030-01-07T10:15", outgoing("regar"), queue)) == (
        "wait",
        "group_window",
        ("regar",),
    )
    assert [item.intent for item in queue.items] == ["regar"]


@pytest.mark.parametrize(("now", "action"), [("10:29:59", "wait"), ("10:30:00", "send")])
def test_la_ventana_de_30_minutos_acaba_a_los_30_justos(conn, now, action):
    """Pilla `<=` en la ventana: a las 10:30 ya sale."""
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T10:00"), 1)
    assert only(run(conn, f"2030-01-07T{now}", outgoing("regar")))[0] == action


def test_dos_que_esperan_salen_juntos_en_el_primer_latido_tras_la_ventana_y_cuentan_uno(conn):
    """Pilla soltar la cola uno a uno (gasta dos del cupo) o no soltarla nunca."""
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T10:00"), 1)
    queue = WaitingNotices()
    run(conn, "2030-01-07T10:15", outgoing("regar"), queue)
    assert only(run(conn, "2030-01-07T10:15", outgoing("basura"), queue))[:2] == (
        "wait",
        "group_window",
    )
    assert only(run(conn, "2030-01-07T10:30", None, queue)) == (
        "send",
        "released",
        ("regar", "basura"),
    )
    assert queue.items == ()


def test_un_aviso_con_la_ventana_pasada_se_suma_a_los_que_esperan(conn):
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T10:00"), 1)
    queue = WaitingNotices()
    run(conn, "2030-01-07T10:15", outgoing("regar"), queue)
    assert only(run(conn, "2030-01-07T10:45", outgoing("basura"), queue)) == (
        "send",
        "released",
        ("regar", "basura"),
    )


def test_una_key_task_no_espera_nunca(conn):
    """Pilla que lo urgente espere en la ventana: el gimnasio no se agrupa."""
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T10:00"), 1)
    assert only(run(conn, "2030-01-07T10:15", outgoing("insistir_gimnasio"))) == (
        "send",
        "ok",
        ("insistir_gimnasio",),
    )


def test_si_sale_un_urgente_la_cola_sigue_esperando(conn):
    """Agrupar nunca manda dos mensajes en un latido: el urgente reinicia la ventana."""
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T10:00"), 1)
    queue = WaitingNotices()
    run(conn, "2030-01-07T10:15", outgoing("regar"), queue)
    assert only(run(conn, "2030-01-07T10:30", outgoing("insistir_gimnasio"), queue))[:2] == (
        "send",
        "ok",
    )
    assert [item.intent for item in queue.items] == ["regar"]


def test_un_aviso_sin_proactivos_hoy_sale_solo_sin_esperar(conn):
    assert only(run(conn, "2030-01-07T10:15", outgoing("regar")))[:2] == ("send", "ok")


def test_el_mismo_aviso_dos_veces_en_la_cola_se_bloquea(conn):
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T10:00"), 1)
    queue = WaitingNotices()
    run(conn, "2030-01-07T10:15", outgoing("regar"), queue)
    assert only(run(conn, "2030-01-07T10:20", outgoing("regar"), queue))[:2] == (
        "block",
        "already_waiting",
    )
    assert len(queue.items) == 1


def test_al_soltar_la_cola_se_reaplica_el_cupo(conn):
    """Pilla soltar sin mirar el cupo: mientras esperaba, un urgente gastó el último hueco libre."""
    fill(conn, 6)  # el último, a las 13:00
    queue = WaitingNotices()
    assert only(run(conn, "2030-01-07T13:15", outgoing("basura"), queue))[0] == "wait"
    assert only(run(conn, "2030-01-07T13:20", outgoing("insistir_gimnasio"), queue))[0] == "send"
    add_notice(conn, "insistir_gimnasio", madrid("2030-01-07T13:20"), 7)  # lo apunta la 1.8
    assert only(run(conn, "2030-01-07T13:50", None, queue)) == (
        "block",
        "close_slot",
        ("basura",),
    )
    assert queue.items == ()


def test_con_ventana_cero_no_se_agrupa(conn):
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T10:00"), 1)
    lim = limits(group_window="0m")
    assert only(run(conn, "2030-01-07T10:00:01", outgoing("regar"), lim=lim))[:2] == ("send", "ok")


def test_la_cola_descarta_lo_de_otro_dia():
    queue = WaitingNotices()
    queue.add(outgoing("regar"), MONDAY)
    queue.add(outgoing("basura"), date(2030, 1, 8))
    assert queue.drop_other_days(date(2030, 1, 8)) == 1
    assert [item.intent for item in queue.items] == ["basura"]


# --- Privacidad -------------------------------------------------------------------------------


def test_los_logs_y_el_repr_de_los_veredictos_no_llevan_el_texto(conn, caplog):
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T10:00"), 1)
    queue = WaitingNotices()
    with caplog.at_level(logging.DEBUG, logger="jimic"):
        verdicts = run(conn, "2030-01-07T10:15", outgoing("regar"), queue)
        verdicts += run(conn, "2030-01-07T10:15", outgoing("insistir_gimnasio"), queue)
    assert "regar" in caplog.text  # el id de la intención sí
    assert SECRET_TEXT not in caplog.text
    assert SECRET_TEXT not in repr(verdicts)
    assert SECRET_TEXT not in repr(queue)
