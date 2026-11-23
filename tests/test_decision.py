"""La decisión del latido con Haiku: la petición, la respuesta, lo no válido y el plan B.

El modelo es un doble (`FakeMessages`) con tope que devuelve mensajes de verdad del SDK; no hay
red. Los textos y motivos son inventados y fáciles de buscar en un log.
"""

import asyncio
import logging
from datetime import date

import anthropic
import httpx2
import pytest
from conftest import VALID_ENV
from heartbeat_data import (
    BINS,
    CLOSE,
    GYM,
    OFFERS,
    PLAN,
    PLANTS,
    SECRET_REASON,
    SECRET_TEXT,
    FakeMessages,
    add_notice,
    answer,
    api_down,
    api_message,
    day_state,
    intentions,
    madrid,
    picks,
)
from pydantic import SecretStr

from jimic.config import Settings
from jimic.heartbeat.decide import (
    API_URL,
    CACHE_TTL,
    EFFORT,
    PLAN_TEMPLATE,
    HaikuDecider,
    build_client,
    fixed_block,
    usage_of,
)
from jimic.memory import key_marks
from jimic.memory.database import open_database
from jimic.memory.notices import record_handover
from jimic.memory.usage import TokenUsage

MODEL = "claude-haiku-5-5"
FAKE_KEY = VALID_ENV["CLAVE_ANTHROPIC"]
ALL = (PLAN, CLOSE, GYM, PLANTS, BINS, OFFERS)


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def decide(conn, now: str, fake: FakeMessages, items=ALL):
    its = intentions(*items)
    state = day_state(conn, madrid(now), its)
    return asyncio.run(HaikuDecider(fake.create, MODEL, its)(state)), state


# --- La petición --------------------------------------------------------------------------------


def test_la_peticion_lleva_el_bloque_fijo_con_cache_y_el_estado_al_final(conn):
    """Pilla una petición sin `cache_control`, con el estado dentro del bloque fijo (rompería
    la caché en cada latido) o sin la salida estructurada."""
    fake = FakeMessages()
    _, state = decide(conn, "2030-01-07T11:15", fake)
    (request,) = fake.requests
    (block,) = request["system"]
    assert block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert CACHE_TTL == "1h"
    assert state.to_json() not in block["text"]
    assert request["messages"] == [{"role": "user", "content": state.to_json()}]
    assert request["model"] == MODEL
    assert request["output_config"]["effort"] == EFFORT == "low"
    schema = request["output_config"]["format"]
    assert schema["type"] == "json_schema"
    assert schema["schema"]["required"] == ["speak", "reason", "picks"]  # 1.8-d
    assert schema["schema"]["$defs"]["Pick"]["required"] == ["intent", "message"]
    assert "buttons" not in schema["schema"]["properties"]  # 1.8-a


def test_el_bloque_fijo_lleva_las_intenciones_de_hoy_y_no_cambia_en_el_dia(conn):
    """Pilla un bloque que cambia de un latido a otro (la caché no leería nunca) o que lleva
    un aviso fuera de sus días (regar es lunes y jueves; el 8 es martes)."""
    its = intentions(*ALL)
    morning = fixed_block(its, madrid("2030-01-07T07:00"))
    assert morning == fixed_block(its, madrid("2030-01-07T22:45"))
    assert '"id":"regar"' in morning and '"habit":"Gimnasio"' in morning
    tuesday = fixed_block(its, madrid("2030-01-08T07:00"))
    assert '"id":"regar"' not in tuesday and '"id":"basura"' in tuesday
    assert "<<INTENCIONES>>" not in morning


# --- La respuesta -------------------------------------------------------------------------------


def test_hablar_con_un_aviso_activo_propone_su_mensaje(conn):
    fake = FakeMessages(api_message(answer(True, "ofertas", SECRET_TEXT)))
    decision, _ = decide(conn, "2030-01-07T16:30", fake)
    assert (decision.speak, decision.intent, decision.reason) == (True, "ofertas", SECRET_REASON)
    assert [(p.intent, p.text) for p in decision.proposals] == [("ofertas", SECRET_TEXT)]
    assert decision.usage == TokenUsage(2500, 100, cache_read_tokens=2000)
    assert not decision.api_down


