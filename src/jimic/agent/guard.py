"""The guard: the `PreToolUse` hook that sees every call of the agent (decisions 15 and 27).

Deny by default (6.5 of the design):

- a tool outside the table of the hito 1 spec is denied (`outside_table`);
- a tool whose level in `tool_levels` of `limites.yaml` is not `free`, or that is missing from
  it, is denied (`blocked`);
- when it lets `read_calendar` or `read_tasks` through, it marks the run (`RunState`) and
  today's session before they run: with a read and a proposal in one message of the model, both
  hooks come before either tool, and the read's own mark could come too late for the proposal.
  The run's mark is what `propose_change` and `log_idea` refuse with (decision 1.12-a, which
  revises 100: the hook no longer denies `log_idea` by the day's mark).

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

from jimic.agent.tools import CONTAMINATING, RunState, model_name
from jimic.memory import conversations
from jimic.memory.schemas import TOOL_NAMES, Limits, is_free

logger = logging.getLogger(__name__)

OURS = {model_name(name): name for name in TOOL_NAMES}
REASONS = {
    "outside_table": "Esa herramienta no existe para JimiC.",
    "blocked": "Esa herramienta está bloqueada en limites.yaml.",
    "unmarked": "No puedo leer eso ahora mismo.",
}


def deny(code: str) -> HookJSONOutput:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASONS[code],
        }
    }


def decide(
    limits: Limits,
    conn: sqlite3.Connection,
    day: date,
    tool_name: str,
    run: RunState | None = None,
) -> str | None:
    """The code of the denial of `tool_name` (as the model sees it), or `None` to allow it."""
    ours = OURS.get(tool_name)
    if ours is None:
        return "outside_table"
    if not is_free(limits, ours):
        return "blocked"
    if ours in CONTAMINATING:
        try:
            conversations.mark_external(conn, day)
        except LookupError:
            return "unmarked"  # a read that cannot be marked must not happen
        if run is not None:
            run.contaminated = True
    return None


def make_guard(limits: Limits, conn: sqlite3.Connection, day: date, run: RunState | None = None):
    """The hook for one run (`run`) of today's session (`day`)."""

    async def guard(
        input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        data: dict[str, Any] = dict(input)
        if data.get("hook_event_name") != "PreToolUse":
            return {}
        tool_name = str(data.get("tool_name", ""))
        code = decide(limits, conn, day, tool_name, run)
        name = OURS.get(tool_name, "?")
        if code is None:
            logger.info("guard: allow %s", name)
            return {}
        logger.warning("guard: deny:%s %s", code, name)
        return deny(code)

    return guard


def guard_hooks(
    limits: Limits, conn: sqlite3.Connection, day: date, run: RunState | None = None
) -> dict[Any, list[HookMatcher]]:
    """The `hooks` of the options: every tool (`matcher=None`), and a failure blocks."""
    return {
        "PreToolUse": [
            HookMatcher(
                matcher=None, hooks=[make_guard(limits, conn, day, run)], on_failure="block"
            )
        ]
    }
