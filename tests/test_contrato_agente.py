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
from decimal import Decimal
from pathlib import Path

import pytest
from agent_data import agent_sources, context
from claude_agent_sdk import ResultMessage, SystemMessage, query
from conftest import CONTRACT
from heartbeat_data import LIMITS, limits, task

from jimic.agent.conversation import usage_of
from jimic.agent.guard import guard_hooks
from jimic.agent.options import build_options
from jimic.agent.tools import build_server
from jimic.memory.database import open_database
from jimic.memory.usage import cost
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
        hooks = guard_hooks(limits(), conn, ctx.day)
        first = asyncio.run(
            run(
                "[lun 07/01 10:15] Llama a read_tasks y dime solo cuántas tareas tengo, en cifra.",
                build_options(settings, limits(), agent_dir, build_server(ctx), None, hooks),
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
                build_options(settings, limits(), agent_dir, build_server(ctx), session, hooks),
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


def budget_with(max_session_usd: float) -> dict:
    return {**LIMITS["budget"], "max_session_usd": max_session_usd}


def _result_of(messages: list) -> ResultMessage:
    (result,) = [m for m in messages if isinstance(m, ResultMessage)]
    return result


def test_el_coste_por_tokens_de_cada_turno_es_la_diferencia_de_acumulados_del_sdk(
    contract_env, tmp_path
):
    """Dos turnos cortos y sin herramientas en la misma sesión (fix #51). El `total_cost_usd` del
    SDK es el acumulado de la sesión: el coste por tokens (`usage.cost`) de cada turno debe
    coincidir con su diferencia, con un 5 % de tolerancia. Un tercer turno, con un
    `max_budget_usd` por encima de su coste y por debajo del acumulado de los dos primeros, dice si
    el SDK compara ese tope con el gasto de la ejecución o con el de la sesión. Imprime solo
    códigos y cifras."""
    _, settings = contract_env
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(mode=0o700)
    conn = open_database(tmp_path / "contrato.db")
    try:
        ctx = context(conn, tmp_path, agent_sources(TaskList(available=True, tasks=[])))
        hooks = guard_hooks(limits(), conn, ctx.day)

        def turn(session, text, cap=None):
            lim = limits() if cap is None else limits(budget=budget_with(cap))
            options = build_options(settings, lim, agent_dir, build_server(ctx), session, hooks)
            return _result_of(asyncio.run(run(text, options)))

        first = turn(None, "[lun 07/01 10:15] Responde solo con la palabra OK, sin herramientas.")
        second = turn(first.session_id, "[lun 07/01 10:16] Otra vez: solo la palabra OK.")
        paid = [
            cost(settings.agent_model, usage_of(first.usage)),
            cost(settings.agent_model, usage_of(second.usage)),
        ]
        total1, total2 = first.total_cost_usd, second.total_cost_usd
        assert total1 is not None and total2 is not None and second.session_id == first.session_id
        sdk = [Decimal(str(total1)), Decimal(str(total2 - total1))]
        print(
            f"\ncontrato gasto: tokens={[str(c) for c in paid]}"
            f" sdk_diferencia={[str(c) for c in sdk]} acumulados={[total1, total2]}"
        )
        for counted, expected in zip(paid, sdk, strict=True):
            assert abs(counted - expected) <= expected * Decimal("0.05")

        cap = (paid[1] * Decimal("1.3") + Decimal(str(total2))) / 2
        if cap >= Decimal(str(total2)):
            print("contrato gasto: sin hueco para el tope de prueba; no se comprueba max_budget")
            return
        third = turn(second.session_id, "[lun 07/01 10:17] Una vez más: solo OK.", float(cap))
        print(
            f"contrato gasto: tope={cap:.5f} < acumulado={total2:.5f}; tercer turno"
            f" subtype={third.subtype} cortado_por_tope={third.subtype == 'error_max_budget_usd'}"
        )
    finally:
        conn.close()
