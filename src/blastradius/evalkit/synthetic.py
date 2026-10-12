"""Synthetic-PR recall: a consistency check, CIRCULAR by construction (plan section 9.2; case 63).

Take elements whose file link is deterministic and high-confidence, simulate a PR that touches that file, run
the retrieval (the `impact` template the reasoning stage wraps, at the product's hop cap) and require that the
element's screens and flows are reported. The expectation comes from the very links that built the graph, so a
pass says "the retrieval plumbing returns what the graph holds" and nothing about whether the links are right:
every output of this module carries `circular: true`. The independent signal is the source-map oracle.

Triviality: a file with a very wide closure (a layout, a hub package) reports nearly every screen, so
recalling its element is free. Files whose reported screens reach `breadth_max` of all screens (the
pre-registered breadth threshold) are flagged `trivial` and recall is also given without them.

`reason.retrieve.retrieve` needs a full PRData and writes evidence rows into the run it is given, which an
evaluation must not do to the product's ledger; the same `impact` template is called directly instead.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Protocol

from ..ids import sha
from ..models import MAX_HOPS, Link
from .stats import Proportion

DETERMINISTIC_METHODS = frozenset({"route", "i18n_key", "testid"})
CIRCULAR_LABEL = (
    "CIRCULAR: the expected screens and flows come from the same links that built the graph. This checks "
    "that retrieval returns what the graph holds, not that the links are correct."
)
CHUNK = 50
MISS_CAP = 25


class GraphReader(Protocol):
    def read(self, template: str, **params: Any) -> list[dict[str, Any]]: ...


Retrieve = Callable[[GraphReader, str, list[str]], list[dict[str, Any]]]


def impact_rows(store: GraphReader, target: str, changed: list[str]) -> list[dict[str, Any]]:
    """The default retrieval: the product's `impact` template at its hop cap, reverse closure only."""
    return store.read("impact", target=target, changed=changed, hops=MAX_HOPS, forward=0)


def candidate_files(links: Iterable[Link], *, min_confidence: float, max_files: int) -> tuple[list[str], int]:
    """Files carrying at least one deterministic, high-confidence IMPLEMENTED_BY link, and how many there
    were before the cap. Over the cap the files are taken in hash order, not alphabetical order."""
    every = sorted({l.dst for l in links if l.kind == "IMPLEMENTED_BY" and l.method in DETERMINISTIC_METHODS
                    and l.confidence >= min_confidence})
    if len(every) <= max_files:
        return every, len(every)
    return sorted(sorted(every, key=lambda p: sha("synthetic", p))[:max_files]), len(every)


def _expected(store: GraphReader, target: str, files: Sequence[str], min_confidence: float,
              ) -> dict[str, dict[str, dict[str, Any]]]:
    """file -> element id -> {name, role, screens} from the graph's own IMPLEMENTED_BY edges."""
    out: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for i in range(0, len(files), CHUNK):
        for row in store.read("element_by_file", target=target, paths=list(files[i:i + CHUNK])):
            confidence = row.get("confidence") or 0.0
            if row.get("method") not in DETERMINISTIC_METHODS or confidence < min_confidence:
                continue
            fresh = {"name": row.get("name") or "", "role": row.get("role") or "", "screens": set()}
            entry = out[row["path"]].setdefault(row["element_id"], fresh)
            entry["screens"].update(row.get("screen_ids") or [])
    return out


def _expected_flows(store: GraphReader, target: str,
                    element_ids: Sequence[str]) -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    ids = sorted(set(element_ids))
    for i in range(0, len(ids), CHUNK):
        for row in store.read("element_context", target=target, element_ids=ids[i:i + CHUNK]):
            out[(row["element_id"], row["screen_id"])].update(f["id"] for f in row.get("flows") or [])
    return out


def run_synthetic_recall(store: GraphReader, target: str, links: Sequence[Link], *,
                         min_confidence: float = 0.8, breadth_max: float = 0.25, max_files: int = 200,
                         retrieve: Retrieve = impact_rows) -> dict[str, Any]:
    """Recall of (element, screen) pairs and of flows for simulated one-file PRs. Never raises on an empty
    input: with no candidate links the result says so (`n_elements == 0`)."""
    base: dict[str, Any] = {"circular": True, "label": CIRCULAR_LABEL,
                            "method": "graph.impact template, direct",
                            "hop_cap": MAX_HOPS, "min_link_confidence": min_confidence,
                            "link_methods": sorted(DETERMINISTIC_METHODS)}
    files, available = candidate_files(links, min_confidence=min_confidence, max_files=max_files)
    expected = _expected(store, target, files, min_confidence)
    total_screens = len(store.read("screen_url_patterns", target=target))
    flows = _expected_flows(store, target, [e for per in expected.values() for e in per])
    instances: list[dict[str, Any]] = []
    trivial_files: list[str] = []
    for path in sorted(expected):
        rows = retrieve(store, target, [path])
        reported = {(r["element_id"], r["screen_id"]) for r in rows}
        reported_flows: dict[tuple[str, str], set[str]] = {
            (r["element_id"], r["screen_id"]): {f["id"] for f in r.get("flows") or []} for r in rows}
        screens_reported = {r["screen_id"] for r in rows}
        trivial = total_screens > 0 and len(screens_reported) / total_screens >= breadth_max
        if trivial:
            trivial_files.append(path)
        for element_id in sorted(expected[path]):
            want = expected[path][element_id]
            pairs = {(element_id, s) for s in want["screens"]}
            if not pairs:
                continue            # an element with no screen cannot be reported on any: not an expectation
            missing = sorted(s for (e, s) in pairs - reported)
            flow_triples = {(e, s, f) for (e, s) in pairs for f in flows.get((e, s), ())}
            flow_missing = sorted(f"{s}:{f}" for (e, s, f) in flow_triples
                                  if f not in reported_flows.get((e, s), set()))
            instances.append({"file": path, "element_id": element_id, "role": want["role"],
                              "name": want["name"],
                              "expected_screens": len(pairs), "missing_screens": missing,
                              "expected_flows": len(flow_triples), "missing_flows": flow_missing,
                              "trivial": trivial})
    return {**base, **_summarise(instances, files, available, trivial_files, total_screens, breadth_max)}


def _summarise(instances: list[dict[str, Any]], files: list[str], available: int, trivial_files: list[str],
               total_screens: int, breadth_max: float) -> dict[str, Any]:
    hit = [i for i in instances if not i["missing_screens"]]
    nontrivial = [i for i in instances if not i["trivial"]]
    flow_total = sum(i["expected_flows"] for i in instances)
    flow_missed = sum(len(i["missing_flows"]) for i in instances)
    misses = [i for i in instances if i["missing_screens"] or i["missing_flows"]]
    return {
        "n_files": len(files), "candidate_files_before_cap": available, "n_elements": len(instances),
        "total_screens": total_screens, "breadth_max": breadth_max,
        "trivial_files": sorted(trivial_files),
        "recall": Proportion.of(len(hit), len(instances)).as_dict(),
        "recall_nontrivial": Proportion.of(sum(1 for i in nontrivial if not i["missing_screens"]),
                                           len(nontrivial)).as_dict(),
        "flow_recall": Proportion.of(flow_total - flow_missed, flow_total).as_dict(),
        "misses": misses[:MISS_CAP], "miss_count": len(misses),
    }
