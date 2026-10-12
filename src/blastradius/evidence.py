"""Evidence ledger: the citation set. Every edge, gap, and report claim points at an evidence_id here.

The verifier treats this file as the universe of things a claim may cite; `explain <claim_id>` walks
claim -> evidence -> node -> span. Ids are content hashes, so re-running a stage does not create duplicates.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .ids import stable_id
from .trace import now_iso


class EvidenceLedger:
    def __init__(self, run_dir: Path | None, run_id: str):
        self.run_id = run_id
        self._lock = threading.Lock()
        self._known: dict[str, dict[str, Any]] = {}
        self._fd: int | None = None
        self.path: Path | None = None
        if run_dir is not None:
            run_dir.mkdir(parents=True, exist_ok=True)
            self.path = run_dir / "evidence.jsonl"
            if self.path.exists():
                for line in self.path.read_text().splitlines():
                    try:
                        rec = json.loads(line)
                        self._known[rec["evidence_id"]] = rec
                    except (json.JSONDecodeError, KeyError):
                        continue
            self._fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)

    def add(self, kind: str, ref: str, summary: str = "", *, span_id: str | None = None, **extra: Any) -> str:
        evidence_id = stable_id("ev", kind, ref, summary)
        with self._lock:
            if evidence_id in self._known:
                return evidence_id
            rec = {"evidence_id": evidence_id, "kind": kind, "ref": ref, "summary": summary[:500],
                   "span_id": span_id, "run_id": self.run_id, "ts": now_iso(), **extra}
            self._known[evidence_id] = rec
            if self._fd is not None:
                os.write(self._fd, (json.dumps(rec, default=str) + "\n").encode())
        return evidence_id

    def exists(self, evidence_id: str) -> bool:
        return evidence_id in self._known

    def get(self, evidence_id: str) -> dict[str, Any] | None:
        return self._known.get(evidence_id)

    def ids(self) -> set[str]:
        return set(self._known)

    def __len__(self) -> int:
        return len(self._known)
