"""Build a pipeline: pick an example or describe your own data, then watch the agents
build it live."""

from __future__ import annotations

import re
import tempfile
from html import escape
from pathlib import Path

import streamlit as st

from adpb.config import REPO_ROOT, load_settings
from adpb.contracts import BudgetLimits, DataRequest, SourceKind, SourceSpec
from adpb.observability.events import EventLog
from adpb.requests_io import load_request_file
from app.components import html, journey, run_dashboard
from app.prompts import as_bullets, text_to_send
from app.state import manager
from app.viewmodel import build_view

REQUESTS = REPO_ROOT / "benchmarks" / "requests"
# Short titles: six buttons share one row under the request box. The blurb is the tooltip.
PRESETS = {
    "shop_join": ("Shop orders", "Clean two CSV exports and join every order to its "
                                     "customer"),
    "shop_monthly_revenue": ("Revenue", "Paid revenue per region per month"),
    "sensors_daily": ("Sensors", "Nested JSON to daily temperatures per site"),
    "crm_contacts": ("CRM contacts", "A CSV plus a SQLite table, deduplicated by email"),
    # for the instructors in the audience: a roster and a gradebook, with made-up students
    "course_grades": ("Course grades", "A class roster and a gradebook export, to one "
                                       "percentage per enrolled student"),
}
CUSTOM = "custom"
KINDS = {".csv": SourceKind.CSV, ".json": SourceKind.JSON, ".jsonl": SourceKind.JSON,
         ".db": SourceKind.SQLITE, ".sqlite": SourceKind.SQLITE}


def _source_name(filename: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", Path(filename).stem.lower()).strip("_") or "source"
    return name if name[0].isalpha() else f"s_{name}"


def _rows(text: str, per_line: int = 74) -> int:
    """How many lines the text takes in the request box (about 74 characters fit a line),
    so the box is as tall as its text and no taller."""
    return sum(max(1, -(-len(line) // per_line)) for line in text.splitlines() or [""])


def preset_cards() -> str:
    """The examples, as a row of small buttons under the request box."""
    chosen = st.session_state.get("preset", "shop_join")
    options = [*PRESETS.items(), (CUSTOM, ("Your own files", "Upload CSV, JSON or SQLite"))]
    html('<p class="try">Start from an example, or bring your own files</p>')
    with st.container(key="presets"):
        cols = st.columns(len(options))
        for col, (key, (title, blurb)) in zip(cols, options, strict=True):
            if col.button(title, key=f"preset-{key}", help=blurb,
                          type="primary" if key == chosen else "secondary", width="stretch"):
                st.session_state["preset"] = key
                st.rerun()
    return chosen


def new_run_form() -> None:
    """The Describe screen: one sentence, one request box, one button. Everything a first
    visitor does not need (expected result, table name, quality rules, spend limit) sits
    behind "More options" with sensible defaults."""
    journey(1)
    html('<div class="describe"><h1>Describe the data you need. '
         '<span class="dim">Five agents build the pipeline.</span></h1></div>')
    _, mid, _ = st.columns([1, 3.4, 1])
    form_box = mid.container(key="prompt")   # drawn first, filled second:
    with mid:                                # the chosen example decides what the form shows
        preset = preset_cards()
    demo_req, demo_files = (load_request_file(REQUESTS / f"{preset}.yaml") if preset != CUSTOM
                            else (None, []))
    uploads, sqlite_table = None, ""
    if not demo_req:
        # Outside the form on purpose: the page reacts as soon as a file is added, so the
        # one database-only question below appears only when a database was uploaded.
        uploads = form_box.file_uploader("Your source files (CSV, JSON or SQLite)",
                                         accept_multiple_files=True,
                                         type=["csv", "json", "jsonl", "db", "sqlite"])
    has_database = any(KINDS.get(Path(u.name).suffix.lower()) is SourceKind.SQLITE
                       for u in uploads or [])
    with form_box, st.form(f"new-run-{preset}"):
        # An example is shown as a goal line and bullets (same words, easier to read). The
        # box grows with the number of lines so most examples need no scrolling.
        shown = as_bullets(demo_req.request) if demo_req else ""
        request = st.text_area("What should the pipeline do?",
                               height=min(370, max(150, 24 + 27 * _rows(shown))),
                               label_visibility="collapsed", value=shown,
                               placeholder="Load these CSVs, clean them, join them and write "
                                           "the result to the database.")
        if demo_req:
            html('<div class="chips">' + "".join(
                f"<span><b>{escape(s.name)}.{escape(str(s.kind.value))}</b>"
                f"{f.stat().st_size / 1024:,.0f} KB</span>"
                for s, f in zip(demo_req.sources, demo_files, strict=True)) + "</div>")
        elif has_database:
            sqlite_table = st.text_input("Which table should be read from the SQLite file?")
        with st.expander("More options"):
            c1, c2 = st.columns([3, 1])
            expected = c1.text_input("What does a correct result look like?",
                                     value=demo_req.expected_result if demo_req else "")
            table = c2.text_input("Table to load", value=demo_req.target_table if demo_req
                                  else "pipeline_output")
            rules = st.text_area("Your quality rules (one per line, optional)", height=70,
                                 value="\n".join(demo_req.quality_rules) if demo_req else "")
            budget = st.slider("Spending limit for this run (US dollars)", 0.10, 2.00,
                               value=float(demo_req.budget.max_usd) if demo_req else 0.50,
                               step=0.05)
        _, c4 = st.columns([2.2, 1])
        go = c4.form_submit_button("Build the pipeline", type="primary", width="stretch")
    html('<div class="promises">'
         '<div><b>Tests come first.</b> Checks are written before any code exists.</div>'
         '<div><b>Sealed sandbox.</b> Generated code runs with no network.</div>'
         '<div><b>You hold the key.</b> Nothing loads without your approval.</div></div>')
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
        # Unedited, an example goes to the agents in its original wording (see app/prompts.py).
        request = text_to_send(request, demo_req.request if demo_req else None)
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

    def new_request() -> None:
        st.session_state.pop("run_id", None)
        if "run" in st.query_params:
            del st.query_params["run"]
        st.rerun()

    if run_id in mgr.errors:
        st.error("The run stopped unexpectedly:\n\n" + mgr.errors[run_id])
    run_dashboard(view, root, live=True, on_decide=decide, on_new_request=new_request)


RUN_ID = re.compile(r"run-[A-Za-z0-9-]+")  # what a run folder is called; nothing else is read


def current_run() -> str | None:
    """The run this page is showing, if any. It is kept in the session and also in the
    address bar (?run=...), so a page reload - including the one that switches between
    the dark and light looks - comes back to the same run instead of the empty form."""
    run_id = st.session_state.get("run_id") or st.query_params.get("run")
    if run_id and RUN_ID.fullmatch(run_id) and (load_settings().runs_dir / run_id).is_dir():
        st.session_state["run_id"] = run_id
        st.query_params["run"] = run_id
        return run_id
    st.session_state.pop("run_id", None)
    if "run" in st.query_params:
        del st.query_params["run"]
    return None


def main() -> None:
    run_id = current_run()
    if not run_id:
        new_run_form()
        return
    live_view(run_id)


main()
