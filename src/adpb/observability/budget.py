"""Per-run circuit breaker for time, tokens and dollars (requirement 23; Module 7).

The budget is enforced *in code, before every model call*. The model is never asked how
much it may spend. When a limit trips, `BudgetExceeded` propagates to the orchestrator,
which ends the run cleanly with status ``budget_exceeded`` and still writes a report.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from adpb.contracts import BudgetLimits


class BudgetExceeded(RuntimeError):
    """Raised before a call that would break a run limit. ``limit`` is cost|tokens|time."""

    def __init__(self, limit: str, message: str):
        super().__init__(message)
        self.limit = limit


class Budget:
    def __init__(self, limits: BudgetLimits, clock: Callable[[], float] = time.monotonic):
        self.limits = limits
        self._clock = clock
        self._started = clock()
        self._lock = threading.Lock()  # agents may be called from worker threads
        self.tokens = 0
        self.usd = 0.0
        self.calls = 0

    @property
    def elapsed(self) -> float:
        return self._clock() - self._started

    def check(self, estimated_tokens: int) -> None:
        """Raise if the next call would break a limit. Called before every model call."""
        with self._lock:
            if self.usd >= self.limits.max_usd:
                raise BudgetExceeded(
                    "cost", f"cost budget spent (${self.usd:.4f} of ${self.limits.max_usd:.2f})"
                )
            if self.tokens + estimated_tokens > self.limits.max_tokens:
                raise BudgetExceeded(
                    "tokens",
                    f"token budget would be exceeded ({self.tokens}+{estimated_tokens} > "
                    f"{self.limits.max_tokens})",
                )
            if self.elapsed > self.limits.max_seconds:
                raise BudgetExceeded(
                    "time",
                    f"time budget spent ({self.elapsed:.0f}s of {self.limits.max_seconds:.0f}s)",
                )

    def charge(self, tokens: int, usd: float) -> None:
        """Record what a finished call actually used (from the provider's usage report)."""
        with self._lock:
            self.tokens += tokens
            self.usd += usd
            self.calls += 1

    def snapshot(self) -> dict[str, float]:
        lim = self.limits
        return {
            "usd_spent": round(self.usd, 6),
            "usd_limit": lim.max_usd,
            "usd_fraction": min(1.0, self.usd / lim.max_usd),
            "tokens_spent": self.tokens,
            "tokens_limit": lim.max_tokens,
            "tokens_fraction": min(1.0, self.tokens / lim.max_tokens),
            "seconds_elapsed": round(self.elapsed, 1),
            "seconds_limit": lim.max_seconds,
            "seconds_fraction": min(1.0, self.elapsed / lim.max_seconds),
            "model_calls": self.calls,
        }


def estimate_tokens(text: str) -> int:
    """About four characters per token: good enough for a pre-call budget check."""
    return max(1, len(text) // 4)
