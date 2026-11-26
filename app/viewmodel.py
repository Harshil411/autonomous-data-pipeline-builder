"""Turn a run's event log into what the screen shows.

`build_view(events)` is pure: the same events always give the same view. Live mode passes
every event so far; replay mode passes the first N. Nothing here touches the network,
the model or the database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    source_specs: dict = field(default_factory=dict)  # as requested: name -> kind, path, url
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
    waited: float = 0.0              # seconds spent waiting for a person to decide
    _gate_opened: float | None = None

    @property
    def elapsed(self) -> float:
        """Working time: wall-clock time minus the time spent waiting at the approval gate."""
        if self.started_ts is None or self.last_ts is None:
            return 0.0
        return self.last_ts - self.started_ts - self.waited


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


def _source_label(v: RunView, e: dict) -> str:
    """What to call a source on screen: its file name ("customers.csv"), not the internal
    dataset name ("raw.customers"). An API source has no file, so it is "name (API)"."""
    spec = v.source_specs.get(e.get("source") or "", {})
    if spec.get("path"):
        return str(spec["path"]).replace("\\", "/").rsplit("/", 1)[-1]
    if spec.get("kind") == "api":
        return f"{spec['name']} (API)"
    return e.get("source") or e["dataset"]


def build_view(events: list[dict]) -> RunView:
    v = RunView()
    for e in events:
        k, seq, ts = e["kind"], e["seq"], e["ts"]
        v.run_id = e.get("run_id", v.run_id)
        if v.started_ts is None:
            v.started_ts = ts
        v.last_ts = ts
        def add(agent, text, tone="neutral", stage=None, detail="", _seq=seq, _ts=ts):
            v.activity.append(Activity(_seq, _ts, agent, text, stage, tone, detail))

        if k == "run_started":
            v.status, v.request = "running", e["request"]
            v.source_specs = {src["name"]: src for src in e.get("sources") or []}
            v.target_table, v.models = e.get("target_table", ""), e.get("models", {})
            v.budget_limits = e.get("budget", {})
            add("orchestrator", "Run started", detail=e["request"])
        elif k == "run_resumed":
            v.status, v.finished, v.error = "running", False, None
            add("orchestrator", f"Resumed from {', '.join(e.get('pending', []))}")
        elif k == "source_loaded":
            v.sources[e["dataset"]] = {"rows": e["rows"], "columns": e["columns"],
                                       "bad_lines": e.get("bad_lines", 0),
                                       "label": _source_label(v, e)}
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
        elif k == "test_disputed" and e["stage"] in v.stages:
            add("debugger", f"Disputed test {e['test']} in {e['stage']}; sent back to the "
                            f"Tester, who owns it", "wait", e["stage"], detail=e.get("reason", ""))
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
            v._gate_opened = ts
            add("you", f"Waiting for your approval to load {_n(e['rows'])} rows into "
                       f"{e['table']}", "wait")
        elif k == "approval_decision":
            v.approval = e
            if v._gate_opened is not None:
                v.waited += ts - v._gate_opened
                v._gate_opened = None
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


# -- what a visitor reads from across the table ---------------------------------------------

LANE = ("planner", "tester", "coder", "debugger", "validator")


def now_line(v: RunView) -> tuple[str, str]:
    """(agent, one plain sentence) describing what is happening right now."""
    table = v.target_table or "the database"
    if v.status == "not started":
        return "orchestrator", "Not started"
    if v.status == "succeeded" and v.load:
        return "orchestrator", f"Done: {v.load['rows']:,} rows loaded into {v.load['table']}"
    if v.status == "rejected":
        return "you", f"Rejected: nothing was written to {table}"
    if v.status == "budget_exceeded":
        return "orchestrator", "Stopped at the budget limit; nothing was written"
    if v.status == "failed":
        return "orchestrator", f"Stopped: {(v.error or 'see the activity log')[:140]}"
    if v.status == "awaiting_approval" and v.approval_request:
        req = v.approval_request
        return "you", f"Waiting for you to approve loading {req['rows']:,} rows into {req['table']}"

    limit = v.budget_limits.get("max_retries_per_stage", 3)
    if v.current_stage and v.current_stage in v.stages:
        s = v.stages[v.current_stage]
        if s.status == "coding":
            return "coder", f"The Stage Coder is writing {s.id}"
        if s.status == "debugging":
            return "debugger", f"The Debugger is fixing {s.id} (attempt {s.attempt} of {limit})"
        if s.status == "testing" and not s.tests:
            return "tester", f"The Tester is writing tests for {s.id}"
        if s.status == "testing":
            return "tester", (f"The Tester is running {s.id} in the sandbox "
                              f"(attempt {max(s.attempt, 1)} of {limit})")
    if not v.stages:
        if not v.sources:
            return "orchestrator", "Reading and profiling the sources"
        return "planner", "The Planner is designing the pipeline"
    if any(s.status == "failed" for s in v.stages.values()):
        return "planner", "The Planner is revising the plan"
    pending = [s for s in v.stages.values() if s.status != "passed"]
    if pending:
        return "tester", f"The Tester is writing tests for {pending[0].id}"
    if not v.quality:
        return "validator", "The Validator is checking data quality"
    return "orchestrator", "Staging the result for your approval"


def agent_states(v: RunView) -> dict[str, str]:
    """working / done / idle for each of the five agents, in pipeline order."""
    active, _ = now_line(v)
    live = v.status == "running"
    return {a: "working" if live and a == active else
            "done" if a in v.cost["by_agent"] else "idle" for a in LANE}


# -- the redesigned run screen: one headline, the journey, and the proof at the gate ----------

# What each agent is allowed to see or do, said in one line under the headline. These are
# the guardrails from the README, phrased for a visitor.
_LIMITS = {
    "planner": "It splits the request into stages with fixed schemas. It never touches data.",
    "coder": "It sees one stage and five sample rows, never the full data.",
    "debugger": "It may change the code, never the test.",
    "validator": "It proposes quality rules. Code decides pass or fail, not a model.",
    "orchestrator": "Agents get column names and five sample rows, never the full files.",
}


# The step a failed run was in, read from the first word of its error ("validate: ...").
_PHASES = {"prepare": "reading the sources", "plan": "planning the stages",
           "validate": "checking data quality", "load": "loading the result"}


def _why_it_stopped(v: RunView) -> str:
    """One plain sentence for a failed run. The raw error (often a stack of exception names)
    is kept for the Details section; a visitor only needs to know where it stopped."""
    stuck = next((s for s in v.stages.values() if s.status == "failed"), None)
    if stuck is not None:
        tries = f"{stuck.attempt} tries" if stuck.attempt != 1 else "1 try"
        return f"{stuck.id} still failed its tests after {tries}."
    phase = _PHASES.get((v.error or "").split(":", 1)[0].strip())
    return f"It stopped while {phase}." if phase else "It stopped before the result was ready."


def hero(v: RunView) -> dict[str, str]:
    """The top of the run screen: a tone (live, wait, done, stopped, idle), a short status
    pill, one headline and one supporting line. Built on `now_line`, so the headline and the
    lit agent in the graph can never disagree."""
    table = v.target_table or "the database"
    if v.status == "succeeded" and v.load:
        by = (v.approval or {}).get("by") or "you"
        return {"tone": "done", "pill": f"Approved by {by}",
                "headline": f"{v.load['rows']:,} rows are in {v.load['table']}",
                "sub": "Every step was recorded, so this run can be replayed, or rebuilt "
                       "with no model to prove it is reproducible."}
    if v.status == "awaiting_approval" and v.approval_request:
        failed = len(v.approval_request.get("failed_checks") or [])
        if failed:  # say so in the headline: a person is about to decide on this
            return {"tone": "wait", "pill": "Waiting for you",
                    "headline": f"{v.approval_request['rows']:,} rows are staged, but {failed} "
                                f"quality check{'s' if failed != 1 else ''} failed",
                    "sub": "Read the failed check before you decide. Nothing is loaded yet, "
                           "and only your approval opens the gate."}
        return {"tone": "wait", "pill": "Waiting for you",
                "headline": f"{v.approval_request['rows']:,} rows are ready. "
                            f"Nothing is loaded yet.",
                "sub": "No agent can load data. Only your approval opens the gate."}
    if v.status == "rejected":
        by = (v.approval or {}).get("by") or "you"
        return {"tone": "stopped", "pill": f"Rejected by {by}",
                "headline": f"Rejected. Nothing was written to {table}.",
                "sub": "The staged rows were discarded and the database is unchanged."}
    if v.status == "budget_exceeded":
        return {"tone": "stopped", "pill": "Stopped at the budget",
                "headline": "Stopped at the spending limit. Nothing was written.",
                "sub": "The limit is checked in code before every model call."}
    if v.status == "failed":
        return {"tone": "stopped", "pill": "Stopped",
                "headline": "Stopped without loading anything",
                "sub": f"{_why_it_stopped(v)} The technical reason is under Details."}
    if v.status == "not started":
        return {"tone": "idle", "pill": "Not started", "headline": "Not started", "sub": ""}

    agent, text = now_line(v)
    # Keep the headline to one line: the try count and the sandbox move to the line below.
    headline = text.split(" (attempt")[0].replace(" in the sandbox", "")
    limit = v.budget_limits.get("max_retries_per_stage", 3)
    s = v.stages.get(v.current_stage) if v.current_stage else None
    sub = _LIMITS.get(agent, "")
    if agent == "tester":
        sub = ("Tests are written before any code exists, by a different agent than the code."
               if "writing" in text else "The code runs in a sealed sandbox with no network.")
    if s is not None and s.status in ("debugging", "testing") and s.tests:
        sub = f"Try {max(s.attempt, 1)} of {limit}. {sub}"
    return {"tone": "live", "pill": "Working now", "headline": headline, "sub": sub}


def journey_step(v: RunView) -> int:
    """Where the visitor is on the three-step journey: 1 Describe, 2 Build, 3 Approve."""
    if v.status == "not started":
        return 1
    if v.status in ("awaiting_approval", "succeeded", "rejected"):
        return 3
    return 2


def proofs(v: RunView) -> dict[str, int]:
    """The evidence shown beside the approval buttons: tests and quality checks that passed
    (out of how many) and how many rows were set aside. Counted from each stage's latest
    test run and the latest quality round, which are the ones the staged data went through."""
    tests_total = tests_passed = 0
    for s in v.stages.values():
        latest = s.test_runs[-1].get("tests", []) if s.test_runs else []
        tests_total += len(latest)
        tests_passed += sum(1 for t in latest if t.get("passed"))
    rnd = v.quality[-1] if v.quality else {"total": 0, "failed": 0}
    set_aside = (v.approval_request or {}).get("quarantine_rows")
    if set_aside is None:
        set_aside = sum(s.rows_quarantined or 0 for s in v.stages.values())
    return {"tests_total": tests_total, "tests_passed": tests_passed,
            "checks_total": rnd["total"], "checks_passed": rnd["total"] - rnd["failed"],
            "set_aside": set_aside}


# A failed quality check, said so a visitor can decide on it. Rule ids look like
# "dataset.column.kind" (see adpb.contracts.RuleKind); {c} is the column, {d} the dataset.
_CHECK_WORDS = {
    "not_null": "{c} is empty in some rows of {d}",
    "max_null_fraction": "too many {c} values are empty in {d}",
    "unique": "{c} repeats in {d}",
    "no_duplicate_rows": "{d} has repeated rows",
    "dtype": "{c} in {d} is not the expected type",
    "range": "{c} in {d} is outside the allowed range",
    "allowed_values": "{c} in {d} has a value that is not allowed",
    "row_count": "{d} has an unexpected number of rows",
    "foreign_key": "{c} in {d} points to a row that does not exist",
}


def plain_check(rule_id: str) -> str:
    """'clean_orders.customer_id.foreign_key' -> 'customer_id in clean_orders points to a
    row that does not exist'. Anything that is not a rule id is returned unchanged."""
    parts = rule_id.split(".")
    if len(parts) < 3:
        return rule_id
    dataset, column, kind = parts[0], ".".join(parts[1:-1]), parts[-1]
    column = column.replace("+", " and ")
    template = _CHECK_WORDS.get(kind, kind.replace("_", " ") + " check on {c} in {d}")
    return template.format(c=column, d=dataset)
