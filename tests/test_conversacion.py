"""La conversación con el agente: sesión del día, un turno a la vez, tope, gasto y fallos.

El SDK es un doble (`FakeAgent`) con tope que devuelve mensajes de verdad del SDK; no se arranca
ningún subproceso (la guarda `no_real_agent` lo impediría). Todo es inventado.
"""

import asyncio
import logging
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from agent_data import (
    AGENT_ANSWER,
    PEDRO_MESSAGE,
    FakeAgent,
    ToolCall,
    agent_sources,
    result_message,
)
from claude_agent_sdk import CLINotFoundError, ProcessError, ResultError, SystemMessage
from conftest import VALID_ENV, FixedClock
from heartbeat_data import MADRID, limits, madrid

from jimic.agent.conversation import (
    API_DOWN_TEXT,
    CAPPED_TEXT,
    DAILY_CAP_TEXT,
    FAILED_TEXT,
    Conversation,
)
from jimic.agent.tools import RunMode
from jimic.config import Settings
from jimic.memory import conversations
from jimic.memory.database import open_database
from jimic.memory.usage import TokenUsage, record_cost, spent_on
from jimic.paths import Paths


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


AGENT_MODEL = "claude-sonnet-5-5"
"""A model with a price (the cost of a run comes from its tokens)."""


class World:
    """A conversation with its doubles, and a clock the test moves."""

    def __init__(self, conn, tmp_path, agent: FakeAgent | None = None, **limit_changes):
        self.conn = conn
        self.clock = FixedClock(madrid("2030-01-07T10:15"))
        self.agent = agent if agent is not None else FakeAgent()
        self.paths = Paths("prod", tmp_path / "config", tmp_path / "data")
        self.paths.config_dir.mkdir()
        self.paths.data_dir.mkdir()
        self.conversation = Conversation(
            settings=Settings.model_validate({**VALID_ENV, "MODELO_AGENTE": AGENT_MODEL}),
            paths=self.paths,
            limits=limits(**limit_changes),
            clock=self.clock,
            conn=conn,
            sources=agent_sources(),
            run_agent=self.agent,
        )

    def say(self, text: str = "Hola", at: str | datetime | None = None) -> str:
        if at is not None:
            self.clock.moment = at if isinstance(at, datetime) else madrid(at)
        return asyncio.run(self.conversation.reply(text))

    def usage_rows(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT day, model, input_tokens, output_tokens, cache_read_tokens,"
            " cache_write_tokens, usd FROM model_usage ORDER BY id"
        ).fetchall()

    def notices(self) -> list[tuple]:
        return self.conn.execute("SELECT kind, detail FROM system_notices ORDER BY id").fetchall()


def utc(moment: str) -> datetime:
    return datetime.fromisoformat(moment).replace(tzinfo=UTC)


# --- La respuesta y la sesión del día (1.10-a) -------------------------------------------------


def test_un_mensaje_llega_al_agente_con_la_hora_y_vuelve_su_respuesta(conn, tmp_path):
    world = World(conn, tmp_path)
    assert world.say("¿Qué tengo hoy?") == AGENT_ANSWER
    ((prompt, options),) = world.agent.runs
    assert prompt == "[lun 07/01 10:15] ¿Qué tengo hoy?"
    assert options.resume is None
    assert conversations.session_of(conn, date(2030, 1, 7)) == "sesion-1"


def test_el_segundo_mensaje_del_dia_reanuda_la_sesion(conn, tmp_path):
    world = World(conn, tmp_path)
    world.say(at="2030-01-07T10:15")
    world.say(at="2030-01-07T22:59")
    assert world.agent.resumed == [None, "sesion-1"]


