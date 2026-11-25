"""Turn a run's event log into what the screen shows.

`build_view(events)` is pure: the same events always give the same view. Live mode passes
every event so far; replay mode passes the first N. Nothing here touches the network,
the model or the database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ACTIVE = {"coding", "testing", "debugging"}
AGENT_NAMES = {"planner": "Planner", "coder": "Stage Coder", "tester": "Tester",
               "debugger": "Debugger", "validator": "Validator", "orchestrator": "Orchestrator",
               "you": "You"}


@dataclass
class StageView:
    id: str
    kind: str
    inputs: list[str]
    language: str
    description: str
    output: dict
    status: str = "pending"
    attempt: int = 0
    tests: list[dict] = field(default_factory=list)
    test_runs: list[dict] = field(default_factory=list)
    code_versions: list[dict] = field(default_factory=list)
    rows_out: int | None = None
    rows_quarantined: int = 0
    last_error: str | None = None


@dataclass
class Activity:
    seq: int
    ts: float
    agent: str
    text: str
    stage: str | None = None
    tone: str = "neutral"  # neutral | good | bad | wait
    detail: str = ""


@dataclass
class RunView:
    run_id: str = ""
    status: str = "not started"
    request: str = ""
    target_table: str = ""
    rationale: str = ""
    models: dict = field(default_factory=dict)
    budget_limits: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    stages: dict[str, StageView] = field(default_factory=dict)
    activity: list[Activity] = field(default_factory=list)
    quality: list[dict] = field(default_factory=list)
    approval_request: dict | None = None
    approval: dict | None = None
    load: dict | None = None
    cost: dict = field(default_factory=lambda: {"usd": 0.0, "tokens": 0, "calls": 0,
                                                "tool_calls": 0, "by_agent": {}})
    budget: dict = field(default_factory=dict)
    current_stage: str | None = None
    error: str | None = None
    finished: bool = False
    started_ts: float | None = None
    last_ts: float | None = None
    replans: int = 0

    @property
    def elapsed(self) -> float:
        return (self.last_ts - self.started_ts) if self.started_ts and self.last_ts else 0.0


def _n(x: int | None) -> str:
    return f"{x:,}" if isinstance(x, int) else "?"


def _set_plan(v: RunView, plan: dict) -> None:
    old = v.stages
    v.stages = {}
    for s in plan["stages"]:
        keep = old.get(s["id"])
        v.stages[s["id"]] = keep if keep and keep.status == "passed" else StageView(
            id=s["id"], kind=s["kind"], inputs=s["inputs"], language=s.get("language", "python"),
            description=s["description"], output=s["output"])
    v.rationale = plan.get("rationale", "")
    v.target_table = plan.get("target_table", v.target_table)


def build_view(events: list[dict]) -> RunView:
    v = RunView()
    for e in events:
        k, seq, ts = e["kind"], e["seq"], e["ts"]
        v.run_id = e.get("run_id", v.run_id)
        v.started_ts = v.started_ts or ts
        v.last_ts = ts
        def add(agent, text, tone="neutral", stage=None, detail="", _seq=seq, _ts=ts):
            v.activity.append(Activity(_seq, _ts, agent, text, stage, tone, detail))

        if k == "run_started":
            v.status, v.request = "running", e["request"]
            v.target_table, v.models = e.get("target_table", ""), e.get("models", {})
            v.budget_limits = e.get("budget", {})
            add("orchestrator", "Run started", detail=e["request"])
        elif k == "run_resumed":
            v.status, v.finished, v.error = "running", False, None
            add("orchestrator", f"Resumed from {', '.join(e.get('pending', []))}")
        elif k == "source_loaded":
            v.sources[e["dataset"]] = {"rows": e["rows"], "columns": e["columns"],
                                       "bad_lines": e.get("bad_lines", 0)}
            extra = f", {e['bad_lines']} malformed line(s) set aside" if e.get("bad_lines") else ""
            add("orchestrator", f"Loaded {e['dataset']}: {_n(e['rows'])} rows, "
                                f"{len(e['columns'])} columns{extra}")
        elif k == "agent_call":
            c = v.cost
            c["usd"] += e.get("usd", 0.0)
            c["tokens"] += e.get("tokens", 0)
            c["calls"] += 1
            a = c["by_agent"].setdefault(e["agent"], {"calls": 0, "tokens": 0, "usd": 0.0})
            a["calls"] += 1
            a["tokens"] += e.get("tokens", 0)
            a["usd"] += e.get("usd", 0.0)
            v.budget = e.get("budget", v.budget)
        elif k == "tool_call":
            v.cost["tool_calls"] += 1
        elif k == "plan_created":
            _set_plan(v, e["plan"])
            add("planner", f"Proposed a {len(e['plan']['stages'])}-stage plan",
                detail=e["plan"].get("rationale", ""))
        elif k == "replanned":
            _set_plan(v, e["plan"])
            v.replans += 1
            add("planner", f"Revised the plan after {e['failed_stage']} failed", "wait",
                detail="changed: " + ", ".join(e.get("changed", [])))
        elif k == "tests_written" and e["stage"] in v.stages:
            s = v.stages[e["stage"]]
            s.tests = e["tests"]
            verb = "Rewrote" if e.get("revised") else "Wrote"
            add("tester", f"{verb} {len(e['tests'])} tests for {s.id}, before any code existed",
                stage=s.id)
        elif k == "code_written" and e["stage"] in v.stages:
            s = v.stages[e["stage"]]
            s.code_versions.append({k2: e.get(k2) for k2 in
                                    ("author", "version", "note", "lines", "code", "path")})
            if e["author"] == "debugger":
                add("debugger", f"Patched {s.id}: {e.get('note', '')}", stage=s.id)
            else:
                again = " again, using the quality feedback" if e.get("note") else ""
                add("coder", f"Wrote {s.id}{again} ({e.get('lines', '?')} lines of "
                             f"{s.language})", stage=s.id)
        elif k == "test_result" and e["stage"] in v.stages:
            s = v.stages[e["stage"]]
            s.test_runs.append(e)
            where = "the sample" if e["scope"] == "sample" else "all the data"
            if e["ok"]:
                add("tester", f"{s.id} passed every test on {where} "
                              f"({_n(e['rows_out'])} rows out)", "good", s.id)
            else:
                failed = [t["name"] for t in e.get("tests", []) if not t["passed"]]
                why = f"{len(failed)} test(s) failed" if failed else e.get("error_type")
                add("tester", f"{s.id} failed on {where}: {why}", "bad", s.id,
                    detail=e.get("error", ""))
        elif k == "stage_status" and e["stage"] in v.stages:
            s = v.stages[e["stage"]]
            s.status = e["status"]
            s.attempt = e.get("attempt", s.attempt) or s.attempt
            if e["status"] in ACTIVE:
                v.current_stage = s.id
            elif e["status"] == "passed":
                s.rows_out, s.rows_quarantined = e.get("rows_out"), e.get("rows_quarantined", 0)
                s.last_error = None
                v.current_stage = None
                add("orchestrator", f"Committed {s.id} to git after attempt {s.attempt}", "good",
                    s.id)
            elif e["status"] == "failed":
                s.last_error = e.get("error")
                v.current_stage = None
                add("orchestrator", f"{s.id} failed after {s.attempt} attempt(s)", "bad", s.id,
                    detail=e.get("error") or "")
            elif e["status"] == "pending":
                v.current_stage = None
        elif k == "quality_result":
            v.quality.append(e)
            passed = e["total"] - e["failed"]
            add("validator", f"Ran {e['total']} quality checks: {passed} passed, "
                             f"{e['failed']} failed", "good" if not e["failed"] else "bad")
        elif k == "quality_routed":
            for sid in e.get("pending", []):
                if sid in v.stages:
                    v.stages[sid].status = "pending"
            add("orchestrator", f"Sent the failed checks back to {e['stage']}", "wait",
                e["stage"], detail=e.get("feedback", ""))
        elif k == "approval_requested":
            v.status, v.approval_request = "awaiting_approval", e
            add("you", f"Waiting for your approval to load {_n(e['rows'])} rows into "
                       f"{e['table']}", "wait")
        elif k == "approval_decision":
            v.approval = e
            verb = "Approved" if e["approved"] else "Rejected"
            add("you", f"{verb} by {e['by']}", "good" if e["approved"] else "bad",
                detail=e.get("note", ""))
        elif k == "load_finished":
            v.load = e
            add("orchestrator", f"Loaded {_n(e['rows'])} rows into {e['table']}", "good")
        elif k == "budget_exceeded":
            add("orchestrator", f"Stopped: {e['error']}", "bad")
        elif k == "run_finished":
            v.status, v.error, v.finished = e["status"], e.get("error"), True
            v.current_stage = None
            tone = "good" if e["status"] == "succeeded" else "bad"
            add("orchestrator", f"Run finished: {e['status'].replace('_', ' ')}", tone,
                detail=e.get("error") or "")
    return v


def agent_label(agent: str) -> str:
    return AGENT_NAMES.get(agent, agent.title())


def summary_numbers(v: RunView) -> dict[str, Any]:
    passed = sum(1 for s in v.stages.values() if s.status == "passed")
    retries = sum(max(0, s.attempt - 1) for s in v.stages.values())
    return {"stages": f"{passed}/{len(v.stages)}", "retries": retries,
            "cost": f"${v.cost['usd']:.3f}", "tokens": f"{v.cost['tokens']:,}",
            "calls": v.cost["calls"], "elapsed": f"{v.elapsed:.0f}s"}
