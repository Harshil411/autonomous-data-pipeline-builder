"""The flyer's "Five things it won't do", one named test each - plus a full pipeline run
over real stdio MCP servers and a real Docker sandbox.

Visitors at the showcase hold a handout making these five promises; each test below is
the evidence for one of them. Docker-backed tests skip when Docker is not running.
"""

import json
import sqlite3
from dataclasses import dataclass

import pytest

from adpb.contracts import DebugPatch
from adpb.llm.base import ModelTurn
from adpb.llm.scripted import ScriptedModel
from adpb.observability.events import EventLog
from adpb.observability.pricing import Usage
from adpb.runtime import RunHandle
from adpb.sandbox.docker_runner import docker_available
from adpb.tools.registry import ROLE_TOOLS
from tests.integration.pipeline_fixtures import (
    CODE_ORDERS_BUGGY,
    CUSTOMERS_CSV,
    ORDERS_CSV,
    happy_script,
    request,
)

needs_docker = pytest.mark.skipif(not docker_available(), reason="Docker not running")


@pytest.fixture
def sources(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "customers.csv").write_text(CUSTOMERS_CSV)
    (src / "orders.csv").write_text(ORDERS_CSV)
    return [src / "customers.csv", src / "orders.csv"]


def _handle(tmp_path, sources, script, req=None, model=None, **kw):
    kw.setdefault("sandbox", "local")
    return RunHandle.create(req or request(), sources, home=tmp_path / "var",
                            model=model or ScriptedModel(script),
                            approval_secret="flyer-approval-secret", embeddings="hash", **kw)


def _tables(h):
    con = sqlite3.connect(h.settings.warehouse_path)
    names = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    con.close()
    return names


# 1 -------------------------------------------------------------------------------------


def test_1_it_wont_write_to_your_db_without_your_ok(tmp_path, sources):
    h = _handle(tmp_path, sources, happy_script())
    assert h.run()["status"] == "awaiting_approval"
    assert "customer_orders" not in _tables(h)
    # No agent role can even ask for the promote tool...
    assert not any("warehouse.promote" in tools for role, tools in ROLE_TOOLS.items()
                   if role != "orchestrator")
    # ...and a rejected run never reaches production.
    h.decide(approved=False, by="visitor")
    assert "customer_orders" not in _tables(h)


# 2 -------------------------------------------------------------------------------------


def test_2_it_wont_see_your_passwords_or_api_keys(tmp_path, sources, monkeypatch):
    secret = "sk-ant-api03-FLYER-SECRET-DO-NOT-LEAK-123456"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    h = _handle(tmp_path, sources, happy_script())
    h.run()
    h.decide(approved=True, by="visitor")
    for call in h.model.calls:
        assert secret not in call["system"] + call["prompt_text"]
    for path in h.ws.root.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".md", ".py"}:
            assert secret not in path.read_text(errors="ignore"), path
    assert "flyer-approval-secret" not in h.ws.events_path.read_text()


@needs_docker
def test_2b_stage_code_cannot_read_host_secrets_from_its_environment(tmp_path, monkeypatch):
    from adpb.sandbox.docker_runner import DockerSandbox
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-HOSTSECRET-XXXXXXXXXXXX")
    import pandas as pd
    data = tmp_path / "d.parquet"
    pd.DataFrame({"x": ["1"]}).to_parquet(data)
    tests = [{"name": "no_key", "description": "",
              "check": "'ANTHROPIC_API_KEY' not in __import__('os').environ"}]
    res = DockerSandbox(timeout_s=20).run_stage(
        stage_id="s", code="def run(inputs):\n    return inputs['raw.t']\n", language="python",
        inputs={"raw.t": data}, tests=tests, job_dir=tmp_path / "job",
        output_schema={"name": "s", "columns": [{"name": "x", "dtype": "integer",
                                                 "nullable": False}]})
    assert res["ok"], res


# 3 -------------------------------------------------------------------------------------


