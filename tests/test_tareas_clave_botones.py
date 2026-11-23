"""Los botones de las tareas clave (tarea 1.15, decisiones 1.15-c a 1.15-g): lo que hace cada toque.

Todoist es el doble detrás del lector y el escritor de verdad (imita lo que vio el contrato del
23-nov-2026: una recurrente completada pasa a mañana); el agente, un doble que pasa por los
*hooks*. Todo es inventado. El día es el lunes 07/01/2030.
"""

import asyncio
from datetime import date, datetime

import pytest
from agent_data import FakeAgent, ToolCall, result_message
from apex_data import invented_snapshot
from execution_data import AssistantWorld, todoist_task
from heartbeat_data import GYM, HABIT, MADRID, calendar_with, intentions, limits, madrid
from todoist_data import http_status_error

from jimic.channel import store
from jimic.channel.bot import Reply
from jimic.channel.message import InteractiveMessage, Tap
from jimic.channel.taps import Answer, Followup, TapResult, process_tap
from jimic.clock import local_day_bounds
from jimic.execution.key_tasks import KEY_UNDO_KIND, KeyTaskActions
from jimic.execution.plan_message import UNDO_KIND
from jimic.heartbeat.key_message import KEY_KIND, key_message
from jimic.heartbeat.limits import WaitingNotices
from jimic.heartbeat.state import build_day_state
from jimic.memory import key_marks
from jimic.memory.database import open_database
from jimic.memory.notices import sent_between

TODAY = date(2030, 1, 7)
OFFERS_KEY = {**GYM, "id": "insistir_ofertas", "habit": "ofertas", "default_time": "17:15"}
OFFERS_HABIT = {
    **HABIT,
    "id": "ofertas",
    "name": "Revisar ofertas",
    "todoist_task": "Revisar ofertas",
}
GYM_TASK = todoist_task("t-gym", "Gimnasio", due="2030-01-07T12:00:00")
OFFERS_TASK = todoist_task("t-ofertas", "Revisar ofertas", due="2030-01-07T16:30:00")
ICONS = {"gimnasio": "🏋️", "revisar ofertas": "🛒"}
MOVE = {"action": "move", "task_id": "t-gym", "due": "2030-01-07T18:00", "reason": "lo pidió"}


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


class KeyWorld(AssistantWorld):
    def __init__(self, conn, tmp_path, tasks=None, agent=None, training=False):
        super().__init__(
            conn,
            tmp_path,
            agent or FakeAgent([result_message("Vale")]),
            tasks if tasks is not None else [GYM_TASK, OFFERS_TASK],
        )
        self.its = intentions(
            GYM, OFFERS_KEY, habits=[{**HABIT, "training": training}, OFFERS_HABIT]
        )
        self.keys = KeyTaskActions(
            conn=conn, clock=self.clock, timezone=MADRID, todoist=self.todoist
        )

    def handlers(self):
        # Como `runtime`: vigilados por el asistente, que recuerda [⏰ Más tarde].
        return self.assistant.track(self.executor.handlers | self.keys.handlers)

    def send(self, *intents: str) -> int:
        """The heartbeat's message with these key tasks, saved as the channel does."""
        now = self.clock.now().astimezone(MADRID)
        state = build_day_state(
            now,
            MADRID,
            calendar_with(),
            asyncio.run(self.todoist.reader.read_today()),
            invented_snapshot(),
            sent_between(self.conn, *local_day_bounds(now.date(), MADRID)),
            self.its,
            limits(),
            WaitingNotices(),
            marks=key_marks.of_day(self.conn, now.date()),
        )
        message = key_message(
            "Oye, que la pesa te echa de menos 😄",
            [state.key_tasks[intent] for intent in intents],
            self.its,
            ICONS,
        )
        expires = local_day_bounds(now.date(), MADRID)[1]
        approval = store.create(self.conn, self.clock, KEY_KIND, message, expires)
        store.set_message_id(self.conn, approval, 900 + approval)
        return approval

    def press(self, approval: int, item: int, option: str) -> TapResult:
        return asyncio.run(
            process_tap(self.conn, self.clock, self.handlers(), Tap(approval, item, option))
        )

    def save(self, followup: Followup) -> int:
        """The message after a tap, saved as the channel does."""
        return store.create(
            self.conn, self.clock, followup.kind, followup.message, followup.expires_at
        )

    def marks(self, day: date = TODAY):
        return key_marks.of_day(self.conn, day)


