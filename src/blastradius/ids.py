"""Stable, content-derived identifiers. Ids never depend on run_id, counters, or clocks."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha(*parts: Any, n: int = 12) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()[:n]


def sha_full(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: Any, n: int = 12) -> str:
    return f"{prefix}_{sha(*parts, n=n)}"
