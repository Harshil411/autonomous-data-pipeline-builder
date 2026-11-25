"""Run sandbox jobs in locked-down Docker containers (requirements 5 and 16).

Every stage execution, quality check and allow-listed shell command runs in a fresh
container started with:

  --network none                 no network at all (flyer: "won't reach the internet")
  --read-only + tmpfs /tmp       the image cannot be modified
  --cpus / --memory / --pids     resource limits; memory swap disabled
  --cap-drop ALL, no-new-privileges, non-root --user
  a host-side wall-clock timeout that kills the container

Mounts are explicit and minimal: each input file read-only at /inputs/<dataset>.parquet,
the job folder read-write at /work, and the harness read-only at /opt/adpb_runtime. The
host's home directory, the .env file and the run's other data are simply not there.
"""

from __future__ import annotations

import functools
import json
import os
import posixpath
import re
import shutil
import subprocess
import uuid
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parent.parent / "sandbox_runtime"
DEFAULT_IMAGE = os.environ.get("ADPB_SANDBOX_IMAGE", "adpb-sandbox:0.1")
STARTUP_GRACE_S = 5.0  # container start-up is not charged against the code's time limit
MAX_OUTPUT_CHARS = 4_000


class ShellNotAllowed(PermissionError):
    pass


class ShellPolicy:
    """Allow-list for shell commands run inside the sandbox (requirement 16)."""

    ALLOWED = frozenset({"ls", "head", "tail", "wc", "cat", "grep", "du", "file", "python"})
    MOUNTS = ("/inputs", "/work", "/tmp")  # noqa: S108 - these are paths inside the container
    _META = re.compile(r"[;&|`$<>(){}\n\\]")

    def check(self, argv: list[str]) -> None:
        if not argv:
            raise ShellNotAllowed("empty command")
        program, args = argv[0], argv[1:]
        if "/" in program:
            raise ShellNotAllowed(f"use a bare program name, not a path: {program!r}")
        if program not in self.ALLOWED:
            raise ShellNotAllowed(f"'{program}' is not on the allow-list {sorted(self.ALLOWED)}")
        skip_next_meta = False
        for arg in args:
            if skip_next_meta:  # the code string after `python -c` runs in the sandbox anyway
                skip_next_meta = False
                continue
            if program == "python" and arg == "-c":
                skip_next_meta = True
                continue
            if self._META.search(arg):
                raise ShellNotAllowed(f"shell metacharacter in argument {arg!r}")
            if arg.startswith("/"):
                norm = posixpath.normpath(arg)
                if not any(norm == m or norm.startswith(m + "/") for m in self.MOUNTS):
                    raise ShellNotAllowed(f"path {arg!r} is outside the sandbox mounts")


@functools.cache
def docker_available(image: str = DEFAULT_IMAGE) -> bool:
    """True when the daemon answers and the sandbox image has been built."""
    if not shutil.which("docker"):
        return False
    try:
        info = subprocess.run(["docker", "info"], capture_output=True, timeout=15)
        img = subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                             timeout=15)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return info.returncode == 0 and img.returncode == 0


