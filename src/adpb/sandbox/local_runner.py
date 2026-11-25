"""In-process stand-in for DockerSandbox - FOR TESTS ONLY, NO ISOLATION.

Runs the very same harness, conform and checks code as the container, but in the test
process, so the multi-agent graph can be exercised offline in milliseconds with scripted
(trusted) stage code. The application refuses to use it unless
``ADPB_UNSAFE_LOCAL_SANDBOX=1`` is set explicitly (see adpb.config).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from adpb.sandbox.docker_runner import ShellPolicy
from adpb.sandbox_runtime.harness import run_job

ENV_FLAG = "ADPB_UNSAFE_LOCAL_SANDBOX"


class LocalSandbox:
    isolated = False

    def __init__(self, policy: ShellPolicy | None = None, *, allow: bool | None = None):
        allowed = allow if allow is not None else os.environ.get(ENV_FLAG) == "1"
        if not allowed and "PYTEST_CURRENT_TEST" not in os.environ:
            raise RuntimeError(f"LocalSandbox has no isolation; set {ENV_FLAG}=1 to use it")
        self.policy = policy or ShellPolicy()
        self.jobs: list[dict] = []

    def run_stage(self, *, stage_id, code, language, inputs, output_schema, tests, job_dir,
                  max_quarantine_fraction=0.25) -> dict:
        job_dir = Path(job_dir)
        (job_dir / "out").mkdir(parents=True, exist_ok=True)
        code_path = job_dir / ("code.sql" if language == "sql" else "code.py")
        code_path.write_text(code)
        job = {"mode": "stage", "stage_id": stage_id, "language": language,
               "code_path": str(code_path), "inputs": {k: str(v) for k, v in inputs.items()},
               "output_schema": output_schema, "tests": tests,
               "output_path": str(job_dir / "out" / f"{stage_id}.parquet"),
               "quarantine_path": str(job_dir / "out" / f"{stage_id}.quarantine.parquet"),
               "result_path": str(job_dir / "result.json"),
               "max_quarantine_fraction": max_quarantine_fraction}
        (job_dir / "job.json").write_text(json.dumps(job))
        self.jobs.append(job)
        return run_job(job)

    def run_quality(self, *, datasets, rules, job_dir) -> dict:
        job_dir = Path(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        job = {"mode": "quality", "datasets": {k: str(v) for k, v in datasets.items()},
               "rules": rules, "result_path": str(job_dir / "result.json")}
        return run_job(job)

    def run_command(self, argv, inputs_dir, timeout_s=20) -> dict:
        self.policy.check(argv)
        return {"exit_code": 0, "stdout": f"(local sandbox) would run {argv}", "stderr": "",
                "timed_out": False}