@pytest.mark.parametrize(
    ("first", "second", "resumes"),
    [
        # 23:59 y 00:01 en invierno: otro día.
        (utc("2030-01-07T22:59"), utc("2030-01-07T23:01"), False),
        # 31-mar: 23:00 CEST y 00:30 CEST del 1-abr (22:30Z). Un desfase fijo +1 lo pondría el 31.
        (utc("2030-03-31T21:00"), utc("2030-03-31T22:30"), False),
        # 27-oct: 09:00 CET y 23:30 CET (22:30Z), el mismo día. Un desfase fijo +2 lo pasaría al 28.
        (utc("2030-10-27T08:00"), utc("2030-10-27T22:30"), True),
        # 31-mar: 01:30 CET y 03:30 CEST, el mismo día.
        (utc("2030-03-31T00:30"), utc("2030-03-31T01:30"), True),
    ],
)
def test_la_sesion_es_la_del_dia_local_tambien_con_el_cambio_de_hora(
    conn, tmp_path, first, second, resumes
):
    """Pilla una sesión que no cambia de día (versión incorrecta 4) o que cuenta el día con un
    desfase fijo o en UTC."""
    world = World(conn, tmp_path)
    world.say(at=first.astimezone(MADRID))
    world.say(at=second.astimezone(MADRID))
    assert world.agent.resumed == [None, "sesion-1" if resumes else None]


def test_si_el_motor_no_puede_reanudar_la_sesion_se_olvida(conn, tmp_path):
    """Un fichero de sesión perdido no puede dejar el resto del día sin conversación."""
    world = World(
        conn,
        tmp_path,
        FakeAgent([result_message()], ProcessError("x", 1), [result_message(session="sesion-2")]),
    )
    world.say(at="2030-01-07T10:15")
    assert world.say(at="2030-01-07T10:20") == FAILED_TEXT
    world.say(at="2030-01-07T10:25")
    assert world.agent.resumed == [None, "sesion-1", None]
    assert conversations.session_of(conn, date(2030, 1, 7)) == "sesion-2"


def test_la_carpeta_del_agente_es_privada(conn, tmp_path):
    world = World(conn, tmp_path)
    world.say()
    assert (world.paths.agent_dir.stat().st_mode & 0o777) == 0o700
    assert world.agent.runs[0][1].cwd == world.paths.agent_dir


# --- Un turno a la vez -------------------------------------------------------------------------


def test_un_turno_a_la_vez_y_en_orden_y_la_hora_se_anota_al_llegar(conn, tmp_path):
    """Pedro escribe dos veces mientras el agente piensa: el segundo espera su turno, sale
    después del primero y su hora ya está anotada mientras espera (1.10-c)."""
    world = World(conn, tmp_path)
    world.agent.gate = asyncio.Event()

    async def scenario():
        first = asyncio.create_task(world.conversation.reply("primero"))
        while not world.agent.runs:
            await asyncio.sleep(0)
        world.clock.moment = madrid("2030-01-07T10:16")
        second = asyncio.create_task(world.conversation.reply("segundo"))
        for _ in range(20):
            await asyncio.sleep(0)
        assert len(world.agent.runs) == 1
        assert conversations.last_message_at(conn) == madrid("2030-01-07T10:16")
        assert world.agent.gate is not None
        world.agent.gate.set()
        return await first, await second

    assert asyncio.run(scenario()) == (AGENT_ANSWER, AGENT_ANSWER)
    assert world.agent.most_active == 1
    assert [prompt.split("] ")[1] for prompt, _ in world.agent.runs] == ["primero", "segundo"]


# --- El gasto -----------------------------------------------------------------------------------


def tokens(input_tokens, output_tokens, cache_read, cache_write) -> dict:
    """The `usage` of a result, as the SDK gives it (cache writes of 5 minutes)."""
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
        "cache_creation": {
            "ephemeral_5m_input_tokens": cache_write,
            "ephemeral_1h_input_tokens": 0,
        },
    }


