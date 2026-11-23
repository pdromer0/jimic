"""Evaluación del latido: los escenarios, la corrección y el recuento (tarea 1.9).

Ningún test llama a la API: el modelo es `FakeMessages`. La evaluación de verdad la lanza
Pedro (o el agente, una vez por PR) con `jimic evaluar latido`.
"""

import asyncio

import pytest
from evals_data import REAL_FILE, outcome, variant
from heartbeat_data import FakeMessages, answer, api_down, api_message, picks

from jimic.evals.heartbeat import PassResult, is_correct, run_pass, run_scenarios, score
from jimic.evals.scenarios import EvalError, build_state, load_suite
from jimic.heartbeat.decide import build_request, fixed_block


@pytest.fixture(scope="module")
def suite():
    return load_suite(REAL_FILE)


def by_id(suite, scenario_id):
    return next(item for item in suite.scenarios if item.id == scenario_id)


def one_pass(suite, scenario_id, reply):
    """Una pasada sobre el escenario con la respuesta `reply` del doble."""
    fake = FakeMessages(reply)
    result = asyncio.run(
        run_pass(fake.create, "claude-haiku-5-5", suite, by_id(suite, scenario_id))
    )
    assert fake.calls == 1
    return result


# --- El fichero de escenarios ------------------------------------------------------------------


def test_el_fichero_real_carga_y_cada_escenario_monta_su_estado_con_lo_esperado_activo(suite):
    """Pilla una errata en `evals/latido.yaml` (un id, una hora, una intención que ese día no
    está activa) antes de gastar un céntimo."""
    ids = [item.id for item in suite.scenarios]
    assert len(ids) == len(set(ids)) == 40
    assert [(item.id, item.pending) for item in suite.scenarios if item.pending] == [
        ("e02", "1.15")
    ]
    runnable = [item for item in suite.scenarios if item.pending is None]
    assert len(runnable) == 39
    assert sum(item.silent_only for item in runnable) == 19
    for item in suite.scenarios:
        active = {intention.id for intention in build_state(suite, item).intentions}
        for alternative in item.expected:
            assert alternative <= active, item.id


def test_una_intencion_esperada_que_ese_dia_no_esta_activa_es_un_error(tmp_path):
    """El viernes no toca regar: esperarlo es una errata del fichero."""
    path = variant(
        tmp_path,
        '\n  - id: zz1\n    title: errata\n    day: "2030-01-11"\n    now: "19:00"\n'
        "    expected: [[regar]]\n",
    )
    with pytest.raises(EvalError, match=r"zz1: espera intenciones que no están activas.*regar"):
        load_suite(path)


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        ('\n  - id: l01\n    title: otra\n    now: "07:05"\n    expected: [[]]\n', "repetido"),
        (
            '\n  - id: zz1\n    title: x\n    like: zz9\n    now: "07:05"\n    expected: [[]]\n',
            "like zz9 no es un escenario anterior",
        ),
        (
            '\n  - id: zz1\n    title: x\n    now: "07:05"\n    expected: [[regar, regar]]\n',
            "repite una intención",
        ),
        ('\n  - id: zz1\n    title: x\n    now: "7h"\n    expected: [[]]\n', "zz1: now"),
        ('\n  - id: zz1\n    title: x\n    now: "07:05"\n    expected: []\n', "zz1: expected"),
    ],
)
def test_un_fichero_con_errores_dice_donde(tmp_path, extra, error):
    with pytest.raises(EvalError, match=error):
        load_suite(variant(tmp_path, extra))


def test_like_hereda_el_estado_pero_no_el_porque_ni_lo_esperado(suite):
    """l27 parte de l26: mismos seis mensajes enviados, pero ni su `why` ni sus alternativas."""
    l26, l27 = by_id(suite, "l26"), by_id(suite, "l27")
    assert l27.sent_today == l26.sent_today
    assert l26.why is not None and l27.why is None
    assert l27.expected == (frozenset({"insistir_gimnasio"}),)


