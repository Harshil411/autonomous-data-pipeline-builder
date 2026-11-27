"""Wiring: one `RunHandle` per pipeline run, used by the CLI, the UI and the tests.

A handle owns the run's workspace and knows how to (re)connect everything a session
needs - MCP servers, the tool gateway, the event log, the budget, the model and the
orchestrator graph - for each of the three things you can do with a run:

    run()       start, or continue until the human-approval pause
    decide()    record the human's approve/reject and finish
    resume()    after a failure, rebuild only the failed stage and its dependants

MCP servers are started per session. In ``stdio`` mode (real runs) every server is its own
subprocess and receives only the environment it needs: the warehouse server alone gets
the database URL and approval secret; no server gets the model API key (requirement 24).
``inproc`` mode connects the same servers in-process (tests, and the local sandbox).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import anyio
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from adpb.config import Settings, load_settings
from adpb.contracts import DataRequest, PipelinePlan, RunStatus, SourceKind
from adpb.llm.base import ModelClient
from adpb.mcp_servers.artifacts import build_artifacts_server
from adpb.mcp_servers.memory import HashEmbedding, build_memory_server
from adpb.mcp_servers.sandbox import build_sandbox_server
from adpb.mcp_servers.warehouse import build_warehouse_server
from adpb.observability.budget import Budget
from adpb.observability.events import EventLog
from adpb.observability.redact import Redactor
from adpb.orchestrator.engine import Orchestrator, restore_failed_run
from adpb.tools.gateway import ToolGateway
from adpb.tools.sources import fetch_api_snapshot
from adpb.workspace import Workspace

INHERITED_ENV = ("PATH", "HOME", "LANG", "TMPDIR", "DOCKER_HOST", "USER",
                 "ADPB_SANDBOX_MEMORY")


def make_model(settings: Settings) -> ModelClient:
    """The configured live model client."""
    if settings.provider == "vtarc":
        from adpb.llm.openai_compat import OpenAICompatModel

        return OpenAICompatModel(model=settings.vt_arc_model, base_url=settings.vt_arc_base_url,
                                 api_key=os.environ.get("VT_ARC_API_KEY"))
    from adpb.llm.anthropic_client import AnthropicModel

    return AnthropicModel(models=settings.models)


def make_sandbox(kind: str):
    if kind == "local":
        from adpb.sandbox.local_runner import LocalSandbox

        return LocalSandbox()
    from adpb.sandbox.docker_runner import DockerSandbox

    return DockerSandbox()


class RunHandle:
    def __init__(self, ws: Workspace, settings: Settings, model: ModelClient | None = None,
                 transport: str | None = None, sandbox=None):
        self.ws, self.settings = ws, settings
        self.model = model if model is not None else make_model(settings)
        self.sandbox = sandbox if sandbox is not None else (
            make_sandbox("local") if settings.sandbox == "local" else None)
        # The local sandbox is a Python object, so it can only be used in-process.
        self.transport = transport or ("inproc" if settings.sandbox == "local" else "stdio")
        self.request = DataRequest.model_validate_json(ws.request_path.read_text())

    # -- construction -----------------------------------------------------------------------

    @classmethod
    def create(cls, request: DataRequest, source_files: list[Path], *,
               home: Path | None = None, model: ModelClient | None = None,
               approval_secret: str | None = None, sandbox: str | None = None,
               embeddings: str | None = None, transport: str | None = None,
               run_id: str | None = None) -> RunHandle:
        settings = load_settings(home=home, approval_secret=approval_secret, sandbox=sandbox,
                                 embeddings=embeddings)
        ws = Workspace.create(settings.runs_dir, run_id)
        for f in source_files:
            shutil.copy2(f, ws.sources_dir / Path(f).name)
        for src in request.sources:  # API sources: fetch once, on the host, allow-listed
            if src.kind is SourceKind.API:
                fetch_api_snapshot(src, ws.sources_dir, settings.api_allowed_hosts)
        ws.request_path.write_text(request.model_dump_json(indent=2))
        return cls(ws, settings, model=model, transport=transport)

    @classmethod
    def open(cls, run_id: str, *, home: Path | None = None, model: ModelClient | None = None,
             **overrides) -> RunHandle:
        settings = load_settings(home=home, **overrides)
        return cls(Workspace.open(settings.runs_dir, run_id), settings, model=model)

    # -- sessions ---------------------------------------------------------------------------

    def _server_env(self, **extra: str) -> dict[str, str]:
        env = {k: os.environ[k] for k in INHERITED_ENV if k in os.environ}
        env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
        return {**env, **extra}

    def _servers(self) -> dict[str, Any]:
        s, ws = self.settings, self.ws
        git = StdioServerParameters(command=sys.executable,
                                    args=["-m", "mcp_server_git", "--repository",
                                          str(ws.pipeline_dir)], env=self._server_env())
        if self.transport == "inproc":
            embedding = HashEmbedding() if s.embeddings == "hash" else None
            return {
                "sandbox": build_sandbox_server(ws.root, sandbox=self.sandbox),
                "artifacts": build_artifacts_server(ws.root),
                "warehouse": build_warehouse_server(s.warehouse_url, s.approval_secret,
                                                    s.runs_dir),
                "memory": build_memory_server(s.memory_dir, embedding=embedding),
                "git": git,
            }

        def module(name: str, **env: str) -> StdioServerParameters:
            return StdioServerParameters(command=sys.executable,
                                         args=["-m", f"adpb.mcp_servers.{name}"],
                                         env=self._server_env(**env))
        return {
            "sandbox": module("sandbox", ADPB_RUN_DIR=str(ws.root)),
            "artifacts": module("artifacts", ADPB_RUN_DIR=str(ws.root)),
            "warehouse": module("warehouse", ADPB_WAREHOUSE_URL=s.warehouse_url,
                                ADPB_APPROVAL_SECRET=s.approval_secret,
                                ADPB_RUNS_DIR=str(s.runs_dir)),
            "memory": module("memory", ADPB_MEMORY_DIR=str(s.memory_dir)),
            "git": git,
        }

    @contextmanager
    def _session(self, state: dict | None = None):
        redactor = Redactor.from_env({**os.environ,
                                      "ADPB_APPROVAL_SECRET": self.settings.approval_secret})
        events = EventLog(self.ws.events_path, self.ws.run_id, redactor=redactor)
        budget = Budget(self.request.budget)
        with ToolGateway(self._servers(), events=events) as gateway, \
                sqlite3.connect(self.ws.root / "checkpoints.sqlite",
                                check_same_thread=False) as con:
            orch = Orchestrator(self.ws, self.request, self.model, gateway, events, budget,
                                approval_secret=self.settings.approval_secret,
                                sample_rows=self.settings.sample_rows)
            yield orch, orch.graph(SqliteSaver(con)), events

    def _config(self, suffix: str = "") -> dict:
        return {"configurable": {"thread_id": self.ws.run_id + suffix}, "recursion_limit": 250}

    def state(self) -> dict:
        return json.loads(self.ws.state_path.read_text()) if self.ws.state_path.exists() else {}

    # -- the three operations -----------------------------------------------------------------

    def run(self) -> dict:
        with self._session() as (_, graph, events):
            if not self.ws.state_path.exists():
                events.emit("run_started", request=self.request.request,
                            sources=[s.model_dump(mode="json") for s in self.request.sources],
                            target_table=self.request.target_table,
                            budget=self.request.budget.model_dump(),
                            models={r: self.model.model_for(r) for r in
                                    ("planner", "coder", "tester", "debugger", "validator")})
            graph.invoke({"run_id": self.ws.run_id, "status": RunStatus.RUNNING.value},
                         self._config(self._thread_suffix()))
        return self.state()

    def decide(self, approved: bool, by: str, note: str = "") -> dict:
        if self.state().get("status") != RunStatus.AWAITING_APPROVAL.value:
            raise RuntimeError(f"run {self.ws.run_id} is not awaiting approval")
        with self._session() as (_, graph, _events):
            graph.invoke(Command(resume={"approved": approved, "by": by, "note": note}),
                         self._config(self._thread_suffix()))
        return self.state()

    def resume(self) -> dict:
        state = self.state()
        if state.get("status") not in (RunStatus.FAILED.value, RunStatus.BUDGET_EXCEEDED.value):
            raise RuntimeError(f"only failed runs can be resumed (status: {state.get('status')})")
        if not state.get("plan"):
            raise RuntimeError("this run failed before a plan existed; start a new run")
        restored = restore_failed_run(state, PipelinePlan.model_validate(state["plan"]))
        resumes = state.get("resumes", 0) + 1
        restored["resumes"] = resumes
        self.ws.state_path.write_text(json.dumps(restored, default=str))
        with self._session() as (_, graph, events):
            events.emit("run_resumed", pending=restored["pending"], resume=resumes)
            graph.invoke(restored, self._config(f"-resume{resumes}"))
        return self.state()

    def _thread_suffix(self) -> str:
        n = self.state().get("resumes", 0)
        return f"-resume{n}" if n else ""

    # -- conveniences -------------------------------------------------------------------------

    def memory_search_fixes(self, error: str) -> list[dict]:
        embedding = HashEmbedding() if self.settings.embeddings == "hash" else None
        server = build_memory_server(self.settings.memory_dir, embedding=embedding)

        async def _go():
            async with Client(server) as c:
                res = await c.call_tool("search_fixes", {"error": error})
                return json.loads(res.content[0].text)["results"]
        return anyio.run(_go)
