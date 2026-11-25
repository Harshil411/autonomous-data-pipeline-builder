"""Claude client (primary provider).

Uses ``client.beta.messages.create(...)`` with a JSON-schema output format derived from the
agent's Pydantic output type (``anthropic.transform_schema``), then validates the reply in
our own code. We deliberately do not use the SDK's ``parse()`` helper: when its validation
fails it raises before returning the response, which loses both the model's answer (needed
to ask for a precise repair) and the token usage (needed to charge the budget). Both bugs
showed up in the first live run. Notes on the request we build:

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
                # The API rejects empty text blocks; keep everything else verbatim.
                content = [b for b in content if not (b.get("type") == "text"
                                                      and not b.get("text"))]
                out.append({"role": "assistant",
                            "content": content or [{"type": "text", "text": "(no answer)"}]})
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
            "output_config": {"effort": self.effort.get(role, "medium"),
                              "format": {"type": "json_schema",
                                         "schema": self._schema(output_type)}},
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
            resp = self._sdk.beta.messages.create(**request)
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
        parsed, parse_error = None, None
        if not calls:
            try:
                parsed = output_type.model_validate_json(text)
            except (ValidationError, ValueError) as exc:
                # Kept, not raised: the agent loop sends this error back for a repair,
                # replaying the model's own answer (raw) so it can see what it wrote.
                parse_error = str(exc)
        return ModelTurn(
            model=getattr(resp, "model", model) or model,
            text=text,
            tool_calls=calls,
            parsed=parsed,
            parse_error=parse_error,
            usage=usage,
            stop_reason=resp.stop_reason,
            raw=raw,
        )

    @staticmethod
    def _schema(output_type: type[BaseModel]) -> dict[str, Any]:
        """The output type as a JSON schema the API accepts (unsupported keywords such as
        ``pattern`` or ``maxItems`` are moved into descriptions; Pydantic still enforces
        them when we validate the reply)."""
        import anthropic

        return anthropic.transform_schema(output_type)
