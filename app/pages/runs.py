"""Past runs: inspect any run, replay its recording, resume a failed one, or re-run it
from scratch without the LLM (requirement 18)."""

from __future__ import annotations

import json
import time

import pandas as pd
import streamlit as st

from adpb.config import load_settings
from adpb.observability.events import EventLog
from app.components import brand_header, run_body
from app.state import manager
from app.viewmodel import build_view

settings = load_settings()


def list_runs() -> list[dict]:
    rows = []
    if not settings.runs_dir.exists():
        return rows
    for run in sorted(settings.runs_dir.glob("run-*"), reverse=True)[:60]:
        state = json.loads((run / "state.json").read_text()) if (run / "state.json").exists() \
            else {}
        req = json.loads((run / "request.json").read_text()) if (run / "request.json").exists() \
            else {}
        cost = (state.get("spent") or {}).get("usd_spent")
        rows.append({"run": run.name, "status": state.get("status", "-"),
                     "request": req.get("request", "")[:90],
                     "cost ($)": round(cost, 4) if cost is not None else None})
    return rows


@st.fragment(run_every=0.5)
def replay(run_id: str, events: list[dict]) -> None:
    key = f"replay-pos-{run_id}"
    pos = st.session_state.get(key, 1)
    speed = st.session_state.get("replay-speed", 6)
    if pos < len(events):
        st.session_state[key] = min(len(events), pos + speed)
    view = build_view(events[: st.session_state[key]])
    st.progress(st.session_state[key] / len(events),
                text=f"Replaying a recorded run: event {st.session_state[key]} of {len(events)}")
    run_body(view, settings.runs_dir / run_id, live=False)


def main() -> None:
    brand_header("Every run is recorded event by event. Replay one, or re-run it without "
                 "the model to prove it is reproducible.")
    runs = list_runs()
    if not runs:
        st.info("No runs yet. Build a pipeline first.")
        return
    st.dataframe(pd.DataFrame(runs), hide_index=True, use_container_width=True, height=240)
    run_id = st.selectbox("Open a run", [r["run"] for r in runs])
    root = settings.runs_dir / run_id
    events = EventLog.read(root / "events.jsonl")
    view = build_view(events)
    mgr = manager()

    c1, c2, c3, c4 = st.columns(4)
    if c1.button("Replay from the start", use_container_width=True, disabled=not events):
        st.session_state[f"replay-pos-{run_id}"] = 1
        st.session_state["replaying"] = run_id
    st.session_state["replay-speed"] = c2.select_slider("Replay speed", [1, 3, 6, 12, 30],
                                                        value=6)
    if c3.button("Resume from the failed stage", use_container_width=True,
                 disabled=view.status not in ("failed", "budget_exceeded")):
        mgr.resume(run_id)
        st.session_state["run_id"] = run_id
        st.switch_page("pages/build.py")
    if c4.button("Re-run without the model", use_container_width=True,
                 disabled=not (root / "pipeline" / "manifest.json").exists()):
        from adpb.orchestrator.rerun import rerun
        from adpb.runtime import RunHandle
        with st.spinner("Rebuilding every stage from the sources with the committed code"):
            started = time.monotonic()
            result = rerun(RunHandle.open(run_id))
        if result["match"]:
            st.success(f"Reproduced exactly in {time.monotonic() - started:.1f}s with 0 model "
                       f"calls. Fingerprint {result['fingerprint'][:16]}...")
        else:
            st.error(f"The re-run differs from the original. Got {result['fingerprint']}, "
                     f"expected {result['expected']}.")
    if view.status == "awaiting_approval" and st.button("Open the approval gate"):
        st.session_state["run_id"] = run_id
        st.switch_page("pages/build.py")

    if st.session_state.get("replaying") == run_id:
        replay(run_id, events)
    else:
        run_body(view, root, live=False)


main()
