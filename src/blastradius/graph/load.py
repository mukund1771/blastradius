"""`blastradius load`: put a run's artifacts into Neo4j and say exactly what happened (D08; cases 46-48).

Reads, when present, the files models.py lists: crawl/screens.jsonl, crawl/transitions.jsonl,
crawl/summary.json, spec/requirements.json, code/index.json. Writes through GraphStore (idempotent MERGE,
every node and edge stamped with this run's id and the crawl's bundle hash), matches Screens to Routes,
then prunes what this run no longer asserts in the layers it loaded. Loading the same run twice therefore
changes nothing; loading a newer run replaces that layer's state and the summary says what was added and
what was removed. The result is also written to graph/load.json.

A layer is pruned ONLY when its artifact is complete (the pruning deletes the expensive link-stage output
that hangs off Screens, UIElements and CodeFiles). Otherwise the layer is still loaded, upserted, but not
pruned, and `layers[<layer>]["prune_skipped"]` plus a line in `notices` say why:

  crawl  summary.json present, stop_reason in {frontier_empty, action_budget, time_budget}, summary.screens ==
         rows read, summary.transitions == rows read, and no invalid or foreign rows. A bounded crawl that
         stopped on its budget IS the complete observation the target was configured for; an errored crawl or
         one stopped by the access circuit breaker never prunes. Goal-directed re-crawls write to their own
         artifact_subdir and are not main artifacts, so they cannot reach this guard. SHRINK GUARD: a budget
         is also what makes a crawl that merely saw less look complete, so when the graph already holds at
         least SHRINK_MIN_SCREENS Screens and the prune would remove more than SHRINK_RATIO of its Screens or
         of its UIElements, the layer is loaded but not pruned (`shrink:...` reason). A reader who knows the
         app really shrank, or whose Screen fingerprints all churned after a redeploy, passes
         `allow_shrink=True`; the notice then says the guard was overridden.
  code   files > 0, no parse-partial marker in index.stats (`parse_partial`, `files_failed`, `files_unparsed`,
         `i18n_parse_error`; `parse_errors` only when they exceed PARSE_ERROR_RATIO of the indexed files: a
         couple of valid files a grammar cannot fully parse must not freeze the layer), a non-empty edge
         list when the graph already had edges, and no collapsed edge set: more than EDGE_DROP_RATIO of the
         graph's IMPORTS/RENDERS gone while the files are similar (at least FILES_SIMILAR_RATIO of the
         graph's CodeFiles) is an indexer that stopped resolving imports, reported as
         `parse_partial:edges_dropped=...`.
  spec   at least one requirement, no invalid or foreign rows, and (when spec/stats.json exists) it does not
         say the extraction was incomplete (`complete` false: chunks failed or were skipped, or `status`
         is not "ok").

Every skipped prune is recorded as FailureCode.GRAPH_PRUNE_SKIPPED (layer and reasons in its context) and
every prune that did delete lists what it removed in `notices`.

After the crawl layer is loaded and pruned, per-screen attribution is neutralised: Screen ids are
fingerprint hashes that churn on a re-crawl while canonical UIElement ids and their IMPLEMENTED_BY /
REALIZED_BY edges survive, so an edge whose `screen_ids` names a Screen that no longer exists would drop its
element from `impact` until the linker ran again. Dead ids are removed (`[]`, every screen of the element,
when none are left) and the count is in `notices` and `dangling_attribution`.

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
from . import writes
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
    notices: list[str] = []  # things a reader of this file must know (e.g. a layer was not pruned)
    # per-screen attribution reset after the crawl layer was loaded: edges changed, reset_to_all, narrowed,
    # ids_dropped (all zero when nothing named a Screen that is gone)
    dangling_attribution: dict[str, int] = {}
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


_PARTIAL_STATS = ("parse_partial", "files_failed", "files_unparsed", "i18n_parse_error")
PARSE_ERROR_RATIO = 0.02  # parse_errors / indexed files above this makes the index partial
SHRINK_RATIO = 0.5  # a crawl prune removing more than this share of Screens or UIElements is held back
SHRINK_MIN_SCREENS = 10  # ... once the graph holds at least this many Screens (small graphs may shrink)
EDGE_DROP_RATIO = 0.5  # a code index with more than this share of the loaded IMPORTS/RENDERS gone is partial
FILES_SIMILAR_RATIO = 0.8  # ... unless it also has fewer files than this share of the loaded CodeFiles
_BUDGET_STOPS = ("frontier_empty", "action_budget", "time_budget")


def _parse_partial(stats: dict[str, Any], n_files: int = 0) -> list[str]:
    """The index.stats markers that say source files were not parsed (truthy value = partial)."""
    out = [f"parse_partial:{key}={stats[key]}" for key in _PARTIAL_STATS if stats.get(key)]
    errors = stats.get("parse_errors") or 0
    if errors and n_files and errors / n_files > PARSE_ERROR_RATIO:
        out.append(f"parse_partial:parse_errors={errors}/{n_files}")
    return out


class _Load:
    """One load: reads each artifact that exists, writes its layer, remembers what may be pruned."""

    def __init__(self, run: RunContext, cfg: TargetConfig, store: GraphStore, *, allow_shrink: bool = False):
        self.run, self.store, self.allow_shrink = run, store, allow_shrink
        self.target, self.run_id, self.root = cfg.name, run.run_id, run.run_dir
        self.out = GraphLoadSummary(target=self.target, run_id=self.run_id, loaded_at=now_iso(), layers={})
        self.prunable: list[str] = []

    def _rejected(self, layer: str, what: str, count: int) -> None:
        if count:
            self.out.layers[layer][what] = count
            self.run.failures.record(
                FailureCode.GRAPH_WRITE_FAILED, f"{layer}: {count} {what} not loaded", layer=layer
            )

    def _done(self, layer: str, reasons: list[str]) -> None:
        """A layer is pruned only when its artifact is complete: a truncated, partial or partly invalid
        artifact must never delete what an earlier, complete run put in the graph. `reasons` is empty when
        the layer may be pruned; otherwise it is recorded (layer entry and notices) and nothing is pruned."""
        if not reasons:
            self.prunable.append(layer)
            return
        self.out.layers[layer]["pruned"] = False
        self.out.layers[layer]["prune_skipped"] = reasons
        message = f"{layer}: loaded but not pruned ({'; '.join(reasons)})"
        self.out.notices.append(message)
        self.run.failures.record(FailureCode.GRAPH_PRUNE_SKIPPED, message, layer=layer, reasons=reasons)

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
        reasons = ["no_rows"] if not reqs else []
        reasons += [f"invalid_rows:{invalid}"] if invalid else []
        reasons += [f"foreign_rows:{foreign}"] if foreign else []
        reasons += _spec_incomplete(path.with_name("stats.json"))
        self._done("spec", reasons)

    def code(self, path: Path) -> None:
        index = CodeIndex.model_validate_json(path.read_text())
        if index.target != self.target:
            self.out.layers["code"] = {"status": "skipped", "reason": f"index is for {index.target!r}"}
            self.run.failures.record(
                FailureCode.CONFIG_INVALID, f"code/index.json target {index.target!r} != {self.target!r}"
            )
            return
        before = self.store.counts(self.target)
        had_edges = before["IMPORTS"] + before["RENDERS"] > 0
        written = self.store.write_code(index, self.run_id)
        self.out.layers["code"] = written
        self.out.code_snapshot_id = index.snapshot_id
        reasons = ["no_files"] if not index.files else []
        reasons += _parse_partial(index.stats, len(index.files))
        reasons += (
            ["edges_empty: the graph already has IMPORTS/RENDERS edges"]
            if had_edges and not index.edges
            else []
        )
        reasons += _edges_degraded(before, written)
        self._done("code", reasons)

    def crawl(self, screens_path: Path) -> None:
        screens, invalid = _read_jsonl(screens_path, ScreenState)
        screens, foreign = _own(screens, self.target)
        trans_path = self.root / "crawl" / "transitions.jsonl"
        transitions, invalid_t = _read_jsonl(trans_path, Transition) if trans_path.exists() else ([], 0)
        summary_path = self.root / "crawl" / "summary.json"
        summary = (
            CrawlSummary.model_validate_json(summary_path.read_text()) if summary_path.exists() else None
        )
        before = self.store.counts(self.target)
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
        reasons = _crawl_incomplete(summary, len(screens), len(transitions))
        reasons += [f"invalid_rows:{invalid + invalid_t}"] if invalid + invalid_t else []
        reasons += [f"foreign_rows:{foreign}"] if foreign else []
        if not reasons:
            reasons += self._shrink(before)
        self._done("crawl", reasons)

    def _shrink(self, before: dict[str, int]) -> list[str]:
        """Why pruning this (otherwise complete) crawl would delete most of what the graph holds, or [].
        `before` is the graph's counts from before this crawl was written; what the prune would remove is
        what the write left carrying another run's id (the predicate of writes.PRUNE["crawl"])."""
        screens, elements = before.get("Screen", 0), before.get("UIElement", 0)
        if screens < SHRINK_MIN_SCREENS:
            return []
        rows = self.store.read("crawl_prune_candidates", target=self.target, run_id=self.run_id)
        gone = {r["label"]: int(r["n"]) for r in rows}
        over = []
        if gone.get("Screen", 0) > SHRINK_RATIO * screens:
            over.append(f"shrink:screens_removed={gone['Screen']}/{screens}")
        if gone.get("UIElement", 0) > SHRINK_RATIO * elements:
            over.append(f"shrink:elements_removed={gone['UIElement']}/{elements}")
        if not over:
            return []
        label = f"more than {SHRINK_RATIO:.0%} of the existing Screens or UIElements would be removed"
        if self.allow_shrink:
            self.out.notices.append(f"crawl: shrink guard overridden by allow_shrink ({'; '.join(over)})")
            return []
        return [f"{o} ({label}; allow_shrink=True prunes anyway)" for o in over]


