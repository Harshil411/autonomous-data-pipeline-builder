"""The on-disk workspace of one pipeline run.

    var/runs/<run_id>/
      request.json          the DataRequest exactly as submitted
      state.json            orchestrator state (plan, stage statuses, retries), for resume
      events.jsonl          the append-only event log (requirement 22)
      sources/              copies of the user's input files (+ API snapshots)
      data/                 versioned datasets: raw.orders.v1.parquet, clean_orders.v2.parquet
      data/samples/         small samples the Tester runs code against
      code/ tests/          versioned artifacts written through the Filesystem MCP
      sandbox/<job>/        job files and results of each container run (kept for audit)
      pipeline/             a git repo with the *passing* plan, stage code and tests;
                            every passing stage is one commit (requirement 12)
      report.json report.md the run report (requirement 25)

Only the orchestrator creates the workspace; agents reach it through MCP servers that are
scoped to this folder.
"""

from __future__ import annotations

import secrets
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def new_run_id() -> str:
    return f"run-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


@dataclass(frozen=True)
class Workspace:
    root: Path
    run_id: str

    @classmethod
    def create(cls, base: Path, run_id: str | None = None) -> Workspace:
        run_id = run_id or new_run_id()
        ws = cls(Path(base).resolve() / run_id, run_id)
        for d in (ws.sources_dir, ws.data_dir, ws.samples_dir, ws.sandbox_dir, ws.pipeline_dir,
                  ws.root / "code", ws.root / "tests"):
            d.mkdir(parents=True, exist_ok=True)
        if not (ws.pipeline_dir / ".git").exists():
            # One-time repository setup; every later commit goes through the Git MCP server.
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=ws.pipeline_dir, check=True)
            for key, value in (("user.name", "ADPB Orchestrator"),
                               ("user.email", "orchestrator@adpb.local"),
                               ("commit.gpgsign", "false")):
                subprocess.run(["git", "config", key, value], cwd=ws.pipeline_dir, check=True)
        return ws

    @classmethod
    def open(cls, base: Path, run_id: str) -> Workspace:
        ws = cls(Path(base).resolve() / run_id, run_id)
        if not ws.root.exists():
            raise FileNotFoundError(f"no run '{run_id}' under {base}")
        return ws

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def samples_dir(self) -> Path:
        return self.root / "data" / "samples"

    @property
    def sandbox_dir(self) -> Path:
        return self.root / "sandbox"

    @property
    def pipeline_dir(self) -> Path:
        return self.root / "pipeline"

    @property
    def events_path(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def request_path(self) -> Path:
        return self.root / "request.json"

    def rel(self, path: Path) -> str:
        return str(Path(path).resolve().relative_to(self.root))
