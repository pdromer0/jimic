"""La conversación sigue tras los toques (decisiones 1.13-c y 1.13-d): cuando un toque cierra una
propuesta que pidió `follow_up`, el código reanuda una vez la sesión del día con una nota suya. Y
tras [✏️], el siguiente mensaje de Pedro lleva delante una línea del código con lo que tocó
(decisión 1.14-h).

El agente es un doble que pasa por los *hooks* y el servidor de las opciones; Todoist, el doble
detrás del lector y el escritor de verdad; los ficheros personales, inventados. Todo es inventado.
"""

import asyncio

import pytest
from agent_data import FakeAgent, ToolCall, result_message
from execution_data import AssistantWorld, todoist_task
from heartbeat_data import intentions, madrid
from memory_data import QUIET_LINE, write_personal

from jimic.channel import store
from jimic.channel.bot import Reply
from jimic.channel.message import InteractiveMessage, Item, Option, Tap
from jimic.channel.taps import TapResult, process_tap, sweep
from jimic.execution.follow_up import follow_up_note
from jimic.execution.memory_message import MEMORY_KIND, MemoryHandlers
from jimic.execution.plan_message import PLAN_KIND
from jimic.memory.database import open_database
from jimic.memory.live import LiveConfig

CREATE = {"action": "create", "title": "Tarea nueva", "due": "2030-01-07T18:00", "reason": "r"}
QUIETER = {
    "file": "limites.yaml",
    "operation": "replace",
    "before": QUIET_LINE,
    "after": QUIET_LINE.replace('"07:00"', '"08:00"'),
    "reason": "Pedro no quiere mensajes antes de las 8",
}
AFTER = "Perfecto. ¿Seguimos con las comidas?"


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def proposing(tool: str, args: dict, *then) -> FakeAgent:
    """First run: the agent proposes; the next ones are `then` (the follow-up)."""
    first = [ToolCall(tool, args), result_message("Te lo propongo")]
    return FakeAgent(first, *(then or ([result_message(AFTER)],)))


class FollowWorld(AssistantWorld):
    def __init__(self, conn, tmp_path, agent, **kwargs):
        super().__init__(conn, tmp_path, agent, [todoist_task("t1")], **kwargs)
        write_personal(self.paths.config_dir)
        live = LiveConfig(intentions(), self.limits, self.paths.config_dir)
        self.memory = MemoryHandlers(
            conn=conn, clock=self.clock, config_dir=self.paths.config_dir, config=live
        )

    def propose(self, text: str = "crea una tarea a las 18") -> tuple[int, str]:
        """Pedro writes, and the proposal is saved as the channel does: `(approval, kind)`."""
        reply = self.say(text)
        assert isinstance(reply, Reply) and reply.outgoing is not None
        out = reply.outgoing
        approval = store.create(self.conn, self.clock, out.kind, out.message, out.expires_at)
        store.set_message_id(self.conn, approval, 700 + approval)  # Telegram la aceptó
        return approval, out.kind

    def press(self, approval_id: int, item: int | None, option: str) -> TapResult:
        # Como `runtime`: los manejadores de las propuestas, vigilados para [✏️] (1.14-h).
        handlers = self.assistant.track(self.executor.handlers | {MEMORY_KIND: self.memory.memory})
        return asyncio.run(
            process_tap(self.conn, self.clock, handlers, Tap(approval_id, item, option))
        )

    def follow_up(self, approval_id: int):
        return asyncio.run(self.assistant.follow_up(approval_id))


# --- Se reanuda: todo resuelto y con `follow_up` ----------------------------------------------


def test_resuelto_y_con_follow_up_se_reanuda_una_vez_con_la_nota(conn, tmp_path):
    w = FollowWorld(
        conn, tmp_path, proposing("propose_plan", {"items": [CREATE], "follow_up": True})
    )
    approval, kind = w.propose("¿qué hago esta tarde?")
    assert kind == PLAN_KIND
    result = w.press(approval, 1, "ok")
    assert result.closed
    assert w.follow_up(approval) == AFTER
    prompt, options = w.agent.runs[-1]
    assert prompt.startswith("[lun 07/01 10:15] (JimiC) Pedro ha resuelto tu propuesta")
    assert "crear «Tarea nueva» (2030-01-07T18:00) — ✓ creada" in prompt
    assert options.resume == "sesion-1"
    # Un segundo toque ya no cierra nada: la reanudación no se repite.
    assert not w.press(approval, 1, "ok").closed
    assert len(w.agent.runs) == 2


