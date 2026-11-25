"""Run pipelines in background threads so the Streamlit page stays live.

One `RunManager` per Streamlit server process (cached with ``st.cache_resource``). The UI
never holds pipeline state itself: it starts work here and then reads the run's event log
and state.json, exactly as the CLI and the replay do.
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

from adpb.contracts import DataRequest
from adpb.runtime import RunHandle


class RunManager:
    def __init__(self, home: Path | None = None):
        self.home = home
        self._threads: dict[str, threading.Thread] = {}
        self.errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def _spawn(self, run_id: str, fn) -> None:
        def target():
            try:
                fn()
            except Exception:  # surfaced in the UI; the run itself writes its own report
                self.errors[run_id] = traceback.format_exc()[-2000:]
        with self._lock:
            if self.busy(run_id):
                raise RuntimeError(f"run {run_id} is already working")
            t = threading.Thread(target=target, name=f"adpb-{run_id}", daemon=True)
            self._threads[run_id] = t
            t.start()

    def busy(self, run_id: str) -> bool:
        t = self._threads.get(run_id)
        return bool(t and t.is_alive())

    def start(self, request: DataRequest, files: list[Path]) -> str:
        handle = RunHandle.create(request, files, home=self.home)
        self._spawn(handle.ws.run_id, handle.run)
        return handle.ws.run_id

    def decide(self, run_id: str, approved: bool, by: str, note: str = "") -> None:
        handle = RunHandle.open(run_id, home=self.home)
        self._spawn(run_id, lambda: handle.decide(approved=approved, by=by, note=note))

    def resume(self, run_id: str) -> None:
        handle = RunHandle.open(run_id, home=self.home)
        self._spawn(run_id, handle.resume)
