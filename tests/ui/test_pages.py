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
    assert any(b.label == "Build the pipeline" for b in at.button)
    presets = [b for b in at.button if b.key and b.key.startswith("preset-")]
    assert len(presets) == 6
    assert "Course grades" in [b.label for b in presets]  # the example for instructors
    # the Describe screen leads with one sentence and the journey's three steps
    page = " ".join(m.value for m in at.markdown)
    assert "Describe the data you need" in page
    assert all(step in page for step in ("Describe", "Build", "Approve"))


def test_form_refuses_a_request_without_sources(home):
    at = AppTest.from_file(str(PAGES / "build.py"), default_timeout=30).run()
    next(b for b in at.button if b.key == "preset-custom").click().run()
    at.text_area[0].set_value("Load my files and clean them please.")
    next(b for b in at.button if b.label == "Build the pipeline").click().run()
    assert any("at least one source" in e.value for e in at.error)


def test_approval_gate_loads_the_data_when_approved(home, paused_run):
    at = AppTest.from_file(str(PAGES / "build.py"), default_timeout=30)
    at.session_state["run_id"] = paused_run.ws.run_id
    at.run()
    assert not at.exception
    # while it waits, one button leads back to an empty request
    assert any(b.label == "New request" for b in at.button)
    load = next(b for b in at.button if b.label.startswith("Approve and load 4 rows"))
    at.text_input[0].set_value("Booth visitor")
    load.click().run()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and paused_run.state().get("status") != "succeeded":
        time.sleep(0.2)
    assert paused_run.state()["status"] == "succeeded"
    con = sqlite3.connect(paused_run.settings.warehouse_path)
    assert con.execute("select count(*) from customer_orders").fetchone()[0] == 4
    con.close()
    # the finished screen shows the result and the next step, not just a headline
    at.run()
    assert not at.exception
    page = " ".join(m.value for m in at.markdown)
    assert "now in the database" in page
    assert len(at.dataframe) >= 1
    assert any(b.label == "Build another" for b in at.button)


def test_runs_page_lists_the_run_and_renders_it(home, paused_run):
    at = AppTest.from_file(str(PAGES / "runs.py"), default_timeout=30).run()
    assert not at.exception
    # runs are picked from one dropdown, so the graph keeps the full width of the screen
    assert at.selectbox(key="selected_run").value == paused_run.ws.run_id
    assert any(b.label == "Replay this run" for b in at.button)
    # "Resume" is offered only for a run that failed
    assert not any(b.label.startswith("Resume") for b in at.button)
    # the run is drawn as the live graph, with the gate asking for a person
    assert any('class="node gate needed"' in m.value for m in at.markdown)
    # a paused run can be decided right here
    assert any(b.label.startswith("Approve and load 4 rows") for b in at.button)


@pytest.mark.parametrize("page", ["benchmarks.py", "how_it_works.py"])
def test_static_pages_render(home, page):
    at = AppTest.from_file(str(PAGES / page), default_timeout=30).run()
    assert not at.exception


def test_a_run_survives_a_page_reload(home, paused_run):
    # Switching the look reloads the page, and a visitor may hit refresh mid-demo. The run
    # is named in the address bar, so a fresh session picks it up again.
    at = AppTest.from_file(str(PAGES / "build.py"), default_timeout=30)
    at.query_params["run"] = paused_run.ws.run_id
    at.run()
    assert not at.exception
    assert any(b.label.startswith("Approve and load 4 rows") for b in at.button)


def test_an_unknown_run_in_the_address_bar_falls_back_to_describe(home):
    at = AppTest.from_file(str(PAGES / "build.py"), default_timeout=30)
    at.query_params["run"] = "run-that-does-not-exist"
    at.run()
    assert not at.exception
    assert any(b.label == "Build the pipeline" for b in at.button)


def test_header_offers_a_switch_between_the_two_looks(home):
    at = AppTest.from_file(str(PAGES.parent / "Home.py"), default_timeout=30).run()
    assert not at.exception
    assert at.button(key="look").label == "Light"   # the look it would switch to


def test_header_carries_the_logo_and_the_name(home):
    at = AppTest.from_file(str(PAGES.parent / "Home.py"), default_timeout=30).run()
    mark = next(m.value for m in at.markdown if 'class="wordmark"' in m.value)
    # the mark is a drawing of the product: two sources merging into one result
    assert '<svg class="logo"' in mark and 'aria-hidden="true"' in mark
    assert "Pipeline Builder" in mark


def test_detail_is_one_closed_section_with_three_tabs(home, paused_run):
    at = AppTest.from_file(str(PAGES / "build.py"), default_timeout=30)
    at.session_state["run_id"] = paused_run.ws.run_id
    at.run()
    details = next(e for e in at.expander if e.label == "Details")
    assert details.proto.expanded is False
    labels = [t.label for t in at.tabs]
    # (the list also holds the tabs nested inside Checks: Tests, Code, Output contract)
    assert labels[0] == "What happened" and {"Checks", "Cost"} <= set(labels)
    assert not {"Activity", "Data quality", "Budget", "Report"} & set(labels)


def test_own_files_form_does_not_ask_about_databases_up_front(home):
    at = AppTest.from_file(str(PAGES / "build.py"), default_timeout=30).run()
    next(b for b in at.button if b.key == "preset-custom").click().run()
    assert not any("SQLite" in t.label for t in at.text_input)
