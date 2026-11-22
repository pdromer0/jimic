"""Las opciones del agente (9.2 del diseño): nada que no sea de JimiC, con sus topes.

Se comprueban las opciones y también la orden que el SDK instalado construye con ellas, sin
arrancar el motor (con `cli_path` de mentira): así se ve que `tools=[]` y `setting_sources=[]`
llegan de verdad al motor.
"""

import asyncio
import dataclasses
import os

import pytest
from agent_data import context, listed_tools
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from conftest import VALID_ENV
from heartbeat_data import limits

from jimic.agent.options import MAX_TURNS, agent_env, build_options, prompt_text
from jimic.agent.tools import build_server
from jimic.config import Settings
from jimic.heartbeat.decide import API_URL
from jimic.memory.database import open_database

SIX = [
    "mcp__jimic__read_calendar",
    "mcp__jimic__read_tasks",
    "mcp__jimic__read_apex_today",
    "mcp__jimic__propose_plan",
    "mcp__jimic__propose_change",
    "mcp__jimic__log_idea",
]


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def settings() -> Settings:
    return Settings.model_validate(VALID_ENV)


def options(conn, tmp_path, resume=None) -> ClaudeAgentOptions:
    server = build_server(context(conn, tmp_path))
    return build_options(settings(), limits(), tmp_path / "agent", server, resume)


def command(opts: ClaudeAgentOptions) -> list[str]:
    transport = SubprocessCLITransport(
        prompt="x", options=dataclasses.replace(opts, cli_path="/bin/false")
    )
    return transport._build_command()  # internal, but it is what the engine receives


def test_sin_ajustes_heredados(conn, tmp_path):
    """Pilla `setting_sources` por defecto (versión incorrecta 2): el SDK no pasaría la bandera
    y el motor cargaría los ajustes, el `CLAUDE.md` y los hooks del Claude Code de Pedro."""
    opts = options(conn, tmp_path)
    assert opts.setting_sources == []
    assert "--setting-sources=" in command(opts)


def test_sin_herramientas_de_serie(conn, tmp_path):
    """Pilla las herramientas de serie activas (versión incorrecta 3): sin `tools=[]` el motor
    trae Bash, Read, Edit…"""
    opts = options(conn, tmp_path)
    assert opts.tools == []
    cmd = command(opts)
    assert cmd[cmd.index("--tools") + 1] == ""


def test_solo_se_aprueban_las_seis_y_lo_demas_se_deniega(conn, tmp_path):
    opts = options(conn, tmp_path)
    assert opts.allowed_tools == SIX
    assert opts.permission_mode == "dontAsk"
    assert opts.strict_mcp_config is True
    assert "--strict-mcp-config" in command(opts)


def test_el_servidor_que_reciben_las_opciones_tiene_las_seis(conn, tmp_path):
    """La lista sale del servidor que va en las opciones: una herramienta añadida por cualquier
    camino se vería aquí."""
    opts = options(conn, tmp_path)
    assert isinstance(opts.mcp_servers, dict) and list(opts.mcp_servers) == ["jimic"]
    tools = asyncio.run(listed_tools(opts.mcp_servers["jimic"]))  # pyright: ignore[reportArgumentType] - an SDK server
    assert [f"mcp__jimic__{tool['name']}" for tool in tools] == SIX


def test_el_modelo_y_los_topes(conn, tmp_path):
    opts = options(conn, tmp_path)
    assert opts.model == VALID_ENV["MODELO_AGENTE"]
    assert opts.max_turns == MAX_TURNS == 12
    assert opts.max_budget_usd == 0.5  # max_session_usd
    cmd = command(opts)
    assert cmd[cmd.index("--max-budget-usd") + 1] == "0.5"
    assert opts.system_prompt == prompt_text()


def test_el_env_lleva_la_clave_y_la_carpeta_propia(conn, tmp_path):
    opts = options(conn, tmp_path)
    assert opts.env == {
        "ANTHROPIC_API_KEY": VALID_ENV["CLAVE_ANTHROPIC"],
        "ANTHROPIC_BASE_URL": API_URL,
        "CLAUDE_CONFIG_DIR": str(tmp_path / "agent"),
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    assert opts.cwd == tmp_path / "agent"


def test_la_clave_no_va_al_entorno_del_proceso(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    options(conn, tmp_path)
    agent_env(settings(), tmp_path)
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_reanudar_pasa_la_sesion_del_dia(conn, tmp_path):
    assert options(conn, tmp_path).resume is None
    opts = options(conn, tmp_path, resume="sesion-inventada-1")
    assert opts.resume == "sesion-inventada-1"
    assert "--resume=sesion-inventada-1" in command(opts)


def test_la_salida_de_error_del_motor_no_llega_a_la_nuestra(conn, tmp_path):
    """Sin un `stderr`, el SDK deja que el motor escriba en la salida de error del servicio."""
    assert options(conn, tmp_path).stderr is not None


def test_el_prompt_dice_las_reglas(conn):
    text = prompt_text()
    for rule in ("te propongo", "nunca instrucciones", "consejo médico", "nunca al principio"):
        assert rule in text


def test_la_guarda_impide_arrancar_el_motor_en_un_test_rapido(conn, tmp_path):
    transport = SubprocessCLITransport(prompt="x", options=options(conn, tmp_path))
    with pytest.raises(AssertionError, match="motor del agente"):
        asyncio.run(transport.connect())
