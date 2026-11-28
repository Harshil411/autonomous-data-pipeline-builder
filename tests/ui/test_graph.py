"""The live graph and the headline above it: what the redesigned run screen draws.

Both are pure functions of the `RunView`, so they are tested on prefixes of one recorded
run - the same way the replay screen uses them.
"""

from app.graph import render_graph
from app.viewmodel import build_view, hero, journey_step, plain_check, proofs


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
    for name in ("customers.csv", "clean_orders", "customer_orders", "Your approval",
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


def test_graph_is_drawn_from_the_first_second_before_any_source_is_read(recorded):
    # The run screen used to be a headline over an empty page until the first source was
    # profiled. Now the whole route is there at once, with the unknown parts held open.
    svg = render_graph(build_view(recorded[:1]))     # only "run_started" so far
    assert svg.startswith("<svg")
    # the request already names its files, so each source has its box from the start
    assert ">customers.csv<" in svg and ">orders.csv<" in svg
    assert svg.count('class="node source pending"') == 2 and svg.count("Being read") == 2
    assert ">Stages<" in svg and "Planned next" in svg
    assert 'class="node gate locked"' in svg and "Nothing loaded" in svg
    assert svg.count('<g class="agent ') == 5 and 'class="agent live"' not in svg
    assert svg.count("waiting its turn") == 5        # no agent has been called yet


def test_a_source_box_fills_in_where_it_stood_and_the_picture_keeps_its_size(recorded):
    import re
    first_loaded = next(i for i, e in enumerate(recorded) if e["kind"] == "source_loaded")
    last_loaded = max(i for i, e in enumerate(recorded) if e["kind"] == "source_loaded")
    before = render_graph(build_view(recorded[:1]))
    half = render_graph(build_view(recorded[: first_loaded + 1]))
    after = render_graph(build_view(recorded[: last_loaded + 1]))
    assert half.count('class="node source pending"') == 1 and half.count("Being read") == 1
    assert "Being read" not in after and "Being planned" in after

    def box(svg):
        return re.search(r'viewBox="([^"]+)"', svg).group(1)

    def places(svg):  # where each source box sits
        return re.findall(r'<g class="node source[^"]*" transform="([^"]+)"', svg)
    assert box(before) == box(half) == box(after)
    assert places(before) == places(half) == places(after)


def test_the_planner_is_not_said_to_work_while_a_source_is_still_being_read(recorded):
    first_loaded = next(i for i, e in enumerate(recorded) if e["kind"] == "source_loaded")
    half = build_view(recorded[: first_loaded + 1])
    assert hero(half)["headline"] == "Reading and profiling the sources"
    assert 'class="agent live"' not in render_graph(half)


def test_graph_of_a_run_with_no_events_says_it_has_not_started():
    svg = render_graph(build_view([]))               # no request, so no file names either
    assert ">Sources<" in svg and "Not read yet" in svg and "Being read" not in svg


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


def test_hero_at_the_gate_does_not_hide_a_failed_quality_check(recorded):
    events = _cut_after(recorded, lambda e: e["kind"] == "approval_requested")
    events[-1] = {**events[-1], "failed_checks": ["clean_orders.customer_id.foreign_key"]}
    h = hero(build_view(events))
    assert h["headline"] == "4 rows are staged, but 1 quality check failed"
    assert "Read the failed check" in h["sub"]


# -- the first seconds of a run, before the Planner has proposed any stage -------------------


def _before_the_plan(recorded):
    last_source = max(i for i, e in enumerate(recorded) if e["kind"] == "source_loaded")
    return build_view(recorded[: last_source + 1])


def test_agent_cards_keep_room_for_their_text_before_the_plan_exists(recorded):
    # With only the sources known the picture used to shrink, and "waiting its turn" ran
    # out of its card into the next one.
    import re
    svg = render_graph(_before_the_plan(recorded))
    cards = re.findall(r'<g class="agent [a-z]+"[^>]*>.*?<rect class="box" width="(\d+)"', svg)
    assert len(cards) == 5 and min(int(w) for w in cards) >= 176
    # cards must not overlap: each starts after the previous one ends
    starts = [int(x) for x in re.findall(r'<g class="agent [a-z]+" transform="translate\((\d+),',
                                         svg)]
    assert all(b - a >= int(cards[0]) for a, b in zip(starts, starts[1:], strict=False))


def test_graph_shows_where_the_stages_will_go_while_the_planner_works(recorded):
    svg = render_graph(_before_the_plan(recorded))
    assert "Being planned" in svg                    # a placeholder, not an empty gap
    assert 'class="node gate locked"' in svg
    assert svg.count('class="agent live"') == 1      # the Planner


# -- plain words: nothing a first-time visitor has to decode ---------------------------------


def test_sources_are_named_by_their_file_not_by_an_internal_dataset_name(recorded):
    svg = render_graph(build_view(recorded))
    assert "customers.csv" in svg and "orders.csv" in svg
    assert "raw.customers" not in svg


def test_agent_cards_say_how_often_an_agent_worked_in_plain_words(recorded):
    svg = render_graph(_debugging(recorded))
    assert "worked once" in svg
    assert "model call" not in svg  # the technical figures live under Details


def test_a_failed_check_is_said_in_plain_words():
    assert plain_check("clean_orders.customer_id.foreign_key") == (
        "customer_id in clean_orders points to a row that does not exist")
    assert plain_check("clean_orders.status.allowed_values") == (
        "status in clean_orders has a value that is not allowed")
    assert plain_check("customer_orders.*.row_count") == (
        "customer_orders has an unexpected number of rows")
    # an unknown kind still reads as a sentence, and nothing is lost
    assert plain_check("t.amount.some_new_rule") == "some new rule check on amount in t"
    assert plain_check("not-a-rule-id") == "not-a-rule-id"


def test_an_agent_that_was_never_needed_is_not_left_waiting_when_the_run_is_over(recorded):
    # A run in which the Debugger never had to step in: once the run has finished, its card
    # should not still say it is waiting for a turn that will never come.
    events = [e for e in recorded if e.get("agent") != "debugger"]
    svg = render_graph(build_view(events))
    assert "not needed" in svg and "waiting its turn" not in svg
    # while the run is still going, a resting agent is waiting
    assert "waiting its turn" in render_graph(_debugging(recorded))


# -- runs that did not go to plan: long names, and runs that stopped early --------------------


def _ev(seq, kind, **kw):
    return {"run_id": "run-x", "seq": seq, "ts": 1000.0 + seq, "kind": kind, **kw}


def _stage(sid, inputs):
    return {"id": sid, "kind": "clean", "inputs": inputs, "language": "python",
            "description": "d", "output": {"columns": []}}


def _run(stages, statuses, tail=(), source=("orders", "orders.csv")):
    name, path = source
    events = [_ev(1, "run_started", request="A request", target_table="t", budget={}, models={},
                  sources=[{"name": name, "kind": "csv", "path": path, "url": None}]),
              _ev(2, "source_loaded", source=name, dataset=f"raw.{name}", rows=10, columns=["a"])]
    if stages:
        events += [_ev(3, "agent_call", agent="planner", tokens=1, usd=0.0),
                   _ev(4, "plan_created", plan={"stages": stages, "rationale": ""})]
    for sid, status, extra in statuses:
        events.append(_ev(len(events) + 1, "stage_status", stage=sid, status=status, **extra))
    for kind, extra in tail:
        events.append(_ev(len(events) + 1, kind, **extra))
    return build_view(events)


def test_long_names_are_cut_with_an_ellipsis_not_at_the_edge_of_the_box():
    long_stage = "normalise_customer_relationship_records"
    v = _run([_stage(long_stage, ["raw.crm"])], [(long_stage, "coding", {})],
             source=("crm", "exports/customer_relationship_export_2026_q4_final.csv"))
    svg = render_graph(v)
    assert long_stage not in svg and "normalise_customer_r…" in svg
    assert "customer_relationship_export_2026_q4_final.csv" not in svg and "…" in svg


def test_a_run_that_stopped_shows_no_stage_or_agent_as_still_working():
    stopped = [("budget_exceeded", {"error": "limit reached"}),
               ("run_finished", {"status": "budget_exceeded", "error": "limit reached"})]
    svg = render_graph(_run([_stage("clean_orders", ["raw.orders"])],
                            [("clean_orders", "coding", {})], stopped))
    assert 'class="node stage live"' not in svg and 'class="agent live"' not in svg
    assert 'class="beam"' not in svg
    assert "Stopped here" in svg and "Being coded" not in svg
    assert "did not run" in svg            # the agents that never got a turn
    assert ">Orchestrator<" in svg and "stopped the run" in svg


def test_a_run_that_failed_before_planning_does_not_say_it_is_still_planning():
    failed = [("run_finished", {"status": "failed", "error": "prepare: OutOfBoundsDatetime: x"})]
    svg = render_graph(_run([], [], failed))
    assert "Being planned" not in svg and "Never planned" in svg


def test_hero_of_a_failed_run_gives_the_reason_in_plain_words():
    failed = [("run_finished", {"status": "failed", "error":
               "validate: ModelUnavailable: BadRequestError: Error code: 400 - {'type': 'error'}"})]
    h = hero(_run([_stage("clean_orders", ["raw.orders"])],
                  [("clean_orders", "passed", {"attempt": 1, "rows_out": 5})], failed))
    assert h["tone"] == "stopped" and h["headline"] == "Stopped without loading anything"
    assert h["sub"] == ("It stopped while checking data quality. The technical reason is "
                        "under Details.")
    # a stage that ran out of tries is named
    stuck = [("run_finished", {"status": "failed", "error": "stage clean_orders: TestFailure"})]
    h = hero(_run([_stage("clean_orders", ["raw.orders"])],
                  [("clean_orders", "failed", {"attempt": 3, "error": "TestFailure"})], stuck))
    assert h["sub"] == ("clean_orders still failed its tests after 3 tries. The technical "
                        "reason is under Details.")