def test_cada_ejecucion_suma_el_coste_de_sus_tokens_con_el_modelo_del_agente(conn, tmp_path):
    """4 de entrada, 39 de salida, 3.588 de lectura y 3.812 de escritura de caché con los precios
    de Sonnet (2, 10, 0,10 y 2,50 $ por millón): 0,0102868 $, calculado a mano. Pilla contar el
    `total_cost_usd` del SDK (0,0123 en el doble)."""
    world = World(conn, tmp_path)
    world.say()
    (row,) = world.usage_rows()
    assert row[:6] == ("2030-01-07", AGENT_MODEL, 4, 39, 3588, 3812)
    assert row[-1] == pytest.approx(0.0102868)


def test_dos_ejecuciones_de_la_misma_sesion_cuentan_cada_una_lo_suyo_y_no_el_acumulado(
    conn, tmp_path
):
    """El SDK da `total_cost_usd` acumulado (0,00602 y luego 0,00748); los tokens de cada
    ejecución valen 0,00602 y 0,00146 (a mano). El gasto del día es 0,00748, no 0,0135."""
    agent = FakeAgent(
        [result_message(cost=0.00602, usage=tokens(10, 100, 0, 2000))],
        [result_message(cost=0.00748, usage=tokens(5, 50, 2000, 300))],
    )
    world = World(conn, tmp_path, agent)
    world.say()
    world.say()
    assert [row[-1] for row in world.usage_rows()] == pytest.approx([0.00602, 0.00146])
    assert spent_on(conn, date(2030, 1, 7)) == pytest.approx(0.00748)


def test_un_resultado_con_tokens_solo_de_cache_cuenta_por_tokens(conn, tmp_path):
    world = World(
        conn, tmp_path, FakeAgent([result_message(cost=9.0, usage=tokens(0, 0, 1000, 0))])
    )
    world.say()
    assert world.usage_rows()[0][-1] == pytest.approx(0.0001)


@pytest.mark.parametrize("usage", [None, {}, tokens(0, 0, 0, 0)])
def test_sin_tokens_en_el_resultado_cuenta_el_tope_de_la_sesion(conn, tmp_path, usage):
    """Aunque el SDK dé un coste (9 $), sin tokens no hay con qué calcularlo: el tope."""
    message = result_message(cost=9.0, usage=usage if usage is not None else {})
    world = World(conn, tmp_path, FakeAgent([message]))
    world.say()
    assert world.usage_rows()[0][-1] == 0.5


def test_una_ejecucion_cortada_por_su_tope_tambien_cuenta_sus_tokens(conn, tmp_path):
    world = World(
        conn,
        tmp_path,
        FakeAgent(
            [
                result_message(
                    None,
                    subtype="error_max_turns",
                    is_error=True,
                    cost=0.2,
                    usage=tokens(10, 100, 0, 2000),
                )
            ]
        ),
    )
    assert world.say() == CAPPED_TEXT
    assert world.usage_rows()[0][-1] == pytest.approx(0.00602)


def test_con_el_gasto_real_bajo_el_tope_diario_el_agente_contesta_aunque_los_acumulados_lo_pasen(
    conn, tmp_path
):
    """14 mensajes de 0,04 $ de tokens (0,56 $ reales); los acumulados del SDK (0,04 · n) suman
    3,12 $ al llegar al 12.º. Con el fallo, los últimos mensajes ya no llegarían al agente."""
    each = tokens(0, 4000, 0, 0)  # 4.000 de salida a 10 $/M = 0,04 $
    scripts = [[result_message(cost=0.04 * n, usage=each)] for n in range(1, 15)]
    world = World(conn, tmp_path, FakeAgent(*scripts))
    replies = [world.say() for _ in range(14)]
    assert DAILY_CAP_TEXT not in replies
    assert len(world.agent.runs) == 14
    assert spent_on(conn, date(2030, 1, 7)) == pytest.approx(0.56)


def test_un_modelo_sin_precio_cuenta_el_tope_y_no_pierde_la_respuesta(conn, tmp_path):
    """Sin precio no se puede calcular: se cuenta lo máximo que pudo gastar, y la respuesta y la
    sesión del día se guardan igual."""
    world = World(conn, tmp_path)
    world.conversation._settings = Settings.model_validate(VALID_ENV)
    assert world.say() == AGENT_ANSWER
    assert world.usage_rows()[0][-1] == 0.5
    assert conversations.session_of(conn, date(2030, 1, 7)) is not None


