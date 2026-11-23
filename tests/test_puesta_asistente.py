"""La puesta en marcha de punta a punta (tarea 1.13): `/puesta_en_marcha`, el bloque del *system
prompt*, la regla asimétrica (1.13-a), la sugerencia del día (1.13-b), APEX y el final.

Con ficheros inventados en una carpeta temporal, un APEX inventado y el doble del agente, que
pasa por los *hooks* y el servidor de las opciones, como el motor. Ningún test toca los ficheros
de Pedro.
"""

import asyncio
import logging
from datetime import timedelta
from functools import partial

import pytest
from agent_data import FakeAgent, ToolCall, result_message
from conftest import VALID_ENV
from execution_data import AssistantWorld
from heartbeat_data import (
    MADRID,
    OFFERS,
    SECRET_TEXT,
    FakeMessages,
    FakeSender,
    answer,
    api_message,
    limits,
    madrid,
    sources,
)
from heartbeat_data import intentions as heartbeat_intentions
from memory_data import (
    AREA_NAMES,
    QUIET_LINE,
    ROUTINE,
    area_text,
    shas,
    write_finished,
)

from jimic.agent.conversation import Conversation
from jimic.channel import store
from jimic.channel.bot import Incoming, Reply
from jimic.channel.message import Tap
from jimic.channel.taps import process_tap
from jimic.config import Settings
from jimic.execution.assistant import ALREADY_DONE, SUGGESTION, Assistant
from jimic.execution.commands import Commands
from jimic.execution.memory_message import FINISHED_TEXT, MEMORY_KIND, MemoryHandlers
from jimic.execution.summary import summarize
from jimic.heartbeat.beat import Heartbeat
from jimic.heartbeat.decide import HaikuDecider
from jimic.memory import changes
from jimic.memory.changes import Proposed
from jimic.memory.database import open_database
from jimic.memory.live import LiveConfig
from jimic.memory.onboarding import create_skeletons, progress
from jimic.memory.personal import init

COMMAND = "/puesta_en_marcha"
ROUTINE_MARK = "Rutina-secreta-de-Pedro-5521"
GOAL_MARK = "Meta-secreta-de-Pedro-3390"
ANSWER_MARK = "Respuesta-secreta-del-agente-7184"
PEDRO_MARK = "Mensaje-secreto-de-Pedro-2207"


def quiet(start: str, end: str) -> dict:
    return {
        "file": "limites.yaml",
        "operation": "replace",
        "before": QUIET_LINE,
        "after": f'quiet_hours: {{from: "{start}", to: "{end}"}}',
        "reason": "Pedro lo pide",
    }


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


class OnboardingWorld(AssistantWorld):
    """The assistant with the onboarding on: only the two YAML files of `jimic config init`
    at the start, as on the first day."""

    def __init__(self, conn, tmp_path, agent, finished=False, **kwargs):
        super().__init__(conn, tmp_path, agent, **kwargs)
        self.config_dir = self.paths.config_dir
        if finished:
            write_finished(self.config_dir)
        else:
            init(self.config_dir)
        self.live = LiveConfig(heartbeat_intentions(), self.limits, self.config_dir)
        self.memory = MemoryHandlers(
            conn=conn, clock=self.clock, config_dir=self.config_dir, config=self.live
        )
        self.restart()

    def restart(self) -> None:
        """A new conversation and assistant over the same database, as after a restart."""
        self.conversation = Conversation(
            settings=Settings.model_validate(VALID_ENV),
            paths=self.paths,
            limits=self.limits,
            clock=self.clock,
            conn=self.conn,
            sources=self.sources,
            run_agent=self.agent,
        )
        self.assistant = Assistant(
            conversation=self.conversation,
            executor=self.executor,
            conn=self.conn,
            clock=self.clock,
            timezone=MADRID,
            commands=Commands(
                conn=self.conn,
                clock=self.clock,
                timezone=MADRID,
                config_dir=self.config_dir,
                config=self.live,
                todoist=self.todoist,
                summarize=partial(summarize, FakeMessages().create, "claude-haiku-5-5"),
                summary_model="claude-haiku-5-5",
            ),
            config_dir=self.config_dir,
        )

    def say(self, text, reply_to=None, last_sent=None):
        return asyncio.run(
            self.assistant.reply(Incoming(text, reply_to=reply_to, last_sent=last_sent))
        )

    def prompt(self, run: int = -1) -> str:
        system = self.agent.runs[run][1].system_prompt
        assert isinstance(system, dict) and system["type"] == "custom"
        return system["prompt"]

    def send(self, reply: str | Reply) -> list[int]:
        if isinstance(reply, str):
            return []
        return [
            store.create(self.conn, self.clock, o.kind, o.message, o.expires_at)
            for o in (reply.outgoing, *reply.more)
            if o is not None
        ]

    def tap_memory(self, approval_id: int, option: str = "save"):
        handlers = {MEMORY_KIND: self.memory.memory}
        return asyncio.run(
            process_tap(self.conn, self.clock, handlers, Tap(approval_id, 1, option))
        )

    def save(self, name: str, before: str, after: str) -> int:
        return changes.save(
            self.conn,
            self.clock,
            self.config_dir,
            Proposed(name, "replace", before, after),
            "inventado",
            None,
        )


