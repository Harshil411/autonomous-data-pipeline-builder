"""Benchmark results (requirement 26): success rate, retries per stage, time and cost."""

from __future__ import annotations

import json
from html import escape

import pandas as pd
import streamlit as st

from adpb.config import REPO_ROOT
from app.components import html, page_title

RESULTS = REPO_ROOT / "benchmarks" / "results"


def main() -> None:
    page_title("Eight dirty-data requests, scored on the final table",
               "A run counts as a success only if the loaded table holds the right rows, not "
               "merely because it finished.")
    files = sorted(RESULTS.glob("bench-*.json"), reverse=True) if RESULTS.exists() else []
    if not files:
        st.info("No benchmark results yet. Run `uv run adpb bench` (it calls the model and "
                "costs money; about $0.10-0.40 per request).")
        return
    pick = st.selectbox("Result file", [f.name for f in files])
    report = json.loads((RESULTS / pick).read_text())
    s = report["summary"]
    items = [("success rate", f"{s['success_rate']:.0%}"),
             ("successful runs", f"{s['successful_runs']} of {s['runs']}"),
             ("retries per stage", s["mean_retries_per_stage"]),
             ("seconds per run", s["mean_seconds"]),
             ("cost per run", f"${s['mean_usd']:.3f}"), ("total cost", f"${s['total_usd']:.3f}")]
    html('<div class="nums">' + "".join(f'<div><div class="n">{v}</div><div class="l">{k}</div>'
                                        f'</div>' for k, v in items) + "</div>")

    # Pass/fail is status, so it is a labelled table, not a coloured chart.
    st.dataframe(pd.DataFrame([{
        "request": r["name"], "run": r["repeat"],
        "result": "passed" if r["success"] else "failed",
        "why": r["why"], "rows": r["rows"], "stages": r["stages"],
        "retries": sum(r["retries"].values()), "replans": r["replans"],
        "seconds": r["seconds"], "cost ($)": r["usd"]} for r in report["runs"]]),
        hide_index=True, width="stretch")

    # One measure, one series, one axis: cost per run.
    top = max((r["usd"] for r in report["runs"]), default=0) or 1
    rows = "".join(
        f'<div class="row" title="{escape(r["name"])} run {r["repeat"]}: ${r["usd"]:.4f}, '
        f'{r["tokens"]:,} tokens, {r["seconds"]}s"><div class="label">{escape(r["name"])}</div>'
        f'<div class="track"><div class="bar" style="width:{r["usd"] / top * 100:.1f}%"></div>'
        f'</div><div class="val">${r["usd"]:.3f}</div></div>' for r in report["runs"])
    st.markdown("**Cost per run**")
    html(f'<div class="bars">{rows}</div>')


main()
