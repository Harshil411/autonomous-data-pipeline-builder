"""The whole agentic loop, end to end, with a scripted model (requirements 2-25).

Real components everywhere except the LLM and the container: real MCP servers through the
gateway (the Git server as a real subprocess), the real harness via `LocalSandbox`, real
SQLite, real ChromaDB (hash embeddings), real LangGraph with a SQLite checkpointer and a
human-approval interrupt.
"""

import json
import sqlite3
import subprocess

import pytest

from adpb.contracts import DebugPatch, PlanDraft
from adpb.llm.scripted import ScriptedModel
from adpb.observability.events import EventLog
from adpb.runtime import RunHandle
from tests.integration.pipeline_fixtures import (
    CLEAN_ORDERS,
    CODE_CUSTOMERS,
    CODE_JOIN,
    CODE_JOIN_BUGGY,
    CODE_ORDERS,
    CODE_ORDERS_BUGGY,
    CODE_ORDERS_NAIVE,
    CUSTOMERS_CSV,
    JOIN,
    NON_NEGATIVE,
    ORDERS_CSV,
    PASSING_RULES,
    PLAN,
    TESTS_CUSTOMERS,
    TESTS_JOIN,
    TESTS_ORDERS,
    happy_script,
    request,
)

SECRET = "approval-secret-for-tests"


@pytest.fixture
def env(tmp_path):
    src = tmp_path / "input"
    src.mkdir()
    (src / "customers.csv").write_text(CUSTOMERS_CSV)
    (src / "orders.csv").write_text(ORDERS_CSV)
    return {"home": tmp_path / "var", "sources": src}


def start(env, script, req=None, **kw) -> RunHandle:
    return RunHandle.create(home=env["home"], request=req or request(),
                            source_files=[env["sources"] / "customers.csv",
                                          env["sources"] / "orders.csv"],
                            model=ScriptedModel(script), approval_secret=SECRET,
                            sandbox="local", embeddings="hash", **kw)


def tables(handle) -> dict[str, int]:
    con = sqlite3.connect(handle.settings.warehouse_path)
    names = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    counts = {n: con.execute(f'select count(*) from "{n}"').fetchone()[0] for n in names}
    con.close()
    return counts


def events(handle, kind=None):
    evs = EventLog.read(handle.ws.events_path)
    return [e for e in evs if kind is None or e["kind"] == kind]