def spend(conn, day: str, usd: str) -> None:
    record_cost(conn, date.fromisoformat(day), "modelo-inventado", TokenUsage(0, 0), Decimal(usd))


@pytest.mark.parametrize(("spent", "calls"), [("2.999", 1), ("3.0", 0), ("3.0001", 0)])
def test_con_el_tope_diario_alcanzado_no_se_llama_al_sdk(conn, tmp_path, spent, calls):
    """`daily_cap_usd` de 3 $: con 2,999 se llama; con 3 o más, ni una llamada."""
    world = World(conn, tmp_path)
    spend(conn, "2030-01-07", spent)
    reply = world.say()
    assert len(world.agent.runs) == calls
    assert (reply == DAILY_CAP_TEXT) is (calls == 0)


def test_el_tope_diario_es_de_hoy_y_mañana_se_sigue(conn, tmp_path):
    world = World(conn, tmp_path)
    spend(conn, "2030-01-07", "3.0")
    assert world.say(at="2030-01-07T20:00") == DAILY_CAP_TEXT
    assert world.say(at="2030-01-08T08:00") == AGENT_ANSWER


def test_con_tope_diario_cero_nunca_se_llama(conn, tmp_path):
    world = World(
        conn,
        tmp_path,
        budget={
            "monthly_target_usd": 30.0,
            "warn_at": 0.8,
            "daily_cap_usd": 0.0,
            "max_session_usd": 0.5,
        },
    )
    assert world.say() == DAILY_CAP_TEXT
    assert world.agent.runs == []


def test_con_el_tope_alcanzado_se_anota_el_aviso_del_presupuesto_si_toca(conn, tmp_path):
    world = World(conn, tmp_path)
    spend(conn, "2030-01-03", "21.0")
    spend(conn, "2030-01-07", "3.0")
    world.say()
    assert world.notices() == [("budget", "")]


def test_tras_una_ejecucion_se_mira_el_presupuesto_del_mes(conn, tmp_path):
    world = World(conn, tmp_path)
    spend(conn, "2030-01-03", "23.99")
    world.say()
    assert world.notices() == [("budget", "")]


# --- Los fallos ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("script", "text", "code"),
    [
        (
            [result_message("API Error: 529", is_error=True, api_status=529)],
            API_DOWN_TEXT,
            "agent:api:529",
        ),
        (
            [result_message(None, subtype="error_max_turns", is_error=True)],
            CAPPED_TEXT,
            "agent:max_turns",
        ),
        (
            [result_message(None, subtype="error_max_budget_usd", is_error=True)],
            CAPPED_TEXT,
            "agent:max_budget",
        ),
        ([result_message(None)], FAILED_TEXT, "agent:no_result"),
        ([], FAILED_TEXT, "agent:no_result"),
        (
            ProcessError(PEDRO_MESSAGE, 1, PEDRO_MESSAGE),
            FAILED_TEXT,
            "agent:process:ProcessError",
        ),
        (CLINotFoundError(), FAILED_TEXT, "agent:process:CLINotFoundError"),
        (
            ResultError(
                PEDRO_MESSAGE,
                {"subtype": "success", "api_error_status": 500, "result": AGENT_ANSWER},
            ),
            API_DOWN_TEXT,
            "agent:api:500",
        ),
        (RuntimeError(PEDRO_MESSAGE), FAILED_TEXT, "agent turn failed (RuntimeError)"),
    ],
)
def test_cada_fallo_da_su_frase_fija_y_un_codigo_en_el_log(
    conn, tmp_path, caplog, script, text, code
):
    caplog.set_level(logging.DEBUG)
    world = World(conn, tmp_path, FakeAgent(script))
    assert world.say(PEDRO_MESSAGE) == text
    assert code in caplog.text
    assert PEDRO_MESSAGE not in caplog.text and AGENT_ANSWER not in caplog.text


