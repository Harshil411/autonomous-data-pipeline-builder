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
@import url('https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible+Next:wght@400;600;700&family=JetBrains+Mono:wght@400;600&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea, select {
  font-family: 'Atkinson Hyperlegible Next', 'Atkinson Hyperlegible', system-ui, sans-serif;
}
code, pre, .stCode * { font-family: 'JetBrains Mono', ui-monospace, monospace !important; }
.block-container { padding-top: 1.6rem; max-width: 1480px; }
h1, h2, h3 { color: #1D2027; letter-spacing: -0.01em; }
h1 { font-size: 2.1rem; font-weight: 700; }
h2 { font-size: 1.45rem; font-weight: 700; }
h3 { font-size: 1.15rem; font-weight: 700; }
p, li { font-size: 1.02rem; line-height: 1.55; }
.muted { color: #4A505C; }

/* header band */
.adpb-head { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem;
  border-bottom: 2px solid #861F41; padding-bottom: .5rem; margin-bottom: 1rem; }
.adpb-head .name { font-size: 1.6rem; font-weight: 700; color: #861F41; }
.adpb-head .tag { color: #4A505C; font-size: 1rem; }

/* status pill: always a word, never colour alone */
.pill { display: inline-block; padding: .18rem .7rem; border-radius: 999px; font-weight: 700;
  font-size: .95rem; border: 1.5px solid currentColor; white-space: nowrap; }
.pill.running { color: #9C4A0C; background: #FFF3E8; }
.pill.awaiting_approval { color: #861F41; background: #FBF2F5; }
.pill.succeeded { color: #1F7A4D; background: #EDF7F1; }
.pill.failed, .pill.budget_exceeded, .pill.rejected { color: #B42318; background: #FDF3F2; }
.pill.not { color: #4A505C; background: #FFFFFF; }

/* key numbers */
.nums { display: flex; flex-wrap: wrap; gap: 2.2rem; margin: .4rem 0 1rem; }
.nums .n { font-size: 1.7rem; font-weight: 700; color: #1D2027; font-variant-numeric: tabular-nums; }
.nums .l { font-size: .92rem; color: #4A505C; }

/* budget meters */
.meter { margin: .2rem 0 .55rem; }
.meter .top { display: flex; justify-content: space-between; font-size: .9rem; color: #4A505C; }
.meter .track { height: 8px; background: #E6E8EC; border-radius: 4px; overflow: hidden; }
.meter .fill { height: 100%; background: #861F41; border-radius: 4px; }
.meter .fill.hot { background: #B42318; }

/* activity timeline: rows with an agent rule, not a stack of identical cards */
.act { border-left: 4px solid #6B7180; padding: .45rem .8rem; margin: 0 0 .35rem;
  background: #FFFFFF; border-radius: 0 6px 6px 0; }
.act .who { font-weight: 700; font-size: .92rem; }
.act .what { font-size: 1.02rem; color: #1D2027; }
.act .why { font-size: .88rem; color: #4A505C; margin-top: .15rem; white-space: pre-wrap;
  max-height: 4.6em; overflow: hidden; }
.act.good { background: #F6FBF8; } .act.bad { background: #FEF7F6; } .act.wait { background: #FFFBF2; }
.act .mark { font-weight: 700; margin-right: .35rem; }
.act.good .mark { color: #1F7A4D; } .act.bad .mark { color: #B42318; } .act.wait .mark { color: #8A5A00; }

/* the approval gate: the one place maroon gets loud */
.gate { border: 2px solid #861F41; background: #FBF2F5; border-radius: 10px; padding: 1rem 1.2rem;
  margin: .6rem 0 1rem; }
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

.check-ok { color: #1F7A4D; font-weight: 700; } .check-bad { color: #B42318; font-weight: 700; }
.small { font-size: .88rem; color: #4A505C; }
button[kind="primary"] { background: #861F41 !important; border-color: #861F41 !important; }
button:focus-visible { outline: 3px solid #E5751F !important; outline-offset: 2px; }
</style>
"""