def text_of(reply: str | Reply) -> str:
    if isinstance(reply, str):
        return reply
    return reply.text or ""


def answers(n: int) -> FakeAgent:
    return FakeAgent(*[[result_message(f"Respuesta {i}")] for i in range(1, n + 1)])


# --- /puesta_en_marcha --------------------------------------------------------------------------


def test_el_comando_crea_los_esqueletos_y_va_a_la_conversacion_del_dia(conn, tmp_path):
    w = OnboardingWorld(conn, tmp_path, FakeAgent([result_message("Empezamos por la rutina")]))

    reply = w.say(COMMAND)

    assert reply == "Empezamos por la rutina"
    assert all((w.config_dir / name).exists() for name in ("rutina.md", "objetivos.md"))
    ((prompt_sent, _),) = w.agent.runs
    assert prompt_sent == f"[lun 07/01 10:15] {COMMAND}"
    assert "## La puesta en marcha" in w.prompt()
    assert "Sigue por: la rutina: Horario base, Comidas, Jornada, Fines de semana." in w.prompt()
    assert "Ya guardado (no lo vuelvas a preguntar): nada aún." in w.prompt()


@pytest.mark.parametrize(
    "text", ["/puesta-en-marcha", "/puesta_en_marcha@jimic_bot", " /puesta_en_marcha "]
)
def test_el_alias_y_las_formas_del_comando(conn, tmp_path, text):
    w = OnboardingWorld(conn, tmp_path, FakeAgent([result_message("Vamos")]))

    assert w.say(text) == "Vamos"
    assert (w.config_dir / "objetivos.md").exists()


def test_con_mas_texto_no_es_el_comando(conn, tmp_path):
    w = OnboardingWorld(conn, tmp_path, FakeAgent([result_message("Vale")]))

    w.say(f"{COMMAND} mañana")

    assert not (w.config_dir / "objetivos.md").exists()


def test_el_comando_no_sobrescribe_lo_que_pedro_ya_escribio(conn, tmp_path):
    w = OnboardingWorld(conn, tmp_path, FakeAgent([result_message("Vamos")]))
    (w.config_dir / "rutina.md").write_text(ROUTINE, encoding="utf-8")

    w.say(COMMAND)

    assert (w.config_dir / "rutina.md").read_text(encoding="utf-8") == ROUTINE
    assert "Ya guardado (no lo vuelvas a preguntar): la rutina." in w.prompt()


def test_con_la_puesta_en_marcha_terminada_el_comando_lo_contesta_el_codigo(conn, tmp_path):
    w = OnboardingWorld(conn, tmp_path, FakeAgent(), finished=True)
    before = shas(w.config_dir)

    assert w.say(COMMAND) == ALREADY_DONE
    assert w.agent.runs == []
    assert shas(w.config_dir) == before


def test_sin_empezar_o_terminada_el_prompt_no_lleva_el_bloque(conn, tmp_path):
    w = OnboardingWorld(conn, tmp_path, answers(2))
    w.say("Hola")
    assert "## La puesta en marcha" not in w.prompt()

    write_finished(w.config_dir)
    w.say("Hola otra vez")
    assert "## La puesta en marcha" not in w.prompt()


def test_propose_change_antes_y_despues_del_comando(conn, tmp_path):
    rutina = {
        "file": "rutina.md",
        "operation": "replace",
        "before": "## Comidas",
        "after": "## Comidas\n\n- Como a las 14:00",
        "reason": "r",
    }
    agent = FakeAgent(
        [ToolCall("propose_change", rutina), result_message("Te lo propongo")],
        [ToolCall("propose_change", rutina), result_message("Te lo propongo")],
    )
    w = OnboardingWorld(conn, tmp_path, agent)

    assert w.say("Como a las 14:00") == "Te lo propongo\n\n" + SUGGESTION
    assert agent.tool_outcomes[0].is_error and "aún no existe" in agent.tool_outcomes[0].text

    reply = w.say(COMMAND)
    assert isinstance(reply, Reply) and reply.outgoing is not None
    assert reply.outgoing.kind == MEMORY_KIND
    assert not agent.tool_outcomes[1].is_error


