"""A daily spending cap for a public deployment. Off unless ADPB_DAILY_USD is set.

On a laptop the per-run budget is enough. On a public server anyone can press Build, so
the Build page also refuses new runs once today's runs have used the daily amount. Like the
per-run budget, this is plain code reading the event logs; no model is asked anything.

A run that is still working counts as its full per-run limit, so several runs started
close together cannot overshoot the cap. Once it stops, it counts as what it really spent.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

MIN_RUN_USD = 0.10   # the smallest per-run limit the Build form offers
STEP = 0.05


def daily_limit() -> float | None:
    """Today's cap in US dollars, or None when there is no cap (the default)."""
    raw = os.environ.get("ADPB_DAILY_USD", "").strip()
    if not raw:
        return None
    return max(0.0, float(raw))


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def run_cost(run: Path) -> float:
    """What one run counts for: real spend when it has stopped, its limit while it works."""
    spent = 0.0
    try:
        with (run / "events.jsonl").open() as lines:
            for line in lines:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("kind") == "agent_call":
                    spent += float(event.get("usd") or 0.0)
    except OSError:
        pass
    status = _read_json(run / "state.json").get("status", "running")
    if status == "running":
        limit = _read_json(run / "request.json").get("budget", {}).get("max_usd", 0.0)
        return max(spent, float(limit or 0.0))
    return spent


def spent_today(runs_dirs: Path | Iterable[Path], now: datetime | None = None) -> float:
    """Run folders are named run-YYYYMMDD-HHMMSS-xxxx in the server's local time. With
    private runs there is one runs folder per visitor; the cap counts all of them."""
    day = f"run-{now or datetime.now():%Y%m%d}-"
    dirs = [runs_dirs] if isinstance(runs_dirs, Path) else list(runs_dirs)
    return sum(run_cost(r) for d in dirs if d.is_dir()
               for r in d.iterdir() if r.name.startswith(day))


def remaining_today(runs_dirs: Path | Iterable[Path],
                    now: datetime | None = None) -> float | None:
    """Dollars left today, or None when there is no cap."""
    limit = daily_limit()
    if limit is None:
        return None
    return max(0.0, limit - spent_today(runs_dirs, now))


def run_ceiling(remaining: float | None, default_max: float = 2.00) -> float | None:
    """The largest per-run limit to offer, rounded down to the slider's step.
    None means no new run fits in what is left today."""
    if remaining is None:
        return default_max
    top = min(default_max, int(remaining / STEP + 1e-9) * STEP)
    return round(top, 2) if top >= MIN_RUN_USD + STEP else None
