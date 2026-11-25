"""Rendering pieces shared by the pages. Every function takes a `RunView` (or plain data)
and draws it; none of them talks to the pipeline directly.

Layout of a run (designed to be read from across a table, on one laptop screen):

    Now banner      who is doing what, in one sentence
    [approval gate  only while a run waits for a person]
    stats strip     stages, retries, calls, tokens, cost of budget, time
    agent lane      the five agents; the one working is filled
    schematic       sources -> stages -> final table
    activity | stage focus (tests passing/failing, attempts, code)
    details         data quality, cost, budget, report
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from app.schematic import render_schematic
from app.theme import AGENT_COLORS, CSS
from app.viewmodel import RunView, agent_label, agent_states, now_line, summary_numbers

STATUS_WORDS = {"running": "Working", "awaiting_approval": "Waiting for your approval",
                "succeeded": "Loaded", "failed": "Failed", "rejected": "Rejected",
                "budget_exceeded": "Stopped at budget", "not started": "Not started"}
MARKS = {"good": "&#10003;", "bad": "&#10007;", "wait": "&#9679;", "neutral": ""}
LANE_NOTES = {"planner": "designs the stages", "tester": "writes tests first",
              "coder": "writes stage code", "debugger": "fixes failing code",
              "validator": "proposes quality rules"}


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def setup_page() -> None:
    html(CSS)


def brand_header(subtitle: str) -> None:
    """One sentence under the shared header band saying what this page is for."""
    html(f'<p class="intro">{escape(subtitle)}</p>')


def status_pill(status: str) -> str:
    cls = status if status in STATUS_WORDS and status != "not started" else "not"
    return f'<span class="pill {cls}">{STATUS_WORDS.get(status, status)}</span>'


# -- the top of a run ----------------------------------------------------------------------


def now_banner(v: RunView, live: bool) -> None:
    agent, text = now_line(v)
    tone = {"succeeded": "done", "awaiting_approval": "wait", "failed": "stopped",
            "budget_exceeded": "stopped", "rejected": "stopped"}.get(v.status, "")
    color = AGENT_COLORS.get(agent, "#6B7180")
    mode = "" if live or v.finished else '<span class="mode">replay</span>'
    html(f'<div class="now {tone}" style="--agent:{color}"><span class="live"></span>'
         f'<span class="who">{agent_label(agent)}</span>'
         f'<span class="what">{escape(text)}</span>{mode}</div>')
    if v.request:
        html(f'<p class="request" title="{escape(v.request)}">{escape(v.request)}</p>')


def stats_strip(v: RunView) -> None:
    n = summary_numbers(v)
    limit = v.budget_limits.get("max_usd")
    cost = f"{n['cost']} of ${limit:.2f}" if limit else n["cost"]
    items = [(n["stages"], "stages passed"), (n["retries"], "retries"),
             (n["calls"], "model calls"), (n["tokens"], "tokens"), (cost, "spent"),
             (n["elapsed"], "elapsed")]
    html('<div class="strip">' + "".join(f"<span><b>{val}</b>{lab}</span>"
                                         for val, lab in items) + "</div>")


def agent_lane(v: RunView) -> None:
    states = agent_states(v)
    cells = []
    for agent, state in states.items():
        word = {"working": "working now", "done": "has worked", "idle": "not yet"}[state]
        calls = v.cost["by_agent"].get(agent, {}).get("calls", 0)
        detail = f"{word}, {calls} call{'s' if calls != 1 else ''}" if calls else word
        cells.append(f'<div class="a {state}" style="--agent:{AGENT_COLORS[agent]}">'
                     f'<div class="n">{agent_label(agent)}</div>'
                     f'<div class="s">{LANE_NOTES[agent]}<br>{detail}</div></div>')
    html('<div class="lane">' + "".join(cells) + "</div>")


def schematic(v: RunView) -> None:
    if not v.stages and not v.sources:
        return
    html(f'<div class="sch-wrap">{render_schematic(v)}</div>')


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
    req = v.approval_request
    if not req or v.load or v.approval:
        return  # nothing to decide (the banner already states any outcome)
    failed = req.get("failed_checks") or []
    existing = req.get("existing_production_rows")
    replace = f"replaces {existing:,} existing rows" if existing else "creates the table"
    checks = (f"{len(failed)} quality check(s) failed - read them below before deciding"
              if failed else "every quality check passed")
    html(f'<div class="gate"><h3>Nothing has been written to {escape(req["table"])} yet</h3>'
         f'<p>The result is in a staging table. Loading it {replace}; {checks}.</p>'
         f'<div class="facts"><div><b>{req["rows"]:,}</b>rows to load</div>'
         f'<div><b>{req.get("quarantine_rows", 0):,}</b>rows set aside, each with a reason</div>'
         f'<div><b>{len(failed)}</b>failed checks</div></div></div>')
    if failed:
        st.warning("Still failing: " + ", ".join(failed))
    if not live or on_decide is None:
        st.caption(replay_note or "Open this run in Runs and replay to decide.")
        return
    with st.form(f"gate-{v.run_id}", border=False):
        c0, c1, c2 = st.columns([2, 2, 2], vertical_alignment="bottom")
        who = c0.text_input("Your name", value=st.session_state.get("approver", ""),
                            placeholder="Recorded with the decision")
        approve = c1.form_submit_button(f"Load {req['rows']:,} rows into {req['table']}",
                                        type="primary", use_container_width=True)
        reject = c2.form_submit_button("Reject and keep production as it is",
                                       use_container_width=True)
        note = st.text_input("Note (optional)")
    with st.expander("Preview the first rows"):
        if req.get("preview"):
            st.dataframe(pd.DataFrame(req["preview"]), hide_index=True,
                         use_container_width=True)
    if approve or reject:
        st.session_state["approver"] = who
        on_decide(bool(approve), who or "booth visitor", note)


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
                     hide_index=True, use_container_width=True)


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
        hide_index=True, use_container_width=True)


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
                 hide_index=True, use_container_width=True)


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
    now_banner(v, live)
    approval_gate(v, live, on_decide, replay_note)
    stats_strip(v)
    agent_lane(v)
    schematic(v)
    left, right = st.columns(2, gap="large")
    with left:
        html('<div class="panel-title">What just happened</div>')
        activity_rows(v, limit=7)
        with st.expander(f"Full activity log ({len(v.activity)} entries)"):
            activity_rows(v, limit=500)
    with right:
        html('<div class="panel-title">Stage in focus</div>')
        stage_focus(v)
    if v.error and v.status in ("failed", "budget_exceeded"):
        st.error(v.error)
    html('<div class="panel-title details">Details</div>')
    tabs = st.tabs(["Data quality", "Cost", "Budget", "Report"])
    with tabs[0]:
        quality_panel(v)
    with tabs[1]:
        cost_panel(v)
    with tabs[2]:
        budget_meters(v)
    with tabs[3]:
        report_panel(run_root)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}