def test_no_hablar_guarda_el_motivo_y_no_propone_nada(conn):
    fake = FakeMessages(api_message(answer(False, "ofertas", SECRET_TEXT)))
    decision, _ = decide(conn, "2030-01-07T11:15", fake)
    assert (decision.speak, decision.intent, decision.reason) == (False, None, SECRET_REASON)
    assert decision.proposals == ()


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (answer(True, "inventada", SECRET_TEXT), "no_valid_pick (descartado: 1:unknown_intent)"),
        # martes: regar no es de hoy
        (answer(True, "regar", SECRET_TEXT), "no_valid_pick (descartado: 1:unknown_intent)"),
        (answer(True, "ofertas", None), "no_valid_pick (descartado: 1:no_message)"),
        (answer(True, "ofertas", "   "), "no_valid_pick (descartado: 1:no_message)"),
        (answer(True, "ofertas", "🏋" * 201), "no_valid_pick (descartado: 1:too_long)"),
        (picks(), "no_valid_pick"),
        (
            picks(*[("ofertas", "x"), ("basura", "y"), ("insistir_gimnasio", "z")] * 2),
            "too_many_picks",
        ),
        ('{"speak": true, "picks": [', "unparsed"),
        ({**picks(("ofertas", "y")), "buttons": []}, "unparsed"),
        ({"speak": True, "intent": "ofertas", "reason": "x", "message": "y"}, "unparsed"),
    ],
)
def test_una_decision_no_valida_no_habla_y_no_se_reintenta(conn, reply, code):
    """Pilla que una respuesta no válida hable, que se reintente (dos llamadas) o que pierda su
    uso (el contador debe sumarla igual)."""
    fake = FakeMessages(api_message(reply))
    decision, _ = decide(conn, "2030-01-08T16:30", fake)
    assert fake.calls == 1
    assert (decision.speak, decision.intent, decision.proposals) == (False, None, ())
    assert decision.reason == f"invalid_decision:{code}"
    assert decision.usage is not None
    assert not decision.api_down


def test_el_tope_del_mensaje_se_mide_como_telegram(conn):
    """400 en UTF-16 caben: 200 🏋 (2 cada uno) sí, 201 no (ver el caso de arriba)."""
    fake = FakeMessages(api_message(answer(True, "ofertas", "🏋" * 200)))
    decision, _ = decide(conn, "2030-01-08T16:30", fake)
    assert decision.speak and decision.proposals


@pytest.mark.parametrize(("intent", "task"), [("plan_del_dia", None), ("cierre_del_dia", "1.16")])
@pytest.mark.parametrize("message", [None, SECRET_TEXT])
def test_el_plan_y_el_cierre_se_deciden_sin_texto_del_latido(conn, intent, task, message):
    """Decisión 1.8-b: se registran con su intención y sin texto propio: los envía el agente.
    El cierre sigue «pendiente» hasta la 1.16; el plan ya no (tarea 1.14)."""
    fake = FakeMessages(api_message(answer(True, intent, message)))
    decision, _ = decide(conn, "2030-01-07T08:00", fake)
    assert (decision.speak, decision.intent) == (True, intent)
    note = f" (pendiente: {task})" if task else ""
    assert decision.reason == f"{SECRET_REASON}{note}"
    # Sin texto: la segunda pasada lo pasa al agente.
    assert [(p.intent, p.text) for p in decision.proposals] == [(intent, "")]


# --- Varias intenciones en un latido (decisión 1.8-d) -----------------------------------------


def test_tres_intenciones_en_un_latido_proponen_las_tres_en_orden(conn):
    """Pilla un `judge` que solo mira el primer elemento."""
    fake = FakeMessages(
        api_message(picks(("ofertas", "uno"), ("basura", "dos"), ("insistir_gimnasio", "tres")))
    )
    decision, _ = decide(conn, "2030-01-07T16:30", fake)
    assert (decision.speak, decision.intent) == (True, "ofertas,basura,insistir_gimnasio")
    assert [(p.intent, p.text) for p in decision.proposals] == [
        ("ofertas", "uno"),
        ("basura", "dos"),
        ("insistir_gimnasio", "tres"),
    ]
    assert decision.reason == SECRET_REASON


def test_tres_elementos_valen_y_cuatro_no(conn):
    """El tope es de 3 (`MAX_PICKS`); con 4, nadie habla y el uso se cuenta igual."""
    four = [("ofertas", "a"), ("basura", "b"), ("insistir_gimnasio", "c"), ("regar", "d")]
    decision, _ = decide(conn, "2030-01-07T16:30", FakeMessages(api_message(picks(*four[:3]))))
    assert len(decision.proposals) == 3
    decision, _ = decide(conn, "2030-01-07T16:30", FakeMessages(api_message(picks(*four))))
    assert (decision.speak, decision.proposals) == (False, ())
    assert decision.reason == "invalid_decision:too_many_picks"
    assert decision.usage is not None


