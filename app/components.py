"""Rendering pieces shared by the pages. Every function takes a `RunView` (or plain data)
and draws it; none of them talks to the pipeline directly.

Layout of a run (one headline, one picture, detail on request):

    journey         Describe / Build / Approve, with the current step lit
    hero            one status tag, one headline, one supporting line, time and cost
    [approval gate  only while a run waits for a person: the proof, the buttons, a preview]
    live graph      sources -> stages -> your approval -> database, with the Orchestrator
                    and the five agents underneath (app/graph.py)
    details         tabs: activity, stage detail, data quality, cost, budget, report
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from app.graph import render_graph
from app.theme import AGENT_COLORS, CSS
from app.viewmodel import RunView, agent_label, hero, journey_step, proofs

STATUS_WORDS = {"running": "Working", "awaiting_approval": "Waiting for your approval",
                "succeeded": "Loaded", "failed": "Failed", "rejected": "Rejected",
                "budget_exceeded": "Stopped at budget", "not started": "Not started"}
MARKS = {"good": "&#10003;", "bad": "&#10007;", "wait": "&#9679;", "neutral": ""}
STEPS = ("Describe", "Build", "Approve")
TICK = ('<svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" '
        'stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="{path}"/></svg>')


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def setup_page() -> None:
    html(CSS)


def page_title(title: str, subtitle: str = "") -> None:
    """The heading of a page that is not a run: one sentence, one supporting line."""
    sub = f"<p>{escape(subtitle)}</p>" if subtitle else ""
    html(f'<div class="page-title"><h1>{escape(title)}</h1>{sub}</div>')


def journey(step: int, done: bool = False) -> None:
    """The three steps of the visit, with the current one lit. `done` turns the last step
    green once the data is loaded."""
    items = "".join(
        f'<li class="{"on" if i == step else "past" if i < step else ""}">{i} '
        f'{"Approved" if done and i == 3 else name}</li>'
        for i, name in enumerate(STEPS, start=1))
    html(f'<nav class="steps{" done" if done else ""}" aria-label="Steps"><ol>{items}</ol></nav>')


# -- the top of a run ----------------------------------------------------------------------


def _clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def run_hero(v: RunView, live: bool) -> None:
    """Status tag, headline, supporting line, and the two numbers people ask about."""
    h = hero(v)
    limit = v.budget_limits.get("max_usd")
    cost = f"<b>${v.cost['usd']:.2f}</b>" + (f" of ${limit:.2f}" if limit else "")
    replay = "" if live or v.finished else '<span class="tag replay">Replay</span>'
    html(f'<div class="hero {h["tone"]}"><div class="main">'
         f'<span class="tag"><i></i>{escape(h["pill"])}</span>{replay}'
         f'<h1>{escape(h["headline"])}</h1></div>'
         f'<div class="side"><p>{escape(h["sub"])}</p>'
         f'<div class="meta"><span><b>{_clock(v.elapsed)}</b> of work</span>'
         f'<span>{cost}</span></div></div></div>')


def graph(v: RunView) -> None:
    svg = render_graph(v)
    if svg:
        html(f'<div class="graph-wrap">{svg}</div>')


# -- activity --------------------------------------------------------------------------------


def activity_rows(v: RunView, limit: int) -> None:
    if not v.activity:
        st.caption("Nothing has happened yet.")
        return
    rows = []
    for a in list(reversed(v.activity))[:limit]:
        color = AGENT_COLORS.get(a.agent, "#6B7180")
        why = f'<div class="why">{escape(a.detail[:300])}</div>' if a.detail else ""
        rows.append(f'<div class="act {a.tone}" style="border-left-color:{color}">'
                    f'<div class="who" style="color:{color}">{agent_label(a.agent)}</div>'
                    f'<div class="what"><span class="mark">{MARKS[a.tone]}</span>'
                    f'{escape(a.text)}</div>{why}</div>')
    html("".join(rows))


# -- the human gate --------------------------------------------------------------------------


def approval_gate(v: RunView, live: bool, on_decide=None, replay_note: str = "") -> None:
    """Shown only while a run waits for a person. Left: the evidence and the two buttons.
    Right: the first rows of what would be loaded. The decision is passed to `on_decide`;
    nothing here writes to the database."""
    req = v.approval_request
    if not req or v.load or v.approval:
        return  # nothing to decide (the headline already states any outcome)
    p = proofs(v)
    failed = req.get("failed_checks") or []
    good = TICK.format(color="#7FD1AE", path="M5 12.5l4.5 4.5L19 7")
    warn = TICK.format(color="#FFB37A",
                       path="M12 7v6M12 17h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0")
    existing = req.get("existing_production_rows")
    replace = (f"Loading replaces {existing:,} existing rows." if existing
               else f"Loading creates the table {req['table']}.")
    checks = (f'<li class="warn">{warn}{len(failed)} of {p["checks_total"]} quality checks '
              f'failed</li>'
              if failed else
              f'<li>{good}{p["checks_passed"]} of {p["checks_total"]} quality checks passed</li>')
    with st.container(key="gate"):
        left, right = st.columns([1, 1.25], gap="large", vertical_alignment="center")
        with left:
            html(f'<ul class="proofs"><li>{good}{p["tests_passed"]} of {p["tests_total"]} tests '
                 f'passed in the sandbox</li>{checks}'
                 f'<li class="warn">{warn}{p["set_aside"]:,} rows set aside, each with a reason'
                 f'</li></ul>')
            if failed:
                st.warning("Still failing: " + ", ".join(failed))
            if not live or on_decide is None:
                st.caption(replay_note or "Open this run in Past runs to decide.")
            else:
                with st.form(f"gate-{v.run_id}", border=False):
                    who = st.text_input("Your name",
                                        value=st.session_state.get("approver", ""),
                                        placeholder="Recorded with the decision")
                    note = st.text_input("Note (optional)")
                    c1, c2 = st.columns([1.6, 1])
                    approve = c1.form_submit_button(f"Approve and load {req['rows']:,} rows",
                                                    type="primary", width="stretch")
                    reject = c2.form_submit_button("Reject", width="stretch")
                html(f'<p class="fine">{replace} Your approval is tied to these exact rows. '
                     f'No agent can load data on its own.</p>')
                if approve or reject:
                    st.session_state["approver"] = who
                    on_decide(bool(approve), who or "booth visitor", note)
        with right:
            preview = req.get("preview") or []
            html(f'<div class="peek"><b>{escape(req["table"])}</b><span>first {len(preview)} of '
                 f'{req["rows"]:,} rows, staged and not loaded</span></div>')
            if preview:
                st.dataframe(pd.DataFrame(preview), hide_index=True, width="stretch")


# -- one stage, in focus ----------------------------------------------------------------------


def _attempt_chips(s) -> str:
    chips = []
    for r in s.test_runs:
        where = "sample" if r["scope"] == "sample" else "all data"
        if r["ok"]:
            chips.append(f'<span class="chip ok">attempt {r["attempt"]}, {where}: passed</span>')
        else:
            failed = [t["name"] for t in r.get("tests", []) if not t["passed"]]
            why = f"{len(failed)} test(s) failed" if failed else (r.get("error_type") or "error")
            chips.append(f'<span class="chip bad">attempt {r["attempt"]}, {where}: '
                         f'{escape(why)}</span>')
    fixes = sum(1 for c in s.code_versions if c["author"] == "debugger")
    if fixes:
        chips.append(f'<span class="chip fix">patched by the Debugger {fixes}x</span>')
    return f'<div class="attempts">{"".join(chips)}</div>' if chips else ""


def stage_focus(v: RunView) -> None:
    if not v.stages:
        st.caption("Stages appear here once the Planner has proposed them.")
        return
    ids = list(v.stages)
    default = v.current_stage if v.current_stage in ids else ids[-1]
    sid = st.segmented_control("Stage", ids, default=default, key=f"focus-{v.run_id}",
                               label_visibility="collapsed") or default
    s = v.stages[sid]
    word = {"passed": "passed", "failed": "failed", "pending": "waiting"}.get(s.status, s.status)
    html(f'<div class="focus-head"><b>{escape(s.id)}</b><span class="small">{s.kind} stage, '
         f'{s.language}, reads {escape(", ".join(s.inputs))}</span>'
         f'<span class="state {s.status}">{word}</span></div>'
         f'<p class="desc">{escape(s.description)}</p>{_attempt_chips(s)}')
    tests_tab, code_tab, schema_tab = st.tabs(["Tests", "Code", "Output contract"])
    with tests_tab:
        latest = {t["name"]: t for t in (s.test_runs[-1]["tests"] if s.test_runs else [])}
        if not s.tests:
            st.caption("The Tester writes these before any code exists.")
        rows = []
        for t in s.tests:
            res = latest.get(t["name"])
            mark = ('<span class="st ok">&#10003; passed</span>' if res and res["passed"] else
                    '<span class="st bad">&#10007; failed</span>' if res else
                    '<span class="st">not run</span>')
            rows.append(f'<div class="test-row">{mark}<div><b>{escape(t["name"])}</b>'
                        f'<div class="small">{escape(t.get("description") or "")}</div>'
                        f'<code>{escape(t["check"])}</code></div></div>')
        html("".join(rows))
    with code_tab:
        if not s.code_versions:
            st.caption("No code yet.")
        else:
            labels = [f"v{c['version']} {agent_label(c['author'])}" for c in s.code_versions]
            pick = labels[-1]
            if len(labels) > 1:
                pick = st.segmented_control("Version", labels, default=labels[-1],
                                            key=f"ver-{v.run_id}-{sid}") or labels[-1]
            chosen = s.code_versions[labels.index(pick)]
            if chosen.get("note") and chosen["author"] == "debugger":
                st.caption(f"Debugger's diagnosis: {chosen['note']}")
            st.code(chosen.get("code") or "", language="sql" if s.language == "sql"
                    else "python", height=340)
    with schema_tab:
        st.dataframe(pd.DataFrame([{"column": c["name"], "type": c["dtype"],
                                    "required": "yes" if not c.get("nullable", True) else "no"}
                                   for c in s.output["columns"]]),
                     hide_index=True, width="stretch")


# -- details below the fold --------------------------------------------------------------------


def quality_panel(v: RunView) -> None:
    if not v.quality:
        st.caption("Quality checks run after every stage has passed.")
        return
    rnd = v.quality[-1]
    origin = {f"{r['dataset']}.{r.get('column') or '+'.join(r.get('columns') or []) or '*'}."
              f"{r['kind']}": r.get("origin", "") for r in rnd.get("rules", [])}
    st.markdown(f"Round {rnd['round']}: **{rnd['total'] - rnd['failed']} of {rnd['total']}** "
                f"checks passed. Code runs the checks; no model decides pass or fail. Only "
                f"rules from the output contract or your request can send a stage back.")
    st.dataframe(pd.DataFrame([{
        "result": "passed" if c["passed"] else "FAILED", "check": c["rule_id"],
        "from": {"schema": "output contract", "request": "your request",
                 "derived": "Validator (advisory)"}.get(origin.get(c["rule_id"], ""), ""),
        "observed": c["observed"], "failing rows": c["failing_rows"]} for c in rnd["checks"]]),
        hide_index=True, width="stretch")


def cost_panel(v: RunView) -> None:
    by = v.cost["by_agent"]
    if not by:
        st.caption("No model calls yet.")
        return
    key = "usd" if any(a["usd"] for a in by.values()) else "tokens"
    top = max(a[key] for a in by.values()) or 1
    rows = []
    for agent, a in sorted(by.items(), key=lambda kv: -kv[1][key]):
        val = f"${a['usd']:.4f}" if key == "usd" else f"{a['tokens']:,} tok"
        tip = f"{agent_label(agent)}: {a['calls']} calls, {a['tokens']:,} tokens, ${a['usd']:.4f}"
        rows.append(f'<div class="row" title="{escape(tip)}"><div class="label">'
                    f'{agent_label(agent)}</div><div class="track"><div class="bar" '
                    f'style="width:{a[key] / top * 100:.1f}%"></div></div>'
                    f'<div class="val">{val}</div></div>')
    st.markdown(f"**{'Cost' if key == 'usd' else 'Tokens'} by agent** (hover a bar for detail). "
                f"{v.cost['tool_calls']} tool calls went through MCP servers; the Orchestrator "
                f"makes no model calls.")
    html('<div class="bars">' + "".join(rows) + "</div>")
    st.dataframe(pd.DataFrame([{"agent": agent_label(k), "model": v.models.get(k, ""),
                                "calls": a["calls"], "tokens": a["tokens"],
                                "cost ($)": round(a["usd"], 5)} for k, a in by.items()]),
                 hide_index=True, width="stretch")


def budget_meters(v: RunView) -> None:
    b = v.budget
    if not b:
        st.caption("No model calls yet.")
        return
    rows = [("Cost", b["usd_fraction"], f"${b['usd_spent']:.3f} of ${b['usd_limit']:.2f}"),
            ("Tokens", b["tokens_fraction"], f"{b['tokens_spent']:,} of {b['tokens_limit']:,}"),
            ("Time", b["seconds_fraction"], f"{b['seconds_elapsed']:.0f}s of "
                                            f"{b['seconds_limit']:.0f}s")]
    out = ['<div class="meters">']
    for label, frac, text in rows:
        hot = " hot" if frac > 0.85 else ""
        out.append(f'<div class="meter"><div class="top"><span>{label}</span><span>{text}'
                   f'</span></div><div class="track"><div class="fill{hot}" style="width:'
                   f'{max(1.5, frac * 100):.1f}%"></div></div></div>')
    html("".join(out) + "</div>")
    st.caption("Checked in code before every model call; the run stops cleanly at a limit.")


def report_panel(run_root: Path) -> None:
    md, js = run_root / "report.md", run_root / "report.json"
    if not md.exists():
        st.caption("The report is written when the run finishes.")
        return
    c1, c2, _ = st.columns([1, 1, 3])
    c1.download_button("Report (Markdown)", md.read_text(), file_name=md.name)
    c2.download_button("Report (JSON)", js.read_text(), file_name=js.name)
    st.markdown(md.read_text())


# -- the whole run ----------------------------------------------------------------------------


def run_dashboard(v: RunView, run_root: Path, live: bool, on_decide=None,
                  replay_note: str = "") -> None:
    journey(journey_step(v), done=bool(v.load))
    run_hero(v, live)
    approval_gate(v, live, on_decide, replay_note)
    graph(v)
    if v.error and v.status in ("failed", "budget_exceeded"):
        st.error(v.error)
    tabs = st.tabs(["Activity", "Stage detail", "Data quality", "Cost", "Budget", "Report"])
    with tabs[0]:
        activity_rows(v, limit=8)
        with st.expander(f"Full activity log ({len(v.activity)} entries)"):
            activity_rows(v, limit=500)
    with tabs[1]:
        stage_focus(v)
    with tabs[2]:
        quality_panel(v)
    with tabs[3]:
        cost_panel(v)
    with tabs[4]:
        budget_meters(v)
    with tabs[5]:
        report_panel(run_root)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}
