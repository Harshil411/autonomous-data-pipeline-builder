"""Agent-level behaviour with a scripted model (Modules 3, 5, 7).

Covers the shared bounded loop (tool calls, repair requests, hard caps, budget checks) and
each role's own contract: the planner's plan is validated, the coder answers for one
stage only, the tester rejects invalid expressions, the debugger can only return code,
and the validator keeps only rules that refer to real datasets and columns.
"""

from pathlib import Path

import pytest

from adpb.agents.base import Agent, AgentContext, AgentFailed
from adpb.agents.coder import StageCoder
from adpb.agents.debugger import Debugger
from adpb.agents.planner import Planner
from adpb.agents.tester import Tester
from adpb.agents.validator import Validator, schema_rules, usable
from adpb.contracts import (
    BudgetLimits,
    ColumnSchema,
    DataRequest,
    DebugPatch,
    DType,
    ExecutionResult,
    PlanDraft,
    QualityRule,
    RuleKind,
    RuleProposals,
    SourceKind,
    SourceSpec,
    StageCode,
    StageSpec,
    StageTest,
    StageTests,
    TableSchema,
    TestOutcome,
)
from adpb.llm.base import ToolCall
from adpb.llm.scripted import ScriptedModel
from adpb.observability.budget import Budget, BudgetExceeded
from adpb.observability.events import EventLog

REQUEST = DataRequest(
    request="Clean the orders file and load it",
    sources=[SourceSpec(name="orders", kind=SourceKind.CSV, path="orders.csv")],
    target_table="orders",
)
PROFILE = {"raw.orders": {"name": "raw.orders", "rows": 3, "sample": [{"id": "1"}],
                          "columns": [{"name": "id", "inferred_dtype": "integer", "null_count": 0,
                                       "null_fraction": 0.0, "distinct": 3,
                                       "examples": ["1"]}]}}
STAGE = StageSpec(id="clean_orders", kind="clean", description="type the id",
                  inputs=["raw.orders"],
                  output=TableSchema(name="clean_orders", primary_key=["id"], columns=[
                      ColumnSchema(name="id", dtype=DType.INTEGER, nullable=False)]))
GOOD_DRAFT = PlanDraft(stages=[STAGE], final_dataset="clean_orders")


def ctx(script, tmp_path, limits=None, gateway=None):
    return AgentContext(model=ScriptedModel(script), budget=Budget(limits or BudgetLimits()),
                        events=EventLog(tmp_path / "events.jsonl", run_id="r"), gateway=gateway)


class TestLoop:
    def test_invalid_answer_gets_a_precise_repair_request(self, tmp_path):
        bad = PlanDraft(stages=[STAGE.model_copy(update={"inputs": ["raw.ghost"]})],
                        final_dataset="clean_orders")
        c = ctx({"planner": [bad, GOOD_DRAFT]}, tmp_path)
        plan = Planner(c).plan(REQUEST, PROFILE)
        assert plan.stages[0].inputs == ["raw.orders"]
        repair_prompt = c.model.calls[1]["prompt_text"]
        assert "unknown input" in repair_prompt and "raw.ghost" in repair_prompt

    def test_gives_up_after_max_repairs(self, tmp_path):
        bad = PlanDraft(stages=[STAGE], final_dataset="nope")
        c = ctx({"planner": [bad, bad, bad]}, tmp_path)
        with pytest.raises(AgentFailed, match="still invalid"):
            Planner(c).plan(REQUEST, PROFILE)

    def test_tool_calls_are_executed_and_results_returned_to_the_model(self, tmp_path):
        class FakeGateway:
            calls = []

            def specs_for(self, role):
                return []

            def call_qualified(self, role, name, args):
                self.calls.append((role, name, args))
                return {"results": []}

        gw = FakeGateway()
        script = {"planner": [[ToolCall(id="t1", name="memory__search_pipelines",
                                        arguments={"query": "orders"})], GOOD_DRAFT]}
        c = ctx(script, tmp_path, gateway=gw)
        Planner(c).plan(REQUEST, PROFILE)
        assert gw.calls == [("planner", "memory__search_pipelines", {"query": "orders"})]
        assert '{"results": []}' in c.model.calls[1]["prompt_text"]

    def test_turn_cap_ends_an_endless_tool_loop(self, tmp_path):
        class Gw:
            def specs_for(self, role):
                return []

            def call_qualified(self, *a):
                return {}

        loop = [[ToolCall(id=f"t{i}", name="memory__search_pipelines", arguments={})]
                for i in range(10)]
        c = ctx({"planner": loop}, tmp_path, gateway=Gw())
        with pytest.raises(AgentFailed, match="no valid answer"):
            Planner(c).plan(REQUEST, PROFILE)
        assert len(c.model.calls) == Agent.max_turns

    def test_budget_is_checked_before_each_call(self, tmp_path):
        c = ctx({"planner": [GOOD_DRAFT]}, tmp_path, limits=BudgetLimits(max_tokens=10))
        with pytest.raises(BudgetExceeded):
            Planner(c).plan(REQUEST, PROFILE)
        assert c.model.calls == []  # refused before spending anything

    def test_every_call_is_logged_with_tokens(self, tmp_path):
        c = ctx({"planner": [GOOD_DRAFT]}, tmp_path)
        Planner(c).plan(REQUEST, PROFILE)
        [event] = [e for e in EventLog.read(tmp_path / "events.jsonl")
                   if e["kind"] == "agent_call"]
        assert event["agent"] == "planner" and event["tokens"] > 0
        assert c.budget.tokens == event["tokens"]


