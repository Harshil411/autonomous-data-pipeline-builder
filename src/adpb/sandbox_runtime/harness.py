"""The program that runs inside the sandbox container.

Usage (inside the container):  python /opt/adpb_runtime/harness.py /work/job.json

The host writes a job file, starts a locked-down container (no network, read-only root,
CPU/memory/pid/time limits, non-root user), and reads back ``result.json``. Two modes:

* ``stage``   - run one stage's code on its inputs, conform the output to the declared
                schema (quarantining bad rows), evaluate the Tester's assertions, and write
                the output parquet.
* ``quality`` - run the Validator's declarative rules over finished datasets.

Whatever goes wrong - syntax errors, exceptions, schema mismatches, failed assertions - is
written into the result as data for the Debugger. The harness itself never "decides" to
retry; that is the orchestrator's job.

Stage code contract (what the Stage Coder is told to write):
    def run(inputs: dict[str, pandas.DataFrame]) -> DataFrame | tuple[DataFrame, DataFrame]
The optional second frame holds rows the code rejected, with a ``_reason`` column.
SQL stages are a single DuckDB query; input ``raw.orders`` is visible as ``raw_orders``.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

try:  # inside the container the runtime folder is on sys.path ...
    from checks import run_checks
    from conform import ConformError, conform
except ImportError:  # ... on the host it is a package
    from adpb.sandbox_runtime.checks import run_checks
    from adpb.sandbox_runtime.conform import ConformError, conform

MAX_ERROR_CHARS = 4_000
MAX_LOG_CHARS = 2_000


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else "...[truncated]...\n" + text[-limit:]


def _stage_traceback(exc: BaseException) -> str:
    """Traceback limited to frames from the stage code, so the Debugger sees its own lines."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "stage_code"]
    lines = traceback.format_list(frames) if frames else []
    lines += traceback.format_exception_only(type(exc), exc)
    return "".join(lines)


def _run_python(code: str, inputs: dict[str, pd.DataFrame]):
    namespace: dict = {"__name__": "stage_code"}
    exec(compile(code, "stage_code", "exec"), namespace)  # noqa: S102 - this IS the sandbox
    fn = namespace.get("run")
    if not callable(fn):
        raise TypeError("stage code must define a function run(inputs) that returns a DataFrame")
    return fn({k: v.copy() for k, v in inputs.items()})


def _run_sql(code: str, inputs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    import duckdb

    con = duckdb.connect(":memory:")
    for name, df in inputs.items():
        con.register(name.replace(".", "_"), df)
    return con.execute(code).df()


def _evaluate_tests(tests, out, inputs, quarantine) -> list[dict]:
    outcomes = []
    scope = {"out": out, "inputs": inputs, "quarantine": quarantine, "pd": pd, "np": np}
    for t in tests:
        try:
            # The names go in *globals*: a generator expression's body cannot see eval's
            # separate locals, so `all(out[c].iloc[i] > 0 for i in ...)` would raise
            # NameError even though the test is correct (found in a live run).
            value = eval(t["check"], {"__builtins__": __builtins__, **scope})  # noqa: S307
            if isinstance(value, pd.Series | pd.DataFrame | np.ndarray):
                raise TypeError("check returned an array; wrap it in .all() or .any()")
            passed = bool(value)
            detail = "" if passed else f"assertion evaluated to {value!r}"
        except Exception as exc:  # a broken check fails, it does not crash the harness
            passed, detail = False, f"{type(exc).__name__}: {exc}"
        outcomes.append({"name": t["name"], "passed": passed, "detail": detail[:500]})
    return outcomes


def _stage(job: dict) -> dict:
    result = {"stage_id": job["stage_id"], "ok": False, "error_type": None, "error": "",
              "rows_in": {}, "rows_out": 0, "rows_quarantined": 0, "schema_problems": [],
              "tests": [], "duration_s": 0.0, "output_path": None, "quarantine_path": None,
              "logs": ""}
    started = time.monotonic()
    stdout = io.StringIO()
    try:
        inputs = {name: pd.read_parquet(path) for name, path in job["inputs"].items()}
        result["rows_in"] = {k: len(v) for k, v in inputs.items()}
        code = Path(job["code_path"]).read_text()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stdout):
            produced = (_run_sql(code, inputs) if job.get("language") == "sql"
                        else _run_python(code, inputs))
        stage_q = None
        if isinstance(produced, tuple):
            if len(produced) != 2:
                raise TypeError("run() may return a DataFrame or a (DataFrame, DataFrame) tuple")
            produced, stage_q = produced
        try:
            conformed = conform(produced, job["output_schema"],
                                max_quarantine_fraction=job.get("max_quarantine_fraction", 0.25),
                                stage_quarantine=stage_q)
        except ConformError as exc:
            result.update(error_type="SchemaMismatch", error=str(exc), schema_problems=[str(exc)])
            return result
        result["schema_problems"] = conformed.warnings
        out, quarantine = conformed.frame, conformed.quarantine
        result["tests"] = _evaluate_tests(job.get("tests", []), out, inputs, quarantine)

        out_path = Path(job["output_path"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(out_path, index=False)
        result["output_path"] = str(out_path)
        if len(quarantine):
            q_path = Path(job["quarantine_path"])
            quarantine.assign(_stage=job["stage_id"]).to_parquet(q_path, index=False)
            result["quarantine_path"] = str(q_path)
        result["rows_out"], result["rows_quarantined"] = len(out), len(quarantine)
        failed = [t["name"] for t in result["tests"] if not t["passed"]]
        if failed:
            result.update(error_type="TestFailure",
                          error=f"{len(failed)} test(s) failed: {failed}")
        else:
            result["ok"] = True
    except Exception as exc:
        result.update(error_type=type(exc).__name__,
                      error=_trim(_stage_traceback(exc), MAX_ERROR_CHARS))
    finally:
        result["duration_s"] = round(time.monotonic() - started, 3)
        result["logs"] = _trim(stdout.getvalue(), MAX_LOG_CHARS)
    return result


def _quality(job: dict) -> dict:
    try:
        datasets = {name: pd.read_parquet(path) for name, path in job["datasets"].items()}
        return {"ok": True, "checks": run_checks(datasets, job["rules"])}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "checks": []}


def run_job(job: dict) -> dict:
    result = _quality(job) if job["mode"] == "quality" else _stage(job)
    Path(job["result_path"]).write_text(json.dumps(result, default=str))
    return result


if __name__ == "__main__":
    run_job(json.loads(Path(sys.argv[1]).read_text()))
