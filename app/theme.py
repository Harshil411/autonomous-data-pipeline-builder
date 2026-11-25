"""Design tokens and the app's CSS.

Palette carries the showcase flyer's Virginia Tech identity: maroon for identity and the
one consequential action (approve the load), burnt orange only as a fill/ring for "being
worked on right now", ink on a cool canvas. Status colours are reserved for status and
always appear with a word. Type: Atkinson Hyperlegible Next - designed for legibility,
which matters on a laptop read from a metre away - with JetBrains Mono for code only.
"""

from __future__ import annotations

TOKENS = {
    "maroon": "#861F41", "maroon_soft": "#FBF2F5",
    "orange": "#E5751F", "orange_text": "#9C4A0C",
    "ink": "#1D2027", "ink_2": "#4A505C", "ink_3": "#6B7180",
    "canvas": "#F4F5F7", "surface": "#FFFFFF", "line": "#DADDE3",
    "good": "#1F7A4D", "good_soft": "#EDF7F1",
    "bad": "#B42318", "bad_soft": "#FDF3F2",
    "wait": "#8A5A00", "wait_soft": "#FFF6E5",
}

# One colour per agent for the left rule of activity rows; the name is always written too.
AGENT_COLORS = {
    "planner": "#861F41", "coder": "#2B5C8A", "tester": "#6B4FA0", "debugger": "#9C4A0C",
    "validator": "#0E6E78", "orchestrator": "#6B7180", "you": "#1D2027",
}