def _trim(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT_CHARS else text[-MAX_OUTPUT_CHARS:]


class DockerSandbox:
    def __init__(self, image: str = DEFAULT_IMAGE, cpus: float = 1.0, memory: str = "1g",
                 pids: int = 256, timeout_s: float = 90.0, policy: ShellPolicy | None = None):
        self.image = image
        self.cpus = cpus
        self.memory = memory
        self.pids = pids
        self.timeout_s = timeout_s
        self.policy = policy or ShellPolicy()

    # -- docker plumbing ----------------------------------------------------------------

    def _base_cmd(self, name: str, mounts: list[tuple[Path, str, bool]]) -> list[str]:
        cmd = [
            "docker", "run", "--rm", "--name", name,
            "--network", "none",
            "--read-only", "--tmpfs", "/tmp:rw,size=128m",
            "--cpus", str(self.cpus), "--memory", self.memory, "--memory-swap", self.memory,
            "--pids-limit", str(self.pids),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            # Host uid, never root: files written to /work stay owned by the user.
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp", "-e", "PYTHONPATH=/opt/adpb_runtime",
            "-v", f"{RUNTIME_DIR}:/opt/adpb_runtime:ro",
        ]
        for host, container, read_only in mounts:
            cmd += ["-v", f"{Path(host).resolve()}:{container}:{'ro' if read_only else 'rw'}"]
        return cmd + [self.image]

    def _exec(self, cmd: list[str], name: str, timeout: float) -> tuple[int | None, str, str]:
        """Run docker; on timeout kill the container. Returns (exit code or None, out, err)."""
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout + STARTUP_GRACE_S)
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
            return None, exc.stdout or "", exc.stderr or ""

    def _harness_job(self, job: dict, job_dir: Path, mounts) -> dict:
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "out").mkdir(exist_ok=True)
        result_file = job_dir / "result.json"
        result_file.unlink(missing_ok=True)
        (job_dir / "job.json").write_text(json.dumps(job, indent=2))
        name = f"adpb-{uuid.uuid4().hex[:12]}"
        cmd = self._base_cmd(name, [*mounts, (job_dir, "/work", False)])
        cmd += ["python", "/opt/adpb_runtime/harness.py", "/work/job.json"]
        code, _out, err = self._exec(cmd, name, self.timeout_s)
        if code is None:
            return {"ok": False, "error_type": "Timeout",
                    "error": f"stage exceeded the {self.timeout_s:.0f}s time limit and was "
                             f"killed"}
        if result_file.exists():
            return json.loads(result_file.read_text())
        if code == 137:
            return {"ok": False, "error_type": "ResourceLimit",
                    "error": f"container was killed (exit 137): most likely the "
                             f"{self.memory} memory limit"}
        return {"ok": False, "error_type": "SandboxError",
                "error": f"sandbox exited with code {code}: {_trim(err)}"}

    # -- public API ---------------------------------------------------------------------

    def run_stage(self, *, stage_id: str, code: str, language: str, inputs: dict[str, Path],
                  output_schema: dict, tests: list[dict], job_dir: Path,
                  max_quarantine_fraction: float = 0.25) -> dict:
        """Execute one stage's code on its inputs and return the harness verdict."""
        job_dir = Path(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        code_name = "code.sql" if language == "sql" else "code.py"
        (job_dir / code_name).write_text(code)
        mounts = [(Path(p), f"/inputs/{name}.parquet", True) for name, p in inputs.items()]
        job = {
            "mode": "stage", "stage_id": stage_id, "language": language,
            "code_path": f"/work/{code_name}",
            "inputs": {name: f"/inputs/{name}.parquet" for name in inputs},
            "output_schema": output_schema, "tests": tests,
            "output_path": f"/work/out/{stage_id}.parquet",
            "quarantine_path": f"/work/out/{stage_id}.quarantine.parquet",
            "result_path": "/work/result.json",
            "max_quarantine_fraction": max_quarantine_fraction,
        }
        result = self._harness_job(job, job_dir, mounts)
        result.setdefault("stage_id", stage_id)
        # Translate container paths back to host paths for the orchestrator.
        for key in ("output_path", "quarantine_path"):
            if result.get(key):
                result[key] = str(job_dir / "out" / Path(result[key]).name)
        return result

    def run_quality(self, *, datasets: dict[str, Path], rules: list[dict], job_dir: Path) -> dict:
        mounts = [(Path(p), f"/inputs/{name}.parquet", True) for name, p in datasets.items()]
        job = {"mode": "quality", "rules": rules, "result_path": "/work/result.json",
               "datasets": {name: f"/inputs/{name}.parquet" for name in datasets}}
        return self._harness_job(job, Path(job_dir), mounts)

    def run_command(self, argv: list[str], inputs_dir: Path, timeout_s: float = 20) -> dict:
        """Run an allow-listed command inside a fresh sandbox, inputs mounted read-only."""
        self.policy.check(argv)  # refuse before any container starts
        name = f"adpb-sh-{uuid.uuid4().hex[:10]}"
        cmd = self._base_cmd(name, [(Path(inputs_dir), "/inputs", True)])
        cmd.insert(cmd.index(self.image), "--tmpfs")
        cmd.insert(cmd.index(self.image), "/work:rw,size=32m")
        code, out, err = self._exec(cmd + argv, name, timeout_s)
        return {"exit_code": code, "stdout": _trim(out), "stderr": _trim(err),
                "timed_out": code is None}
