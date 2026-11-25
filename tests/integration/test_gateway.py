"""Tool gateway: least-privilege tool access over MCP (requirement 15; mitigation 13).

"Tools can only be used via MCPs and each agent should only have access to the tools
they require." The gateway is the single place tools are reached; a role that asks for a
tool outside its allow-list is refused before any request reaches a server.
"""

import pytest

from adpb.mcp_servers.common import ToolError, new_server
from adpb.mcp_servers.sandbox import build_sandbox_server
from adpb.observability.events import EventLog
from adpb.tools.gateway import ToolFailed, ToolGateway, ToolNotAllowed
from adpb.tools.registry import ToolPolicy
from adpb.workspace import Workspace


def _servers(calls):
    memory = new_server("memory", "test memory")

    @memory.tool()
    def search_fixes(error: str, stage_kind: str | None = None, k: int = 3) -> dict:
        """Search fixes."""
        calls.append(("search_fixes", error))
        return {"results": [{"fix": "lower-case the columns"}]}

    @memory.tool()
    def remember_fix(error: str, fix: str, stage_kind: str, code_excerpt: str = "") -> dict:
        """Remember a fix."""
        calls.append(("remember_fix", error))
        return {"stored": True}

    warehouse = new_server("warehouse", "test warehouse")

    @warehouse.tool()
    def promote(table: str, run_id: str, approval_token: str, mode: str = "replace") -> dict:
        """Promote."""
        calls.append(("promote", table))
        raise ToolError("approval token is missing or invalid")

    return {"memory": memory, "warehouse": warehouse}


@pytest.fixture
def gateway(tmp_path):
    calls: list = []
    events = EventLog(tmp_path / "events.jsonl", run_id="r")
    with ToolGateway(_servers(calls), ToolPolicy(), events=events) as gw:
        yield gw, calls, events


def test_allowed_call_returns_parsed_json(gateway):
    gw, calls, _ = gateway
    res = gw.call("coder", "memory", "search_fixes", {"error": "KeyError"})
    assert res["results"][0]["fix"] == "lower-case the columns"
    assert calls == [("search_fixes", "KeyError")]


def test_disallowed_call_is_refused_before_reaching_the_server(gateway):
    gw, calls, _ = gateway
    with pytest.raises(ToolNotAllowed, match="coder.*memory.remember_fix"):
        gw.call("coder", "memory", "remember_fix",
                {"error": "e", "fix": "f", "stage_kind": "clean"})
    assert calls == []


@pytest.mark.parametrize("role", ["planner", "coder", "debugger", "tester", "validator"])
def test_no_agent_can_promote_to_production(gateway, role):
    gw, calls, _ = gateway
    with pytest.raises(ToolNotAllowed):
        gw.call(role, "warehouse", "promote",
                {"table": "t", "run_id": "r", "approval_token": "x"})
    assert calls == []


def test_tool_errors_surface_with_their_message(gateway):
    gw, _, _ = gateway
    with pytest.raises(ToolFailed, match="approval token"):
        gw.call("orchestrator", "warehouse", "promote",
                {"table": "t", "run_id": "r", "approval_token": "x"})


def test_model_sees_only_its_own_tools_with_qualified_names(gateway):
    gw, _, _ = gateway
    names = [t.name for t in gw.specs_for("coder")]
    assert names == ["memory__search_fixes"]
    assert gw.specs_for("tester") == []  # the Tester's model gets no tools at all
    spec = gw.specs_for("coder")[0]
    assert spec.input_schema["properties"]["error"]["type"] == "string"


def test_qualified_calls_route_to_the_right_server(gateway):
    gw, calls, _ = gateway
    gw.call_qualified("coder", "memory__search_fixes", {"error": "boom"})
    assert calls[-1] == ("search_fixes", "boom")


def test_every_call_is_logged_with_agent_server_and_outcome(gateway):
    gw, _, events = gateway
    gw.call("coder", "memory", "search_fixes", {"error": "x"})
    with pytest.raises(ToolNotAllowed):
        gw.call("coder", "memory", "remember_fix", {"error": "e", "fix": "f",
                                                     "stage_kind": "c"})
    logged = [e for e in EventLog.read(events.path) if e["kind"] == "tool_call"]
    assert [(e["agent"], e["tool"], e["outcome"]) for e in logged] == [
        ("coder", "search_fixes", "ok"), ("coder", "remember_fix", "denied")]


class FakeSandbox:
    def __init__(self):
        self.jobs = []

    def run_stage(self, **kw):
        self.jobs.append(kw)
        return {"ok": True, "stage_id": kw["stage_id"]}


def test_sandbox_server_resolves_inputs_inside_the_run(tmp_path):
    ws = Workspace.create(tmp_path, "r1")
    (ws.data_dir / "raw.t.v1.parquet").write_bytes(b"x")
    fake = FakeSandbox()
    servers = {"sandbox": build_sandbox_server(ws.root, sandbox=fake)}
    with ToolGateway(servers, ToolPolicy()) as gw:
        res = gw.call("tester", "sandbox", "run_stage", {
            "stage_id": "s", "code": "def run(i): ...", "language": "python",
            "inputs": {"raw.t": "data/raw.t.v1.parquet"}, "output_schema": {}, "tests": [],
            "job": "s-try1"})
        assert res["ok"]
        assert fake.jobs[0]["inputs"]["raw.t"] == ws.data_dir / "raw.t.v1.parquet"
        with pytest.raises(ToolFailed, match="outside"):
            gw.call("tester", "sandbox", "run_stage", {
                "stage_id": "s", "code": "x", "language": "python",
                "inputs": {"raw.t": "../../../etc/passwd"}, "output_schema": {}, "tests": [],
                "job": "s-try2"})
