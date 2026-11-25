"""Benchmark runner (requirement 26).

"The solution should be tested on a set of benchmark requests and be assessed on the number
of successful runs, number of retries per stage, time, cost and success rate."

Each suite entry is a request file plus *invariants* the final table must satisfy (row-count
range, required columns) - never exact strings (Module 2, Lab 8). A run counts as a success
only if the pipeline succeeded AND the invariants hold, so a run that "succeeds" with the
wrong data does not inflate the score (Module 13, "Evaluation Theater").
Benchmark runs are approved automatically and recorded as approved by "benchmark".
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import threading
from collections.abc import Callable
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from adpb.config import REPO_ROOT
from adpb.contracts import SourceKind
from adpb.llm.base import ModelClient
from adpb.requests_io import load_request_file

API_PORT = 8765


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep benchmark output readable
        pass


def _start_api_server(folder: Path) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", API_PORT),
                                 partial(_QuietHandler, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _check_table(db: Path, table: str, expect: dict) -> tuple[bool, str, int | None]:
    con = sqlite3.connect(db)
    try:
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
        if not cols:
            return False, f"table {table} missing", None
        rows = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]  # noqa: S608
    finally:
        con.close()
    lo, hi = expect.get("min_rows"), expect.get("max_rows")
    if lo is not None and rows < lo or hi is not None and rows > hi:
        return False, f"{rows} rows, expected [{lo}, {hi}]", rows
    missing = [c for c in expect.get("columns", []) if c not in cols]
    if missing:
        return False, f"missing columns {missing}; table has {cols}", rows
    return True, "invariants hold", rows


def run_suite(suite: Path, repeat: int = 1, home: Path | None = None,
              model_factory: Callable[[], ModelClient] | None = None,
              sandbox: str | None = None, embeddings: str | None = None,
              results_dir: Path | None = None) -> dict:
    from adpb.runtime import RunHandle

    suite = Path(suite).resolve()
    entries = yaml.safe_load(suite.read_text())
    home = Path(home or REPO_ROOT / "var" / "bench")
    api_folder = REPO_ROOT / "benchmarks" / "fixtures" / "api"
    server = None
    runs = []
    try:
        for entry in entries:
            request, files = load_request_file(suite.parent / entry["request"])
            if any(s.kind is SourceKind.API for s in request.sources) and server is None:
                os.environ.setdefault("ADPB_API_ALLOWED_HOSTS", "127.0.0.1")
                server = _start_api_server(api_folder)
            for i in range(repeat):
                handle = RunHandle.create(request, files, home=home,
                                          model=model_factory() if model_factory else None,
                                          sandbox=sandbox, embeddings=embeddings)
                state = handle.run()
                if state.get("status") == "awaiting_approval":
                    state = handle.decide(approved=True, by="benchmark")
                report = json.loads((handle.ws.root / "report.json").read_text())
                ok, why, rows = False, report.get("error") or state.get("status"), None
                if state.get("status") == "succeeded":
                    ok, why, rows = _check_table(handle.settings.warehouse_path,
                                                 request.target_table, entry.get("expect", {}))
                stages = report["stages"]
                runs.append({
                    "name": entry["name"], "repeat": i + 1, "run_id": handle.ws.run_id,
                    "status": state.get("status"), "success": ok, "why": why, "rows": rows,
                    "stages": len(stages), "retries": {s["id"]: s["retries"] for s in stages},
                    "replans": report["replans"], "quality_failed": report["quality"]["failed"],
                    "seconds": report["duration_s"], "usd": report["cost"]["usd"],
                    "tokens": report["cost"]["tokens"],
                    "model_calls": report["cost"]["model_calls"],
                })
    finally:
        if server:
            server.shutdown()

    retries = [r for run in runs for r in run["retries"].values()]
    summary = {
        "runs": len(runs),
        "successful_runs": sum(r["success"] for r in runs),
        "success_rate": round(sum(r["success"] for r in runs) / len(runs), 3) if runs else 0.0,
        "mean_retries_per_stage": round(statistics.mean(retries), 3) if retries else 0.0,
        "mean_seconds": round(statistics.mean(r["seconds"] for r in runs), 1) if runs else 0.0,
        "total_usd": round(sum(r["usd"] for r in runs), 4),
        "mean_usd": round(statistics.mean(r["usd"] for r in runs), 4) if runs else 0.0,
        "total_tokens": sum(r["tokens"] for r in runs),
    }
    report = {"suite": str(suite), "finished_at": datetime.now().isoformat(timespec="seconds"),
              "summary": summary, "runs": runs}
    out = Path(results_dir or REPO_ROOT / "benchmarks" / "results")
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out / f"bench-{stamp}.json").write_text(json.dumps(report, indent=2))
    (out / f"bench-{stamp}.md").write_text(render_benchmark(report))
    return report


def render_benchmark(report: dict) -> str:
    s = report["summary"]
    lines = ["# Benchmark results", "", f"Finished {report['finished_at']}", "",
             f"- **Success rate:** {s['success_rate']:.0%} ({s['successful_runs']} of "
             f"{s['runs']} runs)",
             f"- **Mean retries per stage:** {s['mean_retries_per_stage']}",
             f"- **Mean time per run:** {s['mean_seconds']} s",
             f"- **Cost:** ${s['total_usd']:.4f} total, ${s['mean_usd']:.4f} per run, "
             f"{s['total_tokens']:,} tokens", "",
             "| request | # | status | success | rows | stages | retries | replans | s | $ |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in report["runs"]:
        lines.append(f"| {r['name']} | {r['repeat']} | {r['status']} | "
                     f"{'yes' if r['success'] else 'no: ' + str(r['why'])[:60]} | {r['rows']} | "
                     f"{r['stages']} | {sum(r['retries'].values())} | {r['replans']} | "
                     f"{r['seconds']} | {r['usd']:.4f} |")
    return "\n".join(lines) + "\n"
