"""Claude client (primary provider).

Uses ``client.beta.messages.parse(...)`` so the reply is constrained to the agent's
Pydantic output type and validated by the SDK. Notes on the request we build:

* No ``temperature``/``top_p``: current Claude models reject sampling parameters.
  Reproducibility therefore comes from *freezing the code that passed* in git and
  re-running it without the LLM (requirement 18, mitigation 10), not from sampling.
* Adaptive thinking is on by default on these models, so we leave ``thinking`` unset and
  set ``effort`` per role instead (cheap roles think less).
* Sonnet requests opt into server-side refusal fallbacks; Haiku has none.
* Assistant content (including thinking blocks) is replayed *unchanged* on the next
  request, which the API requires for multi-turn tool use.
* The system prompt is marked cacheable: an agent's later turns re-read it cheaply.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from adpb.llm.base import (
    Conversation,
    ModelRefusal,
    ModelTruncated,
    ModelTurn,
    ModelUnavailable,
    ToolCall,
    ToolSpec,
)
from adpb.observability.pricing import Usage

DEFAULT_MODELS = {
    "planner": "claude-sonnet-5-5",
    "coder": "claude-sonnet-5-5",
    "debugger": "claude-sonnet-5-5",
    "tester": "claude-haiku-5-5",
    "validator": "claude-haiku-5-5",
}
DEFAULT_EFFORT = {
    "planner": "medium",
    "coder": "medium",
    "debugger": "medium",
    "tester": "low",
    "validator": "low",
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicModel:
    def __init__(
        self,
        sdk: Any | None = None,
        models: dict[str, str] | None = None,
        effort: dict[str, str] | None = None,
        default_model: str = "claude-sonnet-5-5",
        timeout_s: float = 180.0,
    ):
        if sdk is None:
            import anthropic  # imported lazily so offline tests never need the key

            sdk = anthropic.Anthropic(timeout=timeout_s, max_retries=4)
        self._sdk = sdk
        self.models = {**DEFAULT_MODELS, **(models or {})}
        self.effort = {**DEFAULT_EFFORT, **(effort or {})}
        self.default_model = default_model

    def model_for(self, role: str) -> str:
        return self.models.get(role, self.default_model)

    # -- request translation ----------------------------------------------------------

    @staticmethod
    def _messages(conversation: Conversation) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in conversation.messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                turn = m["turn"]
                content = turn.raw if turn.raw is not None else [{"type": "text",
                                                                  "text": turn.text}]
                out.append({"role": "assistant", "content": content})
            else:  # all tool results of one turn go back in ONE user message
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content,
                     "is_error": r.is_error} for r in m["results"]]})
        return out

    @staticmethod
    def _tools(tools: list[ToolSpec] | None) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools or []]

    # -- the call ---------------------------------------------------------------------

    def complete(
        self,
        *,
        role: str,
        system: str,
        conversation: Conversation,
        output_type: type[BaseModel],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 16_000,
    ) -> ModelTurn:
        model = self.model_for(role)
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": self._messages(conversation),
            "output_format": output_type,
            "output_config": {"effort": self.effort.get(role, "medium")},
            "betas": [],
        }
        if tools:
            request["tools"] = self._tools(tools)
        if "haiku" not in model:
            request["betas"].append(FALLBACK_BETA)
            request["fallbacks"] = "default"
        if not request["betas"]:
            del request["betas"]

        try:
            resp = self._sdk.beta.messages.parse(**request)
        except ValidationError as exc:
            # The SDK validated the JSON against our model and our own validators objected
            # (e.g. a plan with a cycle). Hand it back so the agent can ask for a repair.
            return ModelTurn(model=model, parse_error=str(exc), stop_reason="end_turn")
        except Exception as exc:  # network/auth/5xx after the SDK's own retries
            if type(exc).__module__.startswith("anthropic"):
                raise ModelUnavailable(f"{type(exc).__name__}: {exc}") from exc
            raise

        usage = Usage(
            input_tokens=getattr(resp.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(resp.usage, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0)
            or 0,
        )
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise ModelRefusal(f"model declined the request (category: {category})")
        if resp.stop_reason == "max_tokens":
            raise ModelTruncated(f"reply hit max_tokens={max_tokens}")

        raw = [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b
               for b in resp.content]
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", None) == "text")
        calls = [ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
                 for b in resp.content if getattr(b, "type", None) == "tool_use"]
        parsed = getattr(resp, "parsed_output", None)
        return ModelTurn(
            model=getattr(resp, "model", model) or model,
            text=text,
            tool_calls=calls,
            parsed=parsed if not calls else None,
            usage=usage,
            stop_reason=resp.stop_reason,
            raw=raw,
        )
