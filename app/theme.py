"""Design tokens and the app's CSS: the "Night" look.

A dark surface so that light can carry meaning. Three lights, each with one job:
    orange  - being worked on right now (the live stage, the live agent, the beams)
    green   - passed, approved, loaded
    maroon  - the approval gate, the one thing only a person can open
Everything not yet reached stays dim and dashed. Maroon and orange are the showcase flyer's
Virginia Tech colours, lifted in brightness so they read on a dark background. Status is
always written in words as well; colour never carries it alone.

Type: Geist for text, Geist Mono only for real identifiers (stage and table names, code).
Both are bundled in `app/static/` so the booth laptop needs no internet.

The colours are also set in `.streamlit/config.toml` (base theme for Streamlit's own
widgets); this file styles our own markup and bends the widgets to match.
"""

from __future__ import annotations

TOKENS = {
    "bg": "#09090B", "surface": "#131316", "surface_2": "#1B1B20",
    "line": "#26262B", "line_2": "#34343B", "line_3": "#4A4A54",
    "text": "#EDEDEF", "text_2": "#C9C9CF", "muted": "#9A9AA3",
    "orange": "#FF8A3D", "orange_text": "#FFB37A", "orange_soft": "#2A1808",
    "green": "#7FD1AE", "green_text": "#A9E3C9", "green_soft": "#0F1A16",
    "maroon": "#C83468", "maroon_text": "#F2A7C0", "maroon_soft": "#1A0C12",
    "red": "#F2708A", "red_soft": "#2A0E12",
}

# One colour per agent, used only for the thin rule beside its lines in the activity log;
# the agent's name is always written too.
AGENT_COLORS = {
    "planner": "#8FB4FF", "coder": "#C4A7FF", "tester": "#7FD1AE", "debugger": "#FF8A3D",
    "validator": "#6FD3E0", "orchestrator": "#9A9AA3", "you": "#F2A7C0",
}

