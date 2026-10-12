"""Guarded structured LLM calls for the link stage (D07, D26; case 59).

`guarded` wraps `LLM.structured` so that a missing key, an offline cache miss, an API error or a tripped spend
breaker degrades one stage instead of crashing it: the call returns None, the failure is recorded once by the
code that raised it (or here, for errors `LLM.structured` does not classify), and `CallBudget` stops further
calls after three consecutive hard failures (a missing key costs three attempts, not 400). An offline miss
is free and does not count: later prompts may still be in the cache.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from ..failures import FailureCode
from ..llm import LLM, LLMBudgetExceeded, LLMOfflineMiss
from ..run import RunContext

T = TypeVar("T", bound=BaseModel)
HARD_FAILURES = 3


class CallBudget:
    """At most `max_calls` attempts; closed early after three consecutive hard failures."""

    def __init__(self, max_calls: int):
        self.max_calls = max_calls
        self.calls = 0
        self.failed = 0
        self._streak = 0

    @property
    def open(self) -> bool:
        return self.calls < self.max_calls and self._streak < HARD_FAILURES

    def record(self, exc: Exception | None) -> None:
        self.calls += 1
        if exc is None:
            self._streak = 0
            return
        self.failed += 1
        if isinstance(exc, LLMBudgetExceeded):
            self._streak = HARD_FAILURES
        elif not isinstance(exc, LLMOfflineMiss):
            self._streak += 1


def guarded(run: RunContext, llm: LLM, budget: CallBudget, *, context: str, **kwargs: Any) -> Any | None:
    """`llm.structured(**kwargs)` or None. The caller decides what an unanswered call means."""
    try:
        out = llm.structured(**kwargs)
    except Exception as exc:  # noqa: BLE001 - LLMError subclasses, SDK auth errors and TypeError (no key)
        budget.record(exc)
        if not isinstance(exc, (LLMOfflineMiss, LLMBudgetExceeded)):   # llm.structured recorded those
            run.failures.record(getattr(exc, "code", FailureCode.LLM_API_ERROR),
                                f"{context}: {type(exc).__name__}: {exc}"[:300])
        return None
    budget.record(None)
    return out


def ledger_delta(run: RunContext, before: dict[str, Any]) -> dict[str, Any]:
    """What this stage added to the run's cost ledger since `before` (a `Ledger.summary()`): calls made,
    answered from the content cache, USD spent and USD avoided by the cache."""
    now = run.ledger.summary()
    return {"calls": now["calls"] - before["calls"], "cache_hits": now["cache_hits"] - before["cache_hits"],
            "spent_usd": round(now["spent_usd"] - before["spent_usd"], 4),
            "avoided_usd": round(now["avoided_usd"] - before["avoided_usd"], 4)}