def test_lo_enviado_a_la_misma_hora_cuenta_un_mensaje(suite):
    """Valores a mano: tope 8 y el hueco del cierre; 6 mensajes dejan 1, y 7 (con `regar` y
    `reciclar` juntos a las 19:00, ocho filas) dejan 0 con el hueco guardado."""
    l26, l28 = build_state(suite, by_id(suite, "l26")), build_state(suite, by_id(suite, "l28"))
    assert (len(l26.sent_today), l26.messages_left, l26.close_slot_kept) == (6, 1, True)
    assert (len(l28.sent_today), l28.messages_left, l28.close_slot_kept) == (8, 0, True)


def test_l30_y_l31_piden_a_haiku_lo_mismo_que_l18(suite):
    """Ni el aviso del sistema (86) ni la conversación (91) llegan a Haiku: la petición es la de
    l18, y su huella también."""
    requests = []
    for scenario_id in ("l18", "l30", "l31"):
        state = build_state(suite, by_id(suite, scenario_id))
        requests.append(build_request("m", fixed_block(suite.intentions, state.now), state))
    assert requests[0] == requests[1] == requests[2]
    assert len({by_id(suite, i).fingerprint for i in ("l18", "l30", "l31")}) == 1


def test_la_huella_cambia_con_el_estado_pero_no_con_el_titulo(tmp_path):
    base = load_suite(REAL_FILE)
    text = REAL_FILE.read_text(encoding="utf-8")
    retitled = tmp_path / "titulo.yaml"
    retitled.write_text(text.replace("title: Rutina de mañana, recién levantado", "title: Otro"))
    moved = tmp_path / "hora.yaml"
    moved.write_text(text.replace('now: "07:05"', 'now: "07:10"', 1))
    assert by_id(load_suite(retitled), "l01").fingerprint == by_id(base, "l01").fingerprint
    assert by_id(load_suite(moved), "l01").fingerprint != by_id(base, "l01").fingerprint


def test_un_cambio_en_el_escenario_de_like_cambia_la_huella_de_quien_hereda(tmp_path):
    """l24 parte de l20: mover el aviso de `regar` de l20 cambia también la huella de l24."""
    base = load_suite(REAL_FILE)
    text = REAL_FILE.read_text(encoding="utf-8")
    path = tmp_path / "like.yaml"
    marker = '      - {intent: regar, at: "19:00"}\n      - {intent: reciclar, at: "19:00"}\n'
    assert text.count(marker) >= 1
    path.write_text(
        text.replace(marker, marker.replace('regar, at: "19:00"', 'regar, at: "19:05"'), 1)
    )
    changed = load_suite(path)
    assert by_id(changed, "l24").fingerprint != by_id(base, "l24").fingerprint
    assert by_id(changed, "l18").fingerprint == by_id(base, "l18").fingerprint


# --- La corrección (1.9-b) ---------------------------------------------------------------------


def test_el_orden_de_las_intenciones_no_cuenta(suite):
    result = one_pass(
        suite, "l18", api_message(picks(("reciclar", "Reciclaje"), ("regar", "Agua")))
    )
    assert result.intents == {"regar", "reciclar"}
    assert is_correct(by_id(suite, "l18").expected, result)


@pytest.mark.parametrize(
    "chosen",
    [
        (("regar", "Agua"),),
        (("regar", "Agua"), ("reciclar", "Reciclaje"), ("basura", "Basura")),
    ],
    ids=["de-menos", "de-mas"],
)
def test_un_conjunto_de_mas_o_de_menos_es_fallo(suite, chosen):
    result = one_pass(suite, "l18", api_message(picks(*chosen)))
    assert result.speak
    assert not is_correct(by_id(suite, "l18").expected, result)


@pytest.mark.parametrize(
    ("scenario_id", "reply"),
    [
        ("l10", answer()),
        ("l10", answer(True, "insistir_gimnasio", "¿Vamos?")),
        ("l26", answer(True, "reciclar", "El reciclaje")),
        ("l26", picks(("regar", "Agua"), ("reciclar", "Reciclaje"))),
    ],
    ids=["l10-callar", "l10-gimnasio", "l26-solo-reciclar", "l26-los-dos"],
)
def test_una_alternativa_declarada_es_acierto(suite, scenario_id, reply):
    result = one_pass(suite, scenario_id, api_message(reply))
    assert is_correct(by_id(suite, scenario_id).expected, result)