@pytest.mark.parametrize(
    ("status", "detail"),
    [(529, "down:agent_http_529"), (429, "down:agent_http_429"), (401, "rejected:agent_http_401")],
)
def test_la_api_caida_anota_el_aviso_del_sistema(conn, tmp_path, status, detail):
    world = World(
        conn, tmp_path, FakeAgent([result_message("API Error", is_error=True, api_status=status)])
    )
    world.say()
    assert world.notices() == [("api_down", detail)]


def test_el_resultado_que_llega_antes_del_error_manda(conn, tmp_path):
    """El motor emite el resultado fallido y después sale con `ResultError`: se usa el resultado."""
    world = World(
        conn,
        tmp_path,
        FakeAgent(
            [
                result_message(None, subtype="error_max_turns", is_error=True, cost=0.3),
                ResultError("x", {}),
            ]
        ),
    )
    assert world.say() == CAPPED_TEXT
    assert world.usage_rows()[0][-1] == pytest.approx(0.0102868)  # sus tokens, no el tope


# --- Los logs -----------------------------------------------------------------------------------


def test_los_logs_no_llevan_el_mensaje_la_respuesta_ni_la_clave(conn, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    world = World(conn, tmp_path)
    world.say(PEDRO_MESSAGE)
    assert PEDRO_MESSAGE not in caplog.text
    assert AGENT_ANSWER not in caplog.text
    assert VALID_ENV["CLAVE_ANTHROPIC"] not in caplog.text
    assert "agent: ok" in caplog.text


@pytest.mark.parametrize(
    ("script", "rows"),
    [
        # El motor respondió y se cayó sin resultado: pudo gastar; cuenta el tope de la sesión.
        ([SystemMessage("init", {}), ProcessError("x", 1)], [0.5]),
        ([SystemMessage("init", {})], [0.5]),
        # No llegó a arrancar o no dijo nada: no gastó.
        (CLINotFoundError(), []),
        ([], []),
    ],
)
def test_una_ejecucion_que_se_cae_sin_resultado_cuenta_lo_que_pudo_gastar(
    conn, tmp_path, script, rows
):
    """Hallazgo 2 de la revisión: sin esto, fallos repetidos gastarían sin llegar nunca al tope
    diario."""
    world = World(conn, tmp_path, FakeAgent(script))
    world.say()
    assert [row[-1] for row in world.usage_rows()] == rows


def test_una_sesion_reanudada_que_da_error_de_ejecucion_se_olvida(conn, tmp_path):
    """Hallazgo 3 de la revisión: el motor responde a la reanudación con un resultado de error
    (con la misma sesión). Sin olvidarla, cada mensaje del día volvería a fallar."""
    broken = result_message(None, subtype="error_during_execution", is_error=True)
    world = World(
        conn, tmp_path, FakeAgent([result_message()], [broken], [result_message(session="s-2")])
    )
    world.say(at="2030-01-07T10:15")
    assert world.say(at="2030-01-07T10:20") == FAILED_TEXT
    world.say(at="2030-01-07T10:25")
    assert world.agent.resumed == [None, "sesion-1", None]


def test_una_api_caida_no_olvida_la_sesion(conn, tmp_path):
    """Lo de la API pasa solo: la conversación del día sigue cuando vuelva."""
    down = result_message("API Error", is_error=True, api_status=529)
    world = World(conn, tmp_path, FakeAgent([result_message()], [down], [result_message()]))
    world.say(at="2030-01-07T10:15")
    world.say(at="2030-01-07T10:20")
    world.say(at="2030-01-07T10:25")
    assert world.agent.resumed == [None, "sesion-1", "sesion-1"]


# --- El turno: lo que propuso la ejecución y si leyó contenido externo (1.11) ----------------


def test_respond_devuelve_lo_que_propuso_la_ejecucion(conn, tmp_path):
    """El doble del agente llama a `read_tasks` y a `propose_plan` por el servidor de las
    opciones, como el motor: el turno trae los elementos y la marca de contaminado."""
    create = {"action": "create", "title": "Tarea", "due": "2030-01-07T18:00", "reason": "r"}
    agent = FakeAgent(
        [
            ToolCall("read_tasks", {}),
            ToolCall("propose_plan", {"items": [create], "requested_by_pedro": True}),
            result_message(),
        ]
    )
    turn = asyncio.run(World(conn, tmp_path, agent).conversation.respond(PEDRO_MESSAGE))
    assert turn.text == AGENT_ANSWER
    assert turn.run is not None
    assert turn.run.contaminated and turn.run.requested_by_pedro
    assert [item["title"] for item in turn.run.items] == ["Tarea"]


def test_una_ejecucion_que_falla_no_trae_propuesta(conn, tmp_path):
    create = {"action": "create", "title": "Tarea", "due": "2030-01-07T18:00", "reason": "r"}
    agent = FakeAgent(
        [
            ToolCall("propose_plan", {"items": [create]}),
            result_message(subtype="error_max_turns", is_error=True),
        ]
    )
    turn = asyncio.run(World(conn, tmp_path, agent).conversation.respond(PEDRO_MESSAGE))
    assert turn.run is None


def test_la_guardia_de_la_ejecucion_para_lo_bloqueado_antes_de_la_herramienta(conn, tmp_path):
    """El doble pasa por los *hooks* de las opciones, como el motor: con `read_tasks` bloqueada,
    la fuente no se lee y la ejecución no queda contaminada."""
    agent = FakeAgent([ToolCall("read_tasks", {}), result_message()])
    world = World(conn, tmp_path, agent)
    base = limits()
    world.conversation._limits = base.model_copy(
        update={"tool_levels": dict(base.tool_levels) | {"read_tasks": "blocked"}}
    )
    turn = asyncio.run(world.conversation.respond(PEDRO_MESSAGE))
    assert [outcome.denied for outcome in agent.tool_outcomes] == [True]
    assert turn.run is not None and not turn.run.contaminated
    assert not conversations.read_external(conn, date(2030, 1, 7))


# --- La fila del día sin mensaje de Pedro (tarea 1.14) -----------------------------------------


def test_la_fila_del_dia_sin_mensaje_no_cuenta_como_que_pedro_escribio(conn):
    """Pilla un `ensure_day` que inventa `last_message_at` (el latido creería que Pedro conversa)
    o que pisa la hora de un mensaje que ya estaba."""
    conversations.ensure_day(conn, date(2030, 1, 7))
    assert conversations.last_message_at(conn) is None
    conversations.mark_external(conn, date(2030, 1, 7))  # la guardia ya puede marcarla
    conversations.note_message(conn, date(2030, 1, 7), madrid("2030-01-07T09:00"))
    conversations.ensure_day(conn, date(2030, 1, 7))
    assert conversations.last_message_at(conn) == madrid("2030-01-07T09:00")
    assert conversations.read_external(conn, date(2030, 1, 7))


# --- Las ejecuciones sin mensaje de Pedro (tarea 1.14) -----------------------------------------

CHANGE = {"file": "preferencias.md", "operation": "add", "after": "- Más corto", "reason": "r"}


def run_note(
    world: World, note: str = "Toca proponer el plan del día.", mode: RunMode = "plan", **kwargs
):
    return asyncio.run(world.conversation.run_note(note, mode, **kwargs))


def test_el_plan_corre_en_la_sesion_del_dia_con_el_texto_del_codigo(conn, tmp_path):
    """Pilla una ejecución sin origen que abre otra sesión (perdería la conversación de hoy) o que
    escribe la nota como si fuera de Pedro."""
    world = World(conn, tmp_path)
    world.say("Hola", at="2030-01-07T07:30")
    world.clock.moment = madrid("2030-01-07T07:45")
    turn = run_note(world)
    assert turn is not None and turn.code == "ok" and turn.run is not None
    assert turn.run.origin is None and turn.run.mode == "plan"
    prompt, options = world.agent.runs[-1]
    assert prompt == "[lun 07/01 07:45] (JimiC) Toca proponer el plan del día."
    assert options.resume == "sesion-1"


def test_el_plan_no_cuenta_como_que_pedro_conversa(conn, tmp_path):
    """Versión incorrecta 7: el plan que llama a `note_message` haría esperar los avisos del
    latido como si Pedro estuviera escribiendo."""
    world = World(conn, tmp_path)
    world.clock.moment = madrid("2030-01-07T07:45")
    turn = run_note(world)
    assert turn is not None and turn.code == "ok"
    assert conversations.last_message_at(conn) is None
    assert conversations.session_of(conn, date(2030, 1, 7)) == "sesion-1"


def test_en_el_plan_propose_change_se_rechaza(conn, tmp_path):
    """Versión incorrecta 1: un plan con el origen de un mensaje de Pedro podría proponer
    cambios de memoria nacidos de lo que leyó."""
    world = World(conn, tmp_path, FakeAgent([ToolCall("propose_change", CHANGE), result_message()]))
    turn = run_note(world)
    assert turn is not None and turn.run is not None
    (outcome,) = world.agent.tool_outcomes
    assert outcome.is_error and "Fuera de una conversación" in outcome.text
    assert turn.run.memory == []


def test_el_plan_puede_leer_aunque_pedro_no_haya_escrito_hoy(conn, tmp_path):
    """Pilla una ejecución sin la fila del día: la guardia denegaría la lectura (`unmarked`)."""
    world = World(conn, tmp_path, FakeAgent([ToolCall("read_tasks", {}), result_message()]))
    turn = run_note(world)
    assert turn is not None and turn.run is not None and turn.run.contaminated
    assert [outcome.denied for outcome in world.agent.tool_outcomes] == [False]


def test_el_plan_espera_al_turno_de_pedro_que_esta_en_marcha(conn, tmp_path):
    world = World(conn, tmp_path)
    world.agent.gate = asyncio.Event()

    async def scenario():
        pedro = asyncio.create_task(world.conversation.reply("primero"))
        while not world.agent.runs:
            await asyncio.sleep(0)
        plan = asyncio.create_task(world.conversation.run_note("plan", "plan"))
        for _ in range(20):
            await asyncio.sleep(0)
        assert len(world.agent.runs) == 1
        assert world.agent.gate is not None
        world.agent.gate.set()
        return await pedro, await plan

    _, plan = asyncio.run(scenario())
    assert plan is not None and plan.code == "ok"
    assert world.agent.most_active == 1


def test_con_el_tope_diario_el_plan_no_llama_al_sdk_y_lo_dice_su_codigo(conn, tmp_path):
    world = World(conn, tmp_path)
    record_cost(conn, date(2030, 1, 7), "m", TokenUsage(0, 0), Decimal("9"))
    turn = run_note(world)
    assert turn is not None and turn.code == "agent:daily_cap" and turn.run is None
    assert world.agent.runs == []


def test_un_fallo_del_plan_lleva_su_codigo(conn, tmp_path):
    world = World(conn, tmp_path, FakeAgent([result_message(None, api_status=529, is_error=True)]))
    turn = run_note(world)
    assert turn is not None and turn.code == "agent:api:529" and turn.run is None


def test_si_pedro_escribio_despues_no_corre(conn, tmp_path):
    world = World(conn, tmp_path)
    world.say("ya lo he visto", at="2030-01-07T10:20")
    assert (
        run_note(world, mode="follow_up", unless_written_after=madrid("2030-01-07T10:15")) is None
    )
    assert len(world.agent.runs) == 1
    turn = run_note(world, mode="follow_up", unless_written_after=madrid("2030-01-07T10:20"))
    assert turn is not None and turn.code == "ok"