def only_followup(result: TapResult) -> Followup:
    (followup,) = result.messages
    return followup


# --- [🏃 Voy ahora] ------------------------------------------------------------------------------


def test_voy_ahora_anota_la_hora_y_responde_sin_modelo(conn, tmp_path):
    w = KeyWorld(conn, tmp_path)
    result = w.press(w.send("insistir_gimnasio"), 1, "go")
    assert result.send == ("¡Vamos! 💪",) and result.messages == ()
    assert w.marks().of("insistir_gimnasio").going_at == madrid("2030-01-07T10:15")
    assert w.writes() == [] and w.agent.runs == []


# --- [✅ Ya entrené] / [✅ Hecho] ----------------------------------------------------------------


def test_hecho_completa_la_tarea_de_hoy_y_responde_con_deshacer(conn, tmp_path):
    w = KeyWorld(conn, tmp_path)
    result = w.press(w.send("insistir_gimnasio"), 1, "done")
    assert w.api.calls_of("complete_task") == [{"task_id": "t-gym"}]
    assert w.marks().of("insistir_gimnasio").done
    followup = only_followup(result)
    assert followup.kind == KEY_UNDO_KIND
    assert followup.message.body == "¡Hecho! 💪 Una menos" == followup.if_unsent
    assert [o.label for o in followup.message.items[0].options] == ["↩️ Deshacer"]


def test_el_boton_dice_ya_entrene_solo_en_el_habito_del_entreno(conn, tmp_path):
    for training, label in ((True, "✅ Ya entrené"), (False, "✅ Hecho")):
        folder = tmp_path / str(training)
        folder.mkdir()
        w = KeyWorld(conn, folder, training=training)
        message = store.load(conn, w.send("insistir_gimnasio"))
        assert message is not None
        assert message.message.items[0].options[2].label == label


def test_deshacer_reabre_la_tarea_y_la_insistencia_puede_volver(conn, tmp_path):
    w = KeyWorld(conn, tmp_path)
    followup = only_followup(w.press(w.send("insistir_gimnasio"), 1, "done"))
    undone = w.press(w.save(followup), 1, "undo")
    assert undone.code == "resolved"
    assert w.api.calls_of("uncomplete_task") == [{"task_id": "t-gym"}]
    assert not w.marks().of("insistir_gimnasio").done
    # La tarea vuelve a estar abierta hoy: el estado ya no la da por hecha.
    assert "t-gym" in {t.id for t in asyncio.run(w.todoist.reader.read_today()).tasks}


def test_con_una_recurrente_hecho_la_pasa_a_manana_y_deshacer_la_devuelve(conn, tmp_path):
    """Contrato del 23-nov-2026: reabrir una recurrente no la devuelve a hoy; moverla, sí."""
    recurring = todoist_task("t-gym", "Gimnasio", due="2030-01-07T12:00:00", recurring=True)
    w = KeyWorld(conn, tmp_path, [recurring])
    followup = only_followup(w.press(w.send("insistir_gimnasio"), 1, "done"))
    stored = w.api.find("t-gym")
    assert stored is not None and stored["due"]["date"] == "2030-01-08T12:00:00"
    w.press(w.save(followup), 1, "undo")
    task = asyncio.run(w.todoist.reader.get_task("t-gym"))
    assert task is not None and task.is_recurring
    assert task.due == datetime(2030, 1, 7, 12, tzinfo=MADRID)
    assert w.api.calls_of("uncomplete_task") == []
    assert w.marks().moved == {"t-gym"}  # un movimiento de JimiC: no es un salto
    assert not w.marks().of("insistir_gimnasio").done


def test_si_la_recurrente_cambio_deshacer_no_la_toca(conn, tmp_path):
    recurring = todoist_task("t-gym", "Gimnasio", due="2030-01-07T12:00:00", recurring=True)
    w = KeyWorld(conn, tmp_path, [recurring])
    followup = only_followup(w.press(w.send("insistir_gimnasio"), 1, "done"))
    stored = w.api.find("t-gym")
    assert stored is not None
    stored["due"]["date"] = "2030-01-09T12:00:00"  # Pedro la movió en la app
    result = w.press(w.save(followup), 1, "undo")
    assert "ha cambiado" in render_text(result)
    assert w.api.calls_of("update_task") == []
    assert w.marks().of("insistir_gimnasio").done


