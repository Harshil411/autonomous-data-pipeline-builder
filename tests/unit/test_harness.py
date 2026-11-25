"""The sandbox harness, exercised in-process (the same file runs inside the container).

The harness loads input parquet files, executes the stage code, conforms the output,
runs the Tester's assertions and writes a JSON verdict. Errors become structured results
for the Debugger - never an exception that escapes the harness.
"""

import json

import pandas as pd

from adpb.sandbox_runtime.harness import run_job

SCHEMA = {"name": "clean_orders", "primary_key": ["order_id"], "columns": [
    {"name": "order_id", "dtype": "integer", "nullable": False},
    {"name": "amount", "dtype": "float", "nullable": True},
]}

GOOD_CODE = '''
import pandas as pd

def run(inputs):
    df = inputs["raw.orders"].copy()
    df["amount"] = df["amount"].str.replace("$", "", regex=False)
    return df[["order_id", "amount"]]
'''


def _job(tmp_path, code, tests=None, language="python", inputs=None):
    raw = pd.DataFrame({"order_id": ["1", "2", "3", "bad"], "amount": ["$5", "$7.5", "", "$1"]})
    in_path = tmp_path / "raw.orders.parquet"
    raw.to_parquet(in_path)
    code_path = tmp_path / ("code.sql" if language == "sql" else "code.py")
    code_path.write_text(code)
    job = {
        "mode": "stage", "stage_id": "clean_orders", "language": language,
        "code_path": str(code_path), "inputs": inputs or {"raw.orders": str(in_path)},
        "output_schema": SCHEMA, "tests": tests or [],
        "output_path": str(tmp_path / "out" / "clean_orders.parquet"),
        "quarantine_path": str(tmp_path / "out" / "clean_orders.quarantine.parquet"),
        "result_path": str(tmp_path / "result.json"), "max_quarantine_fraction": 0.5,
    }
    run_job(job)
    return json.loads((tmp_path / "result.json").read_text())


def test_good_code_passes_and_writes_typed_output(tmp_path):
    res = _job(tmp_path, GOOD_CODE)
    assert res["ok"], res["error"]
    out = pd.read_parquet(tmp_path / "out" / "clean_orders.parquet")
    assert out["order_id"].tolist() == [1, 2, 3]
    assert res["rows_in"] == {"raw.orders": 4}
    assert res["rows_out"] == 3 and res["rows_quarantined"] == 1


def test_exceptions_become_structured_errors(tmp_path):
    res = _job(tmp_path, "def run(inputs):\n    return inputs['raw.missing']\n")
    assert not res["ok"]
    assert res["error_type"] == "KeyError"
    assert "raw.missing" in res["error"]


def test_code_without_a_run_function_is_rejected(tmp_path):
    res = _job(tmp_path, "x = 1\nprint('hello')\n")
    assert not res["ok"] and "run(inputs)" in res["error"]


def test_schema_problems_are_reported_as_their_own_error_type(tmp_path):
    res = _job(tmp_path, "def run(inputs):\n    return inputs['raw.orders'][['amount']]\n")
    assert not res["ok"] and res["error_type"] == "SchemaMismatch"


def test_tester_assertions_are_evaluated(tmp_path):
    tests = [
        {"name": "positive_amounts", "description": "",
         "check": "out['amount'].dropna().gt(0).all()"},
        {"name": "has_four_rows", "description": "", "check": "len(out) == 4"},
        {"name": "broken_check", "description": "", "check": "out['nope'].sum() > 0"},
    ]
    res = _job(tmp_path, GOOD_CODE, tests=tests)
    by_name = {t["name"]: t for t in res["tests"]}
    assert by_name["positive_amounts"]["passed"]
    assert not by_name["has_four_rows"]["passed"]
    assert not by_name["broken_check"]["passed"] and "nope" in by_name["broken_check"]["detail"]
    assert not res["ok"]  # any failing test fails the stage


def test_sql_stages_run_on_duckdb_with_dots_mapped_to_underscores(tmp_path):
    sql = "SELECT order_id, replace(amount, '$', '') AS amount FROM raw_orders"
    res = _job(tmp_path, sql, language="sql")
    assert res["ok"], res["error"]
    assert res["rows_out"] == 3


def test_returning_a_quarantine_frame_is_supported(tmp_path):
    code = '''
def run(inputs):
    df = inputs["raw.orders"]
    bad = df[df["amount"] == ""].assign(_reason="empty amount")
    return df[df["amount"] != ""][["order_id", "amount"]].replace({"\\\\$": ""}, regex=True), bad
'''
    res = _job(tmp_path, code)
    assert res["ok"], res["error"]
    q = pd.read_parquet(tmp_path / "out" / "clean_orders.quarantine.parquet")
    assert "empty amount" in q["_reason"].tolist()


def test_quality_mode(tmp_path):
    path = tmp_path / "d.parquet"
    pd.DataFrame({"x": pd.array([1, None], dtype="Int64")}).to_parquet(path)
    job = {"mode": "quality", "datasets": {"d": str(path)},
           "rules": [{"kind": "not_null", "dataset": "d", "column": "x"}],
           "result_path": str(tmp_path / "q.json")}
    run_job(job)
    res = json.loads((tmp_path / "q.json").read_text())
    assert res["ok"] and res["checks"][0]["passed"] is False
