"""How the agent is configured (9.2 of the design; decisions 23, 27 and 30).

The SDK runs the engine of Claude Code as a subprocess. These options keep out everything that
is not JimiC's: the built-in tools (`tools=[]`), Pedro's settings, `CLAUDE.md`, permissions and
hooks (`setting_sources=[]`), any other MCP configuration (`strict_mcp_config`), the automatic
memory and the non-essential traffic. Two layers decide each call: the `PreToolUse` hook of
`guard.py` (task 1.11), which sees every call and denies what is not in the table or not `free`
in `tool_levels`, and `allowed_tools` with only the `free` ones, where `dontAsk` denies the rest.

The system prompt is the fixed instructions of `prompt.md` and of `plan.md` (the rules of the
plan of the day, task 1.14, which only apply when the code asks for the plan) and, after them,
the text of Pedro's five personal files (decision 1.12-c), rebuilt on every request, also when
the session of the day is resumed. The fixed part goes first, so it can be cached.

The key goes in the `env` of the options, never in `os.environ`: JimiC only reads its `.env`.
The options hold it, so they are never logged.
"""

from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, McpSdkServerConfig
from claude_agent_sdk.types import SystemPromptCustom

from jimic.agent.tools import SERVER_NAME, model_name
from jimic.config import Settings
from jimic.heartbeat.decide import API_URL
from jimic.memory.schemas import TOOL_NAMES, Limits, is_free

MAX_TURNS = 12
"""Turns of one run (one message of Pedro): three reads, a proposal and the answer fit with room
to spare; a loop of tools stops here, and so does its cost."""


PROMPT_FILES = ("prompt.md", "plan.md")


@cache
def prompt_text() -> str:
    """The fixed instructions, read once per process: they are files of the package."""
    folder = files("jimic.agent")
    texts = [folder.joinpath(name).read_text(encoding="utf-8").strip() for name in PROMPT_FILES]
    return "\n\n".join(texts) + "\n"


def agent_env(settings: Settings, config_dir: Path) -> dict[str, str]:
    """The variables of the engine, over the inherited environment."""
    return {
        "ANTHROPIC_API_KEY": settings.anthropic_api_key.get_secret_value(),
        # Explicit, as in the heartbeat: the inherited environment could move it.
        "ANTHROPIC_BASE_URL": API_URL,
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }


def _discard(line: str) -> None:
    """The engine's stderr may carry content: it is read and dropped, never shown or logged."""


def system_prompt(memory: str) -> SystemPromptCustom:
    """The fixed instructions first and Pedro's files after them (decision 1.12-c), rebuilt on
    every request (`snapshot=False`): with a plain text the session would keep the prompt of its
    first request and send it again when resumed, so the agent would see the files of the
    morning after a [Guardar]."""
    text = prompt_text() if not memory else f"{prompt_text().rstrip()}\n\n{memory}"
    return {"type": "custom", "prompt": text, "snapshot": False}


def free_tools(limits: Limits) -> list[str]:
    """The names the model sees of the tools that are `free` in `tool_levels`, in table order."""
    return [model_name(name) for name in TOOL_NAMES if is_free(limits, name)]


def build_options(
    settings: Settings,
    limits: Limits,
    agent_dir: Path,
    server: McpSdkServerConfig,
    resume: str | None,
    hooks: dict[Any, list[HookMatcher]],
    memory: str = "",
) -> ClaudeAgentOptions:
    """The options of one run; `resume` is today's session, if there is one, `hooks` the
    guard of this run (`guard.guard_hooks`) and `memory` the text of Pedro's files
    (`memory_context.memory_block`)."""
    return ClaudeAgentOptions(
        tools=[],
        allowed_tools=free_tools(limits),
        hooks=hooks,
        permission_mode="dontAsk",
        mcp_servers={SERVER_NAME: server},
        strict_mcp_config=True,
        setting_sources=[],
        system_prompt=system_prompt(memory),
        model=settings.agent_model,
        max_turns=MAX_TURNS,
        max_budget_usd=limits.budget.max_session_usd,
        env=agent_env(settings, agent_dir),
        cwd=agent_dir,
        resume=resume,
        stderr=_discard,
    )