@pytest.mark.parametrize(
    "reply",
    [api_message(picks(speak=True)), api_message("esto no es JSON")],
    ids=["habla-sin-elegir", "sin-json"],
)
def test_una_decision_invalida_es_fallo_aunque_no_hable(suite, reply):
    """l01 es de callar y la decisión inválida no habla, pero no es un acierto (1.9-b)."""
    result = one_pass(suite, "l01", reply)
    assert result.invalid and not result.speak
    assert not is_correct(by_id(suite, "l01").expected, result)


def test_un_error_de_la_api_es_una_pasada_fallida_con_su_codigo(suite):
    result = one_pass(suite, "l01", api_down())
    assert result.api_error == "down:APIConnectionError"
    assert not result.speak
    assert not is_correct(by_id(suite, "l01").expected, result)


def test_lo_que_judge_descarta_no_entra_en_el_conjunto(suite):
    """Un elemento de una intención que no existe cae en `judge`: cuenta lo que queda."""
    reply = picks(("regar", "Agua"), ("inventada", "x"), ("reciclar", "Reciclaje"))
    result = one_pass(suite, "l18", api_message(reply))
    assert result.intents == {"regar", "reciclar"}


# --- El recuento (1.9-c) -----------------------------------------------------------------------


def test_habla_cuando_no_debia_se_cuenta_solo_sobre_los_escenarios_de_callar():
    """Valores a mano: un escenario de callar que habla 1 de 3 y uno de hablar que habla 3 de
    3 dan 1/3, no 1/6 (dividir entre todas las pasadas)."""
    total = score(
        [
            outcome("callar", (False, True, True), (True, False, False), silent_only=True),
            outcome("hablar", (True, True, True), (True, True, True)),
        ]
    )
    assert total.false_speak == pytest.approx(1 / 3)
    assert (total.spoke_when_silent, total.silent_passes) == (1, 3)
    assert (total.correct, total.passes) == (5, 6)


def test_un_escenario_con_alternativa_de_callar_no_cuenta_para_habla_cuando_no_debia(suite):
    """l10 admite callar o insistir: no es «solo callar»."""
    assert not by_id(suite, "l10").silent_only
    assert by_id(suite, "l09").silent_only


def test_sin_escenarios_de_callar_no_se_divide_entre_cero():
    total = score([outcome("hablar", (True,), (True,))])
    assert total.false_speak is None
    assert total.accuracy == 1


# --- El lanzador -------------------------------------------------------------------------------


def test_el_pendiente_no_se_lanza_ni_cuenta(suite):
    fake = FakeMessages()
    chosen = suite.select(["l01", "e02", "l02"])
    results = asyncio.run(run_scenarios(fake.create, "claude-haiku-5-5", suite, chosen, 3))
    assert [item.scenario.id for item in results] == ["l01", "l02"]
    assert fake.calls == 6


def test_las_pasadas_salen_en_el_orden_del_fichero(suite):
    """Con varias a la vez, cada escenario se queda con sus pasadas: el doble contesta según la
    hora de la petición."""

    def reply(request):
        hour = request["messages"][0]["content"].split('"now":"')[1][11:16]
        return api_message(answer(True, "plan_del_dia") if hour == "07:45" else answer())

    fake = FakeMessages(reply)
    chosen = suite.select(["l01", "l03", "l02"])
    results = asyncio.run(run_scenarios(fake.create, "claude-haiku-5-5", suite, chosen, 2))
    assert [item.scenario.id for item in results] == ["l01", "l02", "l03"]
    assert [item.outcome.correct for item in results] == [(True, True)] * 3


def test_un_id_desconocido_se_rechaza(suite):
    with pytest.raises(EvalError, match="desconocidos: zz9"):
        suite.select(["l01", "zz9"])


def test_pass_result_oculta_el_motivo_y_los_mensajes():
    result = PassResult(True, frozenset({"regar"}), "motivo-oculto", (("regar", "texto-oculto"),))
    assert "motivo-oculto" not in repr(result) and "texto-oculto" not in repr(result)
