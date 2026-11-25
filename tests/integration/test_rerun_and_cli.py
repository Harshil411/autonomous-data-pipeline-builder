"""Reproducibility (requirement 18), request files, and the command-line interface.

"A single command should be able to rerun the pipeline from scratch and yield the same
results": `adpb rerun <run_id>` rebuilds every stage from the sources using only the code
committed in the run's git repo - zero model calls - and compares fingerprints.
"""

import json

import pytest
import yaml

from adpb.cli import main
from adpb.llm.scripted import ScriptedModel
from adpb.orchestrator.rerun import rerun
from adpb.requests_io import load_request_file
from adpb.runtime import RunHandle
from tests.integration.pipeline_fixtures import (
    CUSTOMERS_CSV,
    ORDERS_CSV,
    happy_script,
    request,
)


@pytest.fixture
def finished_run(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "customers.csv").write_text(CUSTOMERS_CSV)
    (src / "orders.csv").write_text(ORDERS_CSV)
    h = RunHandle.create(request(), [src / "customers.csv", src / "orders.csv"],
                         home=tmp_path / "var", model=ScriptedModel(happy_script()),
                         approval_secret="s" * 16, sandbox="local", embeddings="hash")
    h.run()
    h.decide(approved=True, by="pytest")
    return h


def test_rerun_reproduces_the_same_output_without_any_model(finished_run):
    result = rerun(finished_run)
    assert result["match"] is True
    assert result["fingerprint"] == result["expected"]
    assert result["model_calls"] == 0
    assert [s["id"] for s in result["stages"]] == ["clean_customers", "clean_orders",
                                                   "customer_orders"]


def test_rerun_detects_a_tampered_stage(finished_run):
    stage = finished_run.ws.pipeline_dir / "stages" / "customer_orders.py"
    stage.write_text(stage.read_text().replace('how="inner"', 'how="left"'))
    assert rerun(finished_run)["match"] is False


def test_request_files_resolve_sources_relative_to_the_file(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text(ORDERS_CSV)
    spec = {"request": "Clean the orders file and load it into the database.",
            "target_table": "orders", "budget": {"max_usd": 0.5},
            "sources": [{"name": "orders", "kind": "csv", "path": "data/orders.csv"}]}
    (tmp_path / "req.yaml").write_text(yaml.safe_dump(spec))
    req, files = load_request_file(tmp_path / "req.yaml")
    assert req.sources[0].path == "orders.csv"
    assert files == [tmp_path / "data" / "orders.csv"]
    assert req.budget.max_usd == 0.5


def test_cli_status_lists_runs(finished_run, capsys):
    code = main(["--home", str(finished_run.settings.home), "status"])
    out = capsys.readouterr().out
    assert code == 0 and finished_run.ws.run_id in out and "succeeded" in out


def test_cli_report_prints_markdown(finished_run, capsys):
    main(["--home", str(finished_run.settings.home), "report", finished_run.ws.run_id])
    assert "## Stages" in capsys.readouterr().out


def test_cli_replay_prints_the_recorded_events(finished_run, capsys):
    main(["--home", str(finished_run.settings.home), "replay", finished_run.ws.run_id,
          "--speed", "0"])
    out = capsys.readouterr().out
    assert "plan_created" in out and "approval_decision" in out


def test_cli_rerun_exit_code_reflects_reproducibility(finished_run, capsys):
    code = main(["--home", str(finished_run.settings.home), "rerun", finished_run.ws.run_id])
    assert code == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["match"] is True
