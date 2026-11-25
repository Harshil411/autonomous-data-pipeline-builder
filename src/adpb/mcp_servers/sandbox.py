"""Sandbox MCP server: code execution and allow-listed shell, inside Docker (req. 5, 16).

Scoped to one run's workspace: input and dataset paths are relative to the run folder
and may not escape it. The heavy lifting is in `adpb.sandbox.docker_runner`.

Run standalone:  python -m adpb.mcp_servers.sandbox   (reads ADPB_RUN_DIR)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from adpb.mcp_servers.common import MCPServer, ToolError, inside, new_server
from adpb.sandbox.docker_runner import DockerSandbox, ShellNotAllowed

_JOB = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")


def build_sandbox_server(run_dir: Path, sandbox: DockerSandbox | None = None) -> MCPServer:
    root = Path(run_dir).resolve()
    box = sandbox or DockerSandbox()
    server = new_server(
        "sandbox",
        "Runs pipeline code in an isolated container: no network, no host access, CPU, "
        "memory and time limits.",
    )

    def job_dir(job: str) -> Path:
        if not _JOB.match(job):
            raise ToolError(f"invalid job name {job!r}")
        return root / "sandbox" / job

    @server.tool()
    def run_stage(stage_id: str, code: str, language: str, inputs: dict[str, str],
                  output_schema: dict, tests: list[dict], job: str,
                  max_quarantine_fraction: float = 0.25) -> dict:
        """Run one stage's code on its input datasets (paths relative to the run) and
        evaluate its tests. Returns the harness verdict: ok, error, rows, test outcomes."""
        resolved = {name: inside(root, path) for name, path in inputs.items()}
        return box.run_stage(stage_id=stage_id, code=code, language=language, inputs=resolved,
                             output_schema=output_schema, tests=tests, job_dir=job_dir(job),
                             max_quarantine_fraction=max_quarantine_fraction)

    @server.tool()
    def run_quality(datasets: dict[str, str], rules: list[dict], job: str) -> dict:
        """Run declarative quality rules over finished datasets (paths relative to run)."""
        resolved = {name: inside(root, path) for name, path in datasets.items()}
        return box.run_quality(datasets=resolved, rules=rules, job_dir=job_dir(job))

    @server.tool()
    def run_command(argv: list[str]) -> dict:
        """Run an allow-listed command (ls, head, tail, wc, cat, grep, du, file, python)
        inside a fresh sandbox. The run's datasets are mounted read-only at /inputs."""
        try:
            return box.run_command(argv, inputs_dir=root / "data")
        except ShellNotAllowed as exc:
            raise ToolError(str(exc)) from exc

    return server


if __name__ == "__main__":
    build_sandbox_server(Path(os.environ["ADPB_RUN_DIR"])).run("stdio")