def _edges_degraded(before: dict[str, int], written: dict[str, Any]) -> list[str]:
    """`parse_partial:edges_dropped=...` when the new index asserts under EDGE_DROP_RATIO of the graph's
    IMPORTS/RENDERS although it has about as many files: the files were indexed but their imports were not
    resolved. An empty edge list is judged separately (`edges_empty`), and a drop that goes with a smaller
    file set is a codebase that shrank."""
    old_edges, new_edges = before["IMPORTS"] + before["RENDERS"], written["imports"] + written["renders"]
    old_files, new_files = before["CodeFile"], written["files"] + written["stub_files"]
    if old_edges == 0 or new_edges == 0 or new_edges >= old_edges * EDGE_DROP_RATIO:
        return []
    if new_files < old_files * FILES_SIMILAR_RATIO:
        return []
    return [f"parse_partial:edges_dropped={new_edges}/{old_edges} (files {new_files}/{old_files})"]


def _spec_incomplete(stats_path: Path) -> list[str]:
    """Why spec/stats.json says the extraction did not finish ([] = complete, or no stats file to say so)."""
    if not stats_path.exists():
        return []
    try:
        stats = json.loads(stats_path.read_text())
    except ValueError:
        return ["stats_unreadable"]
    if not isinstance(stats, dict):
        return ["stats_unreadable"]
    reasons = [] if stats.get("status", "ok") == "ok" else [f"status:{stats['status']}"]
    if not stats.get("complete", True):
        failed, skipped = stats.get("chunks_failed", 0), stats.get("chunks_skipped", 0)
        reasons.append(f"incomplete:chunks_failed={failed},chunks_skipped={skipped}")
    return reasons


