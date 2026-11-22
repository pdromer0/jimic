"""La guardia del agente: el *hook* `PreToolUse` que ve cada llamada (decisiones 15 y 27).

Deniega lo que está fuera de la tabla y lo que no es `free` en `tool_levels` (lo que falta,
también), y `log_idea` cuando la sesión del día ya leyó contenido externo. Cuando permite, no
opina (`{}`): `allowed_tools` sigue siendo una segunda capa.
"""

import asyncio
import logging

import pytest
from agent_data import SECRET_ARG, context
from claude_agent_sdk import HookMatcher
from heartbeat_data import limits

from jimic.agent.guard import guard_hooks, make_guard
from jimic.agent.tools import model_name
from jimic.memory.conversations import mark_external
from jimic.memory.database import open_database
from jimic.memory.schemas import TOOL_NAMES


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def levels(**changes):
    base = limits()
    return base.model_copy(update={"tool_levels": dict(base.tool_levels) | changes})


def ask(guard, tool_name: str, tool_input: dict | None = None) -> dict:
    data = {
        "hook_event_name": "PreToolUse",
        "session_id": "s",
        "transcript_path": "/dev/null",
        "cwd": "/",
        "tool_name": tool_name,
        "tool_input": tool_input or {},
        "tool_use_id": "u1",
    }
    return asyncio.run(guard(data, "u1", {"signal": None}))


def denied(answer: dict) -> bool:
    specific = answer.get("hookSpecificOutput", {})
    return specific.get("permissionDecision") == "deny" and bool(
        specific.get("permissionDecisionReason")
    )


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_las_libres_pasan_sin_opinar(conn, tmp_path, name):
    """Pilla un *hook* que devuelve «allow»: se saltaría `allowed_tools`, la segunda capa."""
    day = context(conn, tmp_path).day
    assert ask(make_guard(limits(), conn, day), model_name(name)) == {}


@pytest.mark.parametrize(
    "tool_name",
    ["Bash", "mcp__jimic__delete_task", "mcp__todoist__delete-object", "read_tasks", ""],
)
def test_fuera_de_la_tabla_se_deniega(conn, tmp_path, tool_name):
    """Pilla una herramienta con efecto que se cuela por otro camino (una de serie, otro
    servidor MCP o el nombre sin el prefijo de nuestro servidor)."""
    day = context(conn, tmp_path).day
    assert denied(ask(make_guard(limits(), conn, day), tool_name))


def test_una_bloqueada_se_deniega_y_las_demas_siguen_pasando(conn, tmp_path):
    """Pilla un *hook* que deja pasar una `blocked` (versión incorrecta 8)."""
    day = context(conn, tmp_path).day
    guard = make_guard(levels(read_tasks="blocked"), conn, day)
    assert denied(ask(guard, model_name("read_tasks")))
    assert ask(guard, model_name("read_calendar")) == {}


def test_una_que_falta_en_tool_levels_cuenta_como_bloqueada(conn, tmp_path):
    day = context(conn, tmp_path).day
    base = limits()
    missing = base.model_copy(
        update={"tool_levels": {k: v for k, v in base.tool_levels.items() if k != "propose_plan"}}
    )
    assert denied(ask(make_guard(missing, conn, day), model_name("propose_plan")))


def test_log_idea_se_deniega_si_la_sesion_del_dia_ya_leyo_contenido_externo(conn, tmp_path):
    """Pilla la marca por ejecución en lugar de la del día (ajuste 2 del chat): se lee en la
    ejecución 1 y `log_idea` llega en la 2, con lo leído aún en el contexto de la sesión."""
    ctx = context(conn, tmp_path)
    guard_before = make_guard(limits(), conn, ctx.day)
    assert ask(guard_before, model_name("log_idea")) == {}
    mark_external(conn, ctx.day)  # la ejecución 1 leyó las tareas
    guard_next_run = make_guard(limits(), conn, ctx.day)
    assert denied(ask(guard_next_run, model_name("log_idea")))
    assert ask(guard_next_run, model_name("propose_plan")) == {}


def test_al_dia_siguiente_log_idea_vuelve_a_pasar(conn, tmp_path):
    ctx = context(conn, tmp_path)
    mark_external(conn, ctx.day)
    tomorrow = context(conn, tmp_path, now="2030-01-08T09:00").day
    assert ask(make_guard(limits(), conn, tomorrow), model_name("log_idea")) == {}


def test_otros_eventos_no_se_tocan(conn, tmp_path):
    guard = make_guard(limits(), conn, context(conn, tmp_path).day)
    other = {"hook_event_name": "PostToolUse"}
    assert asyncio.run(guard(other, None, {"signal": None})) == {}  # pyright: ignore[reportArgumentType] - a partial input, as the test needs


def test_el_log_dice_la_decision_y_nunca_los_argumentos_ni_nombres_ajenos(conn, tmp_path, caplog):
    guard = make_guard(levels(read_tasks="blocked"), conn, context(conn, tmp_path).day)
    with caplog.at_level(logging.INFO, logger="jimic.agent.guard"):
        ask(guard, model_name("read_calendar"), {"day": SECRET_ARG})
        ask(guard, model_name("read_tasks"), {"project_id": SECRET_ARG})
        ask(guard, "Herramienta-ajena-secreta", {"x": SECRET_ARG})
    assert [record.getMessage() for record in caplog.records] == [
        "guard: allow read_calendar",
        "guard: deny:blocked read_tasks",
        "guard: deny:outside_table ?",
    ]
    assert SECRET_ARG not in caplog.text and "ajena" not in caplog.text


def test_los_hooks_ven_todas_las_herramientas_y_un_fallo_bloquea(conn, tmp_path):
    hooks = guard_hooks(limits(), conn, context(conn, tmp_path).day)
    ((matcher,),) = hooks.values()
    assert list(hooks) == ["PreToolUse"]
    assert isinstance(matcher, HookMatcher)
    assert matcher.matcher is None and matcher.on_failure == "block" and len(matcher.hooks) == 1
