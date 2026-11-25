"""Shared helpers for the project's MCP servers."""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

__all__ = ["MCPServer", "ToolError", "inside", "new_server"]


def new_server(name: str, instructions: str) -> MCPServer:
    # Quiet logs: tool errors are returned to the caller, not printed to the console.
    return MCPServer(name, instructions=instructions, log_level="ERROR")


def inside(root: Path, candidate: str | Path) -> Path:
    """Resolve ``candidate`` (absolute, or relative to ``root``) and refuse escapes."""
    root = Path(root).resolve()
    path = Path(candidate)
    path = (path if path.is_absolute() else root / path).resolve()
    if path != root and root not in path.parents:
        raise ToolError(f"path '{candidate}' is outside the allowed folder")
    return path