# --- Reanudable ---------------------------------------------------------------------------------


def test_reanudable_el_bloque_sigue_por_la_tercera_area_y_vuelve_tras_deshacer(conn, tmp_path):
    w = OnboardingWorld(conn, tmp_path, answers(3))
    create_skeletons(w.config_dir)
    (w.config_dir / "rutina.md").write_text(ROUTINE, encoding="utf-8")
    first, second = AREA_NAMES[:2]
    w.save("objetivos.md", f"## {first}", f"## {first}\n\n{area_text(first)}")
    last = w.save("objetivos.md", f"## {second}", f"## {second}\n\n{area_text(second)}")

    w.say(COMMAND)

    block = w.prompt().split("## La puesta en marcha", 1)[1]
    assert "Sigue por: el área «Hábitos» de los objetivos." in block
    missing = block.split("Falta: ", 1)[1].split("\n", 1)[0]
    assert "Gestión del tiempo" not in missing and "Entrenamiento" not in missing
    assert "la rutina" not in missing
    assert "el área «Gestión del tiempo»; el área «Entrenamiento»" in block

    assert changes.undo(conn, w.clock, w.config_dir, last) == "undone"
    w.say("Sigamos")
    assert "Sigue por: el área «Entrenamiento» de los objetivos." in w.prompt()


# --- La regla asimétrica (1.13-a) ---------------------------------------------------------------


def test_silencio_de_0_a_6_no_ofrece_guardar_y_dice_la_clave_a_mano(conn, tmp_path):
    agent = FakeAgent(
        [result_message("Vamos")],
        [ToolCall("propose_change", quiet("00:00", "06:00")), result_message("Eso, a mano")],
    )
    w = OnboardingWorld(conn, tmp_path, agent)
    w.say(COMMAND)
    before = shas(w.config_dir)

    reply = w.say("Silencio de 0:00 a 6:00")

    assert isinstance(reply, str)  # sin mensaje con [Guardar]
    assert agent.tool_outcomes[0].is_error
    assert "quiet_hours" in reply
    assert 'que quedaría «quiet_hours: {from: "00:00", to: "06:00"}»' in reply
    assert "a mano en limites.yaml" in reply and "sesión de configuración" in reply
    assert shas(w.config_dir) == before


def test_silencio_de_22_a_8_se_ofrece_con_guardar(conn, tmp_path):
    agent = FakeAgent(
        [result_message("Vamos")],
        [ToolCall("propose_change", quiet("22:00", "08:00")), result_message("Te lo propongo")],
    )
    w = OnboardingWorld(conn, tmp_path, agent)
    w.say(COMMAND)

    reply = w.say("Silencio de 22:00 a 8:00")

    assert isinstance(reply, Reply) and reply.outgoing is not None
    assert reply.outgoing.kind == MEMORY_KIND
    assert reply.text == "Te lo propongo"
    (approval,) = w.send(reply)
    assert w.tap_memory(approval).code == "resolved"
    assert w.live.limits.quiet_hours.start.hour == 22


def test_la_nota_de_lo_que_se_cambia_a_mano_no_sale_si_la_ejecucion_leyo_despues(conn, tmp_path):
    """Una inyección no puede decirle a Pedro qué relajar: la ejecución leyó el calendario."""
    agent = FakeAgent(
        [
            ToolCall("propose_change", quiet("00:00", "06:00")),
            ToolCall("read_calendar", {}),
            result_message("Vale"),
        ]
    )
    w = OnboardingWorld(conn, tmp_path, agent, finished=True)

    reply = w.say("¿Qué tengo hoy?")

    assert reply == "Vale"


# --- La sugerencia del día (1.13-b) -------------------------------------------------------------


def test_la_sugerencia_sale_en_la_primera_respuesta_del_dia_y_no_en_la_segunda(conn, tmp_path):
    w = OnboardingWorld(conn, tmp_path, answers(2))

    assert w.say("Hola") == f"Respuesta 1\n\n{SUGGESTION}"
    assert w.say("¿Qué tal?") == "Respuesta 2"


