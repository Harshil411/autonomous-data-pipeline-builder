"""OpenAI-compatible client, used for VT ARC (free, inside the VT network).

VT ARC does not guarantee schema-constrained output, so the JSON schema is spelled out in
the system prompt and the reply is parsed leniently (fences, stray braces). Calls are
rate-limited client-side because ARC allows roughly 30 requests per minute.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any

from pydantic import BaseModel

from adpb.llm.base import (
    Conversation,
    ModelTruncated,
    ModelTurn,
    ModelUnavailable,
    ToolCall,
    ToolSpec,
    json_schema_hint,
    parse_lenient,
)
from adpb.observability.pricing import Usage


class _RateLimiter:
    """Sliding one-minute window shared by all calls from this client."""

    def __init__(self, per_minute: int):
        self.per_minute = max(1, per_minute)
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def wait(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                while self.calls and now - self.calls[0] > 60:
                    self.calls.popleft()
                if len(self.calls) < self.per_minute:
                    self.calls.append(now)
                    return
                sleep_for = 60 - (now - self.calls[0]) + 0.05
            time.sleep(max(0.05, sleep_for))


class OpenAICompatModel:
    def __init__(self, sdk: Any | None = None, model: str = "vt-arc-llm",
                 base_url: str | None = None, api_key: str | None = None,
                 requests_per_minute: int = 25):
        if sdk is None:
            from openai import OpenAI

            sdk = OpenAI(base_url=base_url, api_key=api_key, timeout=180, max_retries=3)
        self._sdk = sdk
        self.model = model
        self._limiter = _RateLimiter(requests_per_minute)

    def model_for(self, role: str) -> str:
        return self.model

    @staticmethod
    def _messages(system: str, conversation: Conversation) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in conversation.messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                turn = m["turn"]
                msg: dict[str, Any] = {"role": "assistant", "content": turn.text or None}
                if turn.tool_calls:
                    msg["tool_calls"] = [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                        for c in turn.tool_calls]
                out.append(msg)
            else:
                out.extend({"role": "tool", "tool_call_id": r.call_id, "content": r.content}
                           for r in m["results"])
        return out

    def complete(
        self,
        *,
        role: str,
        system: str,
        conversation: Conversation,
        output_type: type[BaseModel],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 8_000,  # ARC caps non-streaming output at 8k
    ) -> ModelTurn:
        system = (f"{system}\n\nWhen you give your final answer, reply with ONLY a JSON object "
                  f"matching this JSON schema:\n{json_schema_hint(output_type)}")
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": min(max_tokens, 8_000),
            "messages": self._messages(system, conversation),
        }
        if tools:
            request["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.input_schema}}
                for t in tools]
        self._limiter.wait()
        try:
            resp = self._sdk.chat.completions.create(**request)
        except Exception as exc:
            if type(exc).__module__.startswith("openai"):
                raise ModelUnavailable(f"{type(exc).__name__}: {exc}") from exc
            raise
        choice = resp.choices[0]
        usage = Usage(input_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
                      output_tokens=getattr(resp.usage, "completion_tokens", 0) or 0)
        if choice.finish_reason == "length":
            raise ModelTruncated("reply hit max_tokens")
        msg = choice.message
        calls = [ToolCall(id=c.id, name=c.function.name,
                          arguments=json.loads(c.function.arguments or "{}"))
                 for c in (msg.tool_calls or [])]
        turn = ModelTurn(model=self.model, text=msg.content or "", tool_calls=calls,
                         usage=usage, stop_reason="tool_use" if calls else "end_turn")
        if not calls:
            try:
                turn.parsed = parse_lenient(output_type, turn.text)
            except ValueError as exc:
                turn.parse_error = str(exc)
        return turn
