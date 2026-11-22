"""El latido entero: el ciclo, el envío, `decisions`, `notices`, el gasto y los avisos del sistema.

El modelo es `FakeMessages` (con tope) detrás del `HaikuDecider` de verdad; Telegram, un
`FakeSender`; el reloj, uno fijo que el test mueve. Sin red.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta

import anthropic
import httpx2
import pytest
from calendar_data import SECRET_TITLE
from conftest import FixedClock
from heartbeat_data import (
    BINS,
    CLOSE,
    GYM,
    MADRID,
    OFFERS,
    PLAN,
    PLANTS,
    SECRET_REASON,
    SECRET_TEXT,
    FakeMessages,
    FakeSender,
    add_notice,
    answer,
    api_down,
    api_message,
    calendar_with,
    event,
    intentions,
    limits,
    madrid,
    picks,
    sources,
    task,
)

from jimic.clock import local_day_bounds
from jimic.heartbeat.beat import API_DOWN_TEXT, Heartbeat, next_beat
from jimic.heartbeat.decide import HaikuDecider
from jimic.memory.database import open_database
from jimic.memory.notices import sent_between
from jimic.tools.calendar import CalendarDay
from jimic.tools.todoist import TaskList

MODEL = "claude-haiku-5-5"
MONDAY = date(2030, 1, 7)
MEETING = event(madrid("2030-01-07T10:00"), minutes=60)
ALL = (PLAN, CLOSE, GYM, PLANTS, BINS, OFFERS)


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


class World:
    """A heartbeat with its doubles, and a clock the test moves."""

    def __init__(self, conn, fake=None, calendar=None, lim=None, items=ALL, tasks=None):
        self.conn = conn
        self.clock = FixedClock(madrid("2030-01-07T00:00"))
        self.fake = fake if fake is not None else FakeMessages()
        self.sender = FakeSender()
        its = intentions(*items)
        self.heartbeat = Heartbeat(
            clock=self.clock,
            timezone=MADRID,
            limits=lim if lim is not None else limits(),
            intentions=its,
            sources=sources(calendar, tasks),
            conn=conn,
            decider=HaikuDecider(self.fake.create, MODEL, its),
            sender=self.sender,
        )

    def at(self, moment: str | datetime) -> None:
        self.clock.moment = moment if isinstance(moment, datetime) else madrid(moment)
        asyncio.run(self.heartbeat.beat())

    def rows(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT created_at, speak, intent, reason FROM decisions ORDER BY id"
        ).fetchall()

    def reasons(self) -> list[str]:
        return [row[3] for row in self.rows()]

    def notices(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT intent, telegram_message_id FROM notices ORDER BY id"
        ).fetchall()


def heartbeats(day: date) -> list[datetime]:
    """Every 15 minutes of the local `day`, counted in UTC (absolute time)."""
    start, end = (moment.astimezone(UTC) for moment in local_day_bounds(day, MADRID))
    moments = []
    while start < end:
        moments.append(start.astimezone(MADRID))
        start += timedelta(minutes=15)
    return moments


# --- El día de ejemplo ---------------------------------------------------------------------------


def test_el_dia_de_ejemplo_deja_96_filas_y_llama_60_veces_al_modelo(conn):
    """De 0:00 a 24:00 con el silencio de serie (23:00-7:00) y una reunión de 10:00 a 11:00.

    Pilla un latido cortado que no deja fila (saldrían 60) o uno cortado que llama al modelo.
    """
    world = World(conn, calendar=calendar_with(MEETING))
    calls_before: dict[str, int] = {}
    for moment in heartbeats(MONDAY):
        calls_before[moment.strftime("%H:%M")] = world.fake.calls
        world.at(moment)
    assert len(heartbeats(MONDAY)) == 96
    assert len(world.rows()) == 96
    assert world.fake.calls == 60
    reasons = world.reasons()
    assert reasons.count("quiet_hours") == 32
    assert reasons.count("in_event") == 4
    assert reasons.count(SECRET_REASON) == 60
    # Los cortados no llaman: en la reunión y en el silencio el contador no se mueve.
    assert calls_before["10:00"] == calls_before["11:00"]
    assert calls_before["00:00"] == calls_before["07:00"] == 0
    assert world.sender.texts == []
    (usage_rows,) = conn.execute("SELECT COUNT(*) FROM model_usage").fetchone()
    assert usage_rows == 60


def test_la_fila_del_latido_lleva_su_momento_en_utc(conn):
    world = World(conn)
    world.at("2030-01-07T23:30")
    assert world.rows() == [("2030-01-07T22:30:00+00:00", 0, None, "quiet_hours")]


def test_un_latido_que_habla_envia_registra_y_lo_dice_en_el_motivo(conn):
    world = World(conn, FakeMessages(api_message(answer(True, "ofertas", SECRET_TEXT))))
    world.at("2030-01-07T16:30")
    assert world.sender.texts == [SECRET_TEXT]
    assert world.notices() == [("ofertas", 1001)]
    (row,) = world.rows()
    assert row[1:] == (1, "ofertas", f"{SECRET_REASON} [send ok]")


def test_lo_que_para_la_segunda_pasada_queda_en_el_motivo(conn):
    """El gimnasio con su tope (3) agotado: Haiku habla, el código lo para."""
    for hour in (12, 13, 14):
        add_notice(conn, "insistir_gimnasio", madrid(f"2030-01-07T{hour}:00"), hour)
    world = World(conn, FakeMessages(api_message(answer(True, "insistir_gimnasio", "otra"))))
    world.at("2030-01-07T16:00")
    assert world.sender.texts == []
    assert world.rows()[0][1:] == (1, "insistir_gimnasio", f"{SECRET_REASON} [block insist_cap]")


def test_un_agrupado_es_un_mensaje_con_una_fila_por_intencion_y_el_mismo_id(conn):
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T10:00"), 1)
    replies = iter(
        [
            api_message(answer(True, "regar", "Riega")),
            api_message(answer(True, "basura", "Basura")),
        ]
    )
    world = World(conn, FakeMessages(lambda request: next(replies)))
    world.at("2030-01-07T10:15")
    assert world.sender.texts == []
    world.at("2030-01-07T10:30")
    assert world.sender.texts == ["Riega\n\nBasura"]
    assert world.notices()[1:] == [("regar", 1001), ("basura", 1001)]
    assert world.reasons()[1].endswith("[send released]")


def test_telegram_rechaza_el_envio_no_deja_aviso_y_el_motivo_lo_dice(conn):
    world = World(conn, FakeMessages(api_message(answer(True, "ofertas", SECRET_TEXT))))
    world.sender.fail = "Forbidden"
    world.at("2030-01-07T16:30")
    assert world.notices() == []
    assert world.reasons() == [f"{SECRET_REASON} [send ok send_failed:Forbidden]"]


@pytest.mark.parametrize(
    ("intent", "task_id"), [("plan_del_dia", "1.14"), ("cierre_del_dia", "1.16")]
)
def test_el_plan_y_el_cierre_de_haiku_no_salen_ni_van_a_notices(conn, intent, task_id):
    """Decisión 1.8-b. Pilla el plan que sale sin la 1.14."""
    world = World(conn, FakeMessages(api_message(answer(True, intent, SECRET_TEXT))))
    world.at("2030-01-07T08:00")
    assert world.sender.texts == []
    assert world.notices() == []
    assert world.rows()[0][1:] == (1, intent, f"{SECRET_REASON} (pendiente: {task_id})")


# --- La API caída ------------------------------------------------------------------------------


def test_con_la_api_caida_el_aviso_de_las_16_30_sale_con_su_plantilla(conn):
    world = World(conn, FakeMessages(api_down()))
    world.at("2030-01-07T16:30")
    assert world.sender.texts == ["⏰ Mirar las ofertas", API_DOWN_TEXT]
    assert world.notices() == [("ofertas", 1001)]
    assert world.rows()[0][1:] == (1, "ofertas", "api_down [send ok]")


def test_con_la_api_caida_solo_a_las_16_45_el_aviso_ya_no_sale(conn):
    """Pilla una plantilla que sale en todos los latidos tras su hora. A las 16:30 la API
    responde (y no habla): así no es la segunda pasada la que la para por repetida."""
    replies = iter([api_message(answer()), api_down()])
    world = World(conn, FakeMessages(lambda request: next(replies)))
    world.at("2030-01-07T16:30")
    world.at("2030-01-07T16:45")
    assert world.notices() == []
    assert world.sender.texts == [API_DOWN_TEXT]
    assert world.reasons()[1] == "api_down"


def test_con_la_api_caida_el_plan_y_el_cierre_no_salen(conn):
    world = World(conn, FakeMessages(api_down()))
    world.at("2030-01-07T08:00")
    world.at("2030-01-07T22:00")
    assert world.notices() == []
    assert world.sender.texts == [API_DOWN_TEXT]


def test_el_aviso_de_api_caida_sale_una_vez_al_dia(conn):
    world = World(conn, FakeMessages(api_down()), items=(PLAN, CLOSE))
    for moment in ("2030-01-07T09:00", "2030-01-07T12:00", "2030-01-08T09:00"):
        world.at(moment)
    assert world.sender.texts == [API_DOWN_TEXT, API_DOWN_TEXT]


def test_el_aviso_de_api_caida_espera_si_cae_en_silencio(conn):
    """Silencio de 13:00 a 15:00: el latido de las 12:45 tarda y acaba a las 13:00. El aviso
    espera y sale en el primer latido fuera del silencio, el de las 15:00."""
    world = World(
        conn, items=(PLAN, CLOSE), lim=limits(quiet_hours={"from": "13:00", "to": "15:00"})
    )

    def slow_and_down(request):
        world.clock.moment = madrid("2030-01-07T13:00")
        return api_down()

    world.fake.reply = slow_and_down
    world.at("2030-01-07T12:45")
    assert world.sender.texts == []
    world.at("2030-01-07T14:45")
    assert world.sender.texts == []
    world.fake.reply = api_message(answer())
    world.at("2030-01-07T15:00")
    assert world.sender.texts == [API_DOWN_TEXT]


def test_el_aviso_que_espera_al_dia_siguiente_ya_no_sale(conn):
    """Lo aprobado en el plan: un aviso diario que sigue esperando el día siguiente caduca."""
    world = World(conn, items=(PLAN, CLOSE))

    def slow_and_down(request):
        world.clock.moment = madrid("2030-01-07T23:00")
        return api_down()

    world.fake.reply = slow_and_down
    world.at("2030-01-07T22:45")
    world.fake.reply = api_message(answer())
    world.at("2030-01-08T07:00")
    assert world.sender.texts == []


def test_los_avisos_del_sistema_no_van_a_notices_ni_gastan_cupo(conn):
    """Con 7 enviados y el cierre pendiente, el aviso sale igual y el cupo no cambia. Pilla un
    aviso del sistema que escribe en `notices`."""
    for n in range(7):
        add_notice(conn, f"otro_{n}", madrid(f"2030-01-07T{8 + n:02}:00"), n + 1)
    world = World(conn, FakeMessages(api_down()), items=(PLAN, CLOSE))
    world.at("2030-01-07T16:30")
    assert world.sender.texts == [API_DOWN_TEXT]
    sent = sent_between(conn, *local_day_bounds(MONDAY, MADRID))
    assert sent.message_count == 7
    assert len(world.notices()) == 7


# --- Fuentes, presupuesto y gasto ---------------------------------------------------------------


def test_el_calendario_sin_permiso_avisa_una_vez_con_el_comando(conn):
    down = CalendarDay.unavailable("auth")
    world = World(conn, calendar=down)
    world.at("2030-01-07T09:00")
    world.at("2030-01-07T09:15")
    assert world.sender.texts == [
        "⚠️ Ahora mismo no puedo leer el calendario.\n"
        "Para volver a darle permiso: jimic google autorizar"
    ]


def test_dos_fuentes_caidas_van_en_un_solo_aviso(conn):
    world = World(
        conn, calendar=CalendarDay.unavailable("network"), tasks=TaskList.unavailable("timeout")
    )
    world.at("2030-01-07T09:00")
    assert world.sender.texts == [
        "⚠️ Ahora mismo no puedo leer el calendario ni las tareas de Todoist."
    ]


def add_spend(conn, day: date, usd: float) -> None:
    conn.execute(
        "INSERT INTO model_usage (day, model, input_tokens, output_tokens, cache_read_tokens,"
        " cache_write_tokens, usd) VALUES (?, ?, 0, 0, 0, 0, ?)",
        (day.isoformat(), MODEL, usd),
    )


def test_el_aviso_del_presupuesto_sale_al_80_por_ciento_una_vez_al_mes(conn):
    """24 $ de 30 al 80 %. Las llamadas del test no cuestan nada (uso a cero), así que el
    gasto del mes es el que se apunta: 23,99 no avisa; 24,00 sí, y una sola vez en el mes.
    El mes siguiente vuelve a avisar."""
    world = World(
        conn, FakeMessages(api_message(answer(), {"input_tokens": 0, "output_tokens": 0}))
    )
    add_spend(conn, date(2030, 1, 1), 23.99)
    world.at("2030-01-07T09:00")
    assert world.sender.texts == []
    add_spend(conn, date(2030, 1, 2), 0.01)
    world.at("2030-01-07T09:15")
    world.at("2030-01-07T09:30")
    assert world.sender.texts == [
        "💸 Llevas 24.00 $ gastados este mes, el 80% del objetivo de 30.00 $."
    ]
    add_spend(conn, date(2030, 2, 1), 24.0)
    world.at("2030-02-04T09:00")
    assert len(world.sender.texts) == 2


def test_cada_llamada_suma_en_el_contador_tambien_las_no_validas(conn):
    world = World(conn, FakeMessages(api_message(answer(True, "inventada", "x"))))
    world.at("2030-01-07T09:00")
    (rows, usd) = conn.execute("SELECT COUNT(*), SUM(usd) FROM model_usage").fetchone()
    assert rows == 1 and usd == pytest.approx(0.00032, abs=1e-12)
    assert world.reasons() == ["invalid_decision:no_valid_pick (descartado: 1:unknown_intent)"]


# --- Varias intenciones en un latido (1.8-d) ---------------------------------------------------


def test_tres_avisos_a_las_1630_salen_en_un_mensaje_y_cuentan_uno(conn):
    """Un mensaje con un bloque por intención, tres filas con el mismo `telegram_message_id`, y
    el cupo baja en uno. Pilla un lote que cuenta 3 en el cupo (versión incorrecta 8)."""
    world = World(
        conn,
        FakeMessages(api_message(picks(("ofertas", "Uno"), ("regar", "Dos"), ("basura", "Tres")))),
    )
    world.at("2030-01-07T16:30")
    assert world.sender.texts == ["Uno\n\nDos\n\nTres"]
    assert world.notices() == [("ofertas", 1001), ("regar", 1001), ("basura", 1001)]
    sent = sent_between(conn, *local_day_bounds(MONDAY, MADRID))
    assert sent.message_count == 1
    world.fake.reply = api_message(answer())
    world.at("2030-01-07T16:45")
    assert world.fake.requests[-1]["messages"][0]["content"].count('"messages_left":6') == 1
    (_, speak, intent, reason) = world.rows()[0]
    assert (speak, intent) == (1, "ofertas,regar,basura")
    assert reason == f"{SECRET_REASON} [send ok]"


def test_el_plan_en_un_lote_no_sale_y_los_demas_si(conn):
    world = World(
        conn, FakeMessages(api_message(picks(("plan_del_dia", None), ("basura", "Fuera"))))
    )
    world.at("2030-01-07T08:00")
    assert world.sender.texts == ["Fuera"]
    assert world.notices() == [("basura", 1001)]


def test_un_lote_de_cuatro_no_sale(conn):
    four = [("ofertas", "a"), ("regar", "b"), ("basura", "c"), ("insistir_gimnasio", "d")]
    world = World(conn, FakeMessages(api_message(picks(*four))))
    world.at("2030-01-07T16:30")
    assert world.sender.texts == [] and world.notices() == []
    assert world.reasons() == ["invalid_decision:too_many_picks"]


# --- Uno a la vez, errores y logs ---------------------------------------------------------------


def test_si_el_anterior_sigue_en_marcha_este_se_salta_y_queda_registrado(conn):
    """Pilla dos latidos a la vez: el segundo llamaría al modelo otra vez."""
    world = World(conn)
    world.fake.gate = asyncio.Event()

    async def scenario():
        world.clock.moment = madrid("2030-01-07T09:00")
        first = asyncio.create_task(world.heartbeat.beat())
        while world.fake.calls == 0:
            await asyncio.sleep(0)
        world.clock.moment = madrid("2030-01-07T09:15")
        await world.heartbeat.beat()
        assert world.fake.gate is not None
        world.fake.gate.set()
        await first

    asyncio.run(scenario())
    assert world.fake.calls == 1
    assert world.reasons() == ["overlap", SECRET_REASON]


def test_un_error_inesperado_deja_su_fila_y_suelta_la_guarda(conn):
    world = World(conn, FakeMessages(lambda request: ZeroDivisionError("con datos secretos")))

    world.at("2030-01-07T09:00")
    world.fake.reply = api_message(answer())
    world.at("2030-01-07T09:15")
    assert world.reasons() == ["error:ZeroDivisionError", SECRET_REASON]


def test_los_logs_no_llevan_motivos_mensajes_ni_titulos(conn, caplog):
    caplog.set_level(logging.DEBUG)
    tasks = TaskList(available=True, tasks=[task("t1", SECRET_TITLE)])
    world = World(
        conn, FakeMessages(api_message(answer(True, "ofertas", SECRET_TEXT))), tasks=tasks
    )
    world.at("2030-01-07T16:30")
    world.fake.reply = api_down()
    world.at("2030-01-07T19:00")
    world.fake.reply = lambda request: ZeroDivisionError(SECRET_TEXT)
    world.at("2030-01-07T20:00")
    for secret in (SECRET_REASON, SECRET_TEXT, SECRET_TITLE, "Mirar las ofertas", "Riega"):
        assert secret not in caplog.text
    assert "heartbeat sent ofertas" in caplog.text


# --- El primer latido -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "first"),
    [
        ("2030-01-07T10:07:00", "2030-01-07T10:15:00"),
        ("2030-01-07T10:15:00", "2030-01-07T10:15:00"),
        ("2030-01-07T10:15:01", "2030-01-07T10:30:00"),
        ("2030-01-07T23:50:00", "2030-01-08T00:00:00"),
    ],
)
def test_el_primer_latido_cae_en_el_siguiente_cuarto_de_hora(now, first):
    assert next_beat(madrid(now)) == madrid(first)


def test_el_primer_latido_tras_el_cambio_de_hora_sigue_en_un_cuarto():
    """2030-03-31: a las 2:00 pasan a ser las 3:00. Desde la 1:50, el siguiente es las 3:00."""
    assert next_beat(madrid("2030-03-31T01:50")).isoformat() == "2030-03-31T03:00:00+02:00"
    assert next_beat(madrid("2030-03-31T01:50")).time() == time(3, 0)


def test_si_falla_el_contador_lo_que_esperaba_sale_igual_y_hay_una_sola_fila(conn, monkeypatch):
    """Revisión propia, hallazgos 2 y 5: la segunda pasada ya sacó de la cola lo que esperaba;
    un fallo al contar el gasto no puede perderlo ni dejar dos filas en el latido."""
    add_notice(conn, "plan_del_dia", madrid("2030-01-07T10:00"), 1)
    replies = iter([api_message(answer(True, "regar", "Riega")), api_message(answer())])
    world = World(conn, FakeMessages(lambda request: next(replies)))
    world.at("2030-01-07T10:15")

    def broken(*args, **kwargs):
        raise RuntimeError("la base falla")

    monkeypatch.setattr("jimic.heartbeat.beat.usage.record", broken)
    world.at("2030-01-07T10:30")
    assert world.sender.texts == ["Riega"]
    assert len(world.rows()) == 2
    assert world.reasons()[1] == f"{SECRET_REASON} [send released]"


def test_si_falla_el_registro_de_un_aviso_del_sistema_no_hay_otra_fila(conn, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("la base falla")

    monkeypatch.setattr("jimic.heartbeat.beat.system_notices.detect", broken)
    monkeypatch.setattr("jimic.heartbeat.beat.system_notices.pending", broken)
    world = World(conn, FakeMessages(api_down()))
    world.at("2030-01-07T16:30")
    assert world.reasons() == ["api_down [send ok]"]
    assert world.sender.texts == ["⏰ Mirar las ofertas"]


@pytest.mark.parametrize(
    ("status", "error", "text"),
    [
        (401, "AuthenticationError", "rechaza las peticiones (AuthenticationError)"),
        (404, "NotFoundError", "rechaza las peticiones (NotFoundError)"),
        (429, "RateLimitError", "no responde"),
        (529, "OverloadedError", "no responde"),
    ],
)
def test_una_clave_o_un_modelo_mal_puestos_no_se_avisan_como_api_caida(conn, status, error, text):
    """Revisión propia, hallazgo 4: un 401 o un 404 no se arreglan esperando; el aviso lo dice y
    apunta al `.env`. El plan B sigue igual (plantillas a su hora)."""
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request)
    raised = getattr(anthropic, error)("fallo", response=response, body=None)
    world = World(conn, FakeMessages(raised))
    world.at("2030-01-07T16:30")
    template, notice = world.sender.texts
    assert template == "⏰ Mirar las ofertas"
    assert text in notice
    assert ("CLAVE_ANTHROPIC" in notice) is (status in (401, 404))


def test_el_aviso_del_presupuesto_dice_el_porcentaje_de_verdad(conn):
    """Revisión propia, hallazgo 6: una llamada puede saltar del 70 % al 130 %; el texto no
    puede decir «el 80 %»."""
    world = World(
        conn, FakeMessages(api_message(answer(), {"input_tokens": 0, "output_tokens": 0}))
    )
    add_spend(conn, date(2030, 1, 1), 39.0)
    world.at("2030-01-07T09:00")
    assert world.sender.texts == [
        "💸 Llevas 39.00 $ gastados este mes, el 130% del objetivo de 30.00 $."
    ]
