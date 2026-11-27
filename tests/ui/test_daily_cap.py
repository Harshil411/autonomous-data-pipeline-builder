"""The daily spending cap for a public deployment (app/daily_cap.py)."""

import json
from datetime import datetime

from app.daily_cap import daily_limit, remaining_today, run_ceiling, spent_today

NOW = datetime(2026, 11, 27, 15, 0, 0)


def make_run(runs, name, status, calls, limit=0.50):
    run = runs / name
    run.mkdir(parents=True)
    events = [{"kind": "agent_call", "usd": usd} for usd in calls] + [{"kind": "stage_passed"}]
    (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (run / "request.json").write_text(json.dumps({"budget": {"max_usd": limit}}))
    if status:
        (run / "state.json").write_text(json.dumps({"status": status}))
    return run


def test_no_cap_unless_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("ADPB_DAILY_USD", raising=False)
    assert daily_limit() is None
    assert remaining_today(tmp_path) is None
    assert run_ceiling(None) == 2.00


def test_finished_runs_count_as_what_they_spent(tmp_path):
    make_run(tmp_path, "run-20261127-100000-aaaa", "succeeded", [0.04, 0.02])
    make_run(tmp_path, "run-20261127-110000-bbbb", "awaiting_approval", [0.10])
    assert round(spent_today(tmp_path, NOW), 2) == 0.16


def test_a_working_run_counts_as_its_full_limit(tmp_path):
    make_run(tmp_path, "run-20261127-100000-aaaa", "running", [0.04], limit=0.50)
    make_run(tmp_path, "run-20261127-100500-bbbb", None, [], limit=0.30)  # just started
    assert round(spent_today(tmp_path, NOW), 2) == 0.80


def test_yesterday_does_not_count(tmp_path, monkeypatch):
    monkeypatch.setenv("ADPB_DAILY_USD", "1.00")
    make_run(tmp_path, "run-20261126-235900-aaaa", "succeeded", [0.90])
    make_run(tmp_path, "run-20261127-000100-bbbb", "succeeded", [0.25])
    assert round(remaining_today(tmp_path, NOW), 2) == 0.75


def test_ceiling_shrinks_and_then_closes(tmp_path, monkeypatch):
    monkeypatch.setenv("ADPB_DAILY_USD", "1.00")
    assert run_ceiling(remaining_today(tmp_path, NOW)) == 1.00
    make_run(tmp_path, "run-20261127-100000-aaaa", "succeeded", [0.62])
    assert run_ceiling(remaining_today(tmp_path, NOW)) == 0.35
    make_run(tmp_path, "run-20261127-110000-bbbb", "failed", [0.30])
    assert run_ceiling(remaining_today(tmp_path, NOW)) is None
