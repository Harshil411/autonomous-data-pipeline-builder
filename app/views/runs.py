"""Runs and replay: every recorded run, one click away. Replay a run's recording, decide a
paused run, resume a failed one, or re-run a finished one with no model (requirement 18)."""

from __future__ import annotations

import json
import time
from datetime import datetime

import streamlit as st

from adpb.config import load_settings
from adpb.observability.events import EventLog
from app.components import STATUS_WORDS, brand_header, run_dashboard
from app.state import manager
from app.viewmodel import build_view

settings = load_settings()
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
    selected = st.session_state.get("selected_run")
    if selected not in {r["run"] for r in runs}:
        selected = runs[0]["run"]
    with st.container(key="runlist", height=720, border=False):
        for r in runs:
            word = STATUS_WORDS.get(r["status"], r["status"])
            label = f"**{word}**, {r['when']}  \n{r['detail'] or r['request'][:48]}"
            if st.button(label, key=f"pick-{r['run']}", use_container_width=True,
                         type="primary" if r["run"] == selected else "secondary"):
                st.session_state["selected_run"] = r["run"]
                st.session_state.pop("replaying", None)
                st.rerun()
    return selected


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
        manager().decide(run_id, approved, by, note)
        st.rerun()

    run_dashboard(view, root, live=True, on_decide=decide)


def actions(run_id: str, view) -> None:
    root = settings.runs_dir / run_id
    c1, c2, c3, c4 = st.columns([1.2, 1.3, 1.4, 1.4], vertical_alignment="bottom")
    replaying = st.session_state.get("replaying") == run_id
    if c1.button("Stop replay" if replaying else "Replay this run", use_container_width=True):
        if replaying:
            st.session_state.pop("replaying")
        else:
            st.session_state[f"replay-pos-{run_id}"] = 1
            st.session_state["replaying"] = run_id
        st.rerun()
    st.session_state["replay-speed"] = c2.segmented_control(
        "Replay speed", [1, 2, 4, 8], default=st.session_state.get("replay-speed", 4),
        format_func=lambda x: f"{x}x", key="speed") or 4
    if c3.button("Resume from the failed stage", use_container_width=True,
                 disabled=view.status not in ("failed", "budget_exceeded")):
        manager().resume(run_id)
        st.rerun()
    if c4.button("Re-run with no model", use_container_width=True,
                 disabled=not (root / "pipeline" / "manifest.json").exists(),
                 help="Rebuilds every stage from the sources with the code committed to git "
                      "and checks the result is identical."):
        from adpb.orchestrator.rerun import rerun
        from adpb.runtime import RunHandle
        with st.spinner("Rebuilding every stage from the sources with the committed code"):
            started = time.monotonic()
            result = rerun(RunHandle.open(run_id))
        if result["match"]:
            st.success(f"Reproduced exactly in {time.monotonic() - started:.1f}s with 0 model "
                       f"calls (fingerprint {result['fingerprint'][:12]}).")
        else:
            st.error(f"The re-run differs: got {result['fingerprint']}, expected "
                     f"{result['expected']}.")


def main() -> None:
    brand_header("Every run is recorded event by event. Pick one to replay it, decide it, "
                 "or re-run it with no model to show it is reproducible.")
    runs = list_runs()
    if not runs:
        st.info("No runs yet. Start one on the Build a pipeline page.")
        return
    left, right = st.columns([1.05, 3.2], gap="large")
    with left:
        run_id = run_picker(runs)
    with right:
        root = settings.runs_dir / run_id
        events = EventLog.read(root / "events.jsonl")
        view = build_view(events)
        actions(run_id, view)
        if st.session_state.get("replaying") == run_id and events:
            replay(run_id, events)
        elif view.status in ("running", "awaiting_approval") or manager().busy(run_id):
            live(run_id)
        else:
            run_dashboard(view, root, live=False)


main()
