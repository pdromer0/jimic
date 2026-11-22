"""Contrato del agente contra el Agent SDK y la API reales: lento y a mano.

Solo corre con `JIMIC_CONTRATO=1`, con la clave y el modelo del `.env` de `jimic-dev`. Dos
ejecuciones con lectores **inventados** (`tests/agent_data.py`) y preguntas triviales: la
primera obliga a llamar a `read_tasks`; la segunda reanuda la sesión y pregunta lo mismo. Con
`CLAUDE_CONFIG_DIR` en una carpeta temporal, así que no deja nada fuera de ella. No toca
Todoist, Calendar ni Telegram de verdad. Imprime (`-s`) recuentos, dólares y los nombres de lo
que el motor escribe en su carpeta, nunca el texto de las respuestas.
"""

import asyncio
import os
import time
from pathlib import Path

import pytest
from agent_data import agent_sources, context
from claude_agent_sdk import ResultMessage, SystemMessage, query
from conftest import CONTRACT
from heartbeat_data import limits, task

from jimic.agent.options import build_options
from jimic.agent.tools import build_server
from jimic.memory.database import open_database
from jimic.tools.todoist import TaskList

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CONTRACT, reason="contrato: solo con JIMIC_CONTRATO=1"),
]

SIX = sorted(
    f"mcp__jimic__{name}"
    for name in (
        "read_calendar",
        "read_tasks",
        "read_apex_today",
        "propose_plan",
        "propose_change",
        "log_idea",
    )
)


async def run(prompt: str, options) -> list:
    return [message async for message in query(prompt=prompt, options=options)]


def files_under(folder: Path) -> list[str]:
    """Relative names, sizes and ages of what the engine wrote: no content."""
    now = time.time()
    found = []
    for root, _, names in os.walk(folder):
        for name in names:
            path = Path(root) / name
            info = path.stat()
            found.append(
                f"{path.relative_to(folder)} ({info.st_size} B, {now - info.st_mtime:.0f} s)"
            )
    return sorted(found)


def test_el_agente_real_ve_solo_las_seis_lee_y_reanuda(contract_env, tmp_path):
    _, settings = contract_env
    conn = open_database(tmp_path / "contrato.db")
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(mode=0o700)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    srcs = agent_sources(TaskList(available=True, tasks=[task("t1"), task("t2")]))
    try:
        ctx = context(conn, config_dir, srcs)
        first = asyncio.run(
            run(
                "[lun 07/01 10:15] Llama a read_tasks y dime solo cuántas tareas tengo, en cifra.",
                build_options(settings, limits(), agent_dir, build_server(ctx), None),
            )
        )
        (init,) = [m for m in first if isinstance(m, SystemMessage) and m.subtype == "init"]
        (result,) = [m for m in first if isinstance(m, ResultMessage)]
        print(
            f"\ninit: tools={sorted(init.data.get('tools', []))}"
            f" mcp={init.data.get('mcp_servers')} model={init.data.get('model')}"
            f"\nprimera: subtype={result.subtype} error={result.is_error} turns={result.num_turns}"
            f" cost={result.total_cost_usd} usage={result.usage}"
        )
        assert sorted(init.data["tools"]) == SIX
        assert result.subtype == "success" and not result.is_error
        assert srcs.todoist.reader.reads == 1  # pyright: ignore[reportAttributeAccessIssue] - the double counts
        assert result.result is not None and "2" in result.result
        assert result.total_cost_usd is not None and result.total_cost_usd > 0
        session = result.session_id

        second = asyncio.run(
            run(
                "[lun 07/01 10:20] ¿Cuántas tareas me dijiste antes?"
                " Solo la cifra, sin herramientas.",
                build_options(settings, limits(), agent_dir, build_server(ctx), session),
            )
        )
        (again,) = [m for m in second if isinstance(m, ResultMessage)]
        print(
            f"segunda: subtype={again.subtype} turns={again.num_turns} cost={again.total_cost_usd}"
            f" misma_sesion={again.session_id == session}"
            f"\nficheros del motor:\n  " + "\n  ".join(files_under(agent_dir))
        )
        assert again.subtype == "success"
        assert again.result is not None and "2" in again.result
        assert srcs.todoist.reader.reads == 1  # pyright: ignore[reportAttributeAccessIssue] - the double counts
    finally:
        conn.close()
