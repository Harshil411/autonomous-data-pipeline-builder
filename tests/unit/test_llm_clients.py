"""Model clients (mitigations 9-10; Labs 1-2).

All agents talk to a model through one small `ModelClient` interface with three
implementations: Claude (primary), an OpenAI-compatible client for VT ARC (fallback), and
`ScriptedModel` for offline tests. These tests drive the Claude client with a fake SDK so
we can assert the exact request it builds without spending money.
"""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from adpb.llm.anthropic_client import AnthropicModel
from adpb.llm.base import (
    Conversation,
    ModelRefusal,
    ModelTruncated,
    ToolCall,
    ToolResult,
    ToolSpec,
    parse_lenient,
)
from adpb.llm.openai_compat import OpenAICompatModel
from adpb.llm.scripted import ScriptedModel, ScriptExhausted


class Answer(BaseModel):
    value: int


SEARCH = ToolSpec(name="memory__search", description="search memory",
                  input_schema={"type": "object", "properties": {"q": {"type": "string"}},
                                "required": ["q"]})


# -- conversation -----------------------------------------------------------------------


def test_conversation_records_turns_in_order():
    conv = Conversation()
    conv.user("hi")
    conv.tool_results([ToolResult(call_id="c1", content="ok")])
    assert [m["role"] for m in conv.messages] == ["user", "tool"]


# -- scripted ---------------------------------------------------------------------------


class TestScripted:
    def test_returns_scripted_outputs_per_role_in_order(self):
        m = ScriptedModel({"planner": [Answer(value=1), Answer(value=2)]})
        conv = Conversation()
        conv.user("q")
        assert m.complete(role="planner", system="s", conversation=conv,
                          output_type=Answer).parsed == Answer(value=1)
        assert m.complete(role="planner", system="s", conversation=conv,
                          output_type=Answer).parsed == Answer(value=2)

    def test_dicts_are_validated_into_the_requested_type(self):
        m = ScriptedModel({"coder": [{"value": 7}]})
        turn = m.complete(role="coder", system="s", conversation=Conversation(), output_type=Answer)
        assert turn.parsed == Answer(value=7)

    def test_tool_calls_can_be_scripted(self):
        m = ScriptedModel({"coder": [[ToolCall(id="t1", name="memory__search",
                                              arguments={"q": "x"})]]})
        turn = m.complete(role="coder", system="s", conversation=Conversation(),
                          tools=[SEARCH], output_type=Answer)
        assert turn.tool_calls[0].name == "memory__search" and turn.parsed is None

    def test_callables_see_the_conversation(self):
        m = ScriptedModel({"debugger": [lambda conv: Answer(value=len(conv.messages))]})
        conv = Conversation()
        conv.user("a")
        conv.user("b")
        assert m.complete(role="debugger", system="s", conversation=conv,
                          output_type=Answer).parsed.value == 2

    def test_exhausted_script_fails_loudly(self):
        m = ScriptedModel({"planner": []})
        with pytest.raises(ScriptExhausted):
            m.complete(role="planner", system="s", conversation=Conversation(), output_type=Answer)

    def test_calls_are_recorded_for_assertions(self):
        m = ScriptedModel({"planner": [Answer(value=1)]})
        conv = Conversation()
        conv.user("the request")
        m.complete(role="planner", system="sys", conversation=conv, output_type=Answer)
        assert m.calls[0]["role"] == "planner"
        assert "the request" in m.calls[0]["prompt_text"]

    def test_scripted_usage_is_nonzero_so_budgets_are_exercised(self):
        m = ScriptedModel({"planner": [Answer(value=1)]})
        turn = m.complete(role="planner", system="s", conversation=Conversation(),
                          output_type=Answer)
        assert turn.usage.total > 0 and turn.model == "scripted"


# -- Claude -----------------------------------------------------------------------------


class Block(SimpleNamespace):
    def model_dump(self, **_):
        return {k: v for k, v in vars(self).items() if v is not None}


def _response(content, parsed=None, stop="end_turn", model="claude-sonnet-5-5"):
    return SimpleNamespace(
        content=content, parsed_output=parsed, stop_reason=stop, model=model,
        stop_details=SimpleNamespace(category="cyber", explanation="no") if stop == "refusal"
        else None,
        usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=5,
                              cache_creation_input_tokens=0),
    )


