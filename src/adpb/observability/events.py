"""Append-only, per-run event log (requirement 22: "all agent usage, tool usage, number of
tokens used and cost should be logged per run using a unique run ID").

The log is a JSON-lines file in the run's workspace. It is the single source of truth for
"what happened": the Streamlit live view tails it, the run report totals it, and replay
mode plays it back. Every event is redacted before it reaches disk.

Event shape: {"run_id", "seq", "ts", "kind", ...payload}. Common kinds:
  run_started, plan_created, stage_status, agent_call, tool_call, test_result,
  quality_result, approval_requested, approval_decision, load_finished, run_finished.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from adpb.observability.redact import Redactor


class EventLog:
    def __init__(
        self,
        path: Path,
        run_id: str,
        redactor: Redactor | None = None,
        clock: Callable[[], float] = time.time,
        listeners: list[Callable[[dict], None]] | None = None,
    ):
        self.path = Path(path)
        self.run_id = run_id
        self._redactor = redactor or Redactor.from_env()
        self._clock = clock
        self._lock = threading.Lock()
        self.listeners = listeners or []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Continue numbering if the run is being resumed.
        existing = list(self.read(self.path)) if self.path.exists() else []
        self._seq = existing[-1]["seq"] if existing else 0

    def emit(self, kind: str, **payload: Any) -> dict:
        with self._lock:
            self._seq += 1
            event = {"run_id": self.run_id, "seq": self._seq, "ts": self._clock(), "kind": kind}
            event.update(self._redactor.obj(payload))
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, default=str) + "\n")
        for listener in self.listeners:
            listener(event)
        return event

    @staticmethod
    def read(path: Path, after: int = 0) -> list[dict]:
        """Events with ``seq > after``. Tolerates a half-written last line."""
        path = Path(path)
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # the writer may be mid-line; the next poll will pick it up
            if event.get("seq", 0) > after:
                events.append(event)
        return events

    @staticmethod
    def totals(events: Iterable[dict]) -> dict:
        """Tokens, cost and call counts per agent, for the report and the UI."""
        by_agent: dict[str, dict[str, float]] = {}
        tokens, usd, tool_calls = 0, 0.0, 0
        for e in events:
            if e["kind"] == "agent_call":
                agent = by_agent.setdefault(e.get("agent", "?"),
                                            {"calls": 0, "tokens": 0, "usd": 0.0})
                agent["calls"] += 1
                agent["tokens"] += e.get("tokens", 0)
                agent["usd"] += e.get("usd", 0.0)
                tokens += e.get("tokens", 0)
                usd += e.get("usd", 0.0)
            elif e["kind"] == "tool_call":
                tool_calls += 1
        return {"tokens": tokens, "usd": usd, "tool_calls": tool_calls, "by_agent": by_agent}
