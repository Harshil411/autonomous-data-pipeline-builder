"""Streamlit pages, driven headlessly with `streamlit.testing.AppTest`.

The run under test is produced by the scripted model, so no API key is needed. The
approval test clicks the real button and checks that the load really happened.
"""

import sqlite3
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adpb.llm.scripted import ScriptedModel
from adpb.runtime import RunHandle
from tests.integration.pipeline_fixtures import CUSTOMERS_CSV, ORDERS_CSV, happy_script, request

PAGES = Path(__file__).resolve().parents[2] / "app" / "views"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ADPB_HOME", str(tmp_path / "var"))
    monkeypatch.setenv("ADPB_SANDBOX", "local")
    monkeypatch.setenv("ADPB_EMBEDDINGS", "hash")
    monkeypatch.setenv("ADPB_APPROVAL_SECRET", "page-test-secret-xx")
    return tmp_path / "var"


@pytest.fixture
def paused_run(home, tmp_path):
    (tmp_path / "customers.csv").write_text(CUSTOMERS_CSV)
    (tmp_path / "orders.csv").write_text(ORDERS_CSV)
    h = RunHandle.create(request(), [tmp_path / "customers.csv", tmp_path / "orders.csv"],
                         home=home, model=ScriptedModel(happy_script()))
    assert h.run()["status"] == "awaiting_approval"
    return h


def test_new_run_form_prefills_the_showcase_example(home):
    at = AppTest.from_file(str(PAGES / "build.py"), default_timeout=30).run()
    assert not at.exception
    assert "customers" in at.text_area[0].value.lower()
    assert at.button[0].label == "Build the pipeline"


def test_form_refuses_a_request_without_sources(home):
    at = AppTest.from_file(str(PAGES / "build.py"), default_timeout=30).run()
    at.toggle[0].set_value(False).run()
    at.text_area[0].set_value("Load my files and clean them please.")
    at.button[0].click().run()
    assert any("at least one source" in e.value for e in at.error)


def test_approval_gate_loads_the_data_when_approved(home, paused_run):
    at = AppTest.from_file(str(PAGES / "build.py"), default_timeout=30)
    at.session_state["run_id"] = paused_run.ws.run_id
    at.run()
    assert not at.exception
    load = next(b for b in at.button if b.label.startswith("Load 4 rows"))
    at.text_input[0].set_value("Booth visitor")
    load.click().run()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and paused_run.state().get("status") != "succeeded":
        time.sleep(0.2)
    assert paused_run.state()["status"] == "succeeded"
    con = sqlite3.connect(paused_run.settings.warehouse_path)
    assert con.execute("select count(*) from customer_orders").fetchone()[0] == 4
    con.close()


def test_runs_page_lists_the_run_and_renders_it(home, paused_run):
    at = AppTest.from_file(str(PAGES / "runs.py"), default_timeout=30).run()
    assert not at.exception
    assert at.selectbox[0].value == paused_run.ws.run_id
    assert any(b.label == "Replay from the start" for b in at.button)


@pytest.mark.parametrize("page", ["benchmarks.py", "how_it_works.py"])
def test_static_pages_render(home, page):
    at = AppTest.from_file(str(PAGES / page), default_timeout=30).run()
    assert not at.exception
