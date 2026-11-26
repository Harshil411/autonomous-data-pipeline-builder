"""The live graph: the centre of the run screen.

    sources -> stages -> [ your approval ] -> database        (the data's path)
                 ^
    ======= Orchestrator (plain code, no AI) =======          (who is in charge)
       |        |          |           |          |
    Planner   Tester   Stage Coder  Debugger  Validator       (the five agents)

One picture answers the three questions a visitor asks: where is the data, who is working
on it, and what stops it reaching the database. It is a single self-contained SVG, so it
looks the same live, in replay and in a screenshot. Every node states its status in words,
never by colour alone. It carries no colours of its own: the colours and the motion come
from `theme.py` through the classes below, so the same SVG serves the dark and light looks.

Classes the stylesheet relies on (and the tests check):
    node stage live|passed|failed|pending     node source
    node gate locked|needed|open|rejected     node db empty|loaded
    edge [done|live|flow|blocked]             beam (Orchestrator -> what is being worked on)
    agent live|done|idle                      tether (Orchestrator -> a resting agent)
"""

from __future__ import annotations

from html import escape

from app.viewmodel import ACTIVE, LANE, RunView, agent_label, agent_states

# Sizes in SVG units (the SVG scales to the page width). W x H is a source or stage node.
W, H, GX, GY, PAD = 204, 78, 60, 22, 26
GATE_W, GATE_H, DB_W = 136, 104, 184
BAR_H, AGENT_H, AGENT_GAP = 54, 66, 20
RISE = 62   # vertical room between the data's path and the Orchestrator bar
DROP = 46   # vertical room between the Orchestrator bar and the agents
AGENT_MIN_W = 180  # an agent card is never narrower than this, so its two lines of text fit
PLAN = "__plan__"  # placeholder node shown until the Planner has proposed the stages

# 24x24 stroke icons, one per agent (drawn here so the app needs no icon font or network).
ICONS = {
    "planner": '<path d="M4 6h10M4 12h16M4 18h7"/>',
    "tester": '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8.5 12.5l2.5 2.5 '
              '4.5-5.5"/>',
    "coder": '<path d="M9 7l-5 5 5 5M15 7l5 5-5 5"/>',
    "debugger": '<circle cx="12" cy="12" r="3.5"/><path d="M12 3v4M12 17v4M3 12h4M17 12h4"/>',
    "validator": '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/>',
}

# Gradients for the glows and the gate. The stops carry classes, not colours: the stylesheet
# gives them the active look's colours, so this SVG is identical in Night and in Day.
DEFS = (
    '<defs>'
    '<radialGradient id="glow-live"><stop class="s-live in" offset="0"/>'
    '<stop class="s-live out" offset="1"/></radialGradient>'
    '<radialGradient id="glow-gate"><stop class="s-gate in" offset="0"/>'
    '<stop class="s-gate out" offset="1"/></radialGradient>'
    '<radialGradient id="glow-done"><stop class="s-done in" offset="0"/>'
    '<stop class="s-done out" offset="1"/></radialGradient>'
    '<linearGradient id="gate-fill" x1="0" y1="0" x2="1" y2="1">'
    '<stop class="s-gate-1" offset="0"/><stop class="s-gate-2" offset="1"/></linearGradient>'
    '<marker id="tip" orient="auto" markerWidth="7" markerHeight="7" refX="1" refY="3.5">'
    '<path d="M0,0 L7,3.5 L0,7 Z" fill="context-stroke"/></marker>'
    '</defs>')


def _depths(v: RunView) -> dict[str, int]:
    """Column of every node: sources are 0, a stage is one more than its deepest input."""
    depth = {name: 0 for name in v.sources}
    for s in v.stages.values():
        for name in s.inputs:
            depth.setdefault(name, 0)
    for _ in range(len(v.stages) + 1):  # stages arrive in plan order; repeat until stable
        for s in v.stages.values():
            depth[s.id] = 1 + max((depth.get(i, 0) for i in s.inputs), default=0)
    return depth


