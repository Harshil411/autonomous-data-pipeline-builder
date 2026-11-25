"""The live pipeline schematic: sources -> stages -> approval gate -> warehouse.

Rendered as a self-contained SVG so it looks the same in Streamlit, in a screenshot and in
the PDF. Columns are dependency depth; every node states its status in words, never by
colour alone. The one animation (a ring on the stage being worked on) is switched off
for people who prefer reduced motion.
"""

from __future__ import annotations

from html import escape

from app.viewmodel import ACTIVE, RunView

W, H, GX, GY, PAD = 232, 76, 56, 18, 10

STYLE = """
<style>
  .sch text { font-family: inherit; }
  .sch .title { font-size: 18px; font-weight: 700; fill: #1D2027; }
  .sch .sub { font-size: 15px; fill: #4A505C; }
  .sch .edge { fill: none; stroke: #B9BEC8; stroke-width: 2; }
  .sch .edge.done { stroke: #1F7A4D; }
  .sch .node rect.box { fill: #FFFFFF; stroke: #C9CDD5; stroke-width: 1.5; }
  .sch .node.source rect.box { fill: #EEF0F3; stroke: #C9CDD5; }
  .sch .node.passed rect.box { stroke: #1F7A4D; stroke-width: 2; }
  .sch .node.failed rect.box { stroke: #B42318; stroke-width: 2.5; fill: #FDF3F2; }
  .sch .node.running rect.box { stroke: #E5751F; stroke-width: 2.5; }
  .sch .node.gate rect.box { stroke: #861F41; stroke-width: 2.5; fill: #FBF2F5; }
  .sch .node.loaded rect.box { stroke: #861F41; stroke-width: 2.5; fill: #861F41; }
  .sch .node.loaded .title, .sch .node.loaded .sub { fill: #FFFFFF; }
  .sch .bar { fill: #C9CDD5; }
  .sch .passed .bar { fill: #1F7A4D; } .sch .failed .bar { fill: #B42318; }
  .sch .running .bar { fill: #E5751F; } .sch .gate .bar, .sch .loaded .bar { fill: #861F41; }
  .sch .ring { fill: none; stroke: #E5751F; stroke-width: 2; opacity: .55;
               animation: pulse 1.6s ease-in-out infinite; transform-box: fill-box;
               transform-origin: center; }
  @keyframes pulse { 0%,100% { opacity: .15; } 50% { opacity: .7; } }
  @media (prefers-reduced-motion: reduce) { .sch .ring { animation: none; opacity: .5; } }
</style>
"""


def _depths(v: RunView) -> dict[str, int]:
    depth = {name: 0 for name in v.sources}
    for s in v.stages.values():  # stages arrive in plan order; recompute until stable
        for name in s.inputs:
            depth.setdefault(name, 0)
    for _ in range(len(v.stages) + 1):
        for s in v.stages.values():
            depth[s.id] = 1 + max((depth.get(i, 0) for i in s.inputs), default=0)
    return depth


def _stage_text(status: str, s) -> str:
    if status == "passed":
        q = f", {s.rows_quarantined:,} set aside" if s.rows_quarantined else ""
        return f"passed: {s.rows_out:,} rows{q}" if s.rows_out is not None else "passed"
    if status == "failed":
        return f"failed after {s.attempt} attempt(s)"
    if status in ACTIVE:
        return f"{status}" + (f", attempt {s.attempt}" if s.attempt else "")
    return "waiting"


def render_schematic(v: RunView) -> str:
    depth = _depths(v)
    gate_col = max(depth.values(), default=0) + 1
    columns: dict[int, list[str]] = {}
    for name in [*v.sources, *v.stages]:  # sources in load order, stages in plan order
        if name in depth:
            columns.setdefault(depth[name], []).append(name)
    # Crossing reduction: order each column by the mean row of its inputs (barycentre).
    row_of: dict[str, float] = {n: i for i, n in enumerate(columns.get(0, []))}
    for col in sorted(c for c in columns if c > 0):
        def centre(name: str) -> float:
            ins = [row_of[i] for i in v.stages[name].inputs if i in row_of]
            return sum(ins) / len(ins) if ins else 0.0
        columns[col].sort(key=centre)
        row_of.update({n: i for i, n in enumerate(columns[col])})
    columns[gate_col] = ["__warehouse__"]
    rows = max(len(c) for c in columns.values())
    width = PAD * 2 + (gate_col + 1) * W + gate_col * GX
    height = PAD * 2 + rows * H + (rows - 1) * GY
    pos: dict[str, tuple[int, int]] = {}
    for col, names in columns.items():
        offset = (rows - len(names)) * (H + GY) / 2
        for i, name in enumerate(names):
            pos[name] = (PAD + col * (W + GX), int(PAD + offset + i * (H + GY)))

    parts = [f'<svg class="sch" viewBox="0 0 {width} {height}" width="100%" role="img" '
             f'aria-label="Pipeline schematic, run status {escape(v.status)}" '
             f'xmlns="http://www.w3.org/2000/svg">', STYLE]

    def edge(a: str, b: str, done: bool) -> None:
        (x1, y1), (x2, y2) = pos[a], pos[b]
        x1, y1, x2, y2 = x1 + W, y1 + H / 2, x2, y2 + H / 2
        mid = (x1 + x2) / 2
        parts.append(f'<path class="edge{" done" if done else ""}" '
                     f'd="M{x1},{y1} C{mid},{y1} {mid},{y2} {x2},{y2}"/>')

    final = [s for s in v.stages.values() if not any(s.id in o.inputs for o in v.stages.values())]
    for s in v.stages.values():
        for i in s.inputs:
            if i in pos:
                upstream_done = i in v.sources or v.stages.get(i) and v.stages[i].status == "passed"
                edge(i, s.id, bool(upstream_done))
    for s in final:
        edge(s.id, "__warehouse__", s.status == "passed")

    def node(name: str, cls: str, title: str, sub: str) -> None:
        x, y = pos[name]
        parts.append(f'<g class="node {cls}" transform="translate({x},{y})">')
        if cls == "running":
            parts.append(f'<rect class="ring" x="-5" y="-5" width="{W + 10}" height="{H + 10}" '
                         f'rx="12"/>')
        parts.append(f'<rect class="box" width="{W}" height="{H}" rx="8"/>')
        parts.append(f'<rect class="bar" width="6" height="{H}" rx="3"/>')
        parts.append(f'<text class="title" x="18" y="31">{escape(title[:22])}</text>')
        parts.append(f'<text class="sub" x="18" y="57">{escape(sub[:28])}</text></g>')

    for name, info in v.sources.items():
        node(name, "source", name, f"source: {info['rows']:,} rows")
    for s in v.stages.values():
        cls = "running" if s.status in ACTIVE else s.status if s.status in ("passed",
                                                                            "failed") else "pending"
        node(s.id, cls, s.id, _stage_text(s.status, s))
    table = "Final table"  # the stage feeding it often has the same name
    if v.load:
        node("__warehouse__", "loaded", table, f"loaded: {v.load['rows']:,} rows")
    elif v.status == "awaiting_approval":
        node("__warehouse__", "gate", table, "waiting for your approval")
    elif v.approval and not v.approval.get("approved"):
        node("__warehouse__", "failed", table, "load rejected")
    else:
        node("__warehouse__", "pending", table, "loads only after approval")
    parts.append("</svg>")
    return "".join(parts)
