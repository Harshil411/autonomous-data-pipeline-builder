"""Design tokens and the app's CSS: two looks, one stylesheet.

    Night (dark)   the default: a dark surface so that light can carry meaning
    Day (light)    the same layout on a pale surface, for bright rooms and projectors

Three colours, each with one job, in both looks:
    orange  - being worked on right now (the live stage, the live agent, the beams)
    green   - passed, approved, loaded
    maroon  - the approval gate, the one thing only a person can open
Everything not yet reached stays dim and dashed. Maroon and orange are the showcase flyer's
Virginia Tech colours: used as printed in Day, lifted in brightness in Night so they read on
a dark background. Status is always written in words too; colour never carries it alone.

How the two looks share one stylesheet: `THEMES` holds one value per name for each look,
`css(mode)` writes them out as CSS variables (`--text`, `--orange`, ...) and every rule below
uses only those variables. `tests/ui/test_theme.py` checks that both looks define the same
names, that no rule hard-codes a colour, and that text stays readable (WCAG contrast).

Streamlit's own widgets (inputs, tables, tabs) get matching colours from the `[theme.dark]`
and `[theme.light]` sections of `.streamlit/config.toml`. The browser remembers which look
is active; `active_mode()` asks it.

Type: Geist for text, Geist Mono only for real identifiers (stage and table names, code).
Both are bundled in `app/static/` so the booth laptop needs no internet.
"""

from __future__ import annotations

import streamlit as st

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        # surfaces and lines
        "bg": "#09090B", "bg-rgb": "9, 9, 11", "dot": "#1D1D23",
        "surface": "#131316", "surface-rgb": "19, 19, 22", "surface-2": "#1C1C21",
        "hair": "#1E1E23", "line": "#26262B", "line-2": "#34343B", "line-3": "#4A4A54",
        "shadow": "rgba(0, 0, 0, .6)",
        # text
        "text": "#EDEDEF", "text-2": "#C9C9CF", "muted": "#9A9AA3", "dim": "#8C8C96",
        "faint": "#6E6E78",
        # orange: being worked on
        "orange": "#FF8A3D", "orange-rgb": "255, 138, 61", "orange-text": "#FFB37A",
        "orange-soft": "#2A1808",
        # green: passed, approved, loaded
        "green": "#7FD1AE", "green-rgb": "127, 209, 174", "green-text": "#A9E3C9",
        "green-soft": "#0F1A16", "green-soft-2": "#10241C", "green-ink": "#06140E",
        # maroon: the approval gate and the person
        "maroon-rgb": "200, 52, 104", "maroon-line": "#E0668F", "maroon-text": "#F2A7C0",
        "gate-1": "#A12A55", "gate-2": "#5E1530", "gate-text": "#FFFFFF", "gate-sub": "#F7C5D6",
        # red: failed, rejected
        "red": "#F2708A", "red-rgb": "242, 112, 138", "red-text": "#F7A3B4",
        "red-soft": "#2A0E12",
        # buttons and the maroon-to-orange accent
        "btn-bg": "#EDEDEF", "btn-text": "#09090B", "btn-hover": "#FFFFFF",
        "grad-1": "#C83468", "grad-2": "#FF8A3D", "on-accent": "#130608",
        # the Orchestrator bar
        "orch-bg": "#17171B", "orch-line": "#3A3A42", "orch-text": "#EDEDEF",
        "orch-sub": "#9A9AA3",
        # how strong the glows are (1 = full) and how strong the halo around lit lines is
        "glow": "1", "halo": ".9",
        # one colour per agent for the rule beside its lines in the activity log
        "agent-planner": "#8FB4FF", "agent-coder": "#C4A7FF", "agent-tester": "#7FD1AE",
        "agent-debugger": "#FF8A3D", "agent-validator": "#6FD3E0",
        "agent-orchestrator": "#9A9AA3", "agent-you": "#F2A7C0",
    },
    "light": {
        "bg": "#F6F6F7", "bg-rgb": "246, 246, 247", "dot": "#DFDFE5",
        "surface": "#FFFFFF", "surface-rgb": "255, 255, 255", "surface-2": "#EFEFF2",
        "hair": "#E9E9EC", "line": "#E2E2E7", "line-2": "#D2D2D9", "line-3": "#ADADB8",
        "shadow": "rgba(30, 20, 40, .14)",
        "text": "#131316", "text-2": "#3B3B42", "muted": "#5F5F69", "dim": "#9B9BA6",
        "faint": "#9A9AA6",
        "orange": "#E5650F", "orange-rgb": "229, 101, 15", "orange-text": "#9A4300",
        "orange-soft": "#FFF1E6",
        "green": "#178050", "green-rgb": "23, 128, 80", "green-text": "#0F6B41",
        "green-soft": "#EAF7F0", "green-soft-2": "#DDF3E7", "green-ink": "#FFFFFF",
        "maroon-rgb": "134, 31, 65", "maroon-line": "#861F41", "maroon-text": "#861F41",
        "gate-1": "#9A2349", "gate-2": "#6B1733", "gate-text": "#FFFFFF", "gate-sub": "#F7C5D6",
        "red": "#C4314B", "red-rgb": "196, 49, 75", "red-text": "#A3263C",
        "red-soft": "#FDECEF",
        "btn-bg": "#131316", "btn-text": "#FFFFFF", "btn-hover": "#2A2A30",
        "grad-1": "#861F41", "grad-2": "#C2410C", "on-accent": "#FFFFFF",
        # in Day the Orchestrator bar is the one dark element: the thing in charge
        "orch-bg": "#18181C", "orch-line": "#18181C", "orch-text": "#FFFFFF",
        "orch-sub": "#C4C4CC",
        "glow": ".5", "halo": ".3",
        "agent-planner": "#3B6FD9", "agent-coder": "#7C4DDB", "agent-tester": "#178050",
        "agent-debugger": "#E5650F", "agent-validator": "#0E8496",
        "agent-orchestrator": "#5F5F69", "agent-you": "#861F41",
    },
}


