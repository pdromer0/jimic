"""La guardia del agente: el *hook* `PreToolUse` que ve cada llamada (decisiones 15 y 27).

Deniega lo que está fuera de la tabla y lo que no es `free` en `tool_levels` (lo que falta,
también). Al dejar pasar una lectura que contamina, marca la ejecución y la sesión del día antes
de que corra (1.12-a). Cuando permite, no opina (`{}`): `allowed_tools` sigue siendo una
segunda capa.
"""

import asyncio
import logging
from datetime import date

import pytest
from agent_data import SECRET_ARG, call_tool, context
from claude_agent_sdk import HookMatcher
from heartbeat_data import limits
from memory_data import QUIET_LINE, write_personal

from jimic.agent.guard import guard_hooks, make_guard
from jimic.agent.tools import RunState, build_server, model_name
from jimic.memory.conversations import mark_external, read_external
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


def test_log_idea_ya_no_se_deniega_por_la_marca_del_dia(conn, tmp_path):
    """La decisión 100, revisada por la 1.12-b: la sesión leyó en la ejecución 1 y `log_idea`
    llega en la 2. El *hook* lo deja pasar; la herramienta aplica la regla de la ejecución."""
    ctx = context(conn, tmp_path)
    mark_external(conn, ctx.day)
    assert ask(make_guard(limits(), conn, ctx.day), model_name("log_idea")) == {}


@pytest.mark.parametrize(
    ("name", "marks"),
    [("read_calendar", True), ("read_tasks", True), ("read_apex_today", False)],
)
def test_el_hook_marca_la_ejecucion_al_dejar_pasar_una_lectura(conn, tmp_path, name, marks):
    ctx = context(conn, tmp_path)
    run = RunState(origin="hola")
    assert ask(make_guard(limits(), conn, ctx.day, run), model_name(name)) == {}
    assert run.contaminated is marks
    assert read_external(conn, ctx.day) is marks


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


@pytest.mark.parametrize("proposal", ["propose_change", "log_idea"])
def test_dos_llamadas_en_un_mensaje_del_modelo_no_cuelan_la_memoria(conn, tmp_path, proposal):
    """Versión incorrecta 7: con `read_tasks` y una propuesta de memoria en el mismo mensaje, el
    motor pasa los dos *hooks* antes de ejecutar ninguna, y la propuesta puede ejecutarse antes
    que la lectura. La marca de la ejecución la pone ya el *hook* de la lectura."""
    config_dir = write_personal(tmp_path / "config")
    run = RunState(origin="No me escribas antes de las 8")
    ctx = context(conn, config_dir, run=run)
    guard = make_guard(limits(), conn, ctx.day, run)
    assert ask(guard, model_name("read_tasks")) == {}
    assert ask(guard, model_name(proposal)) == {}
    args = (
        {"text": "una idea"}
        if proposal == "log_idea"
        else {
            "file": "limites.yaml",
            "operation": "replace",
            "before": QUIET_LINE,
            "after": QUIET_LINE.replace('"07:00"', '"08:00"'),
            "reason": "r",
        }
    )
    _, is_error = asyncio.run(call_tool(build_server(ctx), proposal, args))
    assert is_error and run.memory == []


def test_una_lectura_que_no_se_puede_marcar_se_deniega(conn, tmp_path):
    """Sin la fila del día (nadie anotó el mensaje), la lectura no se deja pasar."""
    guard = make_guard(limits(), conn, date(2031, 1, 1))
    assert denied(ask(guard, model_name("read_calendar")))