CSS = """
<style>
/* ---- page ------------------------------------------------------------------------------ */
/* a booth display, not a developer tool: hide Streamlit's own chrome */
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], .stDeployButton, #MainMenu, footer { display: none !important; }
.stApp { background-color: #09090B;
  background-image: radial-gradient(#1D1D23 1.2px, rgba(9,9,11,0) 1.2px);
  background-size: 24px 24px; }
.block-container { padding-top: 1.1rem; padding-bottom: 3rem; max-width: 1400px; }
h1, h2, h3 { color: #EDEDEF; letter-spacing: -0.02em; font-weight: 600; }
h2 { font-size: 1.45rem; } h3 { font-size: 1.15rem; }
p, li { font-size: 1.02rem; line-height: 1.55; }
.muted, .small { color: #9A9AA3; } .small { font-size: .88rem; }
code, pre { font-family: 'Geist Mono', ui-monospace, Menlo, monospace !important; }

/* ---- header: wordmark and page links ----------------------------------------------- */
.wordmark { display: inline-flex; align-items: center; gap: .6rem; font-size: 1.08rem;
  font-weight: 600; color: #EDEDEF; }
.wordmark i { width: 22px; height: 22px; border-radius: 6px;
  background: linear-gradient(135deg, #B0305C, #FF8A3D); }
[data-testid="stPageLink"] a { border-radius: 999px; padding: .25rem .8rem;
  justify-content: flex-end; }
[data-testid="stPageLink"] a p { color: #9A9AA3; font-size: .95rem; }
[data-testid="stPageLink"] a:hover { background: #131316; }
[data-testid="stPageLink"] a:hover p { color: #EDEDEF; }
[data-testid="stPageLink"] a[aria-current="page"] { background: #131316;
  box-shadow: 0 0 0 1px #26262B inset; }
[data-testid="stPageLink"] a[aria-current="page"] p { color: #EDEDEF; font-weight: 600; }

/* ---- the journey: Describe, Build, Approve ------------------------------------------- */
.steps { display: flex; justify-content: center; margin: .1rem 0 .9rem; }
.steps ol { display: inline-flex; gap: 6px; margin: 0; padding: 4px; list-style: none;
  background: #131316; border: 1px solid #26262B; border-radius: 999px; }
.steps li { padding: .32rem 1rem; border-radius: 999px; color: #9A9AA3; font-size: .9rem;
  line-height: 1.4; margin: 0; }
.steps li.past { color: #C9C9CF; }
.steps li.on { background: #EDEDEF; color: #09090B; font-weight: 600; }
.steps.done li.on { background: #7FD1AE; color: #06140E; }

/* ---- hero: the one sentence a visitor reads first -------------------------------- */
.hero { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between;
  gap: .8rem 2.5rem; margin: 0 0 .7rem; }
.hero .main { flex: 999 1 30rem; min-width: 0; }
.hero h1 { margin: .6rem 0 0; padding: 0; font-size: clamp(1.9rem, 3.1vw, 2.75rem);
  line-height: 1.04; font-weight: 600; letter-spacing: -0.035em; }
.hero .side { flex: 1 1 19rem; margin: 0 0 .35rem; }
.hero .side p { margin: 0; font-size: 1.06rem; color: #9A9AA3; }
.hero .meta { display: flex; gap: 1.2rem; margin-top: .45rem; font-size: .92rem;
  color: #9A9AA3; font-variant-numeric: tabular-nums; }
.hero .meta b { color: #EDEDEF; font-weight: 500;
  font-family: 'Geist Mono', ui-monospace, Menlo, monospace; }
.tag { display: inline-flex; align-items: center; gap: .6rem; padding: .28rem .85rem;
  border-radius: 999px; font-size: .9rem; font-weight: 500; border: 1px solid #34343B;
  color: #C9C9CF; background: #131316; }
.tag i { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.hero.live .tag { color: #FFB37A; border-color: rgba(255,138,61,.45);
  background: rgba(255,138,61,.1); }
.hero.live .tag i { background: #FF8A3D; box-shadow: 0 0 12px #FF8A3D;
  animation: blink 1.4s ease-in-out infinite; }
.hero.wait .tag { color: #F2A7C0; border-color: rgba(200,52,104,.55);
  background: rgba(200,52,104,.14); }
.hero.done .tag { color: #A9E3C9; border-color: rgba(127,209,174,.45);
  background: rgba(127,209,174,.1); }
.hero.stopped .tag { color: #F7A3B4; border-color: rgba(242,112,138,.45);
  background: rgba(242,112,138,.1); }
.hero .replay { margin-left: .5rem; }
@keyframes blink { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }

/* ---- the Describe screen ------------------------------------------------------------- */
.describe { position: relative; isolation: isolate; text-align: center;
  margin: 2.2rem auto 1.8rem; }
.describe h1 { margin: 0 auto; padding: 0; max-width: 58rem;
  font-size: clamp(2.3rem, 5vw, 4.4rem); line-height: 1.02; font-weight: 600;
  letter-spacing: -0.04em; text-wrap: balance; }
.describe h1 .dim { color: #8C8C96; }
.describe::after { content: ""; position: absolute; left: 50%; top: 55%; width: min(1100px, 92vw);
  height: 560px; transform: translateX(-50%); z-index: -1; pointer-events: none;
  background: radial-gradient(closest-side, rgba(200,52,104,.5), rgba(229,117,31,.22) 50%,
  rgba(9,9,11,0) 100%); filter: blur(40px); }
.st-key-prompt [data-testid="stForm"] { background: rgba(19,19,22,.9);
  border: 1px solid #34343B; border-radius: 18px; padding: 1.2rem 1.3rem 1rem;
  box-shadow: 0 30px 80px rgba(0,0,0,.6); }
.st-key-prompt [data-testid="stTextArea"] textarea { background: transparent; border: 0;
  font-size: 1.22rem; line-height: 1.45; padding: .2rem; }
.st-key-prompt [data-testid="stTextArea"] > div, .st-key-prompt [data-baseweb="textarea"] {
  background: transparent !important; border-color: transparent !important; }
.chips { display: flex; flex-wrap: wrap; gap: .5rem; margin: .1rem 0 .4rem; }
.chips span { display: inline-flex; gap: .5rem; align-items: baseline; padding: .32rem .7rem;
  background: #1C1C21; border: 1px solid #2E2E35; border-radius: 8px; font-size: .88rem;
  color: #9A9AA3; }
.chips b { color: #EDEDEF; font-weight: 500;
  font-family: 'Geist Mono', ui-monospace, Menlo, monospace; }
.try { text-align: center; color: #9A9AA3; font-size: .9rem; margin: 1.1rem 0 .1rem; }
.st-key-presets button { border-radius: 999px; min-height: 2.3rem; background: transparent;
  border: 1px solid #2E2E35; }
.st-key-presets button p { font-size: .88rem; color: #C9C9CF; white-space: nowrap; }
.st-key-presets button[data-testid="stBaseButton-primary"] { background: #1C1C21 !important;
  border-color: #6E6E78 !important; box-shadow: none; }
.st-key-presets button[data-testid="stBaseButton-primary"] p { color: #EDEDEF !important;
  font-weight: 500; }
.st-key-presets button { padding-left: .3rem; padding-right: .3rem; }
.promises { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
  gap: .7rem 2rem; margin-top: 3rem; padding-top: 1.2rem; border-top: 1px solid #1E1E23;
  font-size: .9rem; color: #9A9AA3; }
.promises b { color: #EDEDEF; font-weight: 500; }

/* ---- buttons --------------------------------------------------------------------------- */
button[data-testid^="stBaseButton-primary"] { background: #EDEDEF !important;
  border-color: #EDEDEF !important; border-radius: 10px; }
button[data-testid^="stBaseButton-primary"] p { color: #09090B !important; font-weight: 600; }
button[data-testid^="stBaseButton-primary"]:hover { background: #FFFFFF !important; }
button[data-testid^="stBaseButton-secondary"] { border-radius: 10px; border-color: #3A3A42;
  background: transparent; }
button:focus-visible { outline: 2px solid #FF8A3D !important; outline-offset: 3px; }

/* ---- the approval gate: the one place the app asks for a person ---------------- */
.st-key-gate { position: relative; margin: .2rem 0 1.4rem; }
.st-key-gate::before { content: ""; position: absolute; left: -4%; top: -30%; width: 46rem;
  height: 30rem; z-index: 0; pointer-events: none; filter: blur(40px);
  background: radial-gradient(closest-side, rgba(200,52,104,.42), rgba(9,9,11,0) 100%); }
.st-key-gate > * { position: relative; z-index: 1; }
.proofs { list-style: none; margin: 0 0 1.1rem; padding: 0; }
.proofs li { display: flex; align-items: center; gap: .75rem; margin: 0 0 .55rem;
  font-size: 1.12rem; color: #EDEDEF; }
.proofs li svg { flex: none; }
.proofs li.warn { color: #FFB37A; }
.st-key-gate [data-testid="stForm"] { border: 0; padding: 0; }
.st-key-gate button[data-testid="stBaseButton-primaryFormSubmit"] {
  background: linear-gradient(135deg, #C83468, #FF8A3D) !important; border: 0 !important;
  min-height: 3.4rem; border-radius: 12px; box-shadow: 0 12px 40px rgba(200,52,104,.45); }
.st-key-gate button[data-testid="stBaseButton-primaryFormSubmit"] p { color: #130608 !important;
  font-weight: 700; font-size: 1.08rem; }
.st-key-gate button[data-testid="stBaseButton-secondaryFormSubmit"] { min-height: 3.4rem;
  border-radius: 12px; }
.fine { font-size: .88rem; color: #9A9AA3; margin: .6rem 0 0; }
.peek { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .4rem 1rem;
  padding: .9rem 1.2rem; background: rgba(19,19,22,.9); border: 1px solid #34343B;
  border-bottom: 0; border-radius: 18px 18px 0 0; }
.peek b { font-family: 'Geist Mono', ui-monospace, Menlo, monospace; font-weight: 500; }
.peek span { color: #9A9AA3; font-size: .9rem; }

/* ---- the live graph (markup in app/graph.py) ---------------------------------------- */
.graph-wrap { margin: .2rem 0 1.2rem; }
.graph { display: block; margin: 0 auto; overflow: visible; }
@media (max-width: 820px) { .graph-wrap { overflow-x: auto; } .graph { min-width: 760px; } }
.graph text { font-family: inherit; fill: #EDEDEF; }
.graph .title { font-size: 15px; font-weight: 600; }
.graph .title.mono { font-family: 'Geist Mono', ui-monospace, Menlo, monospace;
  font-size: 14px; font-weight: 500; }
.graph .sub { font-size: 14px; fill: #9A9AA3; }
.graph .note { font-size: 12.5px; fill: #9A9AA3; }
.graph .box { fill: #131316; stroke: #34343B; stroke-width: 1.2; transition: fill .4s, stroke .4s; }
.graph .halo { fill: none; stroke: none; }
.graph .edge, .graph .beam, .graph .tether { fill: none; stroke-width: 2; stroke-linecap: round; }
.graph .edge { stroke: #4A4A54; transition: stroke .4s; }
.graph .edge.blocked { stroke-dasharray: 6 7; }
.graph .edge.done { stroke: #7FD1AE; filter: drop-shadow(0 0 5px rgba(127,209,174,.85)); }
.graph .edge.flow { stroke: #7FD1AE; stroke-dasharray: 6 7;
  filter: drop-shadow(0 0 6px rgba(127,209,174,.95)); animation: g-flow .9s linear infinite; }
.graph .edge.live, .graph .beam { stroke: #FF8A3D; stroke-dasharray: 6 7;
  filter: drop-shadow(0 0 6px rgba(255,138,61,.95)); animation: g-flow .9s linear infinite; }
.graph .tether { stroke: #4A4A54; stroke-dasharray: 3 7; }
.graph .node.source .title { font-weight: 600; }
.graph .stage.passed .box { fill: #0F1A16; stroke: #7FD1AE;
  filter: drop-shadow(0 0 10px rgba(127,209,174,.22)); }
.graph .stage.passed .sub { fill: #7FD1AE; }
.graph .stage.live .box { fill: #2A1808; stroke: #FF8A3D; stroke-width: 2; }
.graph .stage.live .sub { fill: #FFB37A; }
.graph .stage.failed .box { fill: #2A0E12; stroke: #F2708A; }
.graph .stage.failed .sub { fill: #F7A3B4; }
.graph .stage.pending .box, .graph .db.empty .box, .graph .agent.idle .box {
  fill: rgba(19,19,22,.6); stroke: #4A4A54; stroke-dasharray: 5 5; }
.graph .stage.pending .title, .graph .agent.idle .title { fill: #9A9AA3; }
.graph .gate .box { fill: url(#gate-fill); stroke: #E0668F; }
.graph .gate .sub { fill: #F7C5D6; font-size: 13px; }
.graph .gate.open .box { fill: #0F1A16; stroke: #7FD1AE; }
.graph .gate.open .sub { fill: #7FD1AE; }
.graph .gate.rejected .box { fill: #2A0E12; stroke: #F2708A; }
.graph .db.loaded .box { fill: #10241C; stroke: #7FD1AE; stroke-width: 2; }
.graph .db.loaded .sub { fill: #A9E3C9; }
.graph .orch .box { fill: #17171B; stroke: #3A3A42; }
.graph .agent .icon { fill: none; stroke: #C9C9CF; stroke-width: 1.7; stroke-linecap: round;
  stroke-linejoin: round; }
.graph .agent .sub { font-size: 13px; }
.graph .agent.done .sub { fill: #7FD1AE; }
.graph .agent.live .box { fill: #2A1808; stroke: #FF8A3D; stroke-width: 2; }
.graph .agent.live .title, .graph .agent.live .sub { fill: #FFB37A; }
.graph .agent.live .icon { stroke: #FFB37A; }
.graph .agent.idle .icon { stroke: #6E6E78; }
/* the pulsing halo: only on the live stage, the live agent, a gate that needs a person,
   and the database at the moment it fills */
.graph .stage.live .halo, .graph .agent.live .halo { stroke: #FF8A3D; }
.graph .gate.needed .halo { stroke: #E0668F; }
.graph .db.loaded .halo { stroke: #7FD1AE; }
.graph .stage.live .halo, .graph .agent.live .halo, .graph .gate.needed .halo,
.graph .db.loaded .halo { stroke-width: 7; filter: blur(5px);
  animation: g-pulse 1.8s ease-in-out infinite; }
@keyframes g-flow { to { stroke-dashoffset: -26; } }
@keyframes g-pulse { 0%, 100% { opacity: .22; } 50% { opacity: .8; } }
@media (prefers-reduced-motion: reduce) {
  .graph .edge, .graph .beam, .graph .halo, .hero .tag i { animation: none !important; } }

/* ---- details under the graph ------------------------------------------------------- */
.panel-title { font-weight: 600; font-size: 1.05rem; color: #EDEDEF; margin: .1rem 0 .5rem; }
.act { border-left: 3px solid #6E6E78; padding: .4rem .9rem; margin: 0 0 .3rem;
  background: #131316; border-radius: 0 10px 10px 0;
  display: grid; grid-template-columns: 7rem 1fr; column-gap: .6rem; align-items: baseline; }
.act .who { font-weight: 600; font-size: .9rem; }
.act .what { font-size: 1rem; color: #EDEDEF; }
.act .why { grid-column: 2; font-size: .86rem; color: #9A9AA3; margin-top: .15rem;
  white-space: pre-wrap; max-height: 4.6em; overflow: hidden; }
.act .mark { font-weight: 700; margin-right: .35rem; }
.act.good .mark { color: #7FD1AE; } .act.bad .mark { color: #F2708A; }
.act.wait .mark { color: #F2A7C0; }
.focus-head { display: flex; flex-wrap: wrap; gap: .4rem .8rem; align-items: baseline;
  margin: .3rem 0 .2rem; }
.focus-head b { font-size: 1.15rem; font-weight: 500;
  font-family: 'Geist Mono', ui-monospace, Menlo, monospace; }
.focus-head .state { font-weight: 600; font-size: .86rem; border-radius: 999px;
  padding: .05rem .65rem; border: 1px solid currentColor; color: #9A9AA3; }
.focus-head .state.passed { color: #7FD1AE; } .focus-head .state.failed { color: #F2708A; }
.focus-head .state.coding, .focus-head .state.testing, .focus-head .state.debugging {
  color: #FFB37A; }
.desc { color: #C9C9CF; font-size: .98rem; margin: .1rem 0 .5rem; }
.attempts { display: flex; flex-wrap: wrap; gap: .35rem; margin: 0 0 .5rem; }
.chip { border-radius: 999px; padding: .1rem .65rem; font-size: .84rem; border: 1px solid; }
.chip.ok { color: #7FD1AE; background: #0F1A16; }
.chip.bad { color: #F7A3B4; background: #2A0E12; }
.chip.fix { color: #FFB37A; background: #2A1808; }
.test-row { display: grid; grid-template-columns: 6.2rem 1fr; gap: .6rem; padding: .5rem 0;
  border-bottom: 1px solid #1E1E23; }
.test-row .st { font-weight: 600; color: #9A9AA3; }
.test-row .st.ok { color: #7FD1AE; } .test-row .st.bad { color: #F2708A; }
.test-row code { font-size: .82rem; white-space: pre-wrap; background: transparent;
  color: #9A9AA3; padding: 0; }
.meters { display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: 1.4rem; margin: .4rem 0 .8rem; }
.meter .top { display: flex; justify-content: space-between; font-size: .9rem; color: #9A9AA3;
  margin-bottom: .3rem; }
.meter .track { height: 8px; background: #1E1E23; border-radius: 4px; overflow: hidden; }
.meter .fill { height: 100%; background: #FF8A3D; border-radius: 4px; }
.meter .fill.hot { background: #F2708A; }
.bars .row { display: grid; grid-template-columns: 9rem 1fr 6.5rem; align-items: center;
  gap: .6rem; margin: .3rem 0; }
.bars .label { font-weight: 500; color: #EDEDEF; }
.bars .bar { height: 12px; background: linear-gradient(90deg, #C83468, #FF8A3D);
  border-radius: 0 6px 6px 0; min-width: 2px; }
.bars .val { font-variant-numeric: tabular-nums; color: #9A9AA3; text-align: right; }
.nums { display: flex; flex-wrap: wrap; gap: .6rem 2.4rem; margin: .2rem 0 1.2rem; }
.nums .n { font-size: 1.9rem; font-weight: 600; letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums; }
.nums .l { color: #9A9AA3; font-size: .9rem; }
.page-title { margin: 1.2rem 0 1.4rem; }
.page-title h1 { margin: 0; padding: 0; font-size: clamp(1.9rem, 3vw, 2.7rem); line-height: 1.06;
  font-weight: 600; letter-spacing: -0.035em; max-width: 48rem; }
.page-title p { margin: .6rem 0 0; color: #9A9AA3; font-size: 1.05rem; max-width: 46rem; }

</style>
"""