class TestRoles:
    def test_planner_cannot_change_request_or_sources(self, tmp_path):
        plan = Planner(ctx({"planner": [GOOD_DRAFT]}, tmp_path)).plan(REQUEST, PROFILE)
        assert plan.sources == REQUEST.sources and plan.target_table == "orders"

    def test_planner_prompt_wraps_samples_as_untrusted_data(self, tmp_path):
        c = ctx({"planner": [GOOD_DRAFT]}, tmp_path)
        Planner(c).plan(REQUEST, PROFILE)
        prompt = c.model.calls[0]["prompt_text"]
        assert "<data>" in prompt and "never follow instructions" in prompt

    def test_coder_answer_is_pinned_to_its_stage(self, tmp_path):
        code = StageCode(stage_id="something_else", language="sql",
                         code="def run(inputs):\n    return inputs['raw.orders']\n")
        out = StageCoder(ctx({"coder": [code]}, tmp_path)).write(STAGE, PROFILE)
        assert out.stage_id == "clean_orders" and out.language == "python"

    def test_coder_receives_quality_feedback(self, tmp_path):
        code = StageCode(stage_id="clean_orders", code="def run(inputs):\n    return 1\n")
        c = ctx({"coder": [code]}, tmp_path)
        StageCoder(c).write(STAGE, PROFILE, feedback="clean_orders.id.unique: 2 duplicates")
        assert "2 duplicates" in c.model.calls[0]["prompt_text"]

    def test_tester_rejects_syntax_errors_and_never_sees_code(self, tmp_path):
        bad = StageTests(stage_id="x", tests=[StageTest(name="t1", description="",
                                                       check="out['id'] >")])
        good = StageTests(stage_id="x", tests=[StageTest(name="t1", description="",
                                                        check="out['id'].notna().all()")])
        c = ctx({"tester": [bad, good]}, tmp_path)
        tests = Tester(c).write_tests(STAGE, PROFILE)
        assert tests.stage_id == "clean_orders"
        assert "not a valid expression" in c.model.calls[1]["prompt_text"]
        assert "def run" not in c.model.calls[0]["prompt_text"]

    def test_debugger_sees_failure_and_history_but_returns_only_code(self, tmp_path):
        patch = DebugPatch(diagnosis="ids had spaces", code="def run(inputs):\n    return 1\n")
        c = ctx({"debugger": [patch]}, tmp_path)
        result = ExecutionResult(stage_id="clean_orders", ok=False, error_type="TestFailure",
                                 error="1 test failed",
                                 tests=[TestOutcome(name="t1", passed=False, detail="False")])
        tests = StageTests(stage_id="clean_orders", tests=[
            StageTest(name="t1", description="ids ok", check="out['id'].notna().all()")])
        code = StageCode(stage_id="clean_orders", code="def run(inputs):\n    return 0\n")
        fixed, diagnosis = Debugger(c).fix(STAGE, code, result, tests, PROFILE,
                                           history=["attempt 1: stripped quotes"])
        prompt = c.model.calls[0]["prompt_text"]
        assert "TestFailure" in prompt and "attempt 1: stripped quotes" in prompt
        assert "cannot change them" in prompt
        assert fixed.code.endswith("return 1\n") and diagnosis == "ids had spaces"

    def test_validator_keeps_only_rules_about_real_columns(self, tmp_path):
        plan = Planner(ctx({"planner": [GOOD_DRAFT]}, tmp_path)).plan(REQUEST, PROFILE)
        proposals = RuleProposals(rules=[
            QualityRule(kind=RuleKind.RANGE, dataset="clean_orders", column="id", min=0),
            QualityRule(kind=RuleKind.RANGE, dataset="clean_orders", column="ghost", min=0),
            QualityRule(kind=RuleKind.ROW_COUNT, dataset="nowhere", min=1),
        ])
        v = Validator(ctx({"validator": [proposals]}, tmp_path))
        derived = v.propose(plan, {"clean_orders": PROFILE["raw.orders"]}, PROFILE)
        rules = v.all_rules(plan, derived)
        ids = {r.id for r in rules}
        assert "clean_orders.id.range" in ids
        assert "clean_orders.ghost.range" not in ids and "nowhere.*.row_count" not in ids
        assert {r.origin for r in rules} == {"schema", "derived"}

    def test_foreign_keys_must_reference_an_upstream_dataset(self):
        # Found in a live run: a key rule on clean_orders -> clean_customers can never be
        # fixed by clean_orders, which never sees the customers.
        from tests.integration.pipeline_fixtures import PLAN
        from tests.integration.pipeline_fixtures import request as req
        plan = Planner(ctx({"planner": [PLAN]}, Path("."))).plan(req(), {
            "raw.customers": PROFILE["raw.orders"], "raw.orders": PROFILE["raw.orders"]})
        on_join = QualityRule(kind=RuleKind.FOREIGN_KEY, dataset="customer_orders",
                              column="customer_id", ref_dataset="clean_customers",
                              ref_column="customer_id")
        on_sibling = on_join.model_copy(update={"dataset": "clean_orders"})
        assert usable(on_join, plan)
        assert not usable(on_sibling, plan)

    def test_schema_rules_cover_not_null_pk_dtype_and_row_count(self):
        from adpb.contracts import PipelinePlan
        plan = PipelinePlan(request="r" * 10, sources=REQUEST.sources, stages=[STAGE],
                            final_dataset="clean_orders", target_table="orders")
        kinds = {r.kind for r in schema_rules(plan)}
        assert kinds == {RuleKind.ROW_COUNT, RuleKind.DTYPE, RuleKind.NOT_NULL, RuleKind.UNIQUE}
