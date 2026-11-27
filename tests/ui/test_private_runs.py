"""Private runs on a public server (app/visitor.py): a visitor only sees their own runs."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adpb.llm.scripted import ScriptedModel
from adpb.runtime import RunHandle
from app.visitor import all_runs_dirs, visitor_home
from tests.integration.pipeline_fixtures import CUSTOMERS_CSV, ORDERS_CSV, happy_script, request

PAGES = Path(__file__).resolve().parents[2] / "app" / "views"
ALICE, BOB = "a" * 43, "b" * 43


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ADPB_HOME", str(tmp_path / "var"))
    monkeypatch.setenv("ADPB_SANDBOX", "local")
    monkeypatch.setenv("ADPB_EMBEDDINGS", "hash")
    monkeypatch.setenv("ADPB_PRIVATE_RUNS", "1")
    return tmp_path / "var"


@pytest.fixture
def alices_run(home, tmp_path):
    (tmp_path / "customers.csv").write_text(CUSTOMERS_CSV)
    (tmp_path / "orders.csv").write_text(ORDERS_CSV)
    h = RunHandle.create(request(), [tmp_path / "customers.csv", tmp_path / "orders.csv"],
                         home=visitor_home(home, ALICE), model=ScriptedModel(happy_script()))
    assert h.run()["status"] == "awaiting_approval"
    return h


def page(name, token, **state):
    at = AppTest.from_file(str(PAGES / name), default_timeout=30)
    at.session_state["_visitor"] = token
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def test_each_visitor_gets_a_separate_folder(home):
    assert visitor_home(home, ALICE) != visitor_home(home, BOB)
    assert ALICE not in str(visitor_home(home, ALICE))   # the token is not in the path


def test_the_owner_sees_the_run_on_past_runs(home, alices_run):
    at = page("runs.py", ALICE)
    assert not at.exception
    assert at.selectbox[0].value == alices_run.ws.run_id


def test_another_visitor_sees_no_runs(home, alices_run):
    at = page("runs.py", BOB)
    assert not at.exception
    assert not at.selectbox
    assert "Nothing has run yet" in " ".join(m.value for m in at.markdown)


def test_another_visitor_cannot_open_the_run_by_its_address(home, alices_run):
    at = AppTest.from_file(str(PAGES / "build.py"), default_timeout=30)
    at.session_state["_visitor"] = BOB
    at.query_params["run"] = alices_run.ws.run_id
    at.run()
    assert not at.exception
    assert any(b.label == "Build the pipeline" for b in at.button)   # back at the empty form
    assert not any(b.label.startswith("Approve") for b in at.button)


def test_the_owner_can_open_and_decide_the_run(home, alices_run):
    at = page("build.py", ALICE, run_id=alices_run.ws.run_id)
    assert not at.exception
    assert any(b.label.startswith("Approve and load") for b in at.button)


def test_the_daily_cap_counts_every_visitors_runs(home, alices_run):
    assert alices_run.ws.root.parent in all_runs_dirs()


def test_runs_stay_shared_without_the_setting(home, alices_run, monkeypatch):
    monkeypatch.delenv("ADPB_PRIVATE_RUNS")
    at = page("runs.py", BOB)
    assert not at.selectbox   # the shared folder is empty; visitor folders are not listed