def render_text(result: TapResult) -> str:
    assert result.view is not None
    return result.view.text


def test_sin_tarea_de_hoy_hecho_solo_anota(conn, tmp_path):
    w = KeyWorld(conn, tmp_path, [OFFERS_TASK])
    w.press(w.send("insistir_gimnasio"), 1, "done")
    assert w.writes() == []
    assert w.marks().of("insistir_gimnasio").done


def test_si_ya_estaba_completada_en_la_app_no_se_completa_otra_vez(conn, tmp_path):
    w = KeyWorld(conn, tmp_path)
    approval = w.send("insistir_gimnasio")
    asyncio.run(w.api.complete_task("t-gym"))  # Pedro la marcó en la app tras el aviso
    w.api.calls.clear()
    w.press(approval, 1, "done")
    assert w.api.calls_of("complete_task") == []
    assert w.marks().of("insistir_gimnasio").done


def test_si_todoist_falla_no_se_anota_y_se_puede_repetir(conn, tmp_path):
    w = KeyWorld(conn, tmp_path)
    approval = w.send("insistir_gimnasio")
    w.api.error = http_status_error(503)
    result = w.press(approval, 1, "done")
    assert result.code == "handler_failed"
    assert not w.marks().of("insistir_gimnasio").done
    w.api.error = None
    assert w.press(approval, 1, "done").code == "resolved"


# --- [⏭ Hoy no] --------------------------------------------------------------------------------


def test_hoy_no_anota_el_dia_sin_motivo_y_deshacer_lo_quita(conn, tmp_path):
    w = KeyWorld(conn, tmp_path)
    followup = only_followup(w.press(w.send("insistir_gimnasio"), 1, "skip"))
    assert followup.message.body == "Vale, hoy nada de gimnasio. 🛋️"
    assert w.marks().of("insistir_gimnasio").not_today
    assert w.writes() == []
    w.press(w.save(followup), 1, "undo")
    assert not w.marks().of("insistir_gimnasio").not_today


def test_hoy_no_a_las_00_30_es_del_dia_local(conn, tmp_path):
    """Versión incorrecta 7: con el día en UTC, la marca caería en el día anterior."""
    w = KeyWorld(conn, tmp_path, [todoist_task("t-gym", "Gimnasio", due="2030-01-08T12:00:00")])
    w.clock.moment = madrid("2030-01-08T00:30")
    w.press(w.send("insistir_gimnasio"), 1, "skip")
    assert w.marks(date(2030, 1, 8)).of("insistir_gimnasio").not_today
    assert not w.marks(TODAY).of("insistir_gimnasio").not_today


# --- Dos tareas clave en un mensaje ------------------------------------------------------------


def test_con_dos_tareas_cada_boton_actua_sobre_la_suya(conn, tmp_path):
    """Versión incorrecta 4: los botones de la segunda actuarían sobre la primera."""
    w = KeyWorld(conn, tmp_path)
    approval = w.send("insistir_gimnasio", "insistir_ofertas")
    w.press(approval, 2, "done")
    assert w.api.calls_of("complete_task") == [{"task_id": "t-ofertas"}]
    assert w.marks().of("insistir_ofertas").done
    assert not w.marks().of("insistir_gimnasio").done


def test_con_una_tarea_el_boton_actua_sobre_ella(conn, tmp_path):
    w = KeyWorld(conn, tmp_path)
    w.press(w.send("insistir_ofertas"), 1, "done")
    assert w.api.calls_of("complete_task") == [{"task_id": "t-ofertas"}]


# --- [⏰ Más tarde] -------------------------------------------------------------------------------

LATER_LINE = (
    "[lun 07/01 10:15] (JimiC) Pedro tocó ⏰ Más tarde en: «Gimnasio» (hoy 12:00, tarea t-gym);"
    " su mensaje dice a qué hora moverla"
)


