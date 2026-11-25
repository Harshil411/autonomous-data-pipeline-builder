"""Observability, budgets and secret redaction (requirements 22, 23, 24; Module 7, Lab 9).

Three of the flyer's "five things it won't do" live here:
  * "see your passwords or API keys"        -> Redactor
  * "run past its time or cost budget"      -> Budget
and requirement 22's per-run log of every agent call, tool call, token and dollar.
"""

import json

import pytest

from adpb.contracts import BudgetLimits
from adpb.observability.budget import Budget, BudgetExceeded
from adpb.observability.events import EventLog
from adpb.observability.pricing import Usage, cost_usd
from adpb.observability.redact import Redactor

# Fake keys are assembled from two literals so secret scanners do not flag this file.
FAKE_PREFIX = "sk-ant-" + "api03-"
FAKE_KEY = FAKE_PREFIX + "abcdefghijklmnop"


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


# -- pricing ----------------------------------------------------------------------------


class TestPricing:
    def test_sonnet_cost_uses_per_million_rates(self):
        # $2 in / $10 out per million tokens.
        assert cost_usd("claude-sonnet-5-5", Usage(input_tokens=1_000_000)) == pytest.approx(2.0)
        assert cost_usd("claude-sonnet-5-5", Usage(output_tokens=100_000)) == pytest.approx(1.0)

    def test_haiku_is_cheap(self):
        u = Usage(input_tokens=10_000, output_tokens=2_000)
        assert cost_usd("claude-haiku-5-5", u) == pytest.approx(0.001 + 0.001)

    def test_cache_reads_are_billed_at_the_cache_rate(self):
        u = Usage(cache_read_input_tokens=1_000_000)
        assert cost_usd("claude-sonnet-5-5", u) == pytest.approx(0.10)

    def test_free_and_scripted_models_cost_nothing(self):
        assert cost_usd("vt-arc-llm", Usage(input_tokens=10**6, output_tokens=10**6)) == 0.0
        assert cost_usd("scripted", Usage(input_tokens=500)) == 0.0

    def test_unknown_paid_model_is_priced_conservatively_not_as_free(self):
        # An unknown Claude id must not silently look free and dodge the cost budget.
        assert cost_usd("claude-something-new", Usage(input_tokens=10**6)) >= 4.0

    def test_total_tokens(self):
        assert Usage(input_tokens=3, output_tokens=4, cache_read_input_tokens=5).total == 12


# -- budget -----------------------------------------------------------------------------


class TestBudget:
    def test_charges_accumulate(self):
        b = Budget(BudgetLimits(max_usd=1.0), clock=FakeClock())
        b.charge(tokens=100, usd=0.25)
        b.charge(tokens=50, usd=0.25)
        assert (b.tokens, b.usd) == (150, pytest.approx(0.5))

    def test_check_refuses_a_call_once_the_dollar_cap_is_spent(self):
        b = Budget(BudgetLimits(max_usd=0.10), clock=FakeClock())
        b.charge(tokens=10, usd=0.10)
        with pytest.raises(BudgetExceeded, match=r"cost"):
            b.check(estimated_tokens=1)

    def test_check_refuses_a_call_that_would_break_the_token_cap(self):
        b = Budget(BudgetLimits(max_tokens=1_000), clock=FakeClock())
        b.charge(tokens=900, usd=0)
        with pytest.raises(BudgetExceeded, match=r"token"):
            b.check(estimated_tokens=200)

    def test_wall_clock_limit(self):
        clock = FakeClock()
        b = Budget(BudgetLimits(max_seconds=60), clock=clock)
        b.check(estimated_tokens=1)
        clock.t += 61
        with pytest.raises(BudgetExceeded, match=r"time"):
            b.check(estimated_tokens=1)

    def test_snapshot_reports_remaining_headroom(self):
        b = Budget(BudgetLimits(max_usd=2.0, max_tokens=1000, max_seconds=100), clock=FakeClock())
        b.charge(tokens=250, usd=0.5)
        snap = b.snapshot()
        assert snap["usd_spent"] == pytest.approx(0.5)
        assert snap["usd_fraction"] == pytest.approx(0.25)
        assert snap["tokens_fraction"] == pytest.approx(0.25)

    def test_budget_exceeded_carries_which_limit_tripped(self):
        b = Budget(BudgetLimits(max_usd=0.01), clock=FakeClock())
        b.charge(tokens=1, usd=0.02)
        with pytest.raises(BudgetExceeded) as exc:
            b.check(estimated_tokens=1)
        assert exc.value.limit == "cost"


