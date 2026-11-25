"""A scripted stand-in for an LLM (Lab 7's ``fake_model.py`` idea, generalised).

Each agent role gets a queue of responses. An item can be:
  * a Pydantic object or dict  -> the final structured answer;
  * a list of `ToolCall`        -> the model asks for tools this turn;
  * a callable(conversation)    -> computed from what the agent actually sent;
  * an Exception instance       -> raised, to simulate outages and refusals.

This is what makes the whole multi-agent graph testable offline, with no API key, in
milliseconds - and it lets a test assert exactly what each agent was shown.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from adpb.llm.base import Conversation, ModelTurn, ToolCall, ToolSpec
from adpb.observability.budget import estimate_tokens
from adpb.observability.pricing import Usage


class ScriptExhausted(AssertionError):
    """The code under test called a role more times than the test scripted."""


class ScriptedModel:
    def __init__(self, script: dict[str, list[Any]]):
        self.script = {role: list(items) for role, items in script.items()}
        self.calls: list[dict[str, Any]] = []

    def model_for(self, role: str) -> str:
        return "scripted"

    def remaining(self, role: str) -> int:
        return len(self.script.get(role, []))

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
        prompt_text = conversation.text()
        self.calls.append({"role": role, "system": system, "prompt_text": prompt_text,
                           "tools": [t.name for t in tools or []]})
        queue = self.script.get(role)
        if not queue:
            raise ScriptExhausted(f"no scripted response left for role '{role}'")
        item = queue.pop(0)
        if callable(item) and not isinstance(item, type):
            item = item(conversation)
        if isinstance(item, Exception):
            raise item
        usage = Usage(input_tokens=estimate_tokens(system + prompt_text), output_tokens=50)
        if isinstance(item, list) and all(isinstance(c, ToolCall) for c in item):
            return ModelTurn(model="scripted", tool_calls=item, usage=usage,
                             stop_reason="tool_use")
        return self._answer(item, output_type, usage)

    @staticmethod
    def _answer(item: Any, output_type: type[BaseModel], usage: Usage) -> ModelTurn:
        if isinstance(item, BaseModel):
            text = item.model_dump_json()
            try:
                parsed = output_type.model_validate(item.model_dump())
                return ModelTurn(model="scripted", text=text, parsed=parsed, usage=usage)
            except ValidationError as exc:
                return ModelTurn(model="scripted", text=text, parse_error=str(exc), usage=usage)
        if isinstance(item, dict):
            try:
                return ModelTurn(model="scripted", text=str(item),
                                 parsed=output_type.model_validate(item), usage=usage)
            except ValidationError as exc:
                # Lets tests exercise the agent's "your JSON was invalid, fix it" loop.
                return ModelTurn(model="scripted", text=str(item), parse_error=str(exc),
                                 usage=usage)
        raise TypeError(f"unsupported scripted item: {item!r}")


Script = dict[str, list[BaseModel | dict | list[ToolCall] | Callable | Exception]]
