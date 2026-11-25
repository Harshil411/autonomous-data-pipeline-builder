"""The live graph and the headline above it: what the redesigned run screen draws.

Both are pure functions of the `RunView`, so they are tested on prefixes of one recorded
run - the same way the replay screen uses them.
"""

from app.graph import render_graph
from app.viewmodel import build_view, hero, journey_step, proofs


def _cut_after(events, pred):
    return events[: next(i for i, e in enumerate(events) if pred(e)) + 1]


def _debugging(recorded):
    return build_view(_cut_after(recorded, lambda e: e["kind"] == "stage_status"
                                 and e["stage"] == "clean_orders" and e["status"] == "debugging"))


def _at_gate(recorded):
    return build_view(_cut_after(recorded, lambda e: e["kind"] == "approval_requested"))


# -- the graph ---------------------------------------------------------------------------------


def test_graph_draws_every_node_with_a_text_status(recorded):
    svg = render_graph(build_view(recorded))
    assert svg.startswith("<svg") and "aria-label" in svg
    for name in ("raw.customers", "clean_orders", "customer_orders", "Your approval",
                 "Database", "Orchestrator"):
        assert name in svg
    assert "Passed" in svg  # status is written, not only coloured


def test_graph_lights_the_stage_and_the_agent_being_worked_on(recorded):
    svg = render_graph(_debugging(recorded))
    assert svg.count('class="node stage live"') == 1
    assert svg.count('class="agent live"') == 1
    assert "Being fixed" in svg
    # two animated beams leave the Orchestrator: one to the stage, one to the agent
    assert svg.count('class="beam"') == 2


def test_graph_shows_all_five_agents_in_pipeline_order(recorded):
    svg = render_graph(_debugging(recorded))
    names = ["Planner", "Tester", "Stage Coder", "Debugger", "Validator"]
    assert [svg.index(f">{n}<") for n in names] == sorted(svg.index(f">{n}<") for n in names)
    assert svg.count('class="agent idle"') >= 1  # the Validator has not worked yet


def test_gate_stays_locked_while_agents_work(recorded):
    svg = render_graph(_debugging(recorded))
    assert 'class="node gate locked"' in svg
    assert 'class="node db empty"' in svg and "Nothing loaded" in svg


def test_gate_asks_for_the_person_when_everything_is_staged(recorded):
    svg = render_graph(_at_gate(recorded))
    assert 'class="node gate needed"' in svg
    assert "Nothing loaded" in svg and 'class="beam"' not in svg


def test_gate_opens_and_the_database_lights_up_after_approval(recorded):
    svg = render_graph(build_view(recorded))
    assert 'class="node gate open"' in svg
    assert 'class="node db loaded"' in svg and "4 rows loaded" in svg
    assert 'class="edge flow"' in svg  # the path through the gate is animated


def test_graph_of_an_empty_run_is_empty():
    assert render_graph(build_view([])) == ""


# -- the headline --------------------------------------------------------------------------------


def test_hero_while_working_names_the_agent_and_its_limit(recorded):
    h = hero(_debugging(recorded))
    assert h["tone"] == "live" and h["pill"] == "Working now"
    assert h["headline"] == "The Debugger is fixing clean_orders"
    assert "never the test" in h["sub"] and "Try 1 of 3" in h["sub"]


def test_hero_at_the_gate_says_nothing_is_loaded(recorded):
    h = hero(_at_gate(recorded))
    assert h["tone"] == "wait" and h["pill"] == "Waiting for you"
    assert h["headline"] == "4 rows are ready. Nothing is loaded yet."


def test_hero_after_approval_names_who_approved(recorded):
    h = hero(build_view(recorded))
    assert h["tone"] == "done" and h["pill"] == "Approved by Booth visitor"
    assert h["headline"] == "4 rows are in customer_orders"


def test_hero_of_a_rejected_run_says_nothing_was_written(recorded):
    events = _cut_after(recorded, lambda e: e["kind"] == "approval_requested")
    last = events[-1]
    events = [*events,
              {"run_id": last["run_id"], "seq": last["seq"] + 1, "ts": last["ts"] + 1,
               "kind": "approval_decision", "approved": False, "by": "Visitor", "note": ""},
              {"run_id": last["run_id"], "seq": last["seq"] + 2, "ts": last["ts"] + 2,
               "kind": "run_finished", "status": "rejected", "error": None}]
    v = build_view(events)
    h = hero(v)
    assert h["tone"] == "stopped" and "Nothing was written" in h["headline"]
    assert 'class="node gate rejected"' in render_graph(v)


# -- the journey and the proof shown at the gate ---------------------------------------------


def test_journey_step_follows_the_run(recorded):
    assert journey_step(build_view([])) == 1
    assert journey_step(_debugging(recorded)) == 2
    assert journey_step(_at_gate(recorded)) == 3
    assert journey_step(build_view(recorded)) == 3


def test_proofs_count_tests_checks_and_rows_set_aside(recorded):
    p = proofs(_at_gate(recorded))
    assert p["tests_total"] > 0 and p["tests_passed"] == p["tests_total"]
    assert p["checks_total"] > 0 and p["checks_passed"] <= p["checks_total"]
    assert p["set_aside"] == _at_gate(recorded).approval_request.get("quarantine_rows", 0)