def _stage_words(v: RunView, s, first_pending: str | None) -> str:
    limit = v.budget_limits.get("max_retries_per_stage", 3)
    if s.status == "passed":
        return f"Passed, {s.rows_out:,} rows" if s.rows_out is not None else "Passed"
    if s.status == "failed":
        return f"Failed after {s.attempt} tries"
    if s.status == "coding":
        return "Being coded"
    if s.status == "debugging":
        return f"Being fixed, try {max(s.attempt, 1)} of {limit}"
    if s.status == "testing":
        return "Being tested" if s.tests else "Tests being written"
    return "Up next" if s.id == first_pending else "Waiting"


def _agent_words(v: RunView, agent: str, state: str) -> str:
    if state == "working":
        return "working now"
    calls = v.cost["by_agent"].get(agent, {}).get("calls", 0)
    if state == "done":  # plain words here; tokens and cost per agent are under Details
        return {1: "worked once", 2: "worked twice"}.get(calls, f"worked {calls} times")
    return "waiting its turn"


def _orchestrator_words(v: RunView) -> str:
    if v.finished:
        re = v.replans
        redesign = f" {re} stage{'s' if re != 1 else ''} redesigned along the way." if re else ""
        return f"finished.{redesign} Every step is recorded and can be replayed."
    if v.status == "awaiting_approval":
        return "has staged the result and is waiting. It cannot approve on your behalf."
    return ("plain code, no AI. It decides which agent works next and how many tries each "
            "stage gets.")