def test_la_sugerencia_vuelve_al_dia_siguiente(conn, tmp_path):
    w = OnboardingWorld(conn, tmp_path, answers(3))
    w.say("Hola")
    w.clock.moment = madrid("2030-01-07T23:59:59")
    assert w.say("Buenas noches") == "Respuesta 2"

    w.clock.moment = madrid("2030-01-08T00:00")

    assert w.say("Ya es mañana") == f"Respuesta 3\n\n{SUGGESTION}"


def test_un_reinicio_el_mismo_dia_no_la_repite(conn, tmp_path):
    w = OnboardingWorld(conn, tmp_path, answers(2))
    w.say("Hola")

    w.restart()

    assert w.say("He reiniciado") == "Respuesta 2"


def test_no_sale_con_la_puesta_en_marcha_terminada(conn, tmp_path):
    w = OnboardingWorld(conn, tmp_path, answers(1), finished=True)

    assert w.say("Hola") == "Respuesta 1"


def test_el_comando_gasta_la_sugerencia_del_dia(conn, tmp_path):
    w = OnboardingWorld(conn, tmp_path, answers(2))

    assert w.say(COMMAND) == "Respuesta 1"
    assert w.say("Lo dejamos aquí") == "Respuesta 2"


def test_no_sale_en_memoria_ni_en_deshacer_y_no_los_gasta(conn, tmp_path):
    w = OnboardingWorld(conn, tmp_path, answers(1))

    for command in ("/memoria", "/deshacer"):
        assert SUGGESTION not in text_of(w.say(command))
    assert w.agent.runs == []

    assert w.say("Hola") == f"Respuesta 1\n\n{SUGGESTION}"


def test_no_sale_si_la_ejecucion_falla_y_queda_para_la_siguiente(conn, tmp_path):
    agent = FakeAgent([result_message(None, api_status=529, is_error=True)], [result_message("Ya")])
    w = OnboardingWorld(conn, tmp_path, agent)

    assert SUGGESTION not in text_of(w.say("Hola"))
    assert w.say("¿Estás?") == f"Ya\n\n{SUGGESTION}"


def test_no_sale_en_el_latido_ni_gasta_cupo(conn, tmp_path):
    w = OnboardingWorld(conn, tmp_path, answers(1))
    sender = FakeSender()
    heartbeat = Heartbeat(
        clock=w.clock,
        timezone=MADRID,
        limits=limits(),
        intentions=heartbeat_intentions(OFFERS),
        sources=sources(),
        conn=conn,
        decider=HaikuDecider(
            FakeMessages(api_message(answer(True, "ofertas", SECRET_TEXT))).create,
            "claude-haiku-5-5",
            heartbeat_intentions(OFFERS),
        ),
        sender=sender,
    )
    w.clock.moment = madrid("2030-01-07T16:30")
    asyncio.run(heartbeat.beat())
    assert sender.texts == [SECRET_TEXT]

    w.clock.moment += timedelta(minutes=5)
    assert w.say("Hola") == f"Respuesta 1\n\n{SUGGESTION}"
    assert conn.execute("SELECT COUNT(*) FROM notices").fetchone() == (1,)


# --- Entrenamiento y APEX -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "after",
    [
        "## Entrenamiento\n\nMeta: ganar fuerza\nPrioridad: alta\n\n- [medible] Subir la fuerza",
        "## Entrenamiento\n\nMeta: ganar fuerza\nPrioridad: alta\n\n- [declarado] subir  LA fuerza",
        "## Entrenamiento\n\nMeta: moverse más\nPrioridad: alta\n",
    ],
    ids=["la-descripcion-exacta", "con-mayusculas-y-espacios", "en-la-meta"],
)
def test_ninguna_propuesta_copia_a_objetivos_los_de_apex(conn, tmp_path, after):
    change = {
        "file": "objetivos.md",
        "operation": "replace",
        "before": "## Entrenamiento",
        "after": after,
        "reason": "r",
    }
    agent = FakeAgent(
        [result_message("Vamos")],
        [
            ToolCall("read_apex_today", {}),
            ToolCall("propose_change", change),
            result_message("Los tienes en APEX"),
        ],
    )
    w = OnboardingWorld(conn, tmp_path, agent)
    w.say(COMMAND)
    before = shas(w.config_dir)

    reply = w.say("Mis objetivos de entrenamiento son los de APEX")

    assert reply == "Los tienes en APEX"
    refused = agent.tool_outcomes[1]
    assert refused.is_error and "APEX" in refused.text
    assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone() == (0,)
    assert shas(w.config_dir) == before


