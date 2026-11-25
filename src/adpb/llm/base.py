"""Provider-neutral model interface used by every agent.

Agents only ever call ``ModelClient.complete(...)``. That keeps three things swappable
without touching agent code: Claude (primary), VT ARC (free fallback inside the VT
network), and `ScriptedModel` (offline tests and the deterministic demo). It also gives
one choke point where the orchestrator's budget and event log wrap every call.

Conversations are stored in a neutral shape and translated by each client:
  {"role": "user", "content": str}
  {"role": "assistant", "turn": ModelTurn}      # replayed verbatim to the same provider
  {"role": "tool", "results": [ToolResult]}
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from adpb.observability.pricing import Usage


class ModelError(RuntimeError):
    """Base class for model failures the agent loop knows how to handle."""


class ModelUnavailable(ModelError):
    """Network down, auth failure, or provider outage."""


class ModelRefusal(ModelError):
    """The provider's safety system declined the request."""


class ModelTruncated(ModelError):
    """The reply hit max_tokens before it finished."""


@dataclass(frozen=True)
class ToolSpec:
    """A tool as the model sees it. ``name`` is ``<server>__<tool>`` (MCP-qualified)."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class ModelTurn:
    """One model response, normalised."""

    model: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    parsed: BaseModel | None = None
    parse_error: str | None = None
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "end_turn"
    raw: Any = None  # provider-native content, replayed unchanged on the next request


class Conversation:
    """The message history of one agent invocation (never shared between agents)."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def assistant(self, turn: ModelTurn) -> None:
        self.messages.append({"role": "assistant", "turn": turn})

    def tool_results(self, results: list[ToolResult]) -> None:
        self.messages.append({"role": "tool", "results": results})

    def text(self) -> str:
        """Flattened view used for token estimates and test assertions."""
        parts = []
        for m in self.messages:
            if m["role"] == "user":
                parts.append(m["content"])
            elif m["role"] == "assistant":
                parts.append(m["turn"].text)
            else:
                parts.extend(r.content for r in m["results"])
        return "\n".join(parts)


class ModelClient(Protocol):
    def complete(
        self,
        *,
        role: str,
        system: str,
        conversation: Conversation,
        output_type: type[BaseModel],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 16_000,
    ) -> ModelTurn: ...

    def model_for(self, role: str) -> str: ...


# -- lenient JSON (for providers without schema-constrained output) ---------------------

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_lenient[T: BaseModel](schema: type[T], text: str) -> T:
    """Validate ``text`` against ``schema``, repairing corruptions seen from VT ARC models:
    markdown fences, prose around the JSON, and a stray leading ``{``."""
    text = (text or "").strip()
    candidates = [text, _FENCE_RE.sub("", text).strip()]
    first = text.find("{")
    if first != -1:
        candidates.append(text[first: text.rfind("}") + 1])
        second = text.find("{", first + 1)
        if second != -1:
            candidates.append(text[second: text.rfind("}") + 1])
    last_err: Exception | None = None
    for cand in candidates:
        if not cand:
            continue
        try:
            return schema.model_validate_json(cand)
        except (ValidationError, ValueError) as exc:
            last_err = exc
    raise ValueError(f"unparseable {schema.__name__} output: {last_err}")


def json_schema_hint(output_type: type[BaseModel]) -> str:
    """Compact JSON schema text for providers that need it spelled out in the prompt."""
    return json.dumps(output_type.model_json_schema(), separators=(",", ":"))
