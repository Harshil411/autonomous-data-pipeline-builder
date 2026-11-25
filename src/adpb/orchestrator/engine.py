"""The orchestrator: a deterministic LangGraph state machine with no LLM in it.

    START -> prepare -> plan -> build_stage --(more stages)--> build_stage
                                   |  \\--(retries exhausted)--> replan -> build_stage
                                   |   \\--(no replans left)----> give_up -> finish
                                   v
                               validate --(check failed)--> build_stage (producing stage)
                                   v
                             stage_load -> approve [human interrupt] -> finish -> END

Why plain code here (requirements doc, "Basic Architecture"): the orchestrator controls
execution order, the retry counter, the budget and escalation, and is the only component
that may write to the final data store. None of that needs judgment, so none of it is
delegated to a model - "agency is a design choice, not a default" (Module 1).

Every node is wrapped so that any failure ends the run *cleanly*: budget exhaustion,
model outages and unexpected errors all become a terminal status plus a report, never a
crash. State is plain JSON (saved to state.json after every node) so a run can be
inspected, resumed from its failed stage (requirement 19) or replayed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import traceback
import uuid
from collections.abc import Callable
from typing import Any, TypedDict

import pandas as pd
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from adpb.agents.base import AgentContext, AgentFailed
from adpb.agents.coder import StageCoder
from adpb.agents.debugger import Debugger
from adpb.agents.planner import Planner
from adpb.agents.tester import Tester
from adpb.agents.validator import Validator
from adpb.approval import issue_token
from adpb.contracts import (
    CheckResult,
    DataRequest,
    ExecutionResult,
    PipelinePlan,
    QualityRule,
    RunStatus,
    StageCode,
    StageSpec,
    StageStatus,
    StageTests,
)
from adpb.llm.base import ModelClient, ModelError
from adpb.observability.budget import Budget, BudgetExceeded
from adpb.observability.events import EventLog
from adpb.observability.report import build_report, render_markdown
from adpb.orchestrator.sampling import make_sample
from adpb.tools.gateway import ToolFailed, ToolGateway
from adpb.tools.profiler import profile
from adpb.tools.sources import load_raw
from adpb.workspace import Workspace

TERMINAL = {RunStatus.SUCCEEDED, RunStatus.REJECTED, RunStatus.FAILED, RunStatus.BUDGET_EXCEEDED}
BROKEN_TEST_PREFIXES = ("NameError", "KeyError", "TypeError", "AttributeError", "SyntaxError",
                        "ValueError", "IndexError", "ZeroDivisionError")


class RunState(TypedDict, total=False):
    run_id: str
    status: str
    error: str | None
    prepared: bool
    plan: dict
    stages: dict            # stage id -> record (status, attempts, code, tests, history...)
    pending: list           # stage ids still to build, in execution order
    datasets: dict          # dataset name -> {path, sample, rows, quarantine}
    profiles: dict          # dataset name -> compact profile shown to agents
    needs_replan: bool
    failed_stage: str | None
    replans_used: int
    quality_round: int
    derived_rules: list | None
    quality: list
    quality_passed: bool
    staging: dict
    approval: dict
    load: dict
    spent: dict


def new_stage_record(spec: StageSpec) -> dict:
    return {"status": StageStatus.PENDING.value, "attempts": 0, "code": None, "tests": None,
            "code_version": 0, "feedback": None, "history": [], "last_error": None,
            "rows_out": None, "rows_quarantined": 0,
            "spec_hash": hashlib.sha256(spec.model_dump_json().encode()).hexdigest()[:12]}


def _first_line(text: str, limit: int = 300) -> str:
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return (lines[-1] if lines else "")[:limit]


class Orchestrator:
    def __init__(self, ws: Workspace, request: DataRequest, model: ModelClient,
                 gateway: ToolGateway, events: EventLog, budget: Budget,
                 approval_secret: str, sample_rows: int = 300):
        self.ws, self.request, self.gateway, self.events = ws, request, gateway, events
        self.budget, self.limits = budget, request.budget
        self._secret, self.sample_rows = approval_secret, sample_rows
        ctx = AgentContext(model=model, budget=budget, events=events, gateway=gateway)
        self.planner, self.coder, self.tester = Planner(ctx), StageCoder(ctx), Tester(ctx)
        self.debugger, self.validator = Debugger(ctx), Validator(ctx)

    # ------------------------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------------------------

    def _call(self, server: str, tool: str, args: dict | None = None) -> Any:
        return self.gateway.call("orchestrator", server, tool, args or {})

    def _status(self, stage: str, status: StageStatus, **extra) -> None:
        self.events.emit("stage_status", stage=stage, status=status.value, **extra)

    def _job(self, stage: str, label: str) -> str:
        return f"{stage}-{label}-{uuid.uuid4().hex[:6]}"

    def _commit(self, files: list[str], message: str) -> None:
        """Stage and commit pipeline files through the Git MCP server."""
        repo = str(self.ws.pipeline_dir)
        self._call("git", "git_add", {"repo_path": repo, "files": files})
        try:
            self._call("git", "git_commit", {"repo_path": repo, "message": message})
        except ToolFailed as exc:  # identical content: nothing new to commit
            if "stage" not in str(exc).lower() and "nothing" not in str(exc).lower():
                raise

    def _save_plan(self, plan: PipelinePlan, message: str) -> None:
        text = plan.model_dump_json(indent=2)
        self._call("artifacts", "write_text", {"path": "plan/plan.json", "content": text})
        (self.ws.pipeline_dir / "plan.json").write_text(text)
        self._commit(["plan.json"], message)

    def _save_code(self, rec: dict, code: StageCode, author: str, note: str = "") -> None:
        ext = "sql" if code.language == "sql" else "py"
        res = self._call("artifacts", "write_text",
                         {"path": f"code/{code.stage_id}.{ext}", "content": code.code})
        rec["code"] = code.model_dump()
        rec["code_version"] = res["version"]
        self.events.emit("code_written", stage=code.stage_id, author=author,
                         version=res["version"], path=res["path"], note=note[:500],
                         lines=code.code.count("\n") + 1, code=code.code[:6000])

    # ------------------------------------------------------------------------------------
    # nodes
    # ------------------------------------------------------------------------------------

    def prepare(self, state: RunState) -> dict:
        """Load every source as text, store raw + sample datasets, profile them."""
        datasets, profiles = {}, {}
        tmp = self.ws.sandbox_dir / "prepare"
        tmp.mkdir(parents=True, exist_ok=True)
        for src in self.request.sources:
            raw = load_raw(src, self.ws.sources_dir)
            full, sample = tmp / f"{src.dataset}.parquet", tmp / f"{src.dataset}.sample.parquet"
            raw.frame.to_parquet(full, index=False)
            make_sample(raw.frame, self.sample_rows).to_parquet(sample, index=False)
            stored = self._call("artifacts", "store_dataset",
                                {"name": src.dataset, "source_path": str(full)})
            s_stored = self._call("artifacts", "store_dataset",
                                  {"name": src.dataset, "source_path": str(sample),
                                   "kind": "sample"})
            quarantine = None
            if raw.bad_lines:  # requirement 20: corrupt lines go to quarantine, not to /dev/null
                qpath = tmp / f"ingest_{src.name}.quarantine.parquet"
                pd.DataFrame({"raw_line": raw.bad_lines,
                              "_reason": "malformed line: wrong number of fields",
                              "_stage": f"ingest:{src.name}"}).to_parquet(qpath, index=False)
                quarantine = self._call("artifacts", "store_dataset",
                                        {"name": f"ingest_{src.name}", "source_path": str(qpath),
                                         "kind": "quarantine"})["path"]
            datasets[src.dataset] = {"path": stored["path"], "sample": s_stored["path"],
                                     "rows": len(raw.frame), "quarantine": quarantine}
            profiles[src.dataset] = profile(raw.frame, src.dataset)
            self._call("memory", "remember_schema", {"source": src.name,
                                                     "columns": list(raw.frame.columns),
                                                     "description": src.description})
            self.events.emit("source_loaded", source=src.name, dataset=src.dataset,
                             rows=len(raw.frame), columns=list(raw.frame.columns),
                             bad_lines=len(raw.bad_lines))
        return {"prepared": True, "datasets": datasets, "profiles": profiles,
                "status": RunStatus.RUNNING.value}

    def plan(self, state: RunState) -> dict:
        raw_profiles = {s.dataset: state["profiles"][s.dataset] for s in self.request.sources}
        plan = self.planner.plan(self.request, raw_profiles)
        self._save_plan(plan, f"plan: {len(plan.stages)} stage(s)")
        self.events.emit("plan_created", plan=plan.model_dump(mode="json"))
        return {"plan": plan.model_dump(mode="json"),
                "stages": {s.id: new_stage_record(s) for s in plan.stages},
                "pending": [s.id for s in plan.execution_order()],
                "replans_used": 0, "quality_round": 1, "derived_rules": None}

    def _execute(self, spec, code, tests, datasets, scope, attempt, qround) -> ExecutionResult:
        key = "sample" if scope == "sample" else "path"
        inputs = {n: self.ws.root / datasets[n][key] for n in spec.inputs}
        res = self.tester.execute(spec, code, tests, inputs, run_root=self.ws.root,
                                  job=self._job(spec.id, f"q{qround}a{attempt}{scope}"))
        self.events.emit("test_result", stage=spec.id, attempt=attempt, scope=scope, ok=res.ok,
                         error_type=res.error_type, error=res.error[:1500],
                         tests=[t.model_dump() for t in res.tests], rows_in=res.rows_in,
                         rows_out=res.rows_out, rows_quarantined=res.rows_quarantined,
                         seconds=res.duration_s)
        return res

    @staticmethod
    def _tests_broken(res: ExecutionResult) -> str | None:
        """If every failure is a test that crashed (not one that returned False), the test
        itself is wrong - that goes back to the Tester, never to the Debugger."""
        failed = res.failed_tests
        if res.error_type != "TestFailure" or not failed:
            return None
        if all(t.detail.startswith(BROKEN_TEST_PREFIXES) for t in failed):
            return "\n".join(f"- {t.name}: {t.detail}" for t in failed)
        return None

    def build_stage(self, state: RunState) -> dict:
        """Code -> test on sample -> test on full data -> debug, up to the retry limit."""
        plan = PipelinePlan.model_validate(state["plan"])
        sid = state["pending"][0]
        spec = plan.stage(sid)
        stages, datasets = copy.deepcopy(state["stages"]), copy.deepcopy(state["datasets"])
        profiles = copy.deepcopy(state["profiles"])
        rec = stages[sid]
        inputs_prof = {n: profiles[n] for n in spec.inputs}
        qround = state.get("quality_round", 1)
        passed, attempt, sample_res, full_res, first_error = False, 0, None, None, None
        try:
            if rec["tests"] is None:  # tests first, before any code exists
                self._status(sid, StageStatus.TESTING, note="writing tests")
                tests = self.tester.write_tests(spec, inputs_prof)
                rec["tests"] = tests.model_dump()
                self._call("artifacts", "write_text", {"path": f"tests/{sid}.json",
                                                       "content": tests.model_dump_json(indent=2)})
                self.events.emit("tests_written", stage=sid,
                                 tests=[t.model_dump() for t in tests.tests])
            tests = StageTests.model_validate(rec["tests"])
            if rec["code"] is None or rec["feedback"]:
                self._status(sid, StageStatus.CODING)
                code = self.coder.write(spec, inputs_prof, feedback=rec["feedback"])
                self._save_code(rec, code, author="coder",
                                note="re-coded after quality failure" if rec["feedback"] else "")
                rec["feedback"] = None
            code = StageCode.model_validate(rec["code"])
            tests_revised = False

            for attempt in range(1, self.limits.max_retries_per_stage + 1):
                self._status(sid, StageStatus.TESTING, attempt=attempt)
                sample_res = self._execute(spec, code, tests, datasets, "sample", attempt, qround)
                res = sample_res
                if sample_res.ok:
                    full_res = self._execute(spec, code, tests, datasets, "full", attempt, qround)
                    res = full_res
                    if full_res.ok:
                        passed = True
                        break
                rec["last_error"] = f"{res.error_type}: {_first_line(res.error)}"
                first_error = first_error or f"{res.error_type}: {res.error[:800]}"
                broken = self._tests_broken(res)
                if broken and not tests_revised:
                    tests = self.tester.write_tests(spec, inputs_prof, broken=broken)
                    rec["tests"], tests_revised = tests.model_dump(), True
                    self.events.emit("tests_written", stage=sid, revised=True,
                                     tests=[t.model_dump() for t in tests.tests])
                    continue
                if attempt == self.limits.max_retries_per_stage:
                    break
                self._status(sid, StageStatus.DEBUGGING, attempt=attempt)
                code, patch = self.debugger.fix(spec, code, res, tests, inputs_prof,
                                                rec["history"])
                rec["history"].append(f"attempt {attempt}: {patch.diagnosis}")
                self._save_code(rec, code, author="debugger", note=patch.diagnosis)
                disputed = next((t for t in tests.tests if t.name == patch.disputed_test),
                                None)
                if disputed and not tests_revised:
                    # The Debugger cannot change a test; the Tester, who owns it, reviews it.
                    self.events.emit("test_disputed", stage=sid, test=disputed.name,
                                     reason=patch.dispute_reason[:800])
                    tests = self.tester.write_tests(spec, inputs_prof, broken=(
                        f"The Debugger disputes test '{disputed.name}' (`{disputed.check}`): "
                        f"{patch.dispute_reason}\nReview it. Rewrite it only if it is "
                        f"genuinely wrong; keep every test that checks real behaviour."))
                    rec["tests"], tests_revised = tests.model_dump(), True
                    self.events.emit("tests_written", stage=sid, revised=True,
                                     tests=[t.model_dump() for t in tests.tests])
        except AgentFailed as exc:  # an agent could not produce valid output: stage fails
            rec["last_error"] = str(exc)

        rec["attempts"] = attempt
        if not passed:
            rec["status"] = StageStatus.FAILED.value
            self._status(sid, StageStatus.FAILED, attempt=attempt, error=rec["last_error"])
            return {"stages": stages, "needs_replan": True, "failed_stage": sid,
                    "error": f"stage '{sid}' failed after {attempt} attempt(s): "
                             f"{rec['last_error']}"}

        # Passed: version the outputs, profile them for downstream agents, commit the code.
        stored = self._call("artifacts", "store_dataset",
                            {"name": sid, "source_path": full_res.output_path})
        s_stored = self._call("artifacts", "store_dataset",
                              {"name": sid, "source_path": sample_res.output_path,
                               "kind": "sample"})
        quarantine = None
        if full_res.quarantine_path:
            quarantine = self._call("artifacts", "store_dataset",
                                    {"name": sid, "source_path": full_res.quarantine_path,
                                     "kind": "quarantine"})["path"]
        datasets[sid] = {"path": stored["path"], "sample": s_stored["path"],
                         "rows": full_res.rows_out, "quarantine": quarantine}
        profiles[sid] = profile(pd.read_parquet(self.ws.root / stored["path"]), sid)
        ext = "sql" if code.language == "sql" else "py"
        (self.ws.pipeline_dir / "stages").mkdir(exist_ok=True)
        (self.ws.pipeline_dir / "tests").mkdir(exist_ok=True)
        (self.ws.pipeline_dir / "stages" / f"{sid}.{ext}").write_text(code.code)
        (self.ws.pipeline_dir / "tests" / f"{sid}.json").write_text(tests.model_dump_json(indent=2))
        self._commit([f"stages/{sid}.{ext}", f"tests/{sid}.json"],
                     f"stage {sid} passed (attempt {attempt}, {full_res.rows_out} rows)")
        if rec["history"] and first_error:  # only verified fixes are remembered
            self._call("memory", "remember_fix", {"error": first_error,
                                                  "fix": rec["history"][-1],
                                                  "stage_kind": spec.kind,
                                                  "code_excerpt": code.code[:1500]})
        rec.update(status=StageStatus.PASSED.value, rows_out=full_res.rows_out,
                   rows_quarantined=full_res.rows_quarantined, last_error=None)
        self._status(sid, StageStatus.PASSED, attempt=attempt, rows_out=full_res.rows_out,
                     rows_quarantined=full_res.rows_quarantined)
        return {"stages": stages, "datasets": datasets, "profiles": profiles,
                "pending": state["pending"][1:], "needs_replan": False, "failed_stage": None}

    def replan(self, state: RunState) -> dict:
        """Requirement 8: after the retry limit, the failure goes back to the planner."""
        old = PipelinePlan.model_validate(state["plan"])
        sid, stages = state["failed_stage"], state["stages"]
        passed = [s for s, r in stages.items() if r["status"] == StageStatus.PASSED.value]
        raw_profiles = {s.dataset: state["profiles"][s.dataset] for s in self.request.sources}
        new = self.planner.replan(self.request, raw_profiles, old, sid,
                                  stages[sid]["last_error"] or "", passed)
        old_specs = {s.id: s for s in old.stages}
        changed = [s.id for s in new.stages if old_specs.get(s.id) != s]
        affected: set[str] = set()
        for c in changed:
            affected |= set(new.downstream_of(c))
        new_stages = {}
        for s in new.stages:
            rec = stages.get(s.id)
            keep = rec and s.id not in affected and rec["status"] == StageStatus.PASSED.value
            new_stages[s.id] = copy.deepcopy(rec) if keep else new_stage_record(s)
        pending = [s.id for s in new.execution_order()
                   if new_stages[s.id]["status"] != StageStatus.PASSED.value]
        self._save_plan(new, f"replan after '{sid}' failed")
        self.events.emit("replanned", failed_stage=sid, changed=changed, pending=pending,
                         plan=new.model_dump(mode="json"))
        return {"plan": new.model_dump(mode="json"), "stages": new_stages, "pending": pending,
                "replans_used": state.get("replans_used", 0) + 1, "needs_replan": False,
                "failed_stage": None, "error": None, "derived_rules": None}

    def give_up(self, state: RunState) -> dict:
        return {"status": RunStatus.FAILED.value}

    def validate(self, state: RunState) -> dict:
        """Requirements 9-10: run quality rules; route failures to the producing stage."""
        plan = PipelinePlan.model_validate(state["plan"])
        profiles, datasets = state["profiles"], state["datasets"]
        qround = state.get("quality_round", 1)
        if state.get("derived_rules") is None:  # rules are fixed after round 1: no moving goalposts
            derived = self.validator.propose(
                plan, {s.id: profiles[s.id] for s in plan.stages},
                {n: p for n, p in profiles.items() if n.startswith("raw.")})
            derived_json = [r.model_dump(mode="json") for r in derived]
        else:
            derived_json = state["derived_rules"]
        rules = self.validator.all_rules(plan, [QualityRule.model_validate(r)
                                                for r in derived_json])
        checks = self.validator.check(
            rules, {s.id: self.ws.root / datasets[s.id]["path"] for s in plan.stages},
            run_root=self.ws.root, job=self._job("quality", f"r{qround}"))
        failures = [c for c in checks if not c.passed]
        # Only contracts (schema) and the user's own rules may force a re-code. A rule the
        # Validator guessed is weak evidence: if it fails, the human sees it at approval,
        # but it never makes another agent change the data to fit it.
        origin = {r.id: r.origin for r in rules}
        blocking = [c for c in failures if origin.get(c.rule_id) in ("schema", "request")]
        self.events.emit("quality_result", round=qround, total=len(checks), failed=len(failures),
                         blocking=len(blocking), checks=[c.model_dump() for c in checks],
                         rules=[r.model_dump(mode="json") for r in rules])
        update: dict[str, Any] = {"derived_rules": derived_json,
                                  "quality": [c.model_dump() for c in checks],
                                  "quality_passed": not failures}
        if blocking and qround < self.limits.max_quality_rounds:
            failures = blocking
            failing = {c.dataset for c in failures}
            producer = next(s.id for s in plan.execution_order() if s.id in failing)
            feedback = "\n".join(
                f"- {c.rule_id}: {c.observed}; failing rows: {c.failing_rows}; examples: "
                f"{json.dumps(c.sample[:3], default=str)}"
                for c in failures if c.dataset == producer)
            stages = copy.deepcopy(state["stages"])
            stages[producer]["feedback"] = feedback
            pending = plan.downstream_of(producer)
            for s in pending:
                stages[s]["status"] = StageStatus.PENDING.value
            self.events.emit("quality_routed", stage=producer, pending=pending,
                             feedback=feedback[:2000])
            update.update(stages=stages, pending=pending, quality_round=qround + 1)
        return update

    def stage_load(self, state: RunState) -> dict:
        """Stage the final dataset and quarantine; production is NOT touched here."""
        plan = PipelinePlan.model_validate(state["plan"])
        datasets, table = state["datasets"], plan.target_table
        final_path = self.ws.root / datasets[plan.final_dataset]["path"]
        staged = self._call("warehouse", "stage_table",
                            {"dataset_path": str(final_path), "table": table})
        relevant = [f"{s.dataset}" for s in plan.sources] + [s.id for s in plan.stages]
        q_paths = [str(self.ws.root / datasets[n]["quarantine"]) for n in relevant
                   if datasets.get(n, {}).get("quarantine")]
        q_rows = 0
        if q_paths:
            q_rows = self._call("warehouse", "write_quarantine",
                                {"paths": q_paths, "table": table})["rows"]
        preview = self._call("warehouse", "preview", {"table": f"staging__{table}", "limit": 10})
        staging = {**staged, "table": table, "quarantine_rows": q_rows, "preview": preview["rows"]}
        failed = [c["rule_id"] for c in state.get("quality", []) if not c["passed"]]
        self.events.emit("approval_requested", table=table, rows=staged["rows"],
                         fingerprint=staged["fingerprint"],
                         existing_production_rows=staged["existing_production_rows"],
                         quarantine_rows=q_rows, failed_checks=failed,
                         preview=preview["rows"])
        return {"staging": staging, "status": RunStatus.AWAITING_APPROVAL.value}

    def approve(self, state: RunState) -> dict:
        """Requirement 21: the human decides. The graph pauses here until resumed."""
        staging = state["staging"]
        decision = interrupt({"run_id": self.ws.run_id, "table": staging["table"],
                              "rows": staging["rows"], "fingerprint": staging["fingerprint"]})
        approval = {"approved": bool(decision.get("approved")),
                    "by": str(decision.get("by") or "unknown")[:80],
                    "note": str(decision.get("note") or "")[:500]}
        self.events.emit("approval_decision", **approval)
        if not approval["approved"]:
            return {"status": RunStatus.REJECTED.value, "approval": approval}
        token = issue_token(self._secret, self.ws.run_id, staging["table"],
                            staging["fingerprint"])
        load = self._call("warehouse", "promote", {"table": staging["table"],
                                                   "run_id": self.ws.run_id,
                                                   "approval_token": token})
        self.events.emit("load_finished", **load)
        return {"status": RunStatus.SUCCEEDED.value, "approval": approval, "load": load}

    def finish(self, state: RunState) -> dict:
        """Write the report and manifest, remember the pipeline. Must not raise."""
        notes = []
        report = build_report(dict(state), EventLog.read(self.ws.events_path))
        (self.ws.root / "report.json").write_text(json.dumps(report, indent=2, default=str))
        (self.ws.root / "report.md").write_text(render_markdown(report))
        plan = state.get("plan")
        if plan:
            try:
                manifest = {
                    "run_id": self.ws.run_id, "status": state.get("status"),
                    "sources": [s.model_dump(mode="json") for s in self.request.sources],
                    "final_dataset": plan["final_dataset"], "target_table": plan["target_table"],
                    "fingerprint": (state.get("staging") or {}).get("fingerprint"),
                    "rows": report["rows"]["output"],
                    "stages": [{"id": s["id"], "language": s["language"]}
                               for s in plan["stages"]],
                }
                (self.ws.pipeline_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
                self._commit(["manifest.json"], f"run finished: {state.get('status')}")
                summary = "; ".join(f"{s['id']} [{s['kind']}] {s['description'][:120]}"
                                    for s in plan["stages"])
                self._call("memory", "remember_pipeline", {"request": self.request.request,
                                                           "summary": summary,
                                                           "outcome": str(state.get("status"))})
            except Exception as exc:  # the report already exists; do not fail the run now
                notes.append(f"{type(exc).__name__}: {exc}")
        self.events.emit("run_finished", status=state.get("status"), error=state.get("error"),
                         notes=notes, cost=report["cost"], rows=report["rows"])
        return {}

    # ------------------------------------------------------------------------------------
    # routing
    # ------------------------------------------------------------------------------------

    @staticmethod
    def _terminal(state: RunState) -> bool:
        return state.get("status") in {s.value for s in TERMINAL}

    def route_start(self, state: RunState) -> str:
        if self._terminal(state):
            return "finish"
        if not state.get("prepared"):
            return "prepare"
        if not state.get("plan"):
            return "plan"
        if state.get("pending"):
            return "build_stage"
        return "validate"

    def after_build(self, state: RunState) -> str:
        if self._terminal(state):
            return "finish"
        if state.get("needs_replan"):
            return "replan" if state.get("replans_used", 0) < self.limits.max_replans \
                else "give_up"
        return "build_stage" if state.get("pending") else "validate"

    def after_validate(self, state: RunState) -> str:
        if self._terminal(state):
            return "finish"
        return "build_stage" if state.get("pending") else "stage_load"

    def _next_or_finish(self, nxt: str) -> Callable[[RunState], str]:
        return lambda state: "finish" if self._terminal(state) else nxt

    # ------------------------------------------------------------------------------------
    # graph assembly
    # ------------------------------------------------------------------------------------

    def _wrap(self, name: str, fn: Callable[[RunState], dict]) -> Callable[[RunState], dict]:
        def node(state: RunState) -> dict:
            try:
                update = fn(state) or {}
            except GraphBubbleUp:  # LangGraph's interrupt mechanism: let it through
                raise
            except BudgetExceeded as exc:
                self.events.emit("budget_exceeded", node=name, limit=exc.limit, error=str(exc))
                update = {"status": RunStatus.BUDGET_EXCEEDED.value, "error": str(exc)}
            except (ModelError, AgentFailed, ToolFailed) as exc:
                update = {"status": RunStatus.FAILED.value,
                          "error": f"{name}: {type(exc).__name__}: {exc}"[:2000]}
            except Exception as exc:
                self.events.emit("node_error", node=name,
                                 traceback=traceback.format_exc()[-3000:])
                update = {"status": RunStatus.FAILED.value,
                          "error": f"{name}: {type(exc).__name__}: {exc}"[:2000]}
            update["spent"] = self.budget.snapshot()
            merged = {**state, **update}
            self.ws.state_path.write_text(json.dumps(merged, default=str))
            return update
        return node

    def graph(self, checkpointer):
        g = StateGraph(RunState)
        for name in ("prepare", "plan", "build_stage", "replan", "give_up", "validate",
                     "stage_load", "approve", "finish"):
            g.add_node(name, self._wrap(name, getattr(self, name)))
        g.add_conditional_edges(START, self.route_start,
                                ["prepare", "plan", "build_stage", "validate", "finish"])
        g.add_conditional_edges("prepare", self._next_or_finish("plan"), ["plan", "finish"])
        g.add_conditional_edges("plan", self._next_or_finish("build_stage"),
                                ["build_stage", "finish"])
        g.add_conditional_edges("build_stage", self.after_build,
                                ["build_stage", "replan", "give_up", "validate", "finish"])
        g.add_conditional_edges("replan", self._next_or_finish("build_stage"),
                                ["build_stage", "finish"])
        g.add_edge("give_up", "finish")
        g.add_conditional_edges("validate", self.after_validate,
                                ["build_stage", "stage_load", "finish"])
        g.add_conditional_edges("stage_load", self._next_or_finish("approve"),
                                ["approve", "finish"])
        g.add_edge("approve", "finish")
        g.add_edge("finish", END)
        return g.compile(checkpointer=checkpointer)


def restore_failed_run(state: dict, plan: PipelinePlan) -> dict:
    """Prepare a failed run for `resume`: passed stages and their outputs are kept; the
    failed stage and everything after it are rebuilt (requirement 19)."""
    state = copy.deepcopy(state)
    stages = state["stages"]
    for rec in stages.values():
        if rec["status"] != StageStatus.PASSED.value:
            rec.update(status=StageStatus.PENDING.value, attempts=0, last_error=None,
                       code=None if rec["status"] == StageStatus.FAILED.value else rec["code"])
    state["pending"] = [s.id for s in plan.execution_order()
                        if stages[s.id]["status"] != StageStatus.PASSED.value]
    state.update(status=RunStatus.RUNNING.value, error=None, needs_replan=False,
                 failed_stage=None)
    return state


def check_results(state: dict) -> list[CheckResult]:
    return [CheckResult.model_validate(c) for c in state.get("quality", [])]
