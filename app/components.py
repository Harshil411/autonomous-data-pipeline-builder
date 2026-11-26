"""Rendering pieces shared by the pages. Every function takes a `RunView` (or plain data)
and draws it; none of them talks to the pipeline directly.

Layout of a run (one headline, one picture, detail only on request):

    journey         Describe / Build / Approve, with the current step lit, and one button
                    back to an empty request
    hero            one status tag, one headline, one supporting line, time and cost
    [approval gate  only while a run waits for a person: the proof, the buttons, a preview]
    live graph      sources -> stages -> your approval -> database, with the Orchestrator
                    and the five agents underneath (app/graph.py)
    [result         once loaded: the first rows that are now in the database]
    Details         closed by default; three tabs: What happened, Checks, Cost
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from app.graph import render_graph
from app.theme import THEMES, active_mode, css
from app.viewmodel import RunView, agent_label, hero, journey_step, plain_check, proofs

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
    """Write the stylesheet for whichever look (dark or light) the browser is showing."""
    html(css(active_mode()))


# The pages whose address the browser may be on when it remembers the look (see below).
_PATHS = ["/", "/runs", "/about"]
_SWITCH_JS = """<script>
(function () {
  // Streamlit keeps the chosen theme in the browser, one entry per page address, as
  // "Light" or "Dark". This writes that entry for every page and reloads so it applies.
  // Until someone chooses, the app opens in Night (dark) whatever the laptop's setting.
  var want = %s;
  var top = window.top, store = top.localStorage;
  var mine = "stActiveTheme-" + top.location.pathname + "-v2";
  var keys = %s.map(function (p) { return "stActiveTheme-" + p + "-v2"; });
  if (keys.indexOf(mine) < 0) keys.push(mine);
  function pick(k) {
    var v = store.getItem(k);
    return v === '"Light"' || v === '"Dark"' ? JSON.parse(v) : null;
  }
  var target = want;
  if (!target && !pick(mine)) {
    // This page has no choice stored: follow the choice made on another page, else Night.
    target = keys.map(pick).filter(Boolean)[0] || "Dark";
    // Never loop: at most three automatic reloads per browser tab.
    var n = Number(top.sessionStorage.getItem("adpb-look-auto") || 0);
    if (n >= 3) return;
    top.sessionStorage.setItem("adpb-look-auto", String(n + 1));
  }
  if (!target) return;
  keys.forEach(function (k) { store.setItem(k, JSON.stringify(target)); });
  top.location.reload();
})();
</script>"""


def look_switch() -> None:
    """The header button that flips between Night (dark) and Day (light).

    Streamlit applies a theme in the browser, not from Python, so the button asks the
    browser to remember the other look and reload. A reload starts a fresh session, which
    is why the Build and Past runs pages keep their run in the address bar."""
    other = "light" if active_mode() == "dark" else "dark"
    if st.button(other.title(), key="look", width="stretch",
                 icon=f":material/{other}_mode:", help=f"Switch to the {other} look"):
        st.session_state["_look"] = other.title()
    want = st.session_state.pop("_look", None)
    st.html(_SWITCH_JS % (json.dumps(want), json.dumps(_PATHS)), unsafe_allow_javascript=True)


# The logo: two sources merging into one result, which is what the product builds. It is
# drawn as line art in the maroon-to-orange accent (colours come from the stylesheet, so
# it suits both looks) instead of sitting in a filled square.
LOGO = (
    '<svg class="logo" aria-hidden="true" viewBox="0 0 28 28" width="28" height="28">'
    '<defs><linearGradient id="logo-ink" gradientUnits="userSpaceOnUse" x1="3" y1="14" '
    'x2="25" y2="14"><stop class="l1" offset="0"/><stop class="l2" offset="1"/>'
    '</linearGradient></defs>'
    '<path d="M4.5 7.5H9C14 7.5 13 14 18 14H21.5M4.5 20.5H9C14 20.5 13 14 18 14"/>'  # = _LOGO_PATH
    '<circle cx="4.5" cy="7.5" r="2.2"/><circle cx="4.5" cy="20.5" r="2.2"/>'
    '<circle class="out" cx="22.5" cy="14" r="3.4"/></svg>')


# The same drawing for the browser tab. A tab icon cannot read the app's stylesheet, so
# its colours are written out: the Night accent, which reads on light and dark tab bars.
_LOGO_PATH = "M4.5 7.5H9C14 7.5 13 14 18 14H21.5M4.5 20.5H9C14 20.5 13 14 18 14"


def favicon() -> str:
    """The logo as a self-contained SVG, for `st.set_page_config(page_icon=...)`."""
    dark = THEMES["dark"]
    start, end = dark["grad-1"], dark["grad-2"]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28" width="64" height="64">'
        '<defs><linearGradient id="ink" gradientUnits="userSpaceOnUse" x1="3" y1="14" x2="25" '
        f'y2="14"><stop offset="0" stop-color="{start}"/><stop offset="1" stop-color="{end}"/>'
        '</linearGradient></defs>'
        f'<path d="{_LOGO_PATH}" fill="none" stroke="url(#ink)" stroke-width="2.8" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="4.5" cy="7.5" r="2.4" fill="{start}"/>'
        f'<circle cx="4.5" cy="20.5" r="2.4" fill="{start}"/>'
        f'<circle cx="22.5" cy="14" r="3.6" fill="{end}"/></svg>')


def wordmark() -> str:
    """The logo and the product name, as markup for the header."""
    return f'<span class="wordmark">{LOGO}Pipeline Builder</span>'


# A raw string: the backslash-n below is a JavaScript escape and must reach the browser as
# written. (Unescaped, a line break lands inside a JS string and the script cannot run.)
_BULLET_JS = r"""<script>
(function () {
  // In the request box, Enter at the end of a bullet starts the next bullet, and Enter on
  // an empty bullet removes it (so two Enters end the list) - as in any notes app.
  // One listener on the document, so it keeps working when Streamlit redraws the box.
  var doc = window.top.document;
  if (doc.adpbBullets) return;
  doc.adpbBullets = true;
  var BULLET = "• ";
  doc.addEventListener("keydown", function (ev) {
    var box = ev.target;
    if (ev.key !== "Enter" || ev.shiftKey || ev.metaKey || ev.ctrlKey || ev.isComposing) return;
    if (!box || box.tagName !== "TEXTAREA" || !box.closest(".st-key-prompt")) return;
    var text = box.value, at = box.selectionStart, end = box.selectionEnd;
    var lineStart = text.lastIndexOf("\n", at - 1) + 1;
    var line = text.slice(lineStart, at);
    if (line.indexOf(BULLET) !== 0) return;          // not on a bullet: a normal new line
    ev.preventDefault();
    var from = at, insert = "\n" + BULLET;
    if (line.trim() === BULLET.trim()) { from = lineStart; insert = ""; }  // empty bullet
    var next = text.slice(0, from) + insert + text.slice(end);
    // Set the value the way a keystroke would, so Streamlit records the change.
    var proto = box.ownerDocument.defaultView.HTMLTextAreaElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(box, next);
    box.selectionStart = box.selectionEnd = from + insert.length;
    box.dispatchEvent(new Event("input", { bubbles: true }));
  }, true);
})();
</script>"""


def bullet_typing() -> None:
    """Make the request box continue a bulleted list when the visitor presses Enter."""
    st.html(_BULLET_JS, unsafe_allow_javascript=True)


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
        color = f"var(--agent-{a.agent}, var(--muted))"  # set per look in theme.py
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
    good = TICK.format(color="var(--green)", path="M5 12.5l4.5 4.5L19 7")
    warn = TICK.format(color="var(--orange-text)",
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
            if failed:  # in plain words; the exact rule is under Details, Checks
                st.warning("Still failing: " + "; ".join(plain_check(f) for f in failed) + ".")
            if not live or on_decide is None:
                st.caption(replay_note or "Open this run in Past runs to decide.")
            else:
                with st.form(f"gate-{v.run_id}", border=False):
                    who = st.text_input("Your name",
                                        value=st.session_state.get("approver", ""),
                                        placeholder="Recorded with the decision")
                    # One decision, two buttons. A note is possible but stays out of the way.
                    with st.popover("Add a note"):
                        note = st.text_input("Note (optional)",
                                             placeholder="Recorded with the decision")
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
    c1, c2, c3 = st.columns([1, 1, 3], vertical_alignment="center")
    c1.download_button("Report (Markdown)", md.read_text(), file_name=md.name)
    c2.download_button("Report (JSON)", js.read_text(), file_name=js.name)
    if c3.toggle("Read the report here", key=f"report-{run_root.name}"):
        st.markdown(md.read_text())


# -- the whole run ----------------------------------------------------------------------------


def result_preview(v: RunView) -> None:
    """Once the rows are loaded: show the first of them. The run should end on the result,
    not only on a sentence about it. (These are the rows the person saw and approved.)"""
    req = v.approval_request or {}
    preview = req.get("preview") or []
    if not v.load or not preview:
        return
    html(f'<div class="peek"><b>{escape(v.load["table"])}</b><span>first {len(preview)} of '
         f'{v.load["rows"]:,} rows, now in the database</span></div>')
    st.dataframe(pd.DataFrame(preview), hide_index=True, width="stretch")


def details(v: RunView, run_root: Path) -> None:
    """Everything a curious visitor or a grader may ask for, behind one closed section so
    it never competes with the headline and the graph."""
    with st.expander("Details"):
        happened, checks, cost = st.tabs(["What happened", "Checks", "Cost"])
        with happened:
            if v.error and v.status in ("failed", "budget_exceeded"):
                # the raw error, for whoever wants it; the headline says it in plain words
                html('<div class="panel-title">Technical reason it stopped</div>')
                st.code(v.error, language=None, wrap_lines=True)
            full = st.toggle(f"Show all {len(v.activity)} steps", key=f"log-{v.run_id}")
            activity_rows(v, limit=500 if full else 8)
            report_panel(run_root)
        with checks:
            html('<div class="panel-title">Tests for each stage</div>')
            stage_focus(v)
            html('<div class="panel-title spaced">Quality checks on the result</div>')
            quality_panel(v)
        with cost:
            cost_panel(v)
            html('<div class="panel-title spaced">Limits for this run</div>')
            budget_meters(v)


def run_dashboard(v: RunView, run_root: Path, live: bool, on_decide=None,
                  replay_note: str = "", on_new_request=None) -> None:
    """Draw one run. `on_decide(approved, by, note)` enables the approval buttons;
    `on_new_request()` adds the one button that leads back to an empty request."""
    if on_new_request is None:
        journey(journey_step(v), done=bool(v.load))
    else:  # the button shares the journey's row instead of taking a row of its own
        _, mid, right = st.columns([1.3, 6, 1.3], vertical_alignment="center")
        with mid:
            journey(journey_step(v), done=bool(v.load))
        over = v.finished or bool(v.load)  # nothing left to watch or decide
        if right.button("Build another" if over else "New request", key="new-request",
                        width="stretch"):
            on_new_request()
    run_hero(v, live)
    approval_gate(v, live, on_decide, replay_note)
    graph(v)
    result_preview(v)
    details(v, run_root)


