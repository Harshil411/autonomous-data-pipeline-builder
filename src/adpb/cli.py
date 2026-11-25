"""Command-line interface.

    adpb run REQUEST.yaml [--yes] [--by NAME]   build a pipeline; ask before loading
    adpb approve RUN_ID [--by NAME]             approve a paused run's load
    adpb reject RUN_ID [--note TEXT]            reject it (production stays untouched)
    adpb resume RUN_ID                          rebuild only the failed stage onwards
    adpb rerun RUN_ID                           reproduce from scratch, no LLM (req. 18)
    adpb status [RUN_ID]                        list runs, or show one run's stages
    adpb report RUN_ID                          print the run report
    adpb replay RUN_ID [--speed X]              print the recorded event stream
    adpb bench [--suite FILE] [--repeat N]      run the benchmark suite (req. 26; costs money)
    adpb doctor                                 check Docker, the sandbox image and credentials
    adpb build-sandbox                          build the sandbox Docker image
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from adpb.config import REPO_ROOT, load_settings
from adpb.contracts import RunStatus
from adpb.observability.events import EventLog


def _handle(args, run_id: str):
    from adpb.runtime import RunHandle

    return RunHandle.open(run_id, home=args.home)


def _print_state(state: dict) -> None:
    print(f"status: {state.get('status')}" + (f"  ({state['error']})" if state.get("error")
                                               else ""))
    for sid, rec in (state.get("stages") or {}).items():
        print(f"  {sid:<28} {rec['status']:<10} attempts={rec['attempts']} "
              f"rows={rec.get('rows_out')}")


def _ask_approval(handle, state: dict, args) -> dict:
    staging = state["staging"]
    failed = [c["rule_id"] for c in state.get("quality", []) if not c["passed"]]
    print(f"\nReady to load {staging['rows']} rows into `{staging['table']}` "
          f"(currently {staging.get('existing_production_rows') or 0} rows; will be replaced).")
    print(f"Quarantined rows: {staging.get('quarantine_rows', 0)}. "
          f"Failed quality checks: {failed or 'none'}")
    for row in staging.get("preview", [])[:5]:
        print("  ", row)
    if args.yes:
        answer = "y"
    elif sys.stdin.isatty():
        answer = input("Approve the load? [y/N] ").strip().lower()
    else:
        print("Not a terminal: leaving the run paused. Use `adpb approve` or `adpb reject`.")
        return state
    by = args.by or getpass.getuser()
    return handle.decide(approved=answer in ("y", "yes"), by=by)


def cmd_run(args) -> int:
    from adpb.requests_io import load_request_file
    from adpb.runtime import RunHandle

    request, files = load_request_file(Path(args.request))
    handle = RunHandle.create(request, files, home=args.home)
    print(f"run {handle.ws.run_id}  ->  {handle.ws.root}")
    state = handle.run()
    _print_state(state)
    if state.get("status") == RunStatus.AWAITING_APPROVAL.value:
        state = _ask_approval(handle, state, args)
        _print_state(state)
    print(f"report: {handle.ws.root / 'report.md'}")
    return 0 if state.get("status") in ("succeeded", "awaiting_approval") else 1


def cmd_decide(args, approved: bool) -> int:
    state = _handle(args, args.run_id).decide(approved=approved,
                                              by=args.by or getpass.getuser(),
                                              note=getattr(args, "note", "") or "")
    _print_state(state)
    return 0


def cmd_resume(args) -> int:
    handle = _handle(args, args.run_id)
    state = handle.resume()
    _print_state(state)
    if state.get("status") == RunStatus.AWAITING_APPROVAL.value:
        state = _ask_approval(handle, state, args)
    return 0 if state.get("status") in ("succeeded", "awaiting_approval") else 1


def cmd_rerun(args) -> int:
    from adpb.orchestrator.rerun import rerun

    result = rerun(_handle(args, args.run_id))
    for s in result["stages"]:
        print(f"  {s['id']:<28} {'ok' if s['ok'] else 'FAILED'} rows={s['rows_out']}")
    print(f"fingerprint {result['fingerprint']}\nexpected    {result['expected']}")
    print(json.dumps({"match": result["match"], "model_calls": 0}))
    return 0 if result["match"] else 1


def cmd_status(args) -> int:
    settings = load_settings(home=args.home)
    if args.run_id:
        _print_state(_handle(args, args.run_id).state())
        return 0
    runs = sorted(settings.runs_dir.glob("run-*"), reverse=True) \
        if settings.runs_dir.exists() else []
    for run in runs[:30]:
        state = json.loads((run / "state.json").read_text()) if (run / "state.json").exists() \
            else {}
        req = json.loads((run / "request.json").read_text()).get("request", "") \
            if (run / "request.json").exists() else ""
        print(f"{run.name}  {state.get('status', '-'):<18} {req[:70]}")
    return 0


def cmd_report(args) -> int:
    print((_handle(args, args.run_id).ws.root / "report.md").read_text())
    return 0


def cmd_replay(args) -> int:
    events = EventLog.read(_handle(args, args.run_id).ws.events_path)
    previous = events[0]["ts"] if events else 0
    for e in events:
        if args.speed:
            time.sleep(min(2.0, max(0.0, (e["ts"] - previous) / args.speed)))
        previous = e["ts"]
        who = e.get("agent") or e.get("stage") or ""
        detail = e.get("status") or e.get("tool") or e.get("error_type") or ""
        print(f"{e['seq']:>4}  {e['kind']:<20} {who:<16} {detail}")
    return 0


def cmd_bench(args) -> int:
    from adpb.benchmark import run_suite

    report = run_suite(Path(args.suite), repeat=args.repeat, home=args.home)
    print(json.dumps(report["summary"], indent=2))
    return 0


def cmd_doctor(args) -> int:
    from adpb.sandbox.docker_runner import DEFAULT_IMAGE, docker_available

    settings = load_settings(home=args.home)
    checks = {
        "docker daemon + sandbox image": docker_available(),
        "ANTHROPIC_API_KEY set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "VT ARC key set (fallback)": bool(os.environ.get("VT_ARC_API_KEY")),
        f"home writable ({settings.home})": os.access(settings.home, os.W_OK),
    }
    for name, ok in checks.items():
        print(f"[{'ok' if ok else '--'}] {name}")
    if not checks["docker daemon + sandbox image"]:
        print(f"    start Docker (e.g. `colima start`) and run `adpb build-sandbox` "
              f"to build {DEFAULT_IMAGE}")
    return 0 if checks["docker daemon + sandbox image"] else 1


def cmd_build_sandbox(args) -> int:
    from adpb.sandbox.docker_runner import DEFAULT_IMAGE

    return subprocess.call(["docker", "build", "-t", DEFAULT_IMAGE, str(REPO_ROOT / "sandbox")])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="adpb", description="Autonomous Data Pipeline Builder")
    p.add_argument("--home", type=Path, default=None, help="data folder (default ./var)")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="build a pipeline from a request file")
    r.add_argument("request")
    r.add_argument("--yes", action="store_true", help="approve the load without asking")
    r.add_argument("--by", default=None, help="name recorded with the approval")
    for name in ("approve", "reject"):
        s = sub.add_parser(name)
        s.add_argument("run_id")
        s.add_argument("--by", default=None)
        s.add_argument("--note", default="")
    s = sub.add_parser("resume")
    s.add_argument("run_id")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--by", default=None)
    sub.add_parser("rerun").add_argument("run_id")
    sub.add_parser("status").add_argument("run_id", nargs="?")
    sub.add_parser("report").add_argument("run_id")
    s = sub.add_parser("replay")
    s.add_argument("run_id")
    s.add_argument("--speed", type=float, default=4.0, help="0 = no delays")
    s = sub.add_parser("bench")
    s.add_argument("--suite", default=str(REPO_ROOT / "benchmarks" / "suite.yaml"))
    s.add_argument("--repeat", type=int, default=1)
    sub.add_parser("doctor")
    sub.add_parser("build-sandbox")
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env")
    args = build_parser().parse_args(argv)
    handlers = {
        "run": cmd_run, "approve": lambda a: cmd_decide(a, True),
        "reject": lambda a: cmd_decide(a, False), "resume": cmd_resume, "rerun": cmd_rerun,
        "status": cmd_status, "report": cmd_report, "replay": cmd_replay, "bench": cmd_bench,
        "doctor": cmd_doctor, "build-sandbox": cmd_build_sandbox,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
