"""Real-container tests for the sandbox (requirements 5 and 16).

These start actual Docker containers, so they prove the isolation claims instead of
asserting them: the flyer's "won't reach the internet from its sandbox" is
`test_network_is_unreachable`. Skipped automatically when Docker is not running.
"""

import json
import time

import pandas as pd
import pytest

from adpb.sandbox.docker_runner import DockerSandbox, ShellNotAllowed, docker_available

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available(), reason="Docker daemon or sandbox image missing"),
]

SCHEMA = {"name": "s", "primary_key": [], "columns": [
    {"name": "x", "dtype": "integer", "nullable": False}]}


@pytest.fixture
def sandbox():
    return DockerSandbox(timeout_s=20, memory="256m")


def _run(sandbox, tmp_path, code, tests=None):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    pd.DataFrame({"x": ["1", "2", "3"]}).to_parquet(data / "raw.t.parquet")
    return sandbox.run_stage(
        stage_id="s", code=code, language="python", inputs={"raw.t": data / "raw.t.parquet"},
        output_schema=SCHEMA, tests=tests or [], job_dir=tmp_path / "job")


def test_good_stage_runs_and_output_lands_on_the_host(sandbox, tmp_path):
    res = _run(sandbox, tmp_path, "def run(inputs):\n    return inputs['raw.t']\n")
    assert res["ok"], res
    assert pd.read_parquet(res["output_path"])["x"].tolist() == [1, 2, 3]


def test_network_is_unreachable(sandbox, tmp_path):
    code = '''
import socket
def run(inputs):
    socket.create_connection(("1.1.1.1", 53), timeout=3)
    return inputs["raw.t"]
'''
    res = _run(sandbox, tmp_path, code)
    assert not res["ok"]
    assert res["error_type"] in {"OSError", "TimeoutError", "socket.gaierror", "gaierror"}


def test_host_filesystem_is_not_visible(sandbox, tmp_path):
    code = '''
import os
def run(inputs):
    os.listdir("/Users")
    return inputs["raw.t"]
'''
    res = _run(sandbox, tmp_path, code)
    assert not res["ok"] and res["error_type"] == "FileNotFoundError"


def test_inputs_are_read_only_and_root_fs_is_read_only(sandbox, tmp_path):
    code = '''
def run(inputs):
    open("/etc/evil", "w").write("x")
    return inputs["raw.t"]
'''
    res = _run(sandbox, tmp_path, code)
    assert not res["ok"] and res["error_type"] in {"OSError", "PermissionError"}


def test_code_runs_as_a_non_root_user(sandbox, tmp_path):
    tests = [{"name": "not_root", "description": "", "check": "__import__('os').getuid() != 0"}]
    res = _run(sandbox, tmp_path, "def run(inputs):\n    return inputs['raw.t']\n", tests)
    assert res["ok"], res


def test_runaway_code_is_killed_at_the_time_limit(tmp_path):
    sandbox = DockerSandbox(timeout_s=4)
    started = time.monotonic()
    res = _run(sandbox, tmp_path, "def run(inputs):\n    while True:\n        pass\n")
    assert res["error_type"] == "Timeout"
    assert time.monotonic() - started < 20


def test_memory_limit_is_enforced(tmp_path):
    sandbox = DockerSandbox(timeout_s=20, memory="128m")
    code = ("def run(inputs):\n"
            "    blob = bytearray(1024 * 1024 * 1024)\n"
            "    return inputs['raw.t']\n")
    res = _run(sandbox, tmp_path, code)
    assert not res["ok"]
    assert res["error_type"] in {"MemoryError", "ResourceLimit"}


def test_quality_job_runs_in_the_container(sandbox, tmp_path):
    path = tmp_path / "d.parquet"
    pd.DataFrame({"x": pd.array([1, None], dtype="Int64")}).to_parquet(path)
    res = sandbox.run_quality(datasets={"d": path},
                              rules=[{"kind": "not_null", "dataset": "d", "column": "x"}],
                              job_dir=tmp_path / "q")
    assert res["ok"] and res["checks"][0]["failing_rows"] == 1


def test_allow_listed_shell_command_runs_inside_the_container(sandbox, tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.txt").write_text("hello\n")
    out = sandbox.run_command(["wc", "-l", "/inputs/a.txt"], inputs_dir=tmp_path / "data")
    assert out["exit_code"] == 0 and out["stdout"].strip().startswith("1")


def test_disallowed_shell_command_never_starts_a_container(sandbox, tmp_path):
    with pytest.raises(ShellNotAllowed):
        sandbox.run_command(["curl", "https://example.com"], inputs_dir=tmp_path)


def test_job_file_is_left_for_audit(sandbox, tmp_path):
    _run(sandbox, tmp_path, "def run(inputs):\n    return inputs['raw.t']\n")
    job = json.loads((tmp_path / "job" / "job.json").read_text())
    assert job["inputs"] == {"raw.t": "/inputs/raw.t.parquet"}  # container paths only