# -- redaction --------------------------------------------------------------------------


class TestRedactor:
    def test_known_secret_values_are_masked(self):
        r = Redactor(secrets=["hunter2-very-secret"])
        assert "hunter2" not in r.text("password is hunter2-very-secret ok")

    def test_secret_values_are_collected_from_sensitive_env_names(self):
        env = {"ANTHROPIC_API_KEY": FAKE_KEY, "HOME": "/Users/x",
               "PG_PASSWORD": "pa55word-long", "SOME_TOKEN": "tok-123456789"}
        r = Redactor.from_env(env)
        out = r.text(f"k={FAKE_KEY} p=pa55word-long t=tok-123456789 h=/Users/x")
        assert "sk-ant" not in out and "pa55word" not in out and "tok-123" not in out
        assert "/Users/x" in out  # non-secret values are left alone

    def test_secret_shaped_strings_are_masked_even_if_unknown(self):
        r = Redactor(secrets=[])
        openai_like = "sk-" + "proj-AAAAAAAAAAAAAAAAAAAA"
        out = r.text(f"leaked {FAKE_PREFIX}ZZZZZZZZZZZZZZZZZZZZ and {openai_like}")
        assert "ZZZZ" not in out and "AAAA" not in out

    def test_database_urls_lose_their_password(self):
        r = Redactor(secrets=[])
        out = r.text("postgresql://etl:s3cr3t@db.local:5432/warehouse")
        assert "s3cr3t" not in out and "db.local" in out

    def test_short_values_are_not_treated_as_secrets(self):
        # Masking "1" or "on" would shred every log line.
        r = Redactor(secrets=["1", "on"])
        assert r.text("run 1 is on") == "run 1 is on"

    def test_nested_structures_are_redacted(self):
        r = Redactor(secrets=["topsecretvalue"])
        out = r.obj({"a": ["x topsecretvalue"], "b": {"c": "topsecretvalue"}, "n": 3})
        assert "topsecretvalue" not in json.dumps(out)
        assert out["n"] == 3


# -- event log --------------------------------------------------------------------------


class TestEventLog:
    def test_events_are_appended_as_jsonl_with_run_id_and_sequence(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl", run_id="run-1", clock=FakeClock())
        log.emit("run_started", request="x")
        log.emit("agent_call", agent="planner", tokens=10)
        lines = (tmp_path / "events.jsonl").read_text().splitlines()
        events = [json.loads(line) for line in lines]
        assert [e["seq"] for e in events] == [1, 2]
        assert all(e["run_id"] == "run-1" for e in events)
        assert events[1]["kind"] == "agent_call" and events[1]["agent"] == "planner"

    def test_events_are_redacted_before_they_touch_disk(self, tmp_path):
        log = EventLog(tmp_path / "e.jsonl", run_id="r", redactor=Redactor(["supersecret99"]))
        log.emit("tool_call", args={"dsn": "x supersecret99"})
        assert "supersecret99" not in (tmp_path / "e.jsonl").read_text()

    def test_read_returns_events_after_a_sequence_number(self, tmp_path):
        log = EventLog(tmp_path / "e.jsonl", run_id="r")
        for i in range(5):
            log.emit("tick", i=i)
        assert [e["i"] for e in EventLog.read(tmp_path / "e.jsonl", after=3)] == [3, 4]

    def test_reopening_a_log_continues_the_sequence(self, tmp_path):
        EventLog(tmp_path / "e.jsonl", run_id="r").emit("a")
        log2 = EventLog(tmp_path / "e.jsonl", run_id="r")
        log2.emit("b")
        assert [e["seq"] for e in EventLog.read(tmp_path / "e.jsonl")] == [1, 2]

    def test_totals_summarise_tokens_and_cost_per_agent(self, tmp_path):
        log = EventLog(tmp_path / "e.jsonl", run_id="r")
        log.emit("agent_call", agent="planner", tokens=100, usd=0.01)
        log.emit("agent_call", agent="coder", tokens=50, usd=0.02)
        log.emit("agent_call", agent="coder", tokens=50, usd=0.02)
        log.emit("tool_call", agent="tester", server="sandbox", tool="run_stage")
        totals = EventLog.totals(EventLog.read(tmp_path / "e.jsonl"))
        assert totals["tokens"] == 200
        assert totals["usd"] == pytest.approx(0.05)
        assert totals["by_agent"]["coder"]["calls"] == 2
        assert totals["tool_calls"] == 1