def test_un_elemento_no_valido_cae_solo_y_el_resto_sigue(conn):
    """Un `intent` repetido, uno inactivo y uno sin mensaje caen con su código y su número; los
    otros salen. Pilla que un elemento malo invalide el lote entero."""
    fake = FakeMessages(api_message(picks(("ofertas", "a"), ("ofertas", "b"), ("basura", "c"))))
    decision, _ = decide(conn, "2030-01-07T16:30", fake)
    assert [(p.intent, p.text) for p in decision.proposals] == [("ofertas", "a"), ("basura", "c")]
    assert decision.reason == f"{SECRET_REASON} (descartado: 2:duplicate)"
    fake = FakeMessages(api_message(picks(("inventada", "x"), ("ofertas", "a"), ("basura", None))))
    decision, _ = decide(conn, "2030-01-07T16:30", fake)
    assert decision.intent == "ofertas"
    assert decision.reason == f"{SECRET_REASON} (descartado: 1:unknown_intent, 3:no_message)"


def test_no_hablar_con_elementos_es_silencio(conn):
    """`speak: false` manda: lo discreto."""
    fake = FakeMessages(api_message(picks(("ofertas", SECRET_TEXT), speak=False)))
    decision, _ = decide(conn, "2030-01-07T16:30", fake)
    assert (decision.speak, decision.intent, decision.proposals) == (False, None, ())


def test_el_plan_dentro_de_un_lote_va_sin_texto_y_los_demas_con_el_suyo(conn):
    """El plan sigue la 1.8-b dentro de un lote; el aviso que va con él, no."""
    fake = FakeMessages(api_message(picks(("plan_del_dia", None), ("basura", SECRET_TEXT))))
    decision, _ = decide(conn, "2030-01-07T08:00", fake)
    assert decision.intent == "plan_del_dia,basura"
    assert [(p.intent, p.text) for p in decision.proposals] == [
        ("plan_del_dia", ""),
        ("basura", SECRET_TEXT),
    ]
    assert decision.reason == SECRET_REASON


# --- El plan B (RF-04) --------------------------------------------------------------------------


def down(conn, now: str, items=ALL):
    fake = FakeMessages(api_down())
    decision, _ = decide(conn, now, fake, items)
    assert decision.api_down and decision.reason == "api_down"
    return decision


@pytest.mark.parametrize(("now", "sent"), [("16:30", True), ("16:44", True), ("16:45", False)])
def test_con_la_api_caida_el_aviso_sale_solo_en_su_tramo(conn, now, sent):
    """`16:30 <= ahora < 16:45`. Pilla una plantilla que sale en todos los latidos tras su hora."""
    decision = down(conn, f"2030-01-07T{now}")
    assert [p.intent for p in decision.proposals] == (["ofertas"] if sent else [])
    assert decision.speak is sent


def test_con_la_api_caida_lo_que_ya_salio_hoy_no_vuelve_a_salir(conn):
    add_notice(conn, "ofertas", madrid("2030-01-07T09:00"), 7)
    assert down(conn, "2030-01-07T16:30").proposals == ()


def test_las_plantillas_tienen_su_texto_fijo(conn):
    """La tarea clave nombra su hábito; el aviso, su propósito."""
    gym = down(conn, "2030-01-07T13:45").proposals
    assert [(p.intent, p.text) for p in gym] == [("insistir_gimnasio", "⏰ Toca Gimnasio")]
    offers = down(conn, "2030-01-07T16:30").proposals
    assert [p.text for p in offers] == ["⏰ Mirar las ofertas"]


def test_con_la_api_caida_el_plan_se_propone_a_su_hora_por_defecto(conn):
    """Tarea 1.14 (cierra el pendiente de la decisión 85): a las 8:00 el plan va a la segunda
    pasada, que se lo pasa al agente; su texto nunca sale. Pilla un plan que no sale nunca con
    Haiku caído, o que sale fuera de su tramo."""
    decision = down(conn, "2030-01-07T08:00")
    assert [(p.intent, p.text) for p in decision.proposals] == [("plan_del_dia", PLAN_TEMPLATE)]
    assert decision.speak and decision.intent == "plan_del_dia"
    assert down(conn, "2030-01-07T08:15").proposals == ()


def test_con_la_api_caida_un_plan_ya_pasado_al_agente_no_se_repite(conn):
    """El plan pasado al agente está en `handovers`, no en `notices`: también cuenta."""
    record_handover(conn, "plan_del_dia", madrid("2030-01-07T07:45"))
    assert down(conn, "2030-01-07T08:00").proposals == ()


def test_con_la_api_caida_el_cierre_sigue_sin_plantilla(conn):
    """Decisión 1.8-b: el cierre llega con la 1.16."""
    decision = down(conn, "2030-01-07T22:00")
    assert (decision.speak, decision.intent, decision.proposals) == (False, None, ())


