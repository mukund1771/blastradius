"""Cost ledger. One line per LLM call, including cache hits (with avoided_usd), so spend is reconstructible.

Prices are a local estimate from response.usage, not billing. Table provenance is stamped on every line.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .trace import now_iso

PRICE_TABLE_VERSION = "2026-10-12 mixed: skill cache 2026-09-25; haiku-5-5 and sonnet cache-read are verifier-reported"

# USD per million tokens: (input, output, cache_read, cache_write_5m)
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50),
    "claude-haiku-4-5": (1.00, 5.00, 0.10, 1.25),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}
_FALLBACK = PRICES["claude-opus-5-5"]  # unknown model: assume the dearest, so the breaker errs safe


def price_for(model: str) -> tuple[tuple[float, float, float, float], bool]:
    key = model.split("-2025")[0]
    if key in PRICES:
        return PRICES[key], True
    return _FALLBACK, False


def cost_usd(model: str, usage: dict[str, int]) -> float:
    (p_in, p_out, p_read, p_write), _ = price_for(model)
    return (
        usage.get("input_tokens", 0) * p_in
        + usage.get("output_tokens", 0) * p_out
        + usage.get("cache_read_input_tokens", 0) * p_read
        + usage.get("cache_creation_input_tokens", 0) * p_write
    ) / 1_000_000


class Ledger:
    def __init__(self, run_dir: Path | None, run_id: str):
        self.run_id = run_id
        self._lock = threading.Lock()
        self.total = 0.0
        self.avoided = 0.0
        self.calls = 0
        self.cache_hits = 0
        self._fd: int | None = None
        if run_dir is not None:
            run_dir.mkdir(parents=True, exist_ok=True)
            self.path: Path | None = run_dir / "ledger.jsonl"
            self._fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        else:
            self.path = None

    def record(self, *, span_id: str | None, stage: str | None, purpose: str, model: str,
               usage: dict[str, int], cache_hit: bool, original_span_id: str | None = None) -> dict[str, Any]:
        usd = cost_usd(model, usage)
        _, known = price_for(model)
        rec = {
            "ts": now_iso(), "run_id": self.run_id, "span_id": span_id, "stage": stage, "purpose": purpose,
            "model": model, "in": usage.get("input_tokens", 0), "out": usage.get("output_tokens", 0),
            "cache_read": usage.get("cache_read_input_tokens", 0),
            "cache_write": usage.get("cache_creation_input_tokens", 0),
            "cost_usd": 0.0 if cache_hit else round(usd, 6),
            "avoided_usd": round(usd, 6) if cache_hit else 0.0,
            "cache_hit": cache_hit, "original_span_id": original_span_id,
            "price_known": known, "price_table_version": PRICE_TABLE_VERSION,
        }
        with self._lock:
            self.calls += 1
            if cache_hit:
                self.cache_hits += 1
                self.avoided += usd
            else:
                self.total += usd
            if self._fd is not None:
                os.write(self._fd, (json.dumps(rec) + "\n").encode())
        return rec

    def summary(self) -> dict[str, Any]:
        return {"calls": self.calls, "cache_hits": self.cache_hits, "spent_usd": round(self.total, 4),
                "avoided_usd": round(self.avoided, 4)}