def active_mode() -> str:
    """'light' when the browser is showing Streamlit's light theme, otherwise 'dark'.
    Outside a browser session (tests, scripts) there is no theme to ask, so it is 'dark'."""
    try:
        kind = st.context.theme.type
    except Exception:  # no session, or a Streamlit build without theme info
        kind = None
    return "light" if kind == "light" else "dark"


def css(mode: str) -> str:
    """The whole stylesheet for one look: its colours as CSS variables, then the rules."""
    tokens = THEMES.get(mode, THEMES["dark"])
    declared = "\n".join(f"  --{name}: {value};" for name, value in tokens.items())
    return f"<style>\n:root {{\n{declared}\n}}\n{RULES}</style>\n"


RULES = """/* ---- page ------------------------------------------------------------------------------ */
/* a booth display, not a developer tool: hide Streamlit's own chrome */
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], .stDeployButton, #MainMenu, footer { display: none !important; }
.stApp { background-color: var(--bg);
  background-image: radial-gradient(var(--dot) 1.2px, rgba(var(--bg-rgb), 0) 1.2px);
  background-size: 24px 24px; }
.block-container { padding-top: 1.1rem; padding-bottom: 3rem; max-width: 1400px; }
h1, h2, h3 { color: var(--text); letter-spacing: -0.02em; font-weight: 600; }
h2 { font-size: 1.45rem; } h3 { font-size: 1.15rem; }
p, li { font-size: 1.02rem; line-height: 1.55; }
.muted, .small { color: var(--muted); } .small { font-size: .88rem; }
code, pre { font-family: 'Geist Mono', ui-monospace, Menlo, monospace !important; }

/* ---- header: wordmark and page links ----------------------------------------------- */
.wordmark { display: inline-flex; align-items: center; gap: .6rem; font-size: 1.08rem;
  font-weight: 600; color: var(--text); }
.wordmark .logo { flex: none; width: 30px; height: 30px; fill: none; stroke: url(#logo-ink); stroke-width: 2.4;
  stroke-linecap: round; stroke-linejoin: round; }
.wordmark .logo circle { fill: var(--grad-1); stroke: none; }
.wordmark .logo circle.out { fill: var(--grad-2); }
.wordmark .logo stop.l1 { stop-color: var(--grad-1); }
.wordmark .logo stop.l2 { stop-color: var(--grad-2); }
[data-testid="stPageLink"] a { border-radius: 999px; padding: .25rem .8rem;
  justify-content: flex-end; }
[data-testid="stPageLink"] a p { color: var(--muted); font-size: .95rem; }
[data-testid="stPageLink"] a:hover { background: var(--surface); }
[data-testid="stPageLink"] a:hover p { color: var(--text); }
[data-testid="stPageLink"] a[aria-current="page"] { background: var(--surface);
  box-shadow: 0 0 0 1px var(--line) inset; }
[data-testid="stPageLink"] a[aria-current="page"] p { color: var(--text); font-weight: 600; }

/* the look switch: a small quiet button; the script element beside it takes no room */
.st-key-look button { min-height: 2rem; padding: .1rem .7rem; border-radius: 999px;
  border-color: var(--line-2); }
.st-key-look button p { font-size: .9rem; color: var(--muted); }
[data-testid="stElementContainer"]:has(> [data-testid="stHtml"]) { display: none; }

/* ---- the journey: Describe, Build, Approve ------------------------------------------- */
.steps { display: flex; justify-content: center; margin: .1rem 0 .9rem; }
.steps ol { display: inline-flex; gap: 6px; margin: 0; padding: 4px; list-style: none;
  background: var(--surface); border: 1px solid var(--line); border-radius: 999px; }
.steps li { padding: .32rem 1rem; border-radius: 999px; color: var(--muted); font-size: .9rem;
  line-height: 1.4; margin: 0; }
.steps li.past { color: var(--text-2); }
.steps li.on { background: var(--text); color: var(--bg); font-weight: 600; }
.steps.done li.on { background: var(--green); color: var(--green-ink); }

/* ---- hero: the one sentence a visitor reads first -------------------------------- */
.hero { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between;
  gap: .8rem 2.5rem; margin: 0 0 .7rem; }
.hero .main { flex: 999 1 30rem; min-width: 0; }
.hero h1 { margin: .6rem 0 0; padding: 0; font-size: clamp(1.9rem, 3.1vw, 2.75rem);
  line-height: 1.04; font-weight: 600; letter-spacing: -0.035em; }
.hero .side { flex: 1 1 19rem; margin: 0 0 .35rem; }
.hero .side p { margin: 0; font-size: 1.06rem; color: var(--muted); }
.hero .meta { display: flex; gap: 1.2rem; margin-top: .45rem; font-size: .92rem;
  color: var(--muted); font-variant-numeric: tabular-nums; }
.hero .meta b { color: var(--text); font-weight: 500;
  font-family: 'Geist Mono', ui-monospace, Menlo, monospace; }
.tag { display: inline-flex; align-items: center; gap: .6rem; padding: .28rem .85rem;
  border-radius: 999px; font-size: .9rem; font-weight: 500; border: 1px solid var(--line-2);
  color: var(--text-2); background: var(--surface); }
.tag i { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.hero.live .tag { color: var(--orange-text); border-color: rgba(var(--orange-rgb), .45);
  background: rgba(var(--orange-rgb), .1); }
.hero.live .tag i { background: var(--orange); box-shadow: 0 0 12px var(--orange);
  animation: blink 1.4s ease-in-out infinite; }
.hero.wait .tag { color: var(--maroon-text); border-color: rgba(var(--maroon-rgb), .55);
  background: rgba(var(--maroon-rgb), .14); }
.hero.done .tag { color: var(--green-text); border-color: rgba(var(--green-rgb), .45);
  background: rgba(var(--green-rgb), .1); }
.hero.stopped .tag { color: var(--red-text); border-color: rgba(var(--red-rgb), .45);
  background: rgba(var(--red-rgb), .1); }
.hero .replay { margin-left: .5rem; }
@keyframes blink { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }

/* ---- the Describe screen ------------------------------------------------------------- */
.describe { position: relative; isolation: isolate; text-align: center;
  margin: 1.2rem auto 1.5rem; }
.describe h1 { margin: 0 auto; padding: 0; max-width: 58rem;
  font-size: clamp(2.1rem, 3.9vw, 3.5rem); line-height: 1.04; font-weight: 600;
  letter-spacing: -0.04em; text-wrap: balance; }
.describe h1 .dim { color: var(--dim); }
.describe::after { content: ""; position: absolute; left: 50%; top: 55%; width: min(1100px, 92vw);
  height: 560px; transform: translateX(-50%); z-index: -1; pointer-events: none;
  background: radial-gradient(closest-side, rgba(var(--maroon-rgb), .5), rgba(var(--orange-rgb), .22) 50%,
  rgba(var(--bg-rgb), 0) 100%); filter: blur(40px); opacity: var(--glow); }
.st-key-prompt [data-testid="stForm"] { background: rgba(var(--surface-rgb), .9);
  border: 1px solid var(--line-2); border-radius: 18px; padding: 1.2rem 1.3rem 1rem;
  box-shadow: 0 30px 80px var(--shadow); }
.st-key-prompt [data-testid="stTextArea"] textarea { background: transparent; border: 0;
  font-size: 1.12rem; line-height: 1.5; padding: .2rem; }
.st-key-prompt [data-testid="stTextArea"] > div, .st-key-prompt [data-baseweb="textarea"] {
  background: transparent !important; border-color: transparent !important; }
.chips { display: flex; flex-wrap: wrap; gap: .5rem; margin: .1rem 0 .4rem; }
.chips span { display: inline-flex; gap: .5rem; align-items: baseline; padding: .32rem .7rem;
  background: var(--surface-2); border: 1px solid var(--line-2); border-radius: 8px; font-size: .88rem;
  color: var(--muted); }
.chips b { color: var(--text); font-weight: 500;
  font-family: 'Geist Mono', ui-monospace, Menlo, monospace; }
.try { text-align: center; color: var(--muted); font-size: .9rem; margin: 1.1rem 0 .1rem; }
.st-key-presets button { border-radius: 999px; min-height: 2.3rem; background: transparent;
  border: 1px solid var(--line-2); }
.st-key-presets button p { font-size: .88rem; color: var(--text-2); white-space: nowrap; }
.st-key-presets button[data-testid="stBaseButton-primary"] { background: var(--surface-2) !important;
  border-color: var(--faint) !important; box-shadow: none; }
.st-key-presets button[data-testid="stBaseButton-primary"] p { color: var(--text) !important;
  font-weight: 500; }
.st-key-presets button { padding-left: .3rem; padding-right: .3rem; }
.promises { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
  gap: .7rem 2rem; margin-top: 3rem; padding-top: 1.2rem; border-top: 1px solid var(--hair);
  font-size: .9rem; color: var(--muted); }
.promises b { color: var(--text); font-weight: 500; }

/* ---- buttons --------------------------------------------------------------------------- */
button[data-testid^="stBaseButton-primary"] { background: var(--btn-bg) !important;
  border-color: var(--btn-bg) !important; border-radius: 10px; }
button[data-testid^="stBaseButton-primary"] p { color: var(--btn-text) !important; font-weight: 600; }
button[data-testid^="stBaseButton-primary"]:hover { background: var(--btn-hover) !important; }
button[data-testid^="stBaseButton-secondary"] { border-radius: 10px; border-color: var(--line-2);
  background: transparent; }
button:focus-visible { outline: 2px solid var(--orange) !important; outline-offset: 3px; }

/* ---- the approval gate: the one place the app asks for a person ---------------- */
.st-key-gate { position: relative; margin: .2rem 0 1.4rem; }
.st-key-gate::before { content: ""; position: absolute; left: -4%; top: -30%; width: 46rem;
  height: 30rem; z-index: 0; pointer-events: none; filter: blur(40px);
  background: radial-gradient(closest-side, rgba(var(--maroon-rgb), .42), rgba(var(--bg-rgb), 0) 100%);
  opacity: var(--glow); }
.st-key-gate > * { position: relative; z-index: 1; }
.proofs { list-style: none; margin: 0 0 1.1rem; padding: 0; }
.proofs li { display: flex; align-items: center; gap: .75rem; margin: 0 0 .55rem;
  font-size: 1.12rem; color: var(--text); }
.proofs li svg { flex: none; }
.proofs li.warn { color: var(--orange-text); }
.st-key-gate [data-testid="stForm"] { border: 0; padding: 0; }
.st-key-gate button[data-testid="stBaseButton-primaryFormSubmit"] {
  background: linear-gradient(135deg, var(--grad-1), var(--grad-2)) !important; border: 0 !important;
  min-height: 3.4rem; border-radius: 12px; box-shadow: 0 12px 40px rgba(var(--maroon-rgb), .45); }
.st-key-gate button[data-testid="stBaseButton-primaryFormSubmit"] p { color: var(--on-accent) !important;
  font-weight: 700; font-size: 1.08rem; }
.st-key-gate button[data-testid="stBaseButton-secondaryFormSubmit"] { min-height: 3.4rem;
  border-radius: 12px; }
.fine { font-size: .88rem; color: var(--muted); margin: .6rem 0 0; }
.peek { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .4rem 1rem;
  padding: .9rem 1.2rem; background: rgba(var(--surface-rgb), .9); border: 1px solid var(--line-2);
  border-bottom: 0; border-radius: 18px 18px 0 0; }
.peek b { font-family: 'Geist Mono', ui-monospace, Menlo, monospace; font-weight: 500; }
.peek span { color: var(--muted); font-size: .9rem; }

/* ---- the live graph (markup in app/graph.py) ---------------------------------------- */
.graph-wrap { margin: .2rem 0 1.2rem; }
.graph { display: block; margin: 0 auto; overflow: visible; }
@media (max-width: 820px) { .graph-wrap { overflow-x: auto; } .graph { min-width: 760px; } }
.graph text { font-family: inherit; fill: var(--text); }
.graph .title { font-size: 15px; font-weight: 600; }
.graph .title.mono { font-family: 'Geist Mono', ui-monospace, Menlo, monospace;
  font-size: 14px; font-weight: 500; }
.graph .sub { font-size: 14px; fill: var(--muted); }
.graph .note { font-size: 12.5px; fill: var(--muted); }
.graph .box { fill: var(--surface); stroke: var(--line-2); stroke-width: 1.2; transition: fill .4s, stroke .4s; }
.graph .halo { fill: none; stroke: none; }
/* the soft light behind what matters: its colour and strength come from the mode */
.graph .glow { opacity: var(--glow); }
.graph stop.s-live { stop-color: var(--orange); } .graph stop.s-gate { stop-color: var(--grad-1); }
.graph stop.s-done { stop-color: var(--green); }
.graph stop.s-gate-1 { stop-color: var(--gate-1); } .graph stop.s-gate-2 { stop-color: var(--gate-2); }
.graph stop.in { stop-opacity: .34; } .graph stop.out { stop-opacity: 0; }
.graph .edge, .graph .beam, .graph .tether { fill: none; stroke-width: 2; stroke-linecap: round; }
.graph .edge { stroke: var(--line-3); transition: stroke .4s; }
.graph .edge.blocked { stroke-dasharray: 6 7; }
.graph .edge.done { stroke: var(--green); filter: drop-shadow(0 0 5px rgba(var(--green-rgb), var(--halo))); }
.graph .edge.flow { stroke: var(--green); stroke-dasharray: 6 7;
  filter: drop-shadow(0 0 6px rgba(var(--green-rgb), var(--halo))); animation: g-flow .9s linear infinite; }
.graph .edge.live, .graph .beam { stroke: var(--orange); stroke-dasharray: 6 7;
  filter: drop-shadow(0 0 6px rgba(var(--orange-rgb), var(--halo))); animation: g-flow .9s linear infinite; }
.graph .tether { stroke: var(--line-3); stroke-dasharray: 3 7; }
.graph .node.source .title { font-weight: 600; }
.graph .stage.passed .box { fill: var(--green-soft); stroke: var(--green);
  filter: drop-shadow(0 0 10px rgba(var(--green-rgb), calc(var(--halo) * .25))); }
.graph .stage.passed .sub { fill: var(--green); }
.graph .stage.live .box { fill: var(--orange-soft); stroke: var(--orange); stroke-width: 2; }
.graph .stage.live .sub { fill: var(--orange-text); }
.graph .stage.failed .box { fill: var(--red-soft); stroke: var(--red); }
.graph .stage.failed .sub { fill: var(--red-text); }
.graph .stage.pending .box, .graph .db.empty .box, .graph .agent.idle .box {
  fill: rgba(var(--surface-rgb), .6); stroke: var(--line-3); stroke-dasharray: 5 5; }
.graph .stage.pending .title, .graph .agent.idle .title { fill: var(--muted); }
.graph .gate .box { fill: url(#gate-fill); stroke: var(--maroon-line); }
.graph .gate .title { fill: var(--gate-text); }
.graph .gate .sub { fill: var(--gate-sub); font-size: 13px; }
.graph .gate.open .title, .graph .gate.rejected .title { fill: var(--text); }
.graph .gate.rejected .sub { fill: var(--red-text); }
.graph .gate.open .box { fill: var(--green-soft); stroke: var(--green); }
.graph .gate.open .sub { fill: var(--green); }
.graph .gate.rejected .box { fill: var(--red-soft); stroke: var(--red); }
.graph .db.loaded .box { fill: var(--green-soft-2); stroke: var(--green); stroke-width: 2; }
.graph .db.loaded .sub { fill: var(--green-text); }
.graph .orch .box { fill: var(--orch-bg); stroke: var(--orch-line); }
.graph .orch .title { fill: var(--orch-text); } .graph .orch .sub { fill: var(--orch-sub); }
.graph .agent .icon { fill: none; stroke: var(--text-2); stroke-width: 1.7; stroke-linecap: round;
  stroke-linejoin: round; }
.graph .agent .sub { font-size: 13px; }
.graph .agent.done .sub { fill: var(--green); }
.graph .agent.live .box { fill: var(--orange-soft); stroke: var(--orange); stroke-width: 2; }
.graph .agent.live .title, .graph .agent.live .sub { fill: var(--orange-text); }
.graph .agent.live .icon { stroke: var(--orange-text); }
.graph .agent.idle .icon { stroke: var(--faint); }
/* the pulsing halo: only on the live stage, the live agent, a gate that needs a person,
   and the database at the moment it fills */
.graph .stage.live .halo, .graph .agent.live .halo { stroke: var(--orange); }
.graph .gate.needed .halo { stroke: var(--maroon-line); }
.graph .db.loaded .halo { stroke: var(--green); }
.graph .stage.live .halo, .graph .agent.live .halo, .graph .gate.needed .halo,
.graph .db.loaded .halo { stroke-width: 7; filter: blur(5px);
  animation: g-pulse 1.8s ease-in-out infinite; }
@keyframes g-flow { to { stroke-dashoffset: -26; } }
@keyframes g-pulse { 0%, 100% { opacity: .22; } 50% { opacity: .8; } }
@media (prefers-reduced-motion: reduce) {
  .graph .edge, .graph .beam, .graph .halo, .hero .tag i { animation: none !important; } }

/* ---- details under the graph ------------------------------------------------------- */
.panel-title { font-weight: 600; font-size: 1.05rem; color: var(--text); margin: .1rem 0 .5rem; }
.panel-title.spaced { margin-top: 1.6rem; }
.act { border-left: 3px solid var(--faint); padding: .4rem .9rem; margin: 0 0 .3rem;
  background: var(--surface); border-radius: 0 10px 10px 0;
  display: grid; grid-template-columns: 7rem 1fr; column-gap: .6rem; align-items: baseline; }
.act .who { font-weight: 600; font-size: .9rem; }
.act .what { font-size: 1rem; color: var(--text); }
.act .why { grid-column: 2; font-size: .86rem; color: var(--muted); margin-top: .15rem;
  white-space: pre-wrap; max-height: 4.6em; overflow: hidden; }
.act .mark { font-weight: 700; margin-right: .35rem; }
.act.good .mark { color: var(--green); } .act.bad .mark { color: var(--red); }
.act.wait .mark { color: var(--maroon-text); }
.focus-head { display: flex; flex-wrap: wrap; gap: .4rem .8rem; align-items: baseline;
  margin: .3rem 0 .2rem; }
.focus-head b { font-size: 1.15rem; font-weight: 500;
  font-family: 'Geist Mono', ui-monospace, Menlo, monospace; }
.focus-head .state { font-weight: 600; font-size: .86rem; border-radius: 999px;
  padding: .05rem .65rem; border: 1px solid currentColor; color: var(--muted); }
.focus-head .state.passed { color: var(--green); } .focus-head .state.failed { color: var(--red); }
.focus-head .state.coding, .focus-head .state.testing, .focus-head .state.debugging {
  color: var(--orange-text); }
.desc { color: var(--text-2); font-size: .98rem; margin: .1rem 0 .5rem; }
.attempts { display: flex; flex-wrap: wrap; gap: .35rem; margin: 0 0 .5rem; }
.chip { border-radius: 999px; padding: .1rem .65rem; font-size: .84rem; border: 1px solid; }
.chip.ok { color: var(--green); background: var(--green-soft); }
.chip.bad { color: var(--red-text); background: var(--red-soft); }
.chip.fix { color: var(--orange-text); background: var(--orange-soft); }
.test-row { display: grid; grid-template-columns: 6.2rem 1fr; gap: .6rem; padding: .5rem 0;
  border-bottom: 1px solid var(--hair); }
.test-row .st { font-weight: 600; color: var(--muted); }
.test-row .st.ok { color: var(--green); } .test-row .st.bad { color: var(--red); }
.test-row code { font-size: .82rem; white-space: pre-wrap; background: transparent;
  color: var(--muted); padding: 0; }
.meters { display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: 1.4rem; margin: .4rem 0 .8rem; }
.meter .top { display: flex; justify-content: space-between; font-size: .9rem; color: var(--muted);
  margin-bottom: .3rem; }
.meter .track { height: 8px; background: var(--hair); border-radius: 4px; overflow: hidden; }
.meter .fill { height: 100%; background: var(--orange); border-radius: 4px; }
.meter .fill.hot { background: var(--red); }
.bars .row { display: grid; grid-template-columns: 9rem 1fr 6.5rem; align-items: center;
  gap: .6rem; margin: .3rem 0; }
.bars .label { font-weight: 500; color: var(--text); }
.bars .bar { height: 12px; background: linear-gradient(90deg, var(--grad-1), var(--grad-2));
  border-radius: 0 6px 6px 0; min-width: 2px; }
.bars .val { font-variant-numeric: tabular-nums; color: var(--muted); text-align: right; }
.nums { display: flex; flex-wrap: wrap; gap: .6rem 2.4rem; margin: .2rem 0 1.2rem; }
.nums .n { font-size: 1.9rem; font-weight: 600; letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums; }
.nums .l { color: var(--muted); font-size: .9rem; }
.page-title { margin: 1.2rem 0 1.4rem; }
.page-title h1 { margin: 0; padding: 0; font-size: clamp(1.9rem, 3vw, 2.7rem); line-height: 1.06;
  font-weight: 600; letter-spacing: -0.035em; max-width: 48rem; }
.page-title p { margin: .6rem 0 0; color: var(--muted); font-size: 1.05rem; max-width: 46rem; }

"""