def render_graph(v: RunView) -> str:
    """The whole graph as one SVG string ('' before any source has been read)."""
    if not v.stages and not v.sources:
        return ""
    depth = _depths(v)
    columns: dict[int, list[str]] = {}
    for name in [*v.sources, *v.stages]:  # sources in load order, stages in plan order
        if name in depth:
            columns.setdefault(depth[name], []).append(name)
    # Fewer crossing lines: order each column by the average row of its inputs.
    row_of: dict[str, float] = {n: i for i, n in enumerate(columns.get(0, []))}
    for col in sorted(c for c in columns if c > 0):
        def centre(name: str) -> float:
            ins = [row_of[i] for i in v.stages[name].inputs if i in row_of]
            return sum(ins) / len(ins) if ins else 0.0
        columns[col].sort(key=centre)
        row_of.update({n: i for i, n in enumerate(columns[col])})

    planning = not v.stages  # the first seconds: sources are read, the plan is not written
    if planning:
        columns[1] = [PLAN]  # hold the stages' place, so the picture does not jump later
    last_col = max(columns, default=0)
    rows = max(len(c) for c in columns.values())
    flow_h = max(rows * H + (rows - 1) * GY, GATE_H)
    top = PAD + 8
    # The picture is at least wide enough for five readable agent cards. A short pipeline
    # (or one with no stages yet) is centred in that width instead of shrinking the cards.
    natural = PAD + (last_col + 1) * (W + GX) + GATE_W + GX + DB_W + PAD
    width = max(natural, 2 * PAD + 5 * AGENT_MIN_W + 4 * AGENT_GAP)
    left = PAD + (width - natural) / 2  # x of the first column
    gate_x = left + (last_col + 1) * (W + GX)
    db_x = gate_x + GATE_W + GX
    mid_y = top + flow_h / 2
    bar_y = top + flow_h + RISE
    agents_y = bar_y + BAR_H + DROP
    height = agents_y + AGENT_H + PAD
    agent_w = (width - 2 * PAD - 4 * AGENT_GAP) / 5

    pos: dict[str, tuple[float, float]] = {}
    for col, names in columns.items():
        offset = (flow_h - (len(names) * H + (len(names) - 1) * GY)) / 2
        for i, name in enumerate(names):
            pos[name] = (left + col * (W + GX), top + offset + i * (H + GY))

    loaded = bool(v.load)
    rejected = bool(v.approval and not v.approval.get("approved"))
    gate_cls = ("open" if loaded else "rejected" if rejected else
                "needed" if v.status == "awaiting_approval" else "locked")
    states = agent_states(v)
    working = next((a for a, st in states.items() if st == "working"), None)
    current = v.stages.get(v.current_stage) if v.current_stage else None
    live_stage = current if current is not None and current.status in ACTIVE else None
    first_pending = next((s.id for s in v.stages.values()
                          if s.status not in ("passed", *ACTIVE)), None)

    out = [f'<svg class="graph" viewBox="0 0 {width:.0f} {height:.0f}" width="100%" '
           f'style="max-width:{width:.0f}px" role="img" xmlns="http://www.w3.org/2000/svg" '
           f'aria-label="Pipeline graph, run status {escape(v.status)}">', DEFS]

    # 1. glows behind whatever matters right now
    def glow(kind: str, cx: float, cy: float, r: float) -> None:
        out.append(f'<circle class="glow" cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" '
                   f'fill="url(#glow-{kind})"/>')

    if live_stage is not None:
        x, y = pos[live_stage.id]
        glow("live", x + W / 2, y + H / 2, 250)
    if loaded:
        glow("done", db_x + DB_W / 2, mid_y, 260)
    elif not rejected:
        glow("gate", gate_x + GATE_W / 2, mid_y, 170)

    # 2. lines (under the nodes)
    def edge(x1: float, y1: float, x2: float, y2: float, cls: str) -> None:
        mid = (x1 + x2) / 2
        name = f"edge {cls}" if cls else "edge"
        out.append(f'<path class="{name}" d="M{x1:.0f},{y1:.0f} C{mid:.0f},{y1:.0f} '
                   f'{mid:.0f},{y2:.0f} {x2 - 7:.0f},{y2:.0f}" marker-end="url(#tip)"/>')

    for s in v.stages.values():
        for i in s.inputs:
            if i not in pos:
                continue
            (x1, y1), (x2, y2) = pos[i], pos[s.id]
            upstream_done = i in v.sources or (i in v.stages and v.stages[i].status == "passed")
            cls = ("live" if live_stage is not None and s.id == live_stage.id else
                   "done" if upstream_done and s.status == "passed" else "")
            edge(x1 + W, y1 + H / 2, x2, y2 + H / 2, cls)
    if planning:
        px, py = pos[PLAN]
        for name in v.sources:
            x, y = pos[name]
            edge(x + W, y + H / 2, px, py + H / 2, "")
        edge(px + W, py + H / 2, gate_x, mid_y, "")
    finals = [s for s in v.stages.values()
              if not any(s.id in other.inputs for other in v.stages.values())]
    for s in finals:
        x, y = pos[s.id]
        edge(x + W, y + H / 2, gate_x, mid_y,
             "flow" if loaded else "done" if s.status == "passed" else "")
    edge(gate_x + GATE_W, mid_y, db_x, mid_y, "flow" if loaded else "blocked")

    # 3. the Orchestrator's reach: a beam to what is being worked on, tethers to the rest
    def vertical(cls: str, x: float, y1: float, y2: float) -> None:
        out.append(f'<path class="{cls}" d="M{x:.0f},{y1:.0f} L{x:.0f},{y2:.0f}"/>')

    if live_stage is not None and working:
        x, y = pos[live_stage.id]
        vertical("beam", x + W / 2, bar_y, y + H + 6)
    for i, agent in enumerate(LANE):
        cx = PAD + i * (agent_w + AGENT_GAP) + agent_w / 2
        vertical("beam" if agent == working else "tether", cx, bar_y + BAR_H, agents_y)

    # 4. nodes on the data's path
    def node(cls: str, x: float, y: float, w: float, h: float, title: str, sub: str,
             note: str = "", mono: bool = False, centre: bool = False) -> None:
        out.append(f'<g class="node {cls}" transform="translate({x:.0f},{y:.0f})">')
        out.append(f'<rect class="halo" x="-7" y="-7" width="{w + 14:.0f}" '
                   f'height="{h + 14:.0f}" rx="20"/>')
        out.append(f'<rect class="box" width="{w:.0f}" height="{h:.0f}" rx="14"/>')
        tx, anchor = (w / 2, ' text-anchor="middle"') if centre else (18, "")
        ty = h / 2 - (14 if note else 5)
        out.append(f'<text class="title{" mono" if mono else ""}" x="{tx:.0f}" y="{ty:.0f}"'
                   f'{anchor}>{escape(title[:22])}</text>')
        out.append(f'<text class="sub" x="{tx:.0f}" y="{ty + 21:.0f}"{anchor}>'
                   f'{escape(sub[:26])}</text>')
        if note:
            out.append(f'<text class="note" x="{tx:.0f}" y="{ty + 39:.0f}"{anchor}>'
                       f'{escape(note[:28])}</text>')
        out.append("</g>")

    for name, info in v.sources.items():
        x, y = pos[name]
        node("source", x, y, W, H, info.get("label") or name, f"{info['rows']:,} rows")
    if planning:
        x, y = pos[PLAN]
        node("stage pending", x, y, W, H, "Stages", "Being planned")
    for s in v.stages.values():
        x, y = pos[s.id]
        state = ("live" if s.status in ACTIVE else s.status
                 if s.status in ("passed", "failed") else "pending")
        n = s.rows_quarantined
        aside = f"{n:,} row{'s' if n != 1 else ''} set aside" if n else ""
        node(f"stage {state}", x, y, W, H, s.id, _stage_words(v, s, first_pending), aside,
             mono=True)
    gate_words = {"locked": "locked", "needed": "needed now", "open": "given, gate open",
                  "rejected": "refused"}[gate_cls]
    node(f"gate {gate_cls}", gate_x, mid_y - GATE_H / 2, GATE_W, GATE_H, "Your approval",
         gate_words, centre=True)
    if loaded:
        node("db loaded", db_x, mid_y - H / 2, DB_W, H, "Database",
             f"{v.load['rows']:,} rows loaded")
    else:
        node("db empty", db_x, mid_y - H / 2, DB_W, H, "Database", "Nothing loaded")

    # 5. the Orchestrator bar and the five agents under it
    room = int((width - 2 * PAD - 150) / 7.4)  # characters that fit beside the name
    out.append(f'<g class="orch" transform="translate({PAD},{bar_y:.0f})">'
               f'<rect class="box" width="{width - 2 * PAD:.0f}" height="{BAR_H}" rx="14"/>'
               f'<text class="title" x="20" y="{BAR_H / 2 + 5:.0f}">Orchestrator</text>'
               f'<text class="sub" x="124" y="{BAR_H / 2 + 5:.0f}">'
               f'{escape(_orchestrator_words(v)[:room])}</text></g>')
    for i, agent in enumerate(LANE):
        state = {"working": "live", "done": "done", "idle": "idle"}[states[agent]]
        x = PAD + i * (agent_w + AGENT_GAP)
        out.append(f'<g class="agent {state}" transform="translate({x:.0f},{agents_y:.0f})">'
                   f'<rect class="halo" x="-7" y="-7" width="{agent_w + 14:.0f}" '
                   f'height="{AGENT_H + 14}" rx="20"/>'
                   f'<rect class="box" width="{agent_w:.0f}" height="{AGENT_H}" rx="14"/>'
                   f'<g class="icon" transform="translate(16,{AGENT_H / 2 - 12:.0f})">'
                   f'{ICONS[agent]}</g>'
                   f'<text class="title" x="52" y="{AGENT_H / 2 - 3:.0f}">'
                   f'{agent_label(agent)}</text>'
                   f'<text class="sub" x="52" y="{AGENT_H / 2 + 16:.0f}">'
                   f'{escape(_agent_words(v, agent, states[agent]))}</text></g>')
    out.append("</svg>")
    return "".join(out)