def test_un_guardar_de_memoria_con_follow_up_sigue_la_conversacion(conn, tmp_path):
    """El caso de la puesta en marcha (1.13-c): tras [Guardar], el agente sabe que se guardó."""
    w = FollowWorld(conn, tmp_path, proposing("propose_change", QUIETER | {"follow_up": True}))
    approval, kind = w.propose("no me escribas antes de las 8")
    assert kind == MEMORY_KIND
    assert w.press(approval, 1, "save").closed
    assert w.follow_up(approval) == AFTER
    assert "limites.yaml · cambiar — 💾 guardado" in w.agent.runs[-1][0]


def test_la_reanudacion_no_cuenta_como_que_pedro_escribio(conn, tmp_path):
    w = FollowWorld(
        conn, tmp_path, proposing("propose_plan", {"items": [CREATE], "follow_up": True})
    )
    approval, _ = w.propose()
    w.press(approval, 1, "ok")
    before = conn.execute("SELECT last_message_at FROM conversations").fetchall()
    w.clock.moment = madrid("2030-01-07T10:30")
    w.follow_up(approval)
    assert conn.execute("SELECT last_message_at FROM conversations").fetchall() == before


# --- No se reanuda -----------------------------------------------------------------------------


def test_sin_follow_up_no_se_reanuda(conn, tmp_path):
    w = FollowWorld(conn, tmp_path, proposing("propose_plan", {"items": [CREATE]}))
    approval, _ = w.propose()
    assert w.press(approval, 1, "ok").closed
    assert w.follow_up(approval) is None
    assert len(w.agent.runs) == 1


def test_con_filas_pendientes_no_se_reanuda(conn, tmp_path):
    """Versión incorrecta 3: reanudar con filas pendientes cortaría a Pedro a mitad de sus
    toques."""
    two = {"items": [CREATE, CREATE | {"title": "Otra"}], "follow_up": True}
    w = FollowWorld(conn, tmp_path, proposing("propose_plan", two))
    approval, _ = w.propose()
    assert not w.press(approval, 1, "ok").closed
    assert w.follow_up(approval) is None
    assert len(w.agent.runs) == 1


def test_un_elemento_que_falla_sigue_pendiente_y_no_cierra(conn, tmp_path):
    w = FollowWorld(
        conn, tmp_path, proposing("propose_plan", {"items": [CREATE], "follow_up": True})
    )
    approval, _ = w.propose()
    w.api.error = TimeoutError()
    result = w.press(approval, 1, "ok")
    assert result.code == "handler_failed" and not result.closed
    assert w.follow_up(approval) is None


@pytest.mark.parametrize(
    ("tool", "args", "option"),
    [
        ("propose_plan", {"items": [CREATE], "follow_up": True}, "edit"),
        ("propose_change", QUIETER | {"follow_up": True}, "edit"),
    ],
)
def test_tras_cambiar_no_se_reanuda(conn, tmp_path, tool, args, option):
    """[✏️ Cámbialo] y [✏️ Cambiar] ya preguntan «¿cómo lo quieres?»: la respuesta de Pedro sigue
    la conversación."""
    w = FollowWorld(conn, tmp_path, proposing(tool, args))
    approval, _ = w.propose()
    assert w.press(approval, 1, option).closed
    assert w.follow_up(approval) is None
    assert len(w.agent.runs) == 1


def test_si_pedro_escribio_despues_no_se_reanuda(conn, tmp_path):
    w = FollowWorld(
        conn,
        tmp_path,
        proposing("propose_plan", {"items": [CREATE], "follow_up": True}, [result_message("Vale")]),
    )
    approval, _ = w.propose()
    w.clock.moment = madrid("2030-01-07T10:20")
    w.say("otra cosa")
    w.press(approval, 1, "ok")
    assert w.follow_up(approval) is None
    assert len(w.agent.runs) == 2


