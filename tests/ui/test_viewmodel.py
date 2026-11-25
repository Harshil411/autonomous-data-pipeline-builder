"""The UI's view model: a pure function from the event log to what the screen shows.

Live mode and replay mode share this code - live renders every event so far, replay
renders the first N - so the booth fallback can never drift from the real thing.
"""

import pytest

from adpb.contracts import DebugPatch
from adpb.llm.scripted import ScriptedModel
from adpb.observability.events import EventLog
from adpb.runtime import RunHandle
from app.schematic import render_schematic
from app.viewmodel import agent_states, build_view, now_line
from tests.integration.pipeline_fixtures import (
    CODE_ORDERS,
    CODE_ORDERS_BUGGY,
    CUSTOMERS_CSV,
    ORDERS_CSV,
    happy_script,
    request,
)


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ui")
    (tmp / "customers.csv").write_text(CUSTOMERS_CSV)
    (tmp / "orders.csv").write_text(ORDERS_CSV)
    script = happy_script()
    script["coder"][1] = CODE_ORDERS_BUGGY
    script["debugger"] = [DebugPatch(diagnosis="dataset is raw.orders", code=CODE_ORDERS.code)]
    h = RunHandle.create(request(), [tmp / "customers.csv", tmp / "orders.csv"],
                         home=tmp / "var", model=ScriptedModel(script),
                         approval_secret="ui-secret-xxxxxx", sandbox="local", embeddings="hash")
    h.run()
    h.decide(approved=True, by="Booth visitor")
    return EventLog.read(h.ws.events_path)


def test_full_log_shows_a_finished_run(recorded):
    v = build_view(recorded)
    assert v.status == "succeeded" and v.finished
    assert list(v.stages) == ["clean_customers", "clean_orders", "customer_orders"]
    assert all(s.status == "passed" for s in v.stages.values())
    assert v.approval["by"] == "Booth visitor"
    assert v.load["rows"] == 4


def test_stage_details_include_attempts_tests_and_code_versions(recorded):
    s = build_view(recorded).stages["clean_orders"]
    assert s.attempt == 2
    assert [c["author"] for c in s.code_versions] == ["coder", "debugger"]
    assert s.code_versions[-1]["note"] == "dataset is raw.orders"
    assert s.tests and s.test_runs[0]["ok"] is False


def test_activity_is_plain_language_and_attributed_to_agents(recorded):
    activity = build_view(recorded).activity
    texts = [a.text for a in activity]
    assert any(t.startswith("Proposed a 3-stage plan") for t in texts)
    assert any("Patched clean_orders" in t for t in texts)
    assert {a.agent for a in activity} >= {"planner", "tester", "coder", "debugger",
                                           "validator", "you"}


def test_cost_and_calls_are_totalled_per_agent(recorded):
    v = build_view(recorded)
    assert v.cost["calls"] == sum(a["calls"] for a in v.cost["by_agent"].values())
    assert v.cost["by_agent"]["debugger"]["calls"] == 1
    assert v.cost["tool_calls"] > 0


def test_a_prefix_of_the_log_is_a_consistent_earlier_moment(recorded):
    # Replay: cut the log right after the plan was created.
    cut = next(i for i, e in enumerate(recorded) if e["kind"] == "plan_created") + 1
    v = build_view(recorded[:cut])
    assert v.status == "running" and not v.finished
    assert all(s.status == "pending" for s in v.stages.values())
    assert v.current_stage is None


def test_waiting_for_approval_is_its_own_state(recorded):
    cut = next(i for i, e in enumerate(recorded) if e["kind"] == "approval_requested") + 1
    v = build_view(recorded[:cut])
    assert v.status == "awaiting_approval"
    assert v.approval_request["rows"] == 4 and v.approval is None


def test_empty_log_is_an_empty_view():
    v = build_view([])
    assert v.status == "not started" and v.stages == {} and v.activity == []


def test_schematic_draws_every_node_with_a_text_status(recorded):
    svg = render_schematic(build_view(recorded))
    assert svg.startswith("<svg")
    for name in ("raw.customers", "clean_orders", "customer_orders"):
        assert name in svg
    assert "passed" in svg  # status is written, not only coloured
    assert "aria-label" in svg


def test_schematic_marks_the_running_stage(recorded):
    cut = next(i for i, e in enumerate(recorded)
               if e["kind"] == "stage_status" and e["stage"] == "clean_orders") + 1
    svg = render_schematic(build_view(recorded[:cut]))
    assert 'class="node running"' in svg



# -- the "now" line and the agent lane (what a visitor reads from across the table) -------


def _cut_after(events, pred):
    return events[: next(i for i, e in enumerate(events) if pred(e)) + 1]


def test_now_line_names_the_agent_and_what_it_is_doing(recorded):
    v = build_view(_cut_after(recorded, lambda e: e["kind"] == "stage_status"
                              and e["stage"] == "clean_orders" and e["status"] == "debugging"))
    agent, text = now_line(v)
    assert agent == "debugger"
    assert text == "The Debugger is fixing clean_orders (attempt 1 of 3)"


def test_now_line_before_any_plan_says_the_planner_is_working(recorded):
    v = build_view(_cut_after(recorded, lambda e: e["kind"] == "source_loaded"))
    assert now_line(v)[0] in ("orchestrator", "planner")
    v = build_view(_cut_after(recorded, lambda e: e["kind"] == "run_started"))
    assert "Reading" in now_line(v)[1] or "Planner" in now_line(v)[1]


def test_now_line_at_the_gate_and_at_the_end(recorded):
    gate = build_view(_cut_after(recorded, lambda e: e["kind"] == "approval_requested"))
    assert now_line(gate) == ("you", "Waiting for you to approve loading 4 rows into "
                                     "customer_orders")
    done = build_view(recorded)
    assert now_line(done) == ("orchestrator", "Done: 4 rows loaded into customer_orders")


def test_agent_lane_marks_the_working_agent_and_who_has_finished(recorded):
    v = build_view(_cut_after(recorded, lambda e: e["kind"] == "stage_status"
                              and e["stage"] == "clean_orders" and e["status"] == "debugging"))
    states = agent_states(v)
    assert states["debugger"] == "working"
    assert states["planner"] == "done" and states["validator"] == "idle"
    assert list(states) == ["planner", "tester", "coder", "debugger", "validator"]


def test_elapsed_time_excludes_the_wait_for_a_human_decision():
    def ev(seq, ts, kind, **kw):
        return {"run_id": "r", "seq": seq, "ts": ts, "kind": kind, **kw}
    events = [
        ev(1, 0.0, "run_started", request="r" * 12, target_table="t"),
        ev(2, 10.0, "approval_requested", table="t", rows=1, fingerprint="f",
           existing_production_rows=None, quarantine_rows=0, failed_checks=[], preview=[]),
        ev(3, 300.0, "approval_decision", approved=True, by="x", note=""),
        ev(4, 302.0, "run_finished", status="succeeded", error=None),
    ]
    assert build_view(events).elapsed == pytest.approx(12.0)
    assert build_view(events[:2]).elapsed == pytest.approx(10.0)