CSS = """
<style>
/* a booth display, not a developer tool: hide Streamlit's own chrome */
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], .stDeployButton, #MainMenu, footer { display: none !important; }
.block-container { padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1440px; }
[data-testid="stPageLink"] a { border-radius: 6px; padding: .2rem .55rem; }
[data-testid="stPageLink"] a[aria-current="page"] { background: #FBF2F5; }
[data-testid="stPageLink"] a[aria-current="page"] p { color: #861F41; font-weight: 700; }
h1, h2, h3 { color: #1D2027; letter-spacing: -0.01em; }
h1 { font-size: 2.1rem; font-weight: 700; }
h2 { font-size: 1.45rem; font-weight: 700; }
h3 { font-size: 1.15rem; font-weight: 700; }
p, li { font-size: 1.02rem; line-height: 1.55; }
.muted { color: #4A505C; }

/* header band */
.wordmark { font-size: 1.55rem; font-weight: 700; color: #861F41; }
.band { border-bottom: 2px solid #861F41; margin: -.4rem 0 1rem; }
.intro { color: #4A505C; font-size: 1.05rem; margin: 0 0 1rem; max-width: 80ch; }

/* status pill: always a word, never colour alone */
.pill { display: inline-block; padding: .18rem .7rem; border-radius: 999px; font-weight: 700;
  font-size: .95rem; border: 1.5px solid currentColor; white-space: nowrap; }
.pill.running { color: #9C4A0C; background: #FFF3E8; }
.pill.awaiting_approval { color: #861F41; background: #FBF2F5; }
.pill.succeeded { color: #1F7A4D; background: #EDF7F1; }
.pill.failed, .pill.budget_exceeded, .pill.rejected { color: #B42318; background: #FDF3F2; }
.pill.not { color: #4A505C; background: #FFFFFF; }


/* budget meters */
.meters { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.4rem; margin: .4rem 0 .8rem; }
.meter { margin: .2rem 0 .55rem; }
.meter .top { display: flex; justify-content: space-between; font-size: .9rem; color: #4A505C; }
.meter .track { height: 8px; background: #E6E8EC; border-radius: 4px; overflow: hidden; }
.meter .fill { height: 100%; background: #861F41; border-radius: 4px; }
.meter .fill.hot { background: #B42318; }

/* activity timeline: rows with an agent rule, not a stack of identical cards */
.act { border-left: 4px solid #6B7180; padding: .35rem .8rem; margin: 0 0 .25rem;
  background: #FFFFFF; border-radius: 0 6px 6px 0;
  display: grid; grid-template-columns: 7rem 1fr; column-gap: .6rem; align-items: baseline; }
.act .why { grid-column: 2; }
.act .who { font-weight: 700; font-size: .92rem; }
.act .what { font-size: 1.02rem; color: #1D2027; }
.act .why { font-size: .88rem; color: #4A505C; margin-top: .15rem; white-space: pre-wrap;
  max-height: 4.6em; overflow: hidden; }
.act.good { background: #F6FBF8; } .act.bad { background: #FEF7F6; } .act.wait { background: #FFFBF2; }
.act .mark { font-weight: 700; margin-right: .35rem; }
.act.good .mark { color: #1F7A4D; } .act.bad .mark { color: #B42318; } .act.wait .mark { color: #8A5A00; }


/* the Now banner: the one sentence a visitor reads first */
.now { display: flex; align-items: center; gap: 1rem; background: #FFFFFF;
  border: 1px solid #DADDE3; border-left: 8px solid var(--agent); border-radius: 10px;
  padding: .85rem 1.2rem; margin: 0 0 .35rem; }
.now .who { font-size: .95rem; font-weight: 700; color: var(--agent); min-width: 7rem; }
.now .what { font-size: 1.5rem; font-weight: 700; color: #1D2027; line-height: 1.25; flex: 1; }
.now .live { width: 13px; height: 13px; border-radius: 50%; background: #E5751F; flex: none;
  animation: blink 1.4s ease-in-out infinite; }
.now.done { border-left-color: #1F7A4D; } .now.done .live { background: #1F7A4D; animation: none; }
.now.stopped { border-left-color: #B42318; } .now.wait { border-left-color: #861F41; }
.now.wait .live { background: #861F41; animation: none; }
.now.stopped .live { background: #B42318; animation: none; }
.now .mode { font-size: .85rem; font-weight: 700; color: #4A505C; border: 1.5px solid #C9CDD5;
  border-radius: 999px; padding: .1rem .6rem; }
@keyframes blink { 0%, 100% { opacity: .25; } 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .now .live { animation: none; } }
.request { color: #4A505C; font-size: 1rem; margin: .1rem 0 .7rem .2rem; max-width: 110ch;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

/* stats strip: numbers inline, not a wall of big tiles */
.strip { display: flex; flex-wrap: wrap; gap: .4rem 1.8rem; margin: 0 0 .8rem .2rem;
  color: #4A505C; font-size: .98rem; }
.strip b { font-size: 1.22rem; color: #1D2027; margin-right: .3rem;
  font-variant-numeric: tabular-nums; }

/* agent lane: the team, with whoever is working filled in */
.lane { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: .55rem;
  margin: 0 0 .8rem; }
.lane .a { border: 1.5px solid var(--agent); border-radius: 8px; padding: .5rem .7rem;
  background: #FFFFFF; transition: background-color .2s ease; }
.lane .a .n { font-weight: 700; color: var(--agent); font-size: 1rem; }
.lane .a .s { font-size: .82rem; color: #4A505C; line-height: 1.35; }
.lane .a.working { background: var(--agent); }
.lane .a.working .n, .lane .a.working .s { color: #FFFFFF; }
.lane .a.idle { border-style: dashed; }
.lane .a.idle .n { opacity: .7; }

.sch-wrap { background: #FFFFFF; border: 1px solid #DADDE3; border-radius: 10px;
  padding: .5rem .8rem; margin: 0 0 1rem; }
.panel-title { font-weight: 700; font-size: 1.08rem; color: #1D2027; margin: .1rem 0 .5rem; }
.panel-title.details { margin-top: 1.6rem; padding-top: .8rem; border-top: 1px solid #DADDE3; }

/* stage in focus */
.focus-head { display: flex; flex-wrap: wrap; gap: .4rem .8rem; align-items: baseline;
  margin: .3rem 0 .2rem; }
.focus-head b { font-size: 1.2rem; }
.focus-head .state { font-weight: 700; font-size: .9rem; border-radius: 999px;
  padding: .05rem .6rem; border: 1.5px solid currentColor; color: #4A505C; }
.focus-head .state.passed { color: #1F7A4D; } .focus-head .state.failed { color: #B42318; }
.focus-head .state.coding, .focus-head .state.testing, .focus-head .state.debugging { color: #9C4A0C; }
.desc { color: #1D2027; font-size: .98rem; margin: .1rem 0 .5rem; }
.attempts { display: flex; flex-wrap: wrap; gap: .35rem; margin: 0 0 .5rem; }
.chip { border-radius: 999px; padding: .1rem .6rem; font-size: .84rem; border: 1.5px solid; }
.chip.ok { color: #1F7A4D; background: #EDF7F1; }
.chip.bad { color: #B42318; background: #FDF3F2; }
.chip.fix { color: #9C4A0C; background: #FFF3E8; }
.test-row { display: grid; grid-template-columns: 6.2rem 1fr; gap: .6rem; padding: .45rem 0;
  border-bottom: 1px solid #EEF0F3; }
.test-row .st { font-weight: 700; color: #6B7180; }
.test-row .st.ok { color: #1F7A4D; } .test-row .st.bad { color: #B42318; }
.test-row code { font-size: .82rem; white-space: pre-wrap; }

/* runs list: each run is a full-width button */
.st-key-runlist button { justify-content: flex-start; text-align: left; min-height: 3.2rem;
  white-space: normal; line-height: 1.3; }
.st-key-runlist button p, .st-key-presets button p { text-align: left; }
.st-key-runlist button > div, .st-key-presets button > div { justify-content: flex-start;
  text-align: left; width: 100%; }
.st-key-runlist [data-testid="stMarkdownContainer"],
.st-key-presets [data-testid="stMarkdownContainer"] { text-align: left; width: 100%; }

/* example cards on the build page */
.st-key-presets button { min-height: 5.4rem; white-space: normal; text-align: left;
  justify-content: flex-start; align-items: flex-start; }
.st-key-presets button p { text-align: left; }

/* the approval gate: the one place maroon gets loud */
.gate { border: 2px solid #861F41; background: #FBF2F5; border-radius: 10px; padding: .9rem 1.2rem;
  margin: .3rem 0 .6rem; }
.gate p { margin: 0 0 .3rem; }
.gate h3 { color: #861F41; margin: 0 0 .3rem; }
.gate .facts { display: flex; flex-wrap: wrap; gap: 1.8rem; margin: .5rem 0; }
.gate .facts b { font-size: 1.35rem; display: block; font-variant-numeric: tabular-nums; }

/* simple single-series bars (one measure per chart; no dual axes) */
.bars .row { display: grid; grid-template-columns: 9rem 1fr 6.5rem; align-items: center;
  gap: .6rem; margin: .25rem 0; }
.bars .label { font-weight: 600; color: #1D2027; }
.bars .track { height: 14px; background: transparent; }
.bars .bar { height: 14px; background: #861F41; border-radius: 0 4px 4px 0; min-width: 2px; }
.bars .val { font-variant-numeric: tabular-nums; color: #4A505C; text-align: right; }

.small { font-size: .88rem; color: #4A505C; }
button[kind="primary"] { background: #861F41 !important; border-color: #861F41 !important; }
button:focus-visible { outline: 3px solid #E5751F !important; outline-offset: 2px; }
</style>
"""