def test_mas_tarde_pregunta_y_la_linea_va_con_el_siguiente_mensaje_una_vez(conn, tmp_path):
    agent = FakeAgent([result_message("Vale")], [result_message("De nada")])
    w = KeyWorld(conn, tmp_path, agent=agent)
    result = w.press(w.send("insistir_gimnasio"), 1, "later")
    assert result.send == ("¿A qué hora? ⏰",)
    assert w.writes() == [] and agent.runs == []  # sin modelo hasta que Pedro conteste
    w.say("a las 18")
    assert agent.runs[0][0] == f"{LATER_LINE}\n[lun 07/01 10:15] a las 18"
    w.say("gracias")
    assert agent.runs[1][0] == "[lun 07/01 10:15] gracias"


def test_sin_tarea_de_hoy_la_linea_lo_dice(conn, tmp_path):
    w = KeyWorld(conn, tmp_path, [OFFERS_TASK])
    w.press(w.send("insistir_gimnasio"), 1, "later")
    w.say("a las 18")
    assert "«Gimnasio» (hoy no tiene tarea en Todoist)" in w.agent.runs[0][0]


def test_mas_tarde_se_mueve_al_momento_con_deshacer_sin_leer_todoist(conn, tmp_path):
    """1.15-d: con el id en la línea, el agente propone el `move` sin leer las tareas, y se
    cumplen las condiciones de la 97: va al momento con [Deshacer]."""
    agent = FakeAgent(
        [
            ToolCall("propose_plan", {"items": [MOVE], "requested_by_pedro": True}),
            result_message("Te la paso a las 18"),
        ]
    )
    w = KeyWorld(conn, tmp_path, agent=agent)
    w.press(w.send("insistir_gimnasio"), 1, "later")
    reply = w.say("a las 18")
    assert isinstance(reply, Reply) and reply.outgoing is not None
    assert reply.outgoing.kind == UNDO_KIND
    task = asyncio.run(w.todoist.reader.get_task("t-gym"))
    assert task is not None and task.due == datetime(2030, 1, 7, 18, tzinfo=MADRID)
    assert w.marks().moved == {"t-gym"}  # lo movió JimiC: no es un salto
    assert "origin" not in str(reply.outgoing.message.items[0].data)


def test_ninguna_respuesta_fija_pasa_por_el_modelo(conn, tmp_path):
    w = KeyWorld(conn, tmp_path)
    for option in ("go", "later", "done", "skip"):
        w.press(w.send("insistir_gimnasio"), 1, option)
    assert w.agent.runs == []


def test_un_toque_no_actua_dos_veces(conn, tmp_path):
    w = KeyWorld(conn, tmp_path)
    approval = w.send("insistir_gimnasio")
    w.press(approval, 1, "done")
    assert w.press(approval, 1, "done").code != "resolved"
    assert w.api.calls_of("complete_task") == [{"task_id": "t-gym"}]


def test_la_respuesta_de_un_toque_es_answer(conn, tmp_path):
    """Lo que devuelve el manejador: el elemento queda resuelto con su resultado corto."""
    w = KeyWorld(conn, tmp_path)
    approval = w.send("insistir_gimnasio")
    item = store.load(conn, approval)
    assert item is not None
    outcome = asyncio.run(w.keys.tap(item.message.items[0], "go", approval))
    assert isinstance(outcome, Answer) and outcome.result == "🏃 a por ello"


def test_los_mensajes_caducan_al_acabar_el_dia(conn, tmp_path):
    w = KeyWorld(conn, tmp_path)
    approval = store.load(conn, w.send("insistir_gimnasio"))
    assert approval is not None
    assert approval.expires_at == datetime(2030, 1, 8, 0, 0, tzinfo=MADRID)  # 23:00 en UTC
    assert isinstance(approval.message, InteractiveMessage)


@pytest.mark.parametrize(
    ("option", "note"), [("go", "🏃 a por ello"), ("later", "⏰ ¿a qué hora?")]
)
def test_tras_voy_ahora_o_mas_tarde_quedan_ya_entrene_y_hoy_no(conn, tmp_path, option, note):
    """Para poder dar el entreno por hecho después con su botón (la felicitación, 1.15-h)."""
    w = KeyWorld(conn, tmp_path, training=True)
    result = w.press(w.send("insistir_gimnasio"), 1, option)
    assert result.view is not None
    assert [[b.label for b in row] for row in result.view.keyboard] == [
        ["✅ Ya entrené", "⏭ Hoy no"]
    ]
    assert result.view.text.endswith(f"🏋️ Gimnasio — {note}")
    assert not result.closed
