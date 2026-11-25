"""MCP servers, driven through a real MCP client (requirements 11-15, 21, 24).

Each server is connected in-process with ``mcp.client.Client(server)``, so these tests
exercise the actual MCP request/response path (schemas, JSON results, tool errors)
without spawning processes. The Git server is the official ``mcp-server-git`` and is
started as a real stdio subprocess.
"""

import json
import sys

import anyio
import pandas as pd
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from adpb.approval import issue_token
from adpb.mcp_servers.artifacts import build_artifacts_server
from adpb.mcp_servers.memory import HashEmbedding, build_memory_server
from adpb.mcp_servers.warehouse import build_warehouse_server
from adpb.workspace import Workspace


def call(server, tool, args=None):
    """Call one tool over MCP; return (parsed JSON or text, is_error)."""
    async def _go():
        async with Client(server) as client:
            res = await client.call_tool(tool, args or {})
            text = res.content[0].text if res.content else ""
            try:
                return json.loads(text), res.is_error
            except json.JSONDecodeError:
                return text, res.is_error
    return anyio.run(_go)


@pytest.fixture
def ws(tmp_path):
    return Workspace.create(tmp_path / "runs", run_id="run-test")


# -- artifacts (the Filesystem MCP, requirement 11) ------------------------------------------


class TestArtifacts:
    def test_writes_are_versioned_never_overwritten(self, ws):
        srv = build_artifacts_server(ws.root)
        first, _ = call(srv, "write_text", {"path": "code/clean.py", "content": "v1"})
        second, _ = call(srv, "write_text", {"path": "code/clean.py", "content": "v2"})
        assert first["path"] == "code/clean.v1.py" and second["path"] == "code/clean.v2.py"
        latest, _ = call(srv, "read_text", {"path": "code/clean.py"})
        assert latest["content"] == "v2" and latest["version"] == 2
        old, _ = call(srv, "read_text", {"path": "code/clean.v1.py"})
        assert old["content"] == "v1"

    def test_paths_cannot_escape_the_run_folder(self, ws):
        srv = build_artifacts_server(ws.root)
        res, is_error = call(srv, "write_text", {"path": "../../evil.txt", "content": "x"})
        assert is_error and "outside" in str(res)

    def test_there_is_no_delete_tool(self, ws):
        async def _names():
            async with Client(build_artifacts_server(ws.root)) as c:
                return [t.name for t in (await c.list_tools()).tools]
        names = anyio.run(_names)
        assert not any("delete" in n or "remove" in n for n in names)

    def test_store_dataset_versions_parquet_outputs(self, ws):
        src = ws.sandbox_dir / "job1" / "out" / "clean.parquet"
        src.parent.mkdir(parents=True)
        pd.DataFrame({"a": [1]}).to_parquet(src)
        srv = build_artifacts_server(ws.root)
        res, _ = call(srv, "store_dataset", {"name": "clean", "source_path": str(src)})
        assert res["path"] == "data/clean.v1.parquet" and res["rows"] == 1
        res2, _ = call(srv, "store_dataset", {"name": "clean", "source_path": str(src)})
        assert res2["path"] == "data/clean.v2.parquet"

    def test_store_dataset_refuses_files_from_outside_the_run(self, ws, tmp_path):
        outside = tmp_path / "x.parquet"
        pd.DataFrame({"a": [1]}).to_parquet(outside)
        res, is_error = call(build_artifacts_server(ws.root), "store_dataset",
                             {"name": "x", "source_path": str(outside)})
        assert is_error


# -- warehouse (requirements 13, 20, 21) ----------------------------------------------------


@pytest.fixture
def warehouse(ws, tmp_path):
    db = tmp_path / "warehouse.db"
    out = ws.root / "data" / "final.v1.parquet"
    pd.DataFrame({"id": pd.array([1, 2], dtype="Int64"), "v": ["a", "b"]}).to_parquet(out)
    srv = build_warehouse_server(url=f"sqlite:///{db}", approval_secret="s3cret-approval",
                                 allowed_root=ws.root.parent)
    return srv, db, out, ws


