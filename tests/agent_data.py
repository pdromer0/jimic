"""Doubles and invented data for the agent: nothing here is real.

The tools are listed and called through `SdkMcpBridge`, the same in-memory MCP path the SDK
uses with the engine, so what a test sees is what the model would see. `SpyWriter` stands in
for the writer of Todoist and records every call: the agent must never reach it.
"""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, McpSdkServerConfig, ResultMessage

# The bridge is internal to the SDK, but it is the path the engine's requests take.
from claude_agent_sdk._internal.sdk_mcp_bridge import SdkMcpBridge
from conftest import FixedClock
from heartbeat_data import MADRID, calendar_with, event, madrid, sources, task

from jimic.agent.tools import ToolContext
from jimic.heartbeat.sources import Sources
from jimic.memory.conversations import note_message
from jimic.tools.todoist import TaskList, TodoistTools

SECRET_IDEA = "Idea-secreta-de-Pedro-4417"
SECRET_ARG = "Argumento-secreto-del-agente-7730"
MAX_WRITES = 20


class SpyWriter:
    """Records each call; the tests check it stays empty."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if len(self.calls) > MAX_WRITES:
            raise AssertionError("the writer was called in a loop")

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        self._record("create")

    async def move(self, *args: Any, **kwargs: Any) -> None:
        self._record("move")

    async def complete(self, *args: Any, **kwargs: Any) -> None:
        self._record("complete")

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        self._record("delete")


def agent_sources(tasks: TaskList | None = None, writer: SpyWriter | None = None) -> Sources:
    """The fixed sources of the heartbeat, with a spy as the writer of Todoist."""
    base = sources(
        calendar_with(event(madrid("2030-01-07T10:00"))),
        tasks if tasks is not None else TaskList(available=True, tasks=[task("t1"), task("t2")]),
    )
    todoist = TodoistTools(base.todoist.reader, writer or SpyWriter(), base.todoist.close)
    return Sources(todoist=todoist, calendar=base.calendar, apex=base.apex)


def context(
    conn: sqlite3.Connection,
    config_dir: Path,
    srcs: Sources | None = None,
    now: str = "2030-01-07T10:15",
) -> ToolContext:
    """The tools of Monday's session; the day has its row, as the conversation writes it."""
    moment = madrid(now)
    note_message(conn, moment.date(), moment)
    return ToolContext.of(
        srcs if srcs is not None else agent_sources(),
        conn,
        FixedClock(moment),
        MADRID,
        config_dir,
        moment.date(),
    )


async def _request(bridge: SdkMcpBridge, number: int, method: str, params: dict) -> dict:
    message = {"jsonrpc": "2.0", "id": number, "method": method, "params": params}
    answer = await bridge.handle(message)
    assert answer is not None
    return answer


async def _initialized(server: McpSdkServerConfig) -> SdkMcpBridge:
    bridge = SdkMcpBridge(server["name"], server["instance"])
    await _request(
        bridge,
        0,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "tests", "version": "0"},
        },
    )
    return bridge


async def listed_tools(server: McpSdkServerConfig) -> list[dict]:
    """What the server answers to `tools/list`."""
    bridge = await _initialized(server)
    try:
        return (await _request(bridge, 1, "tools/list", {}))["result"]["tools"]
    finally:
        await bridge.aclose()


async def call_tool(server: McpSdkServerConfig, name: str, args: dict) -> tuple[str, bool]:
    """Call `name` through the bridge; return its text and whether it is an error."""
    bridge = await _initialized(server)
    try:
        answer = await _request(bridge, 1, "tools/call", {"name": name, "arguments": args})
    finally:
        await bridge.aclose()
    result = answer["result"]
    return "".join(block["text"] for block in result["content"]), result["isError"]


def approvals(conn: sqlite3.Connection) -> list[tuple[str, dict, str, str]]:
    return [
        (kind, json.loads(payload), status, expires)
        for kind, payload, status, expires in conn.execute(
            "SELECT kind, payload, status, expires_at FROM approvals ORDER BY id"
        )
    ]


# --- The SDK ------------------------------------------------------------------------------------

MAX_RUNS = 50
"""Runs `FakeAgent` accepts in one test."""
PEDRO_MESSAGE = "Mensaje-de-Pedro-que-no-va-al-log"
AGENT_ANSWER = "Respuesta-del-agente-que-no-va-al-log"


def result_message(
    text: str | None = AGENT_ANSWER,
    session: str = "sesion-1",
    cost: float | None = 0.0123,
    subtype: str = "success",
    is_error: bool = False,
    api_status: int | None = None,
    usage: dict[str, Any] | None = None,
) -> ResultMessage:
    """A real `ResultMessage` of the SDK, invented."""
    return ResultMessage(
        subtype=subtype,
        duration_ms=10,
        duration_api_ms=8,
        is_error=is_error,
        num_turns=2,
        session_id=session,
        total_cost_usd=cost,
        usage=usage
        if usage is not None
        else {
            "input_tokens": 4,
            "output_tokens": 39,
            "cache_read_input_tokens": 3588,
            "cache_creation_input_tokens": 3812,
            "cache_creation": {"ephemeral_5m_input_tokens": 3812, "ephemeral_1h_input_tokens": 0},
        },
        result=text,
        api_error_status=api_status,
    )


type Script = list[Any] | BaseException


class FakeAgent:
    """Stands in for `claude_agent_sdk.query`: records each run and yields its script (a list
    of messages, or an error to raise after them). It has a cap, and a gate to keep a run
    thinking."""

    def __init__(self, *scripts: Script) -> None:
        self.scripts = list(scripts) or [[result_message()]]
        self.runs: list[tuple[str, ClaudeAgentOptions]] = []
        self.gate: asyncio.Event | None = None
        self.active = 0
        self.most_active = 0

    async def _run(self, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        self.runs.append((prompt, options))
        if len(self.runs) > MAX_RUNS:
            raise AssertionError(f"the agent ran more than {MAX_RUNS} times")
        script = self.scripts[min(len(self.runs), len(self.scripts)) - 1]
        self.active += 1
        self.most_active = max(self.most_active, self.active)
        try:
            if self.gate is not None:
                await self.gate.wait()
            if isinstance(script, BaseException):
                raise script
            for message in script:
                if isinstance(message, BaseException):
                    raise message
                yield message
        finally:
            self.active -= 1

    def __call__(self, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        return self._run(prompt, options)

    @property
    def resumed(self) -> list[str | None]:
        return [options.resume for _, options in self.runs]
