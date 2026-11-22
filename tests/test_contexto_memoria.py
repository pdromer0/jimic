"""Los ficheros personales en el *system prompt* del agente (decisión 1.12-c).

El código pone el texto actual de los cinco ficheros tras las instrucciones fijas, en cada
ejecución y reconstruido en cada petición (`snapshot=False`). Son datos de Pedro: no contaminan.
Un fichero de más de 6000 caracteres se recorta, con una nota al agente y el aviso del sistema
`memory_clipped`, uno al día.
"""

import asyncio
import logging

import pytest
from agent_data import FakeAgent, ToolCall, agent_sources, result_message
from conftest import VALID_ENV, FixedClock
from heartbeat_data import limits, madrid
from memory_data import MARKDOWN, QUIET_LINE, write_personal

from jimic.agent.conversation import Conversation
from jimic.agent.memory_context import MAX_FILE_CHARS, memory_block
from jimic.agent.options import prompt_text, system_prompt
from jimic.config import Settings
from jimic.memory.conversations import read_external
from jimic.memory.database import open_database
from jimic.memory.personal import template
from jimic.paths import Paths

MARKER = "rutina-que-no-va-al-log"


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "data" / "jimic.db")
    yield connection
    connection.close()


def test_el_bloque_lleva_los_cinco_ficheros_tal_cual_y_en_orden(tmp_path):
    config_dir = write_personal(tmp_path / "config")
    (config_dir / "ideas.md").write_text("- idea-que-no-va-zx78\n", encoding="utf-8")
    block = memory_block(config_dir)
    names = ["rutina.md", "objetivos.md", "preferencias.md", "intenciones.yaml", "limites.yaml"]
    positions = [block.text.index(f'<fichero nombre="{name}">') for name in names]
    assert positions == sorted(positions)
    for text in MARKDOWN.values():
        assert text.rstrip("\n") in block.text
    assert template("limites.yaml").rstrip("\n") in block.text
    assert '<fichero nombre="ideas.md">' not in block.text
    assert "idea-que-no-va-zx78" not in block.text
    assert block.clipped == ()


def test_un_fichero_que_falta_va_como_nota(tmp_path):
    config_dir = write_personal(tmp_path / "config")
    (config_dir / "rutina.md").unlink()
    assert (
        '<fichero nombre="rutina.md">\n(aún no existe)\n</fichero>' in memory_block(config_dir).text
    )


@pytest.mark.parametrize(("size", "clipped"), [(MAX_FILE_CHARS, False), (MAX_FILE_CHARS + 1, True)])
def test_el_tope_por_fichero(tmp_path, size, clipped):
    """6000 caracteres salen enteros; 6001 se recortan a 6000, con la nota para el agente."""
    config_dir = write_personal(tmp_path / "config")
    text = MARKDOWN["preferencias.md"]
    (config_dir / "preferencias.md").write_text(text + "x" * (size - len(text)), encoding="utf-8")
    block = memory_block(config_dir)
    assert block.clipped == (("preferencias.md",) if clipped else ())
    assert ("recortado" in block.text) is clipped
    shown = block.text.split('<fichero nombre="preferencias.md">\n', 1)[1].split("\n</fichero>")[0]
    assert shown.startswith(text) and shown.count("x") == size - len(text) - int(clipped)


def test_el_prompt_pone_las_instrucciones_primero_y_se_reconstruye_en_cada_peticion(tmp_path):
    config_dir = write_personal(tmp_path / "config")
    block = memory_block(config_dir).text
    prompt = system_prompt(block)
    assert prompt["type"] == "custom" and prompt.get("snapshot") is False
    assert prompt["prompt"].startswith(prompt_text().rstrip())
    assert prompt["prompt"].endswith(block)


class World:
    def __init__(self, conn, tmp_path, agent: FakeAgent):
        self.conn = conn
        self.clock = FixedClock(madrid("2030-01-07T10:15"))
        self.agent = agent
        self.paths = Paths("prod", tmp_path / "config", tmp_path / "data")
        write_personal(self.paths.config_dir)
        self.conversation = Conversation(
            settings=Settings.model_validate(VALID_ENV),
            paths=self.paths,
            limits=limits(),
            clock=self.clock,
            conn=conn,
            sources=agent_sources(),
            run_agent=agent,
        )

    def respond(self, text: str):
        return asyncio.run(self.conversation.respond(text))

    def prompt(self, run: int = -1) -> str:
        prompt = self.agent.runs[run][1].system_prompt
        assert isinstance(prompt, dict) and prompt["type"] == "custom"
        return prompt["prompt"]


