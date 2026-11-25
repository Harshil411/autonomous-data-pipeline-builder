"""Build a pipeline: pick an example or describe your own data, then watch the agents
build it live."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import streamlit as st

from adpb.config import REPO_ROOT, load_settings
from adpb.contracts import BudgetLimits, DataRequest, SourceKind, SourceSpec
from adpb.observability.events import EventLog
from adpb.requests_io import load_request_file
from app.components import brand_header, run_dashboard
from app.state import manager
from app.viewmodel import build_view

REQUESTS = REPO_ROOT / "benchmarks" / "requests"
PRESETS = {
    "shop_join": ("Web-shop orders", "Clean two CSV exports and join every order to its "
                                     "customer"),
    "shop_monthly_revenue": ("Monthly revenue", "Paid revenue per region per month"),
    "sensors_daily": ("Sensor readings", "Nested JSON to daily temperatures per site"),
    "crm_contacts": ("CRM contacts", "A CSV plus a SQLite table, deduplicated by email"),
}
CUSTOM = "custom"
KINDS = {".csv": SourceKind.CSV, ".json": SourceKind.JSON, ".jsonl": SourceKind.JSON,
         ".db": SourceKind.SQLITE, ".sqlite": SourceKind.SQLITE}


def _source_name(filename: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", Path(filename).stem.lower()).strip("_") or "source"
    return name if name[0].isalpha() else f"s_{name}"


def preset_cards() -> str:
    chosen = st.session_state.get("preset", "shop_join")
    options = [*PRESETS.items(), (CUSTOM, ("Your own files", "Upload CSV, JSON or SQLite"))]
    with st.container(key="presets"):
        cols = st.columns(len(options))
        for col, (key, (title, blurb)) in zip(cols, options, strict=True):
            if col.button(f"**{title}**  \n{blurb}", key=f"preset-{key}",
                          type="primary" if key == chosen else "secondary",
                          use_container_width=True):
                st.session_state["preset"] = key
                st.rerun()
    return chosen


def new_run_form() -> None:
    brand_header("Describe the data you need. Five agents plan, test, code and fix the "
                 "pipeline in a locked-down sandbox; nothing reaches the database until you "
                 "approve it.")
    preset = preset_cards()
    demo_req, demo_files = (load_request_file(REQUESTS / f"{preset}.yaml") if preset != CUSTOM
                            else (None, []))
    with st.form(f"new-run-{preset}"):
        request = st.text_area("What should the pipeline do?", height=130,
                               value=demo_req.request if demo_req else "",
                               placeholder="Load these CSVs, clean them, join them and write "
                                           "the result to the database.")
        c1, c2 = st.columns([3, 1])
        expected = c1.text_input("What does a correct result look like?",
                                 value=demo_req.expected_result if demo_req else "")
        table = c2.text_input("Table to load", value=demo_req.target_table if demo_req
                              else "pipeline_output")
        rules = st.text_area("Your quality rules (one per line, optional)", height=70,
                             value="\n".join(demo_req.quality_rules) if demo_req else "")
        uploads, sqlite_table = None, ""
        if demo_req:
            st.caption("Sources: " + ", ".join(
                f"{s.name} ({s.kind}, {f.stat().st_size:,} bytes)"
                for s, f in zip(demo_req.sources, demo_files, strict=True)))
        else:
            uploads = st.file_uploader("Source files", accept_multiple_files=True,
                                       type=["csv", "json", "jsonl", "db", "sqlite"])
            sqlite_table = st.text_input("Table to read from SQLite files", value="")
        c3, c4 = st.columns([3, 1], vertical_alignment="bottom")
        budget = c3.slider("Spending limit for this run (US dollars)", 0.10, 2.00,
                           value=float(demo_req.budget.max_usd) if demo_req else 0.50,
                           step=0.05)
        go = c4.form_submit_button("Build the pipeline", type="primary",
                                   use_container_width=True)
    if not go:
        return
    try:
        if demo_req:
            sources, files = list(demo_req.sources), demo_files
        else:
            if not uploads:
                st.error("Add at least one source file.")
                return
            folder = Path(tempfile.mkdtemp(prefix="adpb-upload-", dir=load_settings().home))
            sources, files = [], []
            for up in uploads:
                path = folder / up.name
                path.write_bytes(up.getvalue())
                kind = KINDS[Path(up.name).suffix.lower()]
                sources.append(SourceSpec(name=_source_name(up.name), kind=kind, path=up.name,
                                          table=(sqlite_table or None)
                                          if kind is SourceKind.SQLITE else None))
                files.append(path)
        req = DataRequest(request=request, expected_result=expected, target_table=table,
                          sources=sources,
                          quality_rules=[r.strip() for r in rules.splitlines() if r.strip()],
                          budget=BudgetLimits(max_usd=budget))
    except Exception as exc:  # validation errors read as instructions, not tracebacks
        st.error(f"That request cannot run yet: {exc}")
        return
    st.session_state["run_id"] = manager().start(req, files)
    st.rerun()


@st.fragment(run_every=1.0)
def live_view(run_id: str) -> None:
    root = load_settings().runs_dir / run_id
    view = build_view(EventLog.read(root / "events.jsonl"))
    mgr = manager()

    def decide(approved: bool, by: str, note: str) -> None:
        mgr.decide(run_id, approved, by, note)
        st.rerun()

    if run_id in mgr.errors:
        st.error("The run stopped unexpectedly:\n\n" + mgr.errors[run_id])
    run_dashboard(view, root, live=True, on_decide=decide)


def main() -> None:
    run_id = st.session_state.get("run_id")
    if not run_id:
        new_run_form()
        return
    _, right = st.columns([6, 1])
    if right.button("New request", use_container_width=True):
        st.session_state.pop("run_id")
        st.rerun()
    live_view(run_id)


main()