def git_log(handle) -> list[str]:
    out = subprocess.run(["git", "log", "--format=%s"], cwd=handle.ws.pipeline_dir,
                         capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


# -- the happy path ---------------------------------------------------------------------


def test_happy_path_pauses_for_approval_then_loads(env):
    h = start(env, happy_script())
    state = h.run()
    assert state["status"] == "awaiting_approval"
    assert "customer_orders" not in tables(h)  # flyer: won't write to your DB without your OK
    assert tables(h)["staging__customer_orders"] == 4

    state = h.decide(approved=True, by="pytest")
    assert state["status"] == "succeeded"
    assert tables(h)["customer_orders"] == 4
    con = sqlite3.connect(h.settings.warehouse_path)
    reasons = [r[0] for r in con.execute('select _reason from "quarantine__customer_orders"')]
    con.close()
    assert any("x9" in r for r in reasons)            # bad customer id
    assert any("oops" in r for r in reasons)          # bad amount
    assert any("negative" in r for r in reasons)      # rejected by stage code

    report = json.loads((h.ws.root / "report.json").read_text())
    assert report["status"] == "succeeded"
    assert {s["id"]: s["retries"] for s in report["stages"]} == {
        "clean_customers": 0, "clean_orders": 0, "customer_orders": 0}
    assert report["rows"]["input"] == {"raw.customers": 4, "raw.orders": 7}
    assert report["rows"]["output"] == 4
    assert report["quality"]["failed"] == 0
    assert "usd" in report["cost"]


def test_every_role_is_logged_and_every_passing_stage_is_a_commit(env):
    h = start(env, happy_script())
    h.run()
    h.decide(approved=True, by="pytest")
    roles = {e["agent"] for e in events(h, "agent_call")}
    assert roles == {"planner", "tester", "coder", "validator"}  # no debugging was needed
    assert any(e["server"] == "warehouse" and e["tool"] == "promote"
               for e in events(h, "tool_call"))
    log = git_log(h)
    assert any("plan" in m for m in log)
    for stage in ("clean_customers", "clean_orders", "customer_orders"):
        assert any(f"stage {stage} passed" in m for m in log)
    assert (h.ws.pipeline_dir / "stages" / "customer_orders.py").exists()
    assert (h.ws.pipeline_dir / "manifest.json").exists()


def test_tester_never_sees_code_and_coder_never_sees_the_whole_plan(env):
    h = start(env, happy_script())
    h.run()
    calls = h.model.calls
    assert all("def run" not in c["prompt_text"] for c in calls if c["role"] == "tester")
    coder_prompts = [c["prompt_text"] for c in calls if c["role"] == "coder"]
    assert "Inner join" not in coder_prompts[0]  # stage 1's coder never sees stage 3


# -- debugging and escalation ---------------------------------------------------------------


def test_failing_stage_is_debugged_until_it_passes_and_the_fix_is_remembered(env):
    script = happy_script()
    script["coder"] = [CODE_CUSTOMERS, CODE_ORDERS_BUGGY, CODE_JOIN]
    script["debugger"] = [
        DebugPatch(diagnosis="first guess: still the wrong key", code=CODE_ORDERS_BUGGY.code),
        DebugPatch(diagnosis="dataset is raw.orders, not raw.order", code=CODE_ORDERS.code),
    ]
    h = start(env, script)
    state = h.run()
    assert state["status"] == "awaiting_approval"
    assert state["stages"]["clean_orders"]["attempts"] == 3
    debugger_prompts = [c["prompt_text"] for c in h.model.calls if c["role"] == "debugger"]
    assert "first guess" in debugger_prompts[1]  # second attempt is told what was tried
    stored = h.memory_search_fixes("KeyError raw.order")
    assert any("raw.orders" in r["fix"] for r in stored)


def test_exhausted_retries_escalate_to_a_replan_that_keeps_passing_stages(env):
    script = happy_script()
    revised = CLEAN_ORDERS.model_copy(update={"description": "Read raw.orders (exact name)."})
    script["planner"] = [PLAN, PlanDraft(stages=[PLAN.stages[0], revised, JOIN],
                                         final_dataset="customer_orders",
                                         quality_rules=PLAN.quality_rules)]
    script["coder"] = [CODE_CUSTOMERS, CODE_ORDERS_BUGGY, CODE_ORDERS, CODE_JOIN]
    script["debugger"] = [DebugPatch(diagnosis=f"try {i}", code=CODE_ORDERS_BUGGY.code)
                          for i in (1, 2)]
    script["tester"] = [TESTS_CUSTOMERS, TESTS_ORDERS, TESTS_ORDERS, TESTS_JOIN]
    h = start(env, script)
    state = h.run()
    assert state["status"] == "awaiting_approval"
    assert state["replans_used"] == 1
    coded = [c["prompt_text"].split("`")[1] for c in h.model.calls if c["role"] == "coder"]
    assert coded.count("clean_customers") == 1  # not redone after the replan
    assert [e for e in events(h, "replanned")]


def test_no_replans_left_ends_the_run_cleanly_without_touching_the_database(env):
    script = happy_script()
    script["coder"] = [CODE_CUSTOMERS, CODE_ORDERS_BUGGY]
    script["debugger"] = [DebugPatch(diagnosis=f"try {i}", code=CODE_ORDERS_BUGGY.code)
                          for i in (1, 2)]
    h = start(env, script, req=request(max_replans=0))
    state = h.run()
    assert state["status"] == "failed"
    assert "clean_orders" in state["error"]
    assert not any(t.startswith(("staging__", "customer_orders")) for t in tables(h))
    assert json.loads((h.ws.root / "report.json").read_text())["status"] == "failed"
    assert events(h, "run_finished")[-1]["status"] == "failed"


def test_quality_failure_is_routed_back_to_the_stage_that_made_the_data(env):
    script = happy_script()
    script["coder"] = [CODE_CUSTOMERS, CODE_ORDERS_NAIVE, CODE_JOIN, CODE_ORDERS]
    script["validator"] = [NON_NEGATIVE]
    h = start(env, script)
    state = h.run()
    assert state["status"] == "awaiting_approval", state.get("error")
    assert state["quality_round"] == 2
    recode_prompt = [c["prompt_text"] for c in h.model.calls if c["role"] == "coder"][-1]
    assert "clean_orders.amount.range" in recode_prompt  # the failing check is fed back
    rounds = events(h, "quality_result")
    assert rounds[0]["failed"] >= 1 and rounds[-1]["failed"] == 0


# -- limits and the human ----------------------------------------------------------------------


def test_budget_exhaustion_ends_the_run_cleanly(env):
    h = start(env, happy_script(), req=request(max_tokens=2_500))
    state = h.run()
    assert state["status"] == "budget_exceeded"
    assert "token" in state["error"]
    assert not any(t.startswith("customer_orders") for t in tables(h))
    assert (h.ws.root / "report.json").exists()


def test_rejected_approval_leaves_production_untouched(env):
    h = start(env, happy_script())
    h.run()
    state = h.decide(approved=False, by="pytest", note="numbers look off")
    assert state["status"] == "rejected"
    assert "customer_orders" not in tables(h)
    assert events(h, "approval_decision")[-1]["note"] == "numbers look off"


def test_resume_reruns_only_the_failed_stage_and_its_dependants(env):
    script = happy_script()
    script["coder"] = [CODE_CUSTOMERS, CODE_ORDERS, CODE_JOIN_BUGGY]
    script["debugger"] = [DebugPatch(diagnosis=f"try {i}", code=CODE_JOIN_BUGGY.code)
                          for i in (1, 2)]
    h = start(env, script, req=request(max_replans=0))
    assert h.run()["status"] == "failed"
    jobs_before = [j["stage_id"] for j in h.sandbox.jobs]

    h.model = ScriptedModel({"coder": [CODE_JOIN], "validator": [PASSING_RULES]})
    state = h.resume()
    assert state["status"] == "awaiting_approval", state.get("error")
    new_jobs = [j["stage_id"] for j in h.sandbox.jobs][len(jobs_before):]
    assert set(new_jobs) == {"customer_orders"}
    assert [c["role"] for c in h.model.calls] == ["coder", "validator"]


# -- what never reaches a prompt ------------------------------------------------------------


def test_no_full_dataset_and_no_secret_ever_reaches_a_prompt(env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-THIS-MUST-NEVER-LEAK-0123456789")
    rows = "\n".join(f"{i},Name{i}" for i in range(1, 3000))
    (env["sources"] / "customers.csv").write_text(
        f"customer_id,name\n{rows}\n2999999,ZZ_MARKER_DEEP_ROW\n")
    h = start(env, happy_script())
    h.run()
    for call in h.model.calls:
        assert "ZZ_MARKER_DEEP_ROW" not in call["prompt_text"]
        assert "THIS-MUST-NEVER-LEAK" not in call["prompt_text"] + call["system"]
    assert "THIS-MUST-NEVER-LEAK" not in h.ws.events_path.read_text()