class TestWarehouse:
    def test_staging_never_touches_the_production_table(self, warehouse):
        srv, db, out, ws = warehouse
        res, _ = call(srv, "stage_table", {"dataset_path": str(out), "table": "orders"})
        assert res["staging_table"] == "staging__orders" and res["rows"] == 2
        tables, _ = call(srv, "describe")
        assert "orders" not in tables["tables"]
        assert tables["tables"]["staging__orders"] == 2

    def test_promotion_without_a_valid_approval_token_is_refused(self, warehouse):
        srv, db, out, ws = warehouse
        staged, _ = call(srv, "stage_table", {"dataset_path": str(out), "table": "orders"})
        res, is_error = call(srv, "promote", {"table": "orders", "run_id": ws.run_id,
                                              "approval_token": "forged"})
        assert is_error and "approval" in str(res).lower()
        tables, _ = call(srv, "describe")
        assert "orders" not in tables["tables"]

    def test_promotion_with_a_human_approval_token_loads_the_final_table(self, warehouse):
        srv, db, out, ws = warehouse
        staged, _ = call(srv, "stage_table", {"dataset_path": str(out), "table": "orders"})
        token = issue_token("s3cret-approval", ws.run_id, "orders", staged["fingerprint"])
        res, is_error = call(srv, "promote", {"table": "orders", "run_id": ws.run_id,
                                              "approval_token": token})
        assert not is_error, res
        assert res["rows"] == 2
        preview, _ = call(srv, "preview", {"table": "orders", "limit": 5})
        assert [r["v"] for r in preview["rows"]] == ["a", "b"]

    def test_token_for_different_data_is_rejected(self, warehouse):
        srv, db, out, ws = warehouse
        call(srv, "stage_table", {"dataset_path": str(out), "table": "orders"})
        token = issue_token("s3cret-approval", ws.run_id, "orders", "fingerprint-of-other-data")
        _, is_error = call(srv, "promote", {"table": "orders", "run_id": ws.run_id,
                                            "approval_token": token})
        assert is_error

    def test_quarantine_rows_get_their_own_table(self, warehouse):
        srv, db, out, ws = warehouse
        q = ws.root / "data" / "q.parquet"
        pd.DataFrame({"id": ["x"], "_reason": ["bad id"], "_stage": ["clean"]}).to_parquet(q)
        res, _ = call(srv, "write_quarantine", {"paths": [str(q)], "table": "orders"})
        assert res["table"] == "quarantine__orders" and res["rows"] == 1

    def test_table_names_are_validated(self, warehouse):
        srv, db, out, ws = warehouse
        _, is_error = call(srv, "stage_table", {"dataset_path": str(out),
                                                "table": "x; drop table y"})
        assert is_error

    def test_secrets_never_appear_in_tool_output(self, warehouse):
        srv, db, out, ws = warehouse
        res, _ = call(srv, "describe")
        assert "s3cret" not in json.dumps(res)


# -- memory (requirement 14, mitigation 8) ------------------------------------------------


class TestMemory:
    def test_fixes_are_found_by_similar_error_and_filtered_by_stage_kind(self, tmp_path):
        srv = build_memory_server(tmp_path / "mem", embedding=HashEmbedding())
        call(srv, "remember_fix", {"error": "KeyError: 'Amount' column not found",
                                   "fix": "normalise column names to lower case first",
                                   "stage_kind": "clean"})
        call(srv, "remember_fix", {"error": "duckdb Binder Error: column amount not found",
                                   "fix": "quote identifiers", "stage_kind": "join"})
        res, _ = call(srv, "search_fixes", {"error": "KeyError: 'Amount'", "stage_kind": "clean"})
        assert res["results"][0]["fix"].startswith("normalise")
        assert all(r["stage_kind"] == "clean" for r in res["results"])

    def test_pipelines_and_schemas_are_searchable(self, tmp_path):
        srv = build_memory_server(tmp_path / "mem", embedding=HashEmbedding())
        call(srv, "remember_pipeline", {"request": "join orders to customers and load",
                                        "summary": "clean x2, join, load", "outcome": "succeeded"})
        call(srv, "remember_schema", {"source": "orders", "columns": ["order_id", "amount"]})
        p, _ = call(srv, "search_pipelines", {"query": "orders customers join"})
        s, _ = call(srv, "search_schemas", {"query": "orders"})
        assert p["results"][0]["outcome"] == "succeeded"
        assert "order_id" in s["results"][0]["columns"]

    def test_memory_persists_across_server_restarts(self, tmp_path):
        call(build_memory_server(tmp_path / "mem", embedding=HashEmbedding()), "remember_fix",
             {"error": "ValueError x", "fix": "f", "stage_kind": "clean"})
        res, _ = call(build_memory_server(tmp_path / "mem", embedding=HashEmbedding()),
                      "search_fixes", {"error": "ValueError x"})
        assert res["results"]

    def test_empty_memory_returns_no_results_not_an_error(self, tmp_path):
        res, is_error = call(build_memory_server(tmp_path / "mem", embedding=HashEmbedding()),
                             "search_fixes", {"error": "anything"})
        assert not is_error and res["results"] == []


# -- git (requirement 12): the official server, over stdio ------------------------------------


def test_official_git_mcp_server_commits_and_logs(ws):
    (ws.pipeline_dir / "stages").mkdir(exist_ok=True)
    (ws.pipeline_dir / "stages" / "clean.py").write_text("def run(inputs): ...\n")
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "mcp_server_git", "--repository",
                                         str(ws.pipeline_dir)])

    async def _go():
        async with Client(params) as c:
            repo = str(ws.pipeline_dir)
            await c.call_tool("git_add", {"repo_path": repo, "files": ["stages/clean.py"]})
            await c.call_tool("git_commit", {"repo_path": repo, "message": "stage clean passed"})
            res = await c.call_tool("git_log", {"repo_path": repo, "max_count": 5})
            return res.content[0].text
    log = anyio.run(_go)
    assert "stage clean passed" in log
