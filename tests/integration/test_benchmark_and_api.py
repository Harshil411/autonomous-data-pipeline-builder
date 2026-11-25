"""Benchmark runner (requirement 26) and API sources fetched through the allow-list.

The live benchmark costs money, so here the runner is driven by a scripted model factory:
these tests prove the measurement (success, retries, time, cost, invariants) is right.
"""

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from adpb.benchmark import run_suite
from adpb.contracts import SourceKind, SourceSpec
from adpb.llm.scripted import ScriptedModel
from adpb.tools.sources import SourceError, fetch_api_snapshot, load_raw
from tests.integration.pipeline_fixtures import CUSTOMERS_CSV, ORDERS_CSV, happy_script


@pytest.fixture
def api_server(tmp_path):
    (tmp_path / "www").mkdir()
    (tmp_path / "www" / "rates.json").write_text(json.dumps(
        {"rates": [{"currency": "EUR", "rate": 0.9}, {"currency": "GBP", "rate": 0.8}]}))
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path / "www"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_api_source_is_snapshotted_then_loaded_like_json(tmp_path, api_server):
    spec = SourceSpec(name="fx", kind=SourceKind.API, url=f"{api_server}/rates.json")
    fetch_api_snapshot(spec, tmp_path, allowed_hosts={"127.0.0.1"})
    frame = load_raw(spec, tmp_path).frame
    assert frame["currency"].tolist() == ["EUR", "GBP"]


def test_api_hosts_off_the_allow_list_are_refused(tmp_path, api_server):
    spec = SourceSpec(name="fx", kind=SourceKind.API, url=f"{api_server}/rates.json")
    with pytest.raises(SourceError, match="allow-list"):
        fetch_api_snapshot(spec, tmp_path, allowed_hosts={"example.com"})


def test_benchmark_measures_success_retries_time_and_cost(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "customers.csv").write_text(CUSTOMERS_CSV)
    (tmp_path / "data" / "orders.csv").write_text(ORDERS_CSV)
    (tmp_path / "req.yaml").write_text(yaml.safe_dump({
        "request": "Load the customers and orders CSVs, clean them, join and load.",
        "target_table": "customer_orders",
        "sources": [{"name": "customers", "kind": "csv", "path": "data/customers.csv"},
                    {"name": "orders", "kind": "csv", "path": "data/orders.csv"}]}))
    (tmp_path / "suite.yaml").write_text(yaml.safe_dump([
        {"name": "ok", "request": "req.yaml",
         "expect": {"min_rows": 4, "max_rows": 4, "columns": ["order_id", "name"]}},
        {"name": "wrong_expectation", "request": "req.yaml", "expect": {"min_rows": 100}},
    ]))
    report = run_suite(tmp_path / "suite.yaml", repeat=1, home=tmp_path / "var",
                       model_factory=lambda: ScriptedModel(happy_script()),
                       sandbox="local", embeddings="hash", results_dir=tmp_path / "results")
    by_name = {r["name"]: r for r in report["runs"]}
    assert by_name["ok"]["success"] is True
    assert by_name["wrong_expectation"]["success"] is False
    assert "rows" in by_name["wrong_expectation"]["why"]
    s = report["summary"]
    assert s["runs"] == 2 and s["success_rate"] == 0.5
    assert s["mean_retries_per_stage"] == 0
    assert "total_usd" in s and "mean_seconds" in s
    assert list((tmp_path / "results").glob("*.md"))