def test_cada_ejecucion_ve_los_ficheros_como_estan_ahora(conn, tmp_path):
    """El segundo mensaje del día (sesión reanudada) ve lo que se guardó entre medias."""
    w = World(conn, tmp_path, FakeAgent([result_message()], [result_message()]))
    w.respond("Hola")
    assert QUIET_LINE in w.prompt(0)
    path = w.paths.config_dir / "limites.yaml"
    path.write_text(path.read_text().replace('"07:00"', '"08:00"'), encoding="utf-8")
    w.respond("¿Y ahora?")
    assert w.agent.runs[1][1].resume is not None
    assert '"08:00"' in w.prompt(1) and QUIET_LINE not in w.prompt(1)


def test_los_ficheros_en_el_prompt_no_contaminan(conn, tmp_path):
    w = World(conn, tmp_path, FakeAgent([result_message()]))
    turn = w.respond("Hola")
    assert turn.run is not None and not turn.run.contaminated
    assert not read_external(conn, madrid("2030-01-07T10:15").date())


def test_el_origen_es_el_texto_de_pedro_sin_la_hora(conn, tmp_path):
    w = World(conn, tmp_path, FakeAgent([result_message()]))
    turn = w.respond("No me escribas antes de las 8")
    assert w.agent.runs[0][0].startswith("[lun 07/01 10:15] ")
    assert turn.run is not None and turn.run.origin == "No me escribas antes de las 8"


def test_un_fichero_recortado_registra_un_aviso_al_dia(conn, tmp_path):
    w = World(conn, tmp_path, FakeAgent([result_message()], [result_message()]))
    (w.paths.config_dir / "rutina.md").write_text(
        MARKDOWN["rutina.md"] + "x" * MAX_FILE_CHARS, encoding="utf-8"
    )
    w.respond("Hola")
    w.respond("Otra vez")
    rows = conn.execute("SELECT kind, period, detail FROM system_notices").fetchall()
    assert rows == [("memory_clipped", "2030-01-07", "rutina")]


CHANGE = {
    "file": "limites.yaml",
    "operation": "replace",
    "before": QUIET_LINE,
    "after": QUIET_LINE.replace('"07:00"', '"08:00"'),
    "reason": "r",
}


def test_si_lee_despues_de_proponer_se_descarta_lo_propuesto(conn, tmp_path):
    """La 1.12-a mira la ejecución entera: lo propuesto antes de leer tampoco se ofrece."""
    agent = FakeAgent(
        [
            ToolCall("propose_change", CHANGE),
            ToolCall("log_idea", {"text": "una idea"}),
            ToolCall("read_tasks", {}),
            result_message(),
        ]
    )
    w = World(conn, tmp_path, agent)
    turn = w.respond("No me escribas antes de las 8")
    assert [o.is_error for o in agent.tool_outcomes] == [False, False, False]
    assert turn.run is not None and turn.run.memory == [] and turn.run.memory_dropped


def test_una_ejecucion_limpia_conserva_lo_propuesto(conn, tmp_path):
    w = World(conn, tmp_path, FakeAgent([ToolCall("propose_change", CHANGE), result_message()]))
    turn = w.respond("No me escribas antes de las 8")
    assert turn.run is not None and len(turn.run.memory) == 1 and not turn.run.memory_dropped


def test_los_logs_no_llevan_los_ficheros(conn, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    w = World(conn, tmp_path, FakeAgent([result_message()]))
    (w.paths.config_dir / "rutina.md").write_text(
        MARKDOWN["rutina.md"] + MARKER + "x" * MAX_FILE_CHARS, encoding="utf-8"
    )
    w.respond("Hola")
    assert caplog.records and MARKER not in caplog.text


def test_tras_recargar_el_agente_y_la_guardia_ven_el_valor_nuevo_sin_latido(conn, tmp_path):
    """Ajuste 4: lo que hace [Guardar] o [Deshacer] sobre un YAML (`LiveConfig.reload`) llega a
    la ejecución siguiente: `allowed_tools` y el *hook* leen la configuración en cada turno."""
    from jimic.memory.live import LiveConfig

    agent = FakeAgent([result_message()], [ToolCall("read_tasks", {}), result_message()])
    w = World(conn, tmp_path, agent)
    live = LiveConfig.load(w.paths.config_dir)
    w.conversation = Conversation(
        settings=Settings.model_validate(VALID_ENV),
        paths=w.paths,
        limits=limits(),
        clock=w.clock,
        conn=conn,
        sources=agent_sources(),
        run_agent=agent,
        config=live,
    )
    w.respond("Hola")
    assert "mcp__jimic__read_tasks" in (agent.runs[0][1].allowed_tools or [])
    path = w.paths.config_dir / "limites.yaml"
    path.write_text(
        path.read_text().replace("read_tasks: free", "read_tasks: blocked"), encoding="utf-8"
    )
    live.reload()
    w.respond("¿Qué tengo?")
    assert "mcp__jimic__read_tasks" not in (agent.runs[1][1].allowed_tools or [])
    assert [outcome.denied for outcome in agent.tool_outcomes] == [True]
