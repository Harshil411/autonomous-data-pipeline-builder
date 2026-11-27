"""Past runs: every recorded run, one dropdown away. Replay a run's recording, decide a
paused run, resume a failed one, or rebuild a finished one with no model (requirement 18)."""

from __future__ import annotations

import json
import time
from datetime import datetime

import streamlit as st

from adpb.observability.events import EventLog
from app.components import STATUS_WORDS, page_title, run_dashboard
from app.state import manager
from app.viewmodel import build_view
from app.visitor import settings as visitor_settings

settings = visitor_settings()  # this visitor's own runs when the server keeps them private
REPLAY_NOTE = "Replay: the decision appears when the recording reaches it."


def list_runs() -> list[dict]:
    rows = []
    if not settings.runs_dir.exists():
        return rows
    for run in sorted(settings.runs_dir.glob("run-*"), reverse=True)[:60]:
        state = json.loads((run / "state.json").read_text()) if (run / "state.json").exists() \
            else {}
        req = json.loads((run / "request.json").read_text()) if (run / "request.json").exists() \
            else {}
        try:
            when = datetime.strptime(run.name[4:19], "%Y%m%d-%H%M%S").strftime("%b %d, %H:%M")
        except ValueError:
            when = ""
        staging = state.get("staging") or {}
        spent = (state.get("spent") or {}).get("usd_spent")
        detail = ", ".join(x for x in (
            req.get("target_table"),
            f"{staging['rows']:,} rows" if staging.get("rows") is not None else None,
            f"${spent:.2f}" if spent else None) if x)
        rows.append({"run": run.name, "status": state.get("status", "running"), "when": when,
                     "request": req.get("request", ""), "detail": detail})
    return rows


def run_picker(runs: list[dict]) -> str:
    """One dropdown, newest run first. A list down the side would take a quarter of the
    screen away from the graph, which has to be readable from across the table."""
    by_id = {r["run"]: r for r in runs}
    if st.session_state.get("selected_run") not in by_id:
        # a fresh session (first visit, or a reload): take the run named in the address bar
        asked = st.query_params.get("run")
        st.session_state["selected_run"] = asked if asked in by_id else runs[0]["run"]

    def label(run_id: str) -> str:
        r = by_id[run_id]
        word = STATUS_WORDS.get(r["status"], r["status"])
        return ", ".join(x for x in (word, r["when"], r["detail"] or r["request"][:48]) if x)

    run_id = st.selectbox("Run", list(by_id), format_func=label, key="selected_run",
                          on_change=lambda: st.session_state.pop("replaying", None))
    st.query_params["run"] = run_id  # so a reload comes back to this run
    return run_id


@st.fragment(run_every=0.6)
def replay(run_id: str, events: list[dict]) -> None:
    key = f"replay-pos-{run_id}"
    pos = st.session_state.get(key, 1)
    if pos < len(events):
        st.session_state[key] = min(len(events), pos + st.session_state.get("replay-speed", 4))
    st.progress(st.session_state[key] / len(events),
                text=f"Replaying the recording: event {st.session_state[key]} of {len(events)}")
    run_dashboard(build_view(events[: st.session_state[key]]), settings.runs_dir / run_id,
                  live=False, replay_note=REPLAY_NOTE)


@st.fragment(run_every=1.5)
def live(run_id: str) -> None:
    """A run that is working or waiting: refresh it and allow the decision here."""
    root = settings.runs_dir / run_id
    view = build_view(EventLog.read(root / "events.jsonl"))

    def decide(approved: bool, by: str, note: str) -> None:
        manager().decide(run_id, approved, by, note, home=settings.home)
        st.rerun()

    run_dashboard(view, root, live=True, on_decide=decide)


def actions(runs: list[dict]) -> tuple[str, list[dict], object]:
    """The control row: which run, replay and its speed, resume, rebuild with no model.
    Returns the chosen run, its events and its view."""
    # Labels are one or two words so the row fits a narrow window; tooltips say the rest.
    c0, c1, c2, c3, c4 = st.columns([2.3, 0.9, 1.9, 0.9, 1.5], vertical_alignment="bottom")
    with c0:
        run_id = run_picker(runs)
    root = settings.runs_dir / run_id
    events = EventLog.read(root / "events.jsonl")
    view = build_view(events)
    replaying = st.session_state.get("replaying") == run_id
    if c1.button("Stop" if replaying else "Replay", width="stretch",
                 help="Play this run's recording from the start: no model calls, no cost."):
        if replaying:
            st.session_state.pop("replaying")
        else:
            st.session_state[f"replay-pos-{run_id}"] = 1
            st.session_state["replaying"] = run_id
        st.rerun()
    st.session_state["replay-speed"] = c2.segmented_control(
        "Speed", [1, 2, 4, 8], default=st.session_state.get("replay-speed", 4),
        format_func=lambda x: f"{x}x", key="speed") or 4
    # Offered only when it can do something: a run that failed or hit its budget.
    if view.status in ("failed", "budget_exceeded") and c3.button(
            "Resume", width="stretch",
            help="Continue from the stage that failed, keeping the stages that passed."):
        manager().resume(run_id, home=settings.home)
        st.rerun()
    if c4.button("Rebuild, no model", width="stretch",
                 disabled=not (root / "pipeline" / "manifest.json").exists(),
                 help="Rebuilds every stage from the sources with the code committed to git "
                      "and checks the result is identical."):
        from adpb.orchestrator.rerun import rerun
        from adpb.runtime import RunHandle
        with st.spinner("Rebuilding every stage from the sources with the committed code"):
            started = time.monotonic()
            result = rerun(RunHandle.open(run_id, home=settings.home))
        if result["match"]:
            st.success(f"Reproduced exactly in {time.monotonic() - started:.1f}s with 0 model "
                       f"calls (fingerprint {result['fingerprint'][:12]}).")
        else:
            st.error(f"The re-run differs: got {result['fingerprint']}, expected "
                     f"{result['expected']}.")
    return run_id, events, view


def main() -> None:
    runs = list_runs()
    if not runs:
        page_title("Every run is recorded, step by step",
                   "Nothing has run yet. Start one on the Build page, then come back here to "
                   "replay it or rebuild it with no model.")
        return
    # No page heading once there is a run to show: the controls explain themselves, and
    # the graph needs the height to fit on one laptop screen.
    run_id, events, view = actions(runs)
    root = settings.runs_dir / run_id
    if st.session_state.get("replaying") == run_id and events:
        replay(run_id, events)
    elif view.status in ("running", "awaiting_approval") or manager().busy(run_id):
        live(run_id)
    else:
        run_dashboard(view, root, live=False)


main()