def test_si_la_reanudacion_falla_no_se_envia_nada(conn, tmp_path):
    w = FollowWorld(
        conn,
        tmp_path,
        proposing(
            "propose_plan",
            {"items": [CREATE], "follow_up": True},
            [result_message(None, api_status=529, is_error=True)],
        ),
    )
    approval, _ = w.propose()
    w.press(approval, 1, "ok")
    assert w.follow_up(approval) is None
    assert len(w.agent.runs) == 2


def test_una_propuesta_caducada_no_se_reanuda(conn, tmp_path):
    """Al caducar, todo queda «caducado» y nada pendiente: solo el estado de la aprobación dice
    que nadie la cerró con un toque."""
    w = FollowWorld(
        conn, tmp_path, proposing("propose_plan", {"items": [CREATE], "follow_up": True})
    )
    approval, _ = w.propose()
    w.clock.moment = madrid("2030-01-08T00:01")
    sweep(conn, w.clock)
    assert w.follow_up(approval) is None
    assert len(w.agent.runs) == 1


# --- La reanudación no tiene origen ------------------------------------------------------------


def test_en_la_reanudacion_propose_change_se_rechaza_y_nada_se_ejecuta_al_momento(conn, tmp_path):
    """Sin mensaje de Pedro: ni cambios de memoria ni ejecución inmediata, aunque el doble marque
    `requested_by_pedro` en una ejecución limpia (versión incorrecta 6)."""
    other = CREATE | {"title": "Otra más"}
    w = FollowWorld(
        conn,
        tmp_path,
        proposing(
            "propose_plan",
            {"items": [CREATE], "follow_up": True},
            [
                ToolCall("propose_change", QUIETER),
                ToolCall("propose_plan", {"items": [other], "requested_by_pedro": True}),
                result_message("Y esta otra"),
            ],
        ),
    )
    approval, _ = w.propose()
    w.press(approval, 1, "ok")
    writes = w.writes()
    reply = w.follow_up(approval)
    assert isinstance(reply, Reply) and reply.outgoing is not None
    assert reply.outgoing.kind == PLAN_KIND  # con sus botones, no «✅ Hecho»
    assert reply.more == ()
    assert w.writes() == writes
    change, plan = w.agent.tool_outcomes[-2:]
    assert change.is_error and "Fuera de una conversación" in change.text
    assert not plan.is_error


# --- La regla, sin agente (`follow_up_note`) ---------------------------------------------------


def note_for(*items, kind: str = PLAN_KIND):
    ok, edit = Option(key="ok", label="✅"), Option(key="edit", label="✏️", approves=False)
    built = tuple(
        Item(title="t", options=(ok, edit), data=data, result=result, chosen=chosen)
        for data, result, chosen in items
    )
    return follow_up_note(kind, InteractiveMessage(header="h", items=built))


def test_la_nota_dice_lo_que_paso_con_cada_elemento():
    note = note_for(
        (CREATE | {"follow_up": True}, "✓ creada", "ok"),
        ({"action": "idea", "text": "una app de lectura"}, "📝 apuntada", "ok"),
    )
    assert note is not None
    assert "crear «Tarea nueva» (2030-01-07T18:00) — ✓ creada" in note
    assert "idea «una app de lectura» — 📝 apuntada" in note


def test_la_regla_no_da_nota_con_un_elemento_pendiente():
    """Versión incorrecta 3, en su capa: la regla sola también para con una fila pendiente."""
    assert note_for((CREATE | {"follow_up": True}, None, None), (CREATE, "✓ creada", "ok")) is None


def test_la_regla_no_da_nota_sin_follow_up_tras_editar_o_en_otra_clase():
    assert note_for((CREATE, "✓ creada", "ok")) is None
    assert note_for((CREATE | {"follow_up": True}, "✏️ ¿cómo la quieres?", "edit")) is None
    assert note_for((CREATE | {"follow_up": True}, "✓ creada", "ok"), kind="undo") is None


# --- El contexto de [✏️] (decisión 1.14-h) -------------------------------------------------------

EDIT_LINE = "[lun 07/01 10:15] (JimiC) Pedro tocó ✏️ en: crear «Tarea nueva» (2030-01-07T18:00)"