def test_la_meta_y_la_prioridad_del_entrenamiento_si_se_ofrecen(conn, tmp_path):
    change = {
        "file": "objetivos.md",
        "operation": "replace",
        "before": "## Entrenamiento",
        "after": "## Entrenamiento\n\nMeta: ganar fuerza este año\nPrioridad: alta",
        "reason": "r",
    }
    agent = FakeAgent(
        [result_message("Vamos")],
        [ToolCall("read_apex_today", {}), ToolCall("propose_change", change), result_message()],
    )
    w = OnboardingWorld(conn, tmp_path, agent)
    w.say(COMMAND)

    reply = w.say("Quiero ganar fuerza, alta")

    assert isinstance(reply, Reply) and reply.outgoing is not None
    assert reply.outgoing.kind == MEMORY_KIND


# --- El final -----------------------------------------------------------------------------------


def test_el_guardar_que_termina_lo_dice_con_el_resumen_y_deja_de_sugerir(conn, tmp_path):
    icon = {
        "file": "preferencias.md",
        "operation": "add",
        "after": "| Gimnasio | 🏋️ |",
        "reason": "r",
    }
    agent = FakeAgent(
        [ToolCall("propose_change", icon), result_message("Te lo propongo")],
        [result_message("Otra cosa")],
    )
    w = OnboardingWorld(conn, tmp_path, agent, finished=True)
    preferences = w.config_dir / "preferencias.md"
    preferences.write_text(
        preferences.read_text(encoding="utf-8").replace("| Gimnasio | 🏋️ |\n", ""),
        encoding="utf-8",
    )
    assert progress(w.config_dir).in_progress

    (approval,) = w.send(w.say("El gimnasio con 🏋️"))
    result = w.tap_memory(approval)

    assert result.code == "resolved"
    (shown,) = result.send
    assert shown.startswith(FINISHED_TEXT)
    assert "Tus metas por prioridad:\nAlta: Gestión del tiempo" in shown
    assert progress(w.config_dir).finished
    w.clock.moment += timedelta(days=1)
    assert w.say("Buenos días") == "Otra cosa"
    assert "## La puesta en marcha" not in w.prompt()


def test_un_guardar_con_la_puesta_en_marcha_ya_terminada_no_repite_el_final(conn, tmp_path):
    line = {"file": "preferencias.md", "operation": "add", "after": "- Sin emojis", "reason": "r"}
    agent = FakeAgent([ToolCall("propose_change", line), result_message("Va")])
    w = OnboardingWorld(conn, tmp_path, agent, finished=True)

    (approval,) = w.send(w.say("Sin emojis"))
    result = w.tap_memory(approval)

    assert result.code == "resolved" and result.send == ()


# --- Logs ---------------------------------------------------------------------------------------


def test_los_logs_no_llevan_contenido(conn, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    routine = {
        "file": "rutina.md",
        "operation": "replace",
        "before": "## Comidas",
        "after": f"## Comidas\n\n- {ROUTINE_MARK}",
        "reason": ROUTINE_MARK,
    }
    goal = {
        "file": "objetivos.md",
        "operation": "replace",
        "before": "## Entrenamiento",
        "after": f"## Entrenamiento\n\nMeta: {GOAL_MARK}\nPrioridad: alta\n\n- Subir la fuerza",
        "reason": GOAL_MARK,
    }
    agent = FakeAgent(
        [result_message(ANSWER_MARK)],
        [ToolCall("propose_change", routine), result_message(ANSWER_MARK)],
        [
            ToolCall("propose_change", quiet("00:00", "06:00")),
            ToolCall("read_apex_today", {}),
            ToolCall("propose_change", goal),
            result_message(ANSWER_MARK),
        ],
    )
    w = OnboardingWorld(conn, tmp_path, agent)
    w.say(f"{COMMAND}")
    (approval,) = w.send(w.say(PEDRO_MARK))
    w.tap_memory(approval)
    w.say(PEDRO_MARK)

    assert ROUTINE_MARK in (w.config_dir / "rutina.md").read_text(encoding="utf-8")
    for mark in (ROUTINE_MARK, GOAL_MARK, ANSWER_MARK, PEDRO_MARK, "00:00", "fuerza"):
        assert mark not in caplog.text
    assert "onboarding: 3 skeleton(s) created" in caplog.text
    assert "memory proposal refused (relaxes)" in caplog.text
    assert "memory proposal refused (apex_copy)" in caplog.text
