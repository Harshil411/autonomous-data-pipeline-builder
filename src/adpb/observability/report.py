"""The run report (requirement 25).

"A run report should be produced which includes the plan, status of each stage, number of
retries for each stage, quality check results, number of input and output rows and cost."
Built from the orchestrator state plus the event log, written as JSON (for the UI and
benchmarks) and Markdown (for people).
"""

from __future__ import annotations

from typing import Any

from adpb.observability.events import EventLog


def build_report(state: dict[str, Any], events: list[dict]) -> dict[str, Any]:
    plan = state.get("plan") or {}
    stages_state = state.get("stages") or {}
    datasets = state.get("datasets") or {}
    totals = EventLog.totals(events)
    order = [s["id"] for s in plan.get("stages", [])]
    stages = []
    for sid in order:
        rec = stages_state.get(sid, {})
        attempts = rec.get("attempts", 0)
        stages.append({
            "id": sid, "status": rec.get("status", "pending"), "attempts": attempts,
            "retries": max(0, attempts - 1), "rows_out": rec.get("rows_out"),
            "rows_quarantined": rec.get("rows_quarantined", 0),
            "code_versions": rec.get("code_version", 0),
            "fixed_by_debugger": bool(rec.get("history")),
            "last_error": rec.get("last_error") if rec.get("status") != "passed" else None,
        })
    checks = state.get("quality") or []
    failures = [c for c in checks if not c.get("passed")]
    quarantined = sum(s["rows_quarantined"] or 0 for s in stages)
    quarantined += sum(e.get("bad_lines", 0) for e in events if e["kind"] == "source_loaded")
    final = plan.get("final_dataset")
    ts = [e["ts"] for e in events]
    return {
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "error": state.get("error"),
        "request": plan.get("request"),
        "target_table": plan.get("target_table"),
        "plan": [{k: s[k] for k in ("id", "kind", "inputs", "language", "description")}
                 for s in plan.get("stages", [])],
        "plan_rationale": plan.get("rationale", ""),
        "replans": state.get("replans_used", 0),
        "stages": stages,
        "quality": {"rounds": state.get("quality_round", 0), "total": len(checks),
                    "passed": len(checks) - len(failures), "failed": len(failures),
                    "failures": failures},
        "rows": {
            "input": {k: v["rows"] for k, v in datasets.items() if k.startswith("raw.")},
            "output": (datasets.get(final) or {}).get("rows") if final else None,
            "quarantined": quarantined,
        },
        "cost": {"usd": round(totals["usd"], 6), "tokens": totals["tokens"],
                 "model_calls": sum(a["calls"] for a in totals["by_agent"].values()),
                 "tool_calls": totals["tool_calls"], "by_agent": totals["by_agent"]},
        "duration_s": round(max(ts) - min(ts), 1) if ts else 0.0,
        "approval": state.get("approval"),
        "load": state.get("load"),
        "staging": {k: v for k, v in (state.get("staging") or {}).items() if k != "preview"},
    }


def render_markdown(r: dict[str, Any]) -> str:
    lines = [f"# Run report `{r['run_id']}`", "",
             f"**Status:** {r['status']}" + (f" - {r['error']}" if r.get("error") else ""),
             f"**Request:** {r.get('request') or '-'}",
             f"**Target table:** `{r.get('target_table')}`", "", "## Plan", ""]
    for s in r["plan"]:
        lines.append(f"- `{s['id']}` ({s['kind']}, {s['language']}) <- {', '.join(s['inputs'])}:"
                     f" {s['description']}")
    lines += ["", "## Stages", "", "| stage | status | retries | rows out | quarantined |",
              "|---|---|---|---|---|"]
    for s in r["stages"]:
        lines.append(f"| `{s['id']}` | {s['status']} | {s['retries']} | {s['rows_out']} | "
                     f"{s['rows_quarantined']} |")
    q = r["quality"]
    lines += ["", "## Quality", "", f"{q['passed']} of {q['total']} checks passed "
              f"({q['rounds']} round(s))."]
    for f in q["failures"]:
        lines.append(f"- FAILED `{f['rule_id']}`: {f['observed']}")
    rows, cost = r["rows"], r["cost"]
    lines += ["", "## Rows", "", f"- input: {rows['input']}", f"- output: {rows['output']}",
              f"- quarantined: {rows['quarantined']}", "", "## Cost", "",
              f"- ${cost['usd']:.4f}, {cost['tokens']:,} tokens, {cost['model_calls']} model "
              f"calls, {cost['tool_calls']} tool calls, {r['duration_s']}s"]
    for agent, a in cost["by_agent"].items():
        lines.append(f"  - {agent}: {a['calls']} calls, {a['tokens']:,} tokens, ${a['usd']:.4f}")
    if r.get("approval"):
        a = r["approval"]
        lines += ["", "## Approval", "",
                  f"{'Approved' if a.get('approved') else 'Rejected'} by {a.get('by')}"
                  + (f": {a['note']}" if a.get("note") else "")]
    return "\n".join(lines) + "\n"