@needs_docker
def test_3_it_wont_reach_the_internet_from_its_sandbox(tmp_path):
    import pandas as pd

    from adpb.sandbox.docker_runner import DockerSandbox
    data = tmp_path / "d.parquet"
    pd.DataFrame({"x": ["1"]}).to_parquet(data)
    code = ("import urllib.request\n"
            "def run(inputs):\n"
            "    urllib.request.urlopen('https://example.com', timeout=5)\n"
            "    return inputs['raw.t']\n")
    res = DockerSandbox(timeout_s=30).run_stage(
        stage_id="s", code=code, language="python", inputs={"raw.t": data}, tests=[],
        job_dir=tmp_path / "job",
        output_schema={"name": "s", "columns": [{"name": "x", "dtype": "integer",
                                                 "nullable": False}]})
    assert not res["ok"] and res["error_type"] in {"URLError", "OSError", "gaierror"}


# 4 -------------------------------------------------------------------------------------


def test_4_it_wont_retry_the_same_fix_forever(tmp_path, sources):
    script = happy_script()
    script["coder"] = [happy_script()["coder"][0], CODE_ORDERS_BUGGY, CODE_ORDERS_BUGGY]
    script["planner"] = [script["planner"][0], script["planner"][0]]
    script["tester"] = [*script["tester"][:2], script["tester"][1]]
    # Far more debugger answers than allowed: the cap, not the script, must stop it.
    script["debugger"] = [DebugPatch(diagnosis=f"same fix {i}", code=CODE_ORDERS_BUGGY.code)
                          for i in range(50)]
    h = _handle(tmp_path, sources, script, req=request(max_retries_per_stage=3, max_replans=1))
    state = h.run()
    assert state["status"] == "failed"
    debug_calls = [c for c in h.model.calls if c["role"] == "debugger"]
    assert len(debug_calls) <= 2 * (1 + 1)  # (retries - 1) per build, original + one replan
    assert state["replans_used"] == 1


# 5 -------------------------------------------------------------------------------------


@dataclass
class PricedModel:
    """Wraps the scripted model but reports Sonnet usage, so every call costs money."""

    inner: ScriptedModel

    @property
    def calls(self):
        return self.inner.calls

    def model_for(self, role):
        return "claude-sonnet-5-5"

    def complete(self, **kw) -> ModelTurn:
        turn = self.inner.complete(**kw)
        turn.model = "claude-sonnet-5-5"
        turn.usage = Usage(input_tokens=20_000, output_tokens=4_000)  # $0.08 per call
        return turn


def test_5_it_wont_run_past_its_cost_budget(tmp_path, sources):
    h = _handle(tmp_path, sources, {}, req=request(max_usd=0.20),
                model=PricedModel(ScriptedModel(happy_script())))
    state = h.run()
    assert state["status"] == "budget_exceeded"
    spent = sum(e.get("usd", 0) for e in EventLog.read(h.ws.events_path)
                if e["kind"] == "agent_call")
    assert spent <= 0.20 + 0.08 + 1e-9  # at most the one call that crossed the line
    assert "customer_orders" not in _tables(h)
    report = json.loads((h.ws.root / "report.json").read_text())
    assert report["status"] == "budget_exceeded"


# The real thing ------------------------------------------------------------------------


@needs_docker
@pytest.mark.slow
def test_full_pipeline_over_stdio_mcp_servers_and_a_docker_sandbox(tmp_path, sources):
    """Same path as a live demo: every MCP server is a subprocess, every stage runs in a
    locked-down container. Only the LLM is scripted."""
    h = _handle(tmp_path, sources, happy_script(), sandbox="docker", transport="stdio")
    assert h.run()["status"] == "awaiting_approval"
    state = h.decide(approved=True, by="e2e")
    assert state["status"] == "succeeded"
    con = sqlite3.connect(h.settings.warehouse_path)
    assert con.execute("select count(*) from customer_orders").fetchone()[0] == 4
    con.close()
    jobs = list((h.ws.sandbox_dir).glob("*/job.json"))
    assert jobs and all(json.loads(j.read_text())["inputs"] for j in jobs if "quality" not in
                        j.parent.name)