def edited(w: FollowWorld) -> int:
    """Pedro recibe la propuesta y toca [✏️] en su primer elemento; el canal avisa del cierre."""
    approval, _ = w.propose()
    result = w.press(approval, 1, "edit")
    if result.closed:
        assert w.follow_up(approval) is None  # con [✏️] no hay reanudación
    return approval


def test_tras_cambialo_la_linea_llega_con_el_siguiente_mensaje_y_no_con_el_de_despues(
    conn, tmp_path
):
    w = FollowWorld(
        conn,
        tmp_path,
        proposing(
            "propose_plan",
            {"items": [CREATE]},
            [result_message("¿A qué hora te va?")],
            [result_message("Vale")],
        ),
    )
    edited(w)
    w.say("mejor a las 19")
    assert w.agent.runs[1][0] == f"{EDIT_LINE}\n[lun 07/01 10:15] mejor a las 19"
    w.say("gracias")
    assert w.agent.runs[2][0] == "[lun 07/01 10:15] gracias"


def test_otro_dia_la_linea_no_llega(conn, tmp_path):
    w = FollowWorld(
        conn,
        tmp_path,
        proposing("propose_plan", {"items": [CREATE]}, [result_message("Buenos días")]),
    )
    edited(w)
    w.clock.moment = madrid("2030-01-08T09:00")
    w.say("buenos días")
    assert w.agent.runs[1][0] == "[mar 08/01 09:00] buenos días"


def test_la_linea_no_entra_en_el_origen(conn, tmp_path):
    """Versión incorrecta: si la línea del código entrara en el origen, un cambio de memoria
    nacería de algo que Pedro no escribió."""
    text = "y no me escribas antes de las 8"
    w = FollowWorld(
        conn,
        tmp_path,
        proposing(
            "propose_plan",
            {"items": [CREATE]},
            [ToolCall("propose_change", QUIETER), result_message("¿Lo guardo?")],
        ),
    )
    edited(w)
    reply = w.say(text)
    assert EDIT_LINE in w.agent.runs[1][0]  # la línea llegó al agente...
    assert isinstance(reply, Reply) and reply.outgoing is not None
    assert reply.outgoing.kind == MEMORY_KIND
    assert isinstance(reply.outgoing.message, InteractiveMessage)
    (item,) = reply.outgoing.message.items
    assert (item.data or {})["origin"] == text  # ...pero el origen es solo su texto


def test_con_cambiar_de_la_memoria_la_linea_llega_igual(conn, tmp_path):
    w = FollowWorld(
        conn,
        tmp_path,
        proposing("propose_change", QUIETER, [result_message("¿Cómo lo quieres?")]),
    )
    edited(w)
    w.say("mejor a las 9")
    assert w.agent.runs[1][0] == (
        "[lun 07/01 10:15] (JimiC) Pedro tocó ✏️ en: limites.yaml · cambiar\n"
        "[lun 07/01 10:15] mejor a las 9"
    )


def test_si_otro_toque_cierra_el_mensaje_la_linea_se_olvida(conn, tmp_path):
    two = {"items": [CREATE, CREATE | {"title": "Otra"}]}
    w = FollowWorld(conn, tmp_path, proposing("propose_plan", two, [result_message("Vale")]))
    approval = edited(w)
    result = w.press(approval, 2, "ok")
    assert result.closed
    w.follow_up(approval)  # el canal avisa del cierre
    w.say("hecho")
    assert w.agent.runs[1][0] == "[lun 07/01 10:15] hecho"


def test_con_otro_toque_que_no_cierra_la_linea_sigue(conn, tmp_path):
    """Lo que olvida la línea es que el mensaje se cierre, no cualquier toque."""
    three = {"items": [CREATE, CREATE | {"title": "Otra"}, CREATE | {"title": "Tercera"}]}
    w = FollowWorld(conn, tmp_path, proposing("propose_plan", three, [result_message("Vale")]))
    approval = edited(w)
    assert not w.press(approval, 2, "ok").closed
    w.say("la primera, a las 19")
    assert w.agent.runs[1][0].startswith(EDIT_LINE + "\n")
