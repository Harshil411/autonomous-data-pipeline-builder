"""Re-run a finished pipeline from scratch, with no LLM (requirement 18; mitigation 10).

Reads the plan and the stage code *as committed* in the run's git repo, re-ingests the
original sources, executes every stage in the sandbox on the full data, and compares the
final dataset's fingerprint with the one recorded in ``manifest.json`` at load time.
Because the code is frozen, model non-determinism cannot change the result.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from adpb.contracts import PipelinePlan, StageCode, StageTests
from adpb.observability.events import EventLog
from adpb.tools.fingerprint import dataset_fingerprint
from adpb.tools.gateway import ToolGateway
from adpb.tools.sources import load_raw

if TYPE_CHECKING:
    from adpb.runtime import RunHandle


def rerun(handle: RunHandle) -> dict:
    ws = handle.ws
    plan = PipelinePlan.model_validate_json((ws.pipeline_dir / "plan.json").read_text())
    manifest = json.loads((ws.pipeline_dir / "manifest.json").read_text())
    out_dir = ws.root / "reruns" / f"{datetime.now():%Y%m%d-%H%M%S-%f}"
    out_dir.mkdir(parents=True)
    events = EventLog(out_dir / "events.jsonl", run_id=f"{ws.run_id}-rerun")
    paths: dict[str, str] = {}
    for src in plan.sources:  # re-ingest from the original files, exactly as the run did
        target = out_dir / f"{src.dataset}.parquet"
        load_raw(src, ws.sources_dir).frame.to_parquet(target, index=False)
        paths[src.dataset] = str(target.relative_to(ws.root))

    stages = []
    servers = handle._servers()
    with ToolGateway({"sandbox": servers["sandbox"]}, events=events) as gateway:
        for spec in plan.execution_order():
            ext = "sql" if spec.language == "sql" else "py"
            code = StageCode(stage_id=spec.id, language=spec.language,
                             code=(ws.pipeline_dir / "stages" / f"{spec.id}.{ext}").read_text())
            tests = StageTests.model_validate_json(
                (ws.pipeline_dir / "tests" / f"{spec.id}.json").read_text())
            res = gateway.call("orchestrator", "sandbox", "run_stage", {
                "stage_id": spec.id, "code": code.code, "language": code.language,
                "inputs": {n: paths[n] for n in spec.inputs},
                "output_schema": spec.output.model_dump(mode="json"),
                "tests": [t.model_dump() for t in tests.tests],
                "job": f"rerun-{out_dir.name}-{spec.id}"})
            stages.append({"id": spec.id, "ok": res["ok"], "rows_out": res.get("rows_out"),
                           "error": res.get("error", "")[:500]})
            if not res["ok"]:
                break
            # The sandbox reports an absolute host path; the next stage wants it run-relative.
            paths[spec.id] = str(Path(res["output_path"]).resolve().relative_to(ws.root))

    final = paths.get(plan.final_dataset)
    fingerprint = dataset_fingerprint(pd.read_parquet(ws.root / final)) if final else None
    expected = manifest.get("fingerprint")
    result = {"run_id": ws.run_id, "stages": stages, "fingerprint": fingerprint,
              "expected": expected, "match": bool(fingerprint and fingerprint == expected),
              "model_calls": 0, "folder": str(out_dir.relative_to(ws.root))}
    (out_dir / "rerun.json").write_text(json.dumps(result, indent=2))
    events.emit("rerun_finished", **result)
    return result
