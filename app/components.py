"""Rendering pieces shared by the pages. Every function takes a `RunView` (or plain data)
and draws it; none of them talks to the pipeline directly."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from app.schematic import render_schematic
from app.theme import AGENT_COLORS, CSS
from app.viewmodel import RunView, agent_label, summary_numbers

STATUS_WORDS = {"running": "Working", "awaiting_approval": "Waiting for your approval",
                "succeeded": "Loaded", "failed": "Failed", "rejected": "Rejected by you",
                "budget_exceeded": "Stopped at budget", "not started": "Not started"}
MARKS = {"good": "&#10003;", "bad": "&#10007;", "wait": "&#9679;", "neutral": ""}


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


def run_heading(v: RunView) -> None:
    html(f'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap">'
         f'{status_pill(v.status)}<span class="small">{escape(v.run_id)}</span></div>')
    if v.request:
        html(f'<p style="font-size:1.12rem;margin:.6rem 0 .2rem;max-width:72ch">'
             f'{escape(v.request)}</p>')
    nums = summary_numbers(v)
    items = [("stages passed", nums["stages"]), ("retries", nums["retries"]),
             ("model calls", nums["calls"]), ("tokens", nums["tokens"]),
             ("cost", nums["cost"]), ("elapsed", nums["elapsed"])]
    html('<div class="nums">' + "".join(f'<div><div class="n">{val}</div><div class="l">{lab}'
                                        f'</div></div>' for lab, val in items) + "</div>")
    if v.error and v.status != "succeeded":
        st.error(v.error)


def budget_meters(v: RunView) -> None:
    b = v.budget
    if not b:
        return
    rows = [("Cost", b["usd_fraction"], f"${b['usd_spent']:.3f} of ${b['usd_limit']:.2f}"),
            ("Tokens", b["tokens_fraction"], f"{b['tokens_spent']:,} of {b['tokens_limit']:,}"),
            ("Time", b["seconds_fraction"], f"{b['seconds_elapsed']:.0f}s of "
                                            f"{b['seconds_limit']:.0f}s")]
    out = ['<div class="meters">']
    for label, frac, text in rows:
        hot = " hot" if frac > 0.85 else ""
        out.append(f'<div class="meter"><div class="top"><span>{label} budget</span>'
                   f'<span>{text}</span></div><div class="track"><div class="fill{hot}" '
                   f'style="width:{max(1.5, frac * 100):.1f}%"></div></div></div>')
    html("".join(out) + "</div>")


def schematic(v: RunView) -> None:
    if not v.stages and not v.sources:
        st.info("The plan appears here as soon as the Planner proposes it.")
        return
    html(f'<div style="background:#FFFFFF;border:1px solid #DADDE3;border-radius:10px;'
         f'padding:.6rem .8rem;margin:.4rem 0 1rem">{render_schematic(v)}</div>')


def activity_feed(v: RunView, limit: int = 40) -> None:
    if not v.activity:
        st.caption("Nothing has happened yet.")
        return
    rows = []
    for a in list(reversed(v.activity))[:limit]:
        color = AGENT_COLORS.get(a.agent, "#6B7180")
        why = f'<div class="why">{escape(a.detail[:400])}</div>' if a.detail else ""
        rows.append(f'<div class="act {a.tone}" style="border-left-color:{color}">'
                    f'<div class="who" style="color:{color}">{agent_label(a.agent)}</div>'
                    f'<div class="what"><span class="mark">{MARKS[a.tone]}</span>'
                    f'{escape(a.text)}</div>{why}</div>')
    html("".join(rows))


def approval_gate(v: RunView, live: bool, on_decide=None, replay_note: str = "") -> None:
    req = v.approval_request
    if not req:
        return
    if v.load:  # decided and loaded: the gate gives way to the outcome
        by = (v.approval or {}).get("by", "someone")
        st.success(f"Loaded {v.load['rows']:,} rows into {v.load['table']}. Approved by {by}.")
        return
    if v.approval and not v.approval["approved"]:
        st.error(f"Rejected by {v.approval['by']}"
                 + (f": {v.approval['note']}" if v.approval.get("note") else "")
                 + f". {req['table']} was not touched.")
        return
    failed = req.get("failed_checks") or []
    existing = req.get("existing_production_rows")
    replace = (f"replaces {existing:,} existing rows" if existing
               else "creates the table")
    html(f'<div class="gate"><h3>Nothing has been written to {escape(req["table"])} yet</h3>'
         f'<div>The data is in a staging table. Loading it {replace}.</div>'
         f'<div class="facts"><div><b>{req["rows"]:,}</b>rows to load</div>'
         f'<div><b>{req.get("quarantine_rows", 0):,}</b>rows set aside with a reason</div>'
         f'<div><b>{len(failed)}</b>failed quality checks</div></div></div>')
    if failed:
        st.warning("These checks still fail after the retries allowed: " + ", ".join(failed))
    if req.get("preview"):
        st.dataframe(pd.DataFrame(req["preview"]), hide_index=True, use_container_width=True)
    if v.approval:
        verb = "Approved" if v.approval["approved"] else "Rejected"
        st.success(f"{verb} by {v.approval['by']}") if v.approval["approved"] else \
            st.error(f"{verb} by {v.approval['by']}: {v.approval.get('note') or 'no note'}")
        return
    if not live or on_decide is None:
        st.caption(replay_note or "Use \u201cOpen the approval gate\u201d above to decide.")
        return
    with st.form(f"gate-{v.run_id}"):
        who = st.text_input("Your name", value=st.session_state.get("approver", ""),
                            placeholder="Recorded with the decision")
        note = st.text_input("Note (optional)")
        c1, c2 = st.columns(2)
        approve = c1.form_submit_button(f"Load {req['rows']:,} rows into {req['table']}",
                                        type="primary", use_container_width=True)
        reject = c2.form_submit_button("Reject and keep production as it is",
                                       use_container_width=True)
    if approve or reject:
        st.session_state["approver"] = who
        on_decide(bool(approve), who or "booth visitor", note)


def stage_inspector(v: RunView) -> None:
    if not v.stages:
        st.caption("No stages yet.")
        return
    ids = list(v.stages)
    default = ids.index(v.current_stage) if v.current_stage in ids else len(ids) - 1
    sid = st.selectbox("Stage", ids, index=default, key=f"inspect-{v.run_id}")
    s = v.stages[sid]
    st.markdown(f"**{s.kind.title()} stage** written in {s.language}, reads "
                f"{', '.join(f'`{i}`' for i in s.inputs)}")
    st.markdown(s.description)
    cols = pd.DataFrame([{"column": c["name"], "type": c["dtype"],
                          "required": "yes" if not c.get("nullable", True) else "no"}
                         for c in s.output["columns"]])
    left, right = st.columns([2, 3])
    with left:
        st.markdown("**Output contract**")
        st.dataframe(cols, hide_index=True, use_container_width=True)
        st.markdown("**Tests** (written by the Tester before any code existed)")
        latest = {t["name"]: t for t in (s.test_runs[-1]["tests"] if s.test_runs else [])}
        for t in s.tests:
            res = latest.get(t["name"])
            mark = ('<span class="check-ok">&#10003; passed</span>' if res and res["passed"]
                    else '<span class="check-bad">&#10007; failed</span>' if res else
                    '<span class="small">not run yet</span>')
            html(f'<div style="margin:.2rem 0"><b>{escape(t["name"])}</b> {mark}<br>'
                 f'<code>{escape(t["check"])}</code></div>')
    with right:
        if s.code_versions:
            labels = [f"v{c['version']} by {agent_label(c['author'])}" for c in s.code_versions]
            pick = st.radio("Code version", labels, index=len(labels) - 1, horizontal=True,
                            key=f"code-{v.run_id}-{sid}")
            chosen = s.code_versions[labels.index(pick)]
            if chosen.get("note"):
                st.caption(f"Debugger's diagnosis: {chosen['note']}")
            st.code(chosen.get("code") or "", language="sql" if s.language == "sql"
                    else "python")
        else:
            st.caption("No code yet.")
        if s.test_runs:
            st.markdown("**Sandbox runs**")
            st.dataframe(pd.DataFrame([{
                "attempt": r["attempt"], "data": r["scope"], "result": "passed" if r["ok"]
                else (r.get("error_type") or "failed"), "rows in": sum(r["rows_in"].values())
                if r.get("rows_in") else None, "rows out": r.get("rows_out"),
                "set aside": r.get("rows_quarantined"), "seconds": r.get("seconds")}
                for r in s.test_runs]), hide_index=True, use_container_width=True)


def quality_panel(v: RunView) -> None:
    if not v.quality:
        st.caption("Quality checks run after every stage has passed.")
        return
    rnd = v.quality[-1]
    origin = {f"{r['dataset']}.{r.get('column') or '+'.join(r.get('columns') or []) or '*'}."
              f"{r['kind']}": r.get("origin", "") for r in rnd.get("rules", [])}
    st.markdown(f"Round {rnd['round']}: **{rnd['total'] - rnd['failed']} of {rnd['total']}** "
                f"checks passed. Checks are run by code, not judged by a model.")
    st.dataframe(pd.DataFrame([{
        "result": "passed" if c["passed"] else "FAILED", "check": c["rule_id"],
        "source": {"schema": "output contract", "request": "your request",
                   "derived": "Validator"}.get(origin.get(c["rule_id"], ""), ""),
        "observed": c["observed"], "failing rows": c["failing_rows"]} for c in rnd["checks"]]),
        hide_index=True, use_container_width=True)


def cost_panel(v: RunView) -> None:
    by = v.cost["by_agent"]
    if not by:
        st.caption("No model calls yet.")
        return
    top = max(a["usd"] for a in by.values()) or max(a["tokens"] for a in by.values()) or 1
    key = "usd" if any(a["usd"] for a in by.values()) else "tokens"
    rows = []
    for agent, a in sorted(by.items(), key=lambda kv: -kv[1][key]):
        val = f"${a['usd']:.4f}" if key == "usd" else f"{a['tokens']:,} tok"
        tip = f"{agent_label(agent)}: {a['calls']} calls, {a['tokens']:,} tokens, ${a['usd']:.4f}"
        rows.append(f'<div class="row" title="{escape(tip)}"><div class="label">'
                    f'{agent_label(agent)}</div><div class="track"><div class="bar" '
                    f'style="width:{a[key] / top * 100:.1f}%"></div></div>'
                    f'<div class="val">{val}</div></div>')
    st.markdown(f"**{'Cost' if key == 'usd' else 'Tokens'} by agent** "
                f"(hover a bar for calls and tokens)")
    html('<div class="bars">' + "".join(rows) + "</div>")
    st.dataframe(pd.DataFrame([{"agent": agent_label(k), "model": v.models.get(k, ""),
                                "calls": a["calls"], "tokens": a["tokens"],
                                "cost ($)": round(a["usd"], 5)} for k, a in by.items()]),
                 hide_index=True, use_container_width=True)
    st.caption(f"{v.cost['tool_calls']} tool calls through MCP servers. The Orchestrator "
               f"makes no model calls.")


def report_panel(run_root: Path) -> None:
    md, js = run_root / "report.md", run_root / "report.json"
    if not md.exists():
        st.caption("The report is written when the run finishes.")
        return
    c1, c2 = st.columns(2)
    c1.download_button("Download report (Markdown)", md.read_text(), file_name=md.name)
    c2.download_button("Download report (JSON)", js.read_text(), file_name=js.name)
    st.markdown(md.read_text())


def run_body(v: RunView, run_root: Path, live: bool, on_decide=None,
             replay_note: str = "") -> None:
    run_heading(v)
    schematic(v)
    budget_meters(v)
    approval_gate(v, live, on_decide, replay_note)
    tabs = st.tabs(["Team activity", "Stage inspector", "Data quality", "Cost", "Report"])
    with tabs[0]:
        activity_feed(v)
    with tabs[1]:
        stage_inspector(v)
    with tabs[2]:
        quality_panel(v)
    with tabs[3]:
        cost_panel(v)
    with tabs[4]:
        report_panel(run_root)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}