def _crawl_incomplete(summary: CrawlSummary | None, screens: int, transitions: int) -> list[str]:
    """Why a crawl artifact is not a complete observation of the app ([] = complete)."""
    if summary is None:
        return ["summary_missing"]
    reasons = [] if screens > 0 else ["no_rows"]
    if summary.stop_reason not in _BUDGET_STOPS:
        reasons.append(f"stop_reason:{summary.stop_reason}")
    if summary.screens != screens:
        reasons.append(f"screens_mismatch:summary={summary.screens},read={screens}")
    if summary.transitions != transitions:
        reasons.append(f"transitions_mismatch:summary={summary.transitions},read={transitions}")
    return reasons


def _pruned_notice(layer: str, pruned: dict[str, int], run_id: str) -> str | None:
    """`<layer>: pruned 4 Screen, 8 UIElement, ...` for what a prune actually deleted (None when nothing)."""
    names = list(dict.fromkeys(name for name, _ in writes.PRUNE[layer]))
    parts = [f"{pruned[n]} {n}" for n in names if pruned.get(n)]
    if not parts:
        return None
    return f"{layer}: pruned {', '.join(parts)} (not asserted by run {run_id})"


def run_load(
    run: RunContext, cfg: TargetConfig, store: GraphStore, *, allow_shrink: bool = False
) -> dict[str, Any]:
    """Load a run's artifacts. `allow_shrink=True` lets a crawl that removes most of the graph's Screens or
    UIElements prune anyway (default False: it is loaded and the layer is left unpruned)."""
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
    load = _Load(run, cfg, store, allow_shrink=allow_shrink)
    store.ensure_schema()
    for path, step in ((spec_p, load.spec), (code_p, load.code), (screens_p, load.crawl)):
        if path.exists():
            step(path)
    out, target = load.out, load.target
    if {"code", "crawl"} & set(out.layers):
        out.served_by = store.link_routes(run.run_id, target=target)
    out.pruned = store.prune_stale(run.run_id, load.prunable, target=target)
    for layer in load.prunable:
        notice = _pruned_notice(layer, out.pruned, run.run_id)
        if notice:
            out.notices.append(notice)
    if "crawl" in out.layers:
        out.dangling_attribution = store.reset_dangling_attribution(target=target)
        edges = out.dangling_attribution["edges"]
        if edges:
            out.notices.append(
                f"links: {edges} IMPLEMENTED_BY/REALIZED_BY edges had screen_ids naming Screens that no "
                f"longer exist ({out.dangling_attribution['reset_to_all']} reset to every screen, "
                f"{out.dangling_attribution['narrowed']} narrowed to the Screens that remain); "
                "re-run link to restore per-screen attribution"
            )
    out.counts = store.counts(target)
    others = store.read("runs", target=target, limit=6)
    out.other_runs = [r["id"] for r in others if r["id"] != run.run_id][:5]
    out.elapsed_s = round(time.monotonic() - t0, 3)

    result = out.model_dump(mode="json")
    run.path("graph", "load.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    return result
