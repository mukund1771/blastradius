"""`blastradius load`: put a run's artifacts into Neo4j and say exactly what happened (D08; cases 46-48).

Reads, when present, the files models.py lists: crawl/screens.jsonl, crawl/transitions.jsonl,
crawl/summary.json, spec/requirements.json, code/index.json. Writes through GraphStore (idempotent MERGE,
every node and edge stamped with this run's id and the crawl's bundle hash), matches Screens to Routes,
then prunes what this run no longer asserts in the layers it loaded. Loading the same run twice therefore
changes nothing; loading a newer run replaces that layer's state and the summary says what was added and
what was removed. The result is also written to graph/load.json.

Nothing here calls an LLM and nothing is read from the network.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from ..failures import FailureCode
from ..ids import stable_id
from ..models import CodeIndex, CrawlSummary, Requirement, ScreenState, Transition
from ..run import RunContext
from ..targets import TargetConfig
from ..trace import now_iso
from .store import GraphStore


class GraphLoadSummary(BaseModel):
    """Shape of graph/load.json (models.py names it but does not define it)."""

    model_config = ConfigDict(extra="forbid")

    target: str
    run_id: str
    loaded_at: str
    layers: dict[str, dict[str, Any]]
    served_by: dict[str, Any] = {}
    pruned: dict[str, int] = {}
    counts: dict[str, int] = {}
    code_snapshot_id: str | None = None
    crawl_snapshot_id: str | None = None
    bundle_hash: str | None = None
    bundle_hash_end: str | None = None
    bundle_drift: bool = False
    other_runs: list[str] = []
    elapsed_s: float = 0.0


def _read_jsonl[T: BaseModel](path: Path, model: type[T]) -> tuple[list[T], int]:
    rows: list[T] = []
    invalid = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(model.model_validate_json(line))
        except ValidationError:
            invalid += 1
    return rows, invalid


def _own(rows: list[Any], target: str) -> tuple[list[Any], int]:
    mine = [r for r in rows if r.target == target]
    return mine, len(rows) - len(mine)


class _Load:
    """One load: reads each artifact that exists, writes its layer, remembers what may be pruned."""

    def __init__(self, run: RunContext, cfg: TargetConfig, store: GraphStore):
        self.run, self.store = run, store
        self.target, self.run_id, self.root = cfg.name, run.run_id, run.run_dir
        self.out = GraphLoadSummary(target=self.target, run_id=self.run_id, loaded_at=now_iso(), layers={})
        self.prunable: list[str] = []

    def _rejected(self, layer: str, what: str, count: int) -> None:
        if count:
            self.out.layers[layer][what] = count
            self.run.failures.record(
                FailureCode.GRAPH_WRITE_FAILED, f"{layer}: {count} {what} not loaded", layer=layer
            )

    def _done(self, layer: str, rows: int, rejected: int) -> None:
        """A layer is pruned only when it was read cleanly and is not empty: a truncated or partly
        invalid artifact must never delete what an earlier, complete run put in the graph."""
        if rows > 0 and rejected == 0:
            self.prunable.append(layer)
        else:
            self.out.layers[layer]["pruned"] = False

    def spec(self, path: Path) -> None:
        raw = json.loads(path.read_text())
        reqs, invalid = [], 0
        for item in raw if isinstance(raw, list) else []:
            try:
                reqs.append(Requirement.model_validate(item))
            except ValidationError:
                invalid += 1
        reqs, foreign = _own(reqs, self.target)
        self.out.layers["spec"] = self.store.write_requirements(reqs, self.run_id, target=self.target)
        self._rejected("spec", "invalid_rows", invalid)
        self._rejected("spec", "foreign_target_rows", foreign)
        self._done("spec", len(reqs), invalid + foreign)

    def code(self, path: Path) -> None:
        index = CodeIndex.model_validate_json(path.read_text())
        if index.target != self.target:
            self.out.layers["code"] = {"status": "skipped", "reason": f"index is for {index.target!r}"}
            self.run.failures.record(
                FailureCode.CONFIG_INVALID, f"code/index.json target {index.target!r} != {self.target!r}"
            )
            return
        self.out.layers["code"] = self.store.write_code(index, self.run_id)
        self.out.code_snapshot_id = index.snapshot_id
        self._done("code", len(index.files), 0)

    def crawl(self, screens_path: Path) -> None:
        screens, invalid = _read_jsonl(screens_path, ScreenState)
        screens, foreign = _own(screens, self.target)
        trans_path = self.root / "crawl" / "transitions.jsonl"
        transitions, invalid_t = _read_jsonl(trans_path, Transition) if trans_path.exists() else ([], 0)
        summary_path = self.root / "crawl" / "summary.json"
        summary = (
            CrawlSummary.model_validate_json(summary_path.read_text()) if summary_path.exists() else None
        )
        out = self.out
        out.bundle_hash = summary.bundle_hash if summary else None
        out.bundle_hash_end = summary.bundle_hash_end if summary else None
        out.bundle_drift = bool(
            out.bundle_hash and out.bundle_hash_end and out.bundle_hash != out.bundle_hash_end
        )
        app_version = (summary.app_version if summary else None) or ""
        out.crawl_snapshot_id = stable_id("snap", self.target, out.bundle_hash or "", app_version)
        out.layers["crawl"] = self.store.write_crawl(
            screens,
            transitions,
            self.run_id,
            out.bundle_hash,
            target=self.target,
            snapshot_id=out.crawl_snapshot_id,
            bundle_hash_end=out.bundle_hash_end,
        )
        if summary is not None and summary.screens != len(screens):
            out.layers["crawl"]["summary_screens"] = summary.screens
        self._rejected("crawl", "invalid_rows", invalid + invalid_t)
        self._rejected("crawl", "foreign_target_rows", foreign)
        self._done("crawl", len(screens), invalid + invalid_t + foreign)


def run_load(run: RunContext, cfg: TargetConfig, store: GraphStore) -> dict[str, Any]:
    t0 = time.monotonic()
    root = run.run_dir
    screens_p, spec_p, code_p = (
        root / "crawl" / "screens.jsonl",
        root / "spec" / "requirements.json",
        root / "code" / "index.json",
    )
    if not any(p.exists() for p in (screens_p, spec_p, code_p)):
        raise FileNotFoundError(
            f"nothing to load under {root}: expected crawl/screens.jsonl, spec/requirements.json or "
            "code/index.json (run `blastradius crawl`, `ingest` or `index` first)"
        )
    load = _Load(run, cfg, store)
    store.ensure_schema()
    for path, step in ((spec_p, load.spec), (code_p, load.code), (screens_p, load.crawl)):
        if path.exists():
            step(path)
    out, target = load.out, load.target
    if {"code", "crawl"} & set(out.layers):
        out.served_by = store.link_routes(run.run_id, target=target)
    out.pruned = store.prune_stale(run.run_id, load.prunable, target=target)
    out.counts = store.counts(target)
    others = store.read("runs", target=target, limit=6)
    out.other_runs = [r["id"] for r in others if r["id"] != run.run_id][:5]
    out.elapsed_s = round(time.monotonic() - t0, 3)

    result = out.model_dump(mode="json")
    run.path("graph", "load.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    return result