class FakeSDK:
    """Stands in for anthropic.Anthropic(); records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


def _claude(responses, **kw):
    sdk = FakeSDK(responses)
    return AnthropicModel(sdk=sdk, models={"planner": "claude-sonnet-5-5",
                                           "tester": "claude-haiku-5-5"}, **kw), sdk


class TestAnthropicModel:
    def test_structured_answer_is_returned_with_usage(self):
        model, sdk = _claude([_response([Block(type="text", text='{"value": 3}')],
                                        parsed=Answer(value=3))])
        conv = Conversation()
        conv.user("question")
        turn = model.complete(role="planner", system="be precise", conversation=conv,
                              output_type=Answer)
        assert turn.parsed == Answer(value=3)
        assert turn.usage.input_tokens == 100 and turn.usage.cache_read_input_tokens == 5
        req = sdk.requests[0]
        assert req["model"] == "claude-sonnet-5-5"
        assert req["output_format"] is Answer
        assert req["messages"] == [{"role": "user", "content": "question"}]

    def test_no_sampling_parameters_are_sent(self):
        # Sampling parameters are rejected on current Claude models; reproducibility comes
        # from freezing the committed code instead (mitigation 10).
        model, sdk = _claude([_response([], parsed=Answer(value=1))])
        model.complete(role="planner", system="s", conversation=Conversation(), output_type=Answer)
        assert not {"temperature", "top_p", "top_k"} & set(sdk.requests[0])

    def test_sonnet_requests_opt_into_refusal_fallbacks_haiku_does_not(self):
        model, sdk = _claude([_response([], parsed=Answer(value=1)),
                              _response([], parsed=Answer(value=1), model="claude-haiku-5-5")])
        model.complete(role="planner", system="s", conversation=Conversation(), output_type=Answer)
        model.complete(role="tester", system="s", conversation=Conversation(), output_type=Answer)
        assert sdk.requests[0]["fallbacks"] == "default"
        assert "server-side-fallback-2026-07-01" in sdk.requests[0]["betas"]
        assert "fallbacks" not in sdk.requests[1]

    def test_tool_use_blocks_become_tool_calls_and_are_replayed_verbatim(self):
        tool_block = Block(type="tool_use", id="toolu_1", name="memory__search",
                           input={"q": "orders"})
        model, sdk = _claude([
            _response([Block(type="thinking", thinking="", signature="sig"), tool_block],
                      stop="tool_use"),
            _response([Block(type="text", text='{"value": 9}')], parsed=Answer(value=9)),
        ])
        conv = Conversation()
        conv.user("q")
        turn = model.complete(role="planner", system="s", conversation=conv, tools=[SEARCH],
                              output_type=Answer)
        assert turn.tool_calls == [ToolCall(id="toolu_1", name="memory__search",
                                            arguments={"q": "orders"})]
        assert sdk.requests[0]["tools"][0]["name"] == "memory__search"
        conv.assistant(turn)
        conv.tool_results([ToolResult(call_id="toolu_1", content="[]")])
        model.complete(role="planner", system="s", conversation=conv, tools=[SEARCH],
                       output_type=Answer)
        msgs = sdk.requests[1]["messages"]
        # Thinking + tool_use blocks go back unchanged, then one user message of results.
        assert msgs[1]["role"] == "assistant"
        assert [b["type"] for b in msgs[1]["content"]] == ["thinking", "tool_use"]
        assert msgs[2] == {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "[]",
             "is_error": False}]}

    def test_refusal_is_raised_not_parsed(self):
        model, _ = _claude([_response([], stop="refusal")])
        with pytest.raises(ModelRefusal, match="cyber"):
            model.complete(role="planner", system="s", conversation=Conversation(),
                           output_type=Answer)

    def test_truncation_is_raised(self):
        model, _ = _claude([_response([Block(type="text", text='{"val')], stop="max_tokens")])
        with pytest.raises(ModelTruncated):
            model.complete(role="planner", system="s", conversation=Conversation(),
                           output_type=Answer)

    def test_unconfigured_role_uses_the_default_model(self):
        model, sdk = _claude([_response([], parsed=Answer(value=1))], default_model="claude-x")
        model.complete(role="some_new_role", system="s", conversation=Conversation(),
                       output_type=Answer)
        assert sdk.requests[0]["model"] == "claude-x"


# -- VT ARC (OpenAI-compatible) -----------------------------------------------------------


class FakeOpenAI:
    def __init__(self, messages):
        self.replies = list(messages)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        msg = self.replies.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5), model="vt-arc-llm")


class TestOpenAICompat:
    def test_json_answer_is_parsed_leniently(self):
        sdk = FakeOpenAI([SimpleNamespace(content='```json\n{"value": 4}\n```', tool_calls=None)])
        model = OpenAICompatModel(sdk=sdk, model="vt-arc-llm", requests_per_minute=1000)
        turn = model.complete(role="planner", system="s", conversation=Conversation(),
                              output_type=Answer)
        assert turn.parsed == Answer(value=4) and turn.usage.total == 15

    def test_function_calls_become_tool_calls(self):
        call = SimpleNamespace(id="c1", function=SimpleNamespace(name="memory__search",
                                                                arguments='{"q": "a"}'))
        sdk = FakeOpenAI([SimpleNamespace(content=None, tool_calls=[call])])
        model = OpenAICompatModel(sdk=sdk, model="vt-arc-llm", requests_per_minute=1000)
        turn = model.complete(role="planner", system="s", conversation=Conversation(),
                              tools=[SEARCH], output_type=Answer)
        assert turn.tool_calls == [ToolCall(id="c1", name="memory__search", arguments={"q": "a"})]
        assert sdk.requests[0]["tools"][0]["function"]["name"] == "memory__search"


# -- lenient parsing --------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    '{"value": 1}',
    '```json\n{"value": 1}\n```',
    'Here you go:\n{"value": 1}\nThanks',
    '{{"value": 1}',  # stray leading brace seen from VT ARC
])
def test_parse_lenient_repairs_common_corruptions(text):
    assert parse_lenient(Answer, text) == Answer(value=1)


def test_parse_lenient_raises_on_garbage():
    with pytest.raises(ValueError):
        parse_lenient(Answer, "no json here")
