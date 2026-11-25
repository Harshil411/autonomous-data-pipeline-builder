"""The tool gateway: the only way any agent reaches a tool (requirement 15).

It holds one MCP client session per server and exposes a *synchronous* API, because the
orchestrator and agents are plain sequential Python. Internally an anyio "blocking
portal" runs the async MCP clients on a background event loop.

Every call is checked against `ToolPolicy` *before* it is sent, and logged to the run's
event log with the calling role, server, tool, outcome and latency (requirement 22).
Servers may be in-process ``MCPServer`` objects (tests) or ``StdioServerParameters``
(real runs: each server is a subprocess with only the environment it needs).
"""

from __future__ import annotations

import json
import time
from contextlib import ExitStack
from typing import Any

import anyio.from_thread
from mcp.client.client import Client

from adpb.llm.base import ToolSpec
from adpb.observability.events import EventLog
from adpb.tools.registry import ToolPolicy

SEP = "__"  # model-visible name: "<server>__<tool>"
MAX_LOGGED_ARG_CHARS = 300


class ToolNotAllowed(PermissionError):
    pass


class ToolFailed(RuntimeError):
    pass


def _summarise(args: dict[str, Any]) -> dict[str, Any]:
    """Short argument preview for the log (code bodies and schemas are truncated)."""
    out = {}
    for k, v in args.items():
        text = v if isinstance(v, str) else json.dumps(v, default=str)
        out[k] = text if len(text) <= MAX_LOGGED_ARG_CHARS else text[:MAX_LOGGED_ARG_CHARS] + "..."
    return out


class ToolGateway:
    def __init__(self, servers: dict[str, Any], policy: ToolPolicy | None = None,
                 events: EventLog | None = None, read_timeout_s: float = 300.0):
        self._targets = servers
        self.policy = policy or ToolPolicy()
        self.events = events
        self._read_timeout = read_timeout_s
        self._stack = ExitStack()
        self._clients: dict[str, Client] = {}
        self._schemas: dict[str, dict[str, Any]] = {}  # "server.tool" -> (description, schema)

    # -- lifecycle ----------------------------------------------------------------------

    def __enter__(self) -> ToolGateway:
        try:
            portal = self._stack.enter_context(anyio.from_thread.start_blocking_portal())
            self._portal = portal
            for name, target in self._targets.items():
                client = Client(target, read_timeout_seconds=self._read_timeout)
                self._clients[name] = self._stack.enter_context(
                    portal.wrap_async_context_manager(client))
                listed = portal.call(self._clients[name].list_tools)
                for tool in listed.tools:
                    self._schemas[f"{name}.{tool.name}"] = {
                        "description": tool.description or "",
                        "schema": tool.input_schema,
                    }
        except BaseException:
            self._stack.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self._stack.close()

    # -- calls --------------------------------------------------------------------------

    def call(self, role: str, server: str, tool: str, args: dict[str, Any] | None = None) -> Any:
        args = args or {}
        started = time.monotonic()
        if not self.policy.allows(role, server, tool):
            self._log(role, server, tool, args, "denied", started)
            raise ToolNotAllowed(f"role '{role}' may not call {server}.{tool}")
        if server not in self._clients:
            self._log(role, server, tool, args, "error", started, "server not connected")
            raise ToolFailed(f"MCP server '{server}' is not connected")
        result = self._portal.call(self._clients[server].call_tool, tool, args)
        text = "".join(getattr(c, "text", "") for c in result.content or [])
        if result.is_error:
            self._log(role, server, tool, args, "error", started, text[:500])
            raise ToolFailed(text or f"{server}.{tool} failed")
        self._log(role, server, tool, args, "ok", started)
        try:
            return json.loads(text) if text else None
        except json.JSONDecodeError:
            return text

    def call_qualified(self, role: str, qualified: str, args: dict[str, Any]) -> Any:
        server, _, tool = qualified.partition(SEP)
        return self.call(role, server, tool, args)

    def specs_for(self, role: str) -> list[ToolSpec]:
        """The tools this role's model may call, named ``server__tool``."""
        specs = []
        for dotted in sorted(self.policy.visible(role)):
            if dotted not in self._schemas:
                continue
            server, tool = dotted.split(".", 1)
            meta = self._schemas[dotted]
            specs.append(ToolSpec(name=f"{server}{SEP}{tool}", description=meta["description"],
                                  input_schema=meta["schema"]))
        return specs

    def _log(self, role, server, tool, args, outcome, started, detail: str = "") -> None:
        if self.events is None:
            return
        self.events.emit("tool_call", agent=role, server=server, tool=tool, outcome=outcome,
                         ms=round((time.monotonic() - started) * 1000), args=_summarise(args),
                         detail=detail)
