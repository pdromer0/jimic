"""The guard: the `PreToolUse` hook that sees every call of the agent (decisions 15 and 27).

Deny by default (6.5 of the design):

- a tool outside the table of the hito 1 spec is denied (`outside_table`);
- a tool whose level in `tool_levels` of `limites.yaml` is not `free`, or that is missing from
  it, is denied (`blocked`);
- `log_idea` is denied once today's session has read external content (`contaminated`,
  decision 100): `ideas.md` is memory, and what was read stays in the context of the session,
  so the day's mark counts, not only this run's.

When it allows, the hook returns `{}`: no opinion, so `allowed_tools` and `dontAsk` still decide
as a second layer. A `permissionDecision: "allow"` would skip them. The matcher goes with
`on_failure="block"`: a hook that raises blocks the call.

The log says only the decision and, for our tools, their name: never the arguments, and never
the name of a tool outside the table (the model chose it).
"""

import logging
import sqlite3
from datetime import date
from typing import Any

from claude_agent_sdk import HookContext, HookMatcher
from claude_agent_sdk.types import HookInput, HookJSONOutput

from jimic.agent.tools import model_name
from jimic.memory import conversations
from jimic.memory.schemas import TOOL_NAMES, Limits

logger = logging.getLogger(__name__)

OURS = {model_name(name): name for name in TOOL_NAMES}
REASONS = {
    "outside_table": "Esa herramienta no existe para JimiC.",
    "blocked": "Esa herramienta está bloqueada en limites.yaml.",
    "contaminated": (
        "Hoy esta sesión ya ha leído el calendario o las tareas, así que no anoto ideas:"
        " dile a Pedro que la apunte él en ideas.md."
    ),
}


def deny(code: str) -> HookJSONOutput:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASONS[code],
        }
    }


def decide(limits: Limits, conn: sqlite3.Connection, day: date, tool_name: str) -> str | None:
    """The code of the denial of `tool_name` (as the model sees it), or `None` to allow it."""
    ours = OURS.get(tool_name)
    if ours is None:
        return "outside_table"
    levels = {str(name): level for name, level in limits.tool_levels.items()}
    if levels.get(ours) != "free":  # missing counts as blocked
        return "blocked"
    if ours == "log_idea" and conversations.read_external(conn, day):
        return "contaminated"
    return None


def make_guard(limits: Limits, conn: sqlite3.Connection, day: date):
    """The hook for one run of today's session (`day`)."""

    async def guard(
        input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        data: dict[str, Any] = dict(input)
        if data.get("hook_event_name") != "PreToolUse":
            return {}
        tool_name = str(data.get("tool_name", ""))
        code = decide(limits, conn, day, tool_name)
        name = OURS.get(tool_name, "?")
        if code is None:
            logger.info("guard: allow %s", name)
            return {}
        logger.warning("guard: deny:%s %s", code, name)
        return deny(code)

    return guard


def guard_hooks(
    limits: Limits, conn: sqlite3.Connection, day: date
) -> dict[Any, list[HookMatcher]]:
    """The `hooks` of the options: every tool (`matcher=None`), and a failure blocks."""
    return {
        "PreToolUse": [
            HookMatcher(matcher=None, hooks=[make_guard(limits, conn, day)], on_failure="block")
        ]
    }