def test_dos_plantillas_en_el_mismo_tramo_van_las_dos(conn):
    late_gym = {**GYM, "default_time": "16:00"}  # se puede insistir desde las 16:45
    decision = down(
        conn,
        "2030-01-07T16:45",
        (late_gym, OFFERS, {**OFFERS, "id": "o2", "default_time": "16:40"}),
    )
    assert decision.intent == "insistir_gimnasio,o2"
    assert len(decision.proposals) == 2


@pytest.mark.parametrize(("now", "sent"), [("13:00", False), ("13:45", True), ("14:00", False)])
def test_con_la_api_caida_la_tarea_clave_sale_en_el_tramo_de_su_margen(conn, now, sent):
    """Tarea 1.15: sin tarea de hoy, 13:00 + 45 min = 13:45. Pilla la plantilla a su
    `default_time`, que la segunda pasada frenaría siempre (`key_too_early`)."""
    decision = down(conn, f"2030-01-07T{now}", (GYM,))
    assert [p.intent for p in decision.proposals] == (["insistir_gimnasio"] if sent else [])


def test_con_la_api_caida_una_tarea_clave_hecha_no_tiene_plantilla(conn):
    """El botón [Ya entrené] la da por hecha: tampoco sale con la plantilla."""
    key_marks.record(
        conn, date(2030, 1, 7), "insistir_gimnasio", "done", madrid("2030-01-07T09:00")
    )
    assert down(conn, "2030-01-07T13:45", (GYM,)).proposals == ()


def test_un_aviso_fuera_de_sus_dias_no_tiene_plantilla(conn):
    """Regar es lunes y jueves: el martes a su hora no sale."""
    assert down(conn, "2030-01-08T19:00").proposals == ()
    assert [p.intent for p in down(conn, "2030-01-07T19:00").proposals] == ["regar"]


@pytest.mark.parametrize("error", ["status", "timeout"])
def test_cualquier_error_de_la_api_es_api_caida(conn, error):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    raised = (
        anthropic.APITimeoutError(request)
        if error == "timeout"
        else anthropic.InternalServerError(
            "caída", response=httpx2.Response(529, request=request), body=None
        )
    )
    decision, _ = decide(conn, "2030-01-07T16:30", FakeMessages(raised))
    assert decision.api_down and [p.intent for p in decision.proposals] == ["ofertas"]


# --- El uso, el cliente y los logs --------------------------------------------------------------


def test_el_uso_separa_la_escritura_de_cache_por_su_ttl():
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": None,
        "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20},
    }
    assert usage_of(api_message(answer(), usage)) == TokenUsage(10, 5, 0, 10, 20)


def test_sin_el_desglose_la_escritura_cuenta_al_ttl_que_se_pide():
    usage = {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 30}
    assert usage_of(api_message(answer(), usage)) == TokenUsage(10, 5, 0, 0, 30)


def test_el_cliente_usa_la_clave_del_env_y_no_el_entorno(monkeypatch):
    """JimiC solo lee su `.env`: ni `ANTHROPIC_API_KEY` ni `ANTHROPIC_BASE_URL` del proceso."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-clave-del-entorno-000")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://otro-servidor.invalid")
    settings = Settings.model_construct(anthropic_api_key=SecretStr(FAKE_KEY))
    client = build_client(settings)
    try:
        assert client.api_key == FAKE_KEY
        assert str(client.base_url).rstrip("/") == API_URL
        assert client.max_retries == 2
    finally:
        asyncio.run(client.close())


def test_los_logs_no_llevan_motivos_ni_mensajes(conn, caplog):
    caplog.set_level(logging.DEBUG)
    for reply in (
        api_message(answer(True, "ofertas", SECRET_TEXT)),
        api_message(answer(False)),
        api_message(answer(True, "inventada", SECRET_TEXT)),
        api_down(),
    ):
        decide(conn, "2030-01-07T16:30", FakeMessages(reply))
    assert SECRET_REASON not in caplog.text and SECRET_TEXT not in caplog.text
    assert "api_down (APIConnectionError)" in caplog.text
    assert "invalid_decision:no_valid_pick (descartado: 1:unknown_intent)" in caplog.text


def test_el_repr_de_la_decision_no_lleva_el_motivo(conn):
    decision, _ = decide(
        conn, "2030-01-07T16:30", FakeMessages(api_message(answer(True, "ofertas", SECRET_TEXT)))
    )
    assert SECRET_REASON not in repr(decision) and SECRET_TEXT not in repr(decision)
