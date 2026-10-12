"""Score the linker's UI -> code predictions against independent file labels (D23; cases 53, 63, 71).

The labels come from the source-map oracle (`oracle_sourcemap`) or from a hand-labelled file; this module only
joins them to what the product produced and counts. It reads the product through artifacts alone: the crawl's
`ScreenState` rows, the `Link` rows of links/links.jsonl and the set of indexed file paths. It never imports a
linker module (tests/test_eval_import_lint.py), so the oracle cannot be influenced by the code it grades.

Unit of scoring = a distinct (oracle_file, role, name, ordinal): a layout control seen on four pages is one
element, as in the canonical UIElement rule (case 46); the observations are kept in `states`.

Join (docs/RECON.md section 6): a label is matched to the crawl's elements by (role, name, ordinal) on the
same url_pattern. When the crawl has a screen in exactly the same state (same active tabs and dialog title)
that screen decides (`join == "state"`); otherwise any screen of the url_pattern does (`join == "pattern"`),
which can conflate two states' ordinals and is reported as such.

Restriction (case 71): only files that exist on `main` are scored, because the product can only predict files
it indexed; the oracle's other labels are counted as `file_not_on_main`, never as misses.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..models import Link, ScreenState
from .stats import Proportion

METHOD_RANK = {"route": 4, "i18n_key": 3, "testid": 3, "literal_text": 2, "llm_pick": 1}
TOP_K = 3


def norm_name(name: str) -> str:
    return " ".join(name.split()).casefold()


@dataclass(frozen=True)
class Label:
    """One independent file label for a perceived element in one UI state."""

    url_pattern: str
    active_tabs: tuple[str, ...]
    dialog_title: str | None
    role: str
    name: str
    ordinal: int
    oracle_file: str
    owner_hops: int = 0
    owner_chain: tuple[str, ...] = ()

    @property
    def state(self) -> tuple[str, tuple[str, ...], str]:
        return self.url_pattern, tuple(sorted(self.active_tabs)), self.dialog_title or ""

    @property
    def unit(self) -> tuple[str, str, str, int]:
        return self.oracle_file, self.role, norm_name(self.name), self.ordinal


@dataclass(frozen=True)
class Prediction:
    path: str
    confidence: float
    method: str


@dataclass
class UnitResult:
    oracle_file: str
    role: str
    name: str
    ordinal: int
    states: list[str]
    owner_hops: int
    status: str                       # scored | not_in_crawl | file_not_on_main
    join: str | None = None           # state | pattern
    predicted: list[Prediction] = field(default_factory=list)
    owner_chain: tuple[str, ...] = ()

    @property
    def top1(self) -> Prediction | None:
        return self.predicted[0] if self.predicted else None

    @property
    def hit1(self) -> bool:
        return bool(self.predicted) and self.predicted[0].path == self.oracle_file

    @property
    def hit3(self) -> bool:
        return any(p.path == self.oracle_file for p in self.predicted[:TOP_K])

    def as_dict(self) -> dict[str, Any]:
        return {
            "oracle_file": self.oracle_file, "role": self.role, "name": self.name, "ordinal": self.ordinal,
            "states": self.states, "owner_hops": self.owner_hops, "owner_chain": list(self.owner_chain),
            "status": self.status, "join": self.join,
            "predicted": [{"path": p.path, "confidence": round(p.confidence, 4), "method": p.method}
                          for p in self.predicted],
            "hit_top1": self.hit1 if self.status == "scored" else None,
            "hit_at_3": self.hit3 if self.status == "scored" else None,
        }


def _state_label(state: tuple[str, tuple[str, ...], str]) -> str:
    pattern, tabs, dialog = state
    return f"{pattern} [{', '.join(tabs)}]" + (f" dialog={dialog}" if dialog else "")


class _CrawlIndex:
    """Crawl elements by (url_pattern, role, normalised name, ordinal), per screen."""

    def __init__(self, screens: Iterable[ScreenState]):
        self.by_key: dict[tuple[str, str, str, int], list[tuple[ScreenState, str]]] = defaultdict(list)
        for screen in sorted(screens, key=lambda s: s.id):
            for el in screen.elements:
                key = (screen.url_pattern, el.role, norm_name(el.name), el.ordinal)
                self.by_key[key].append((screen, el.id))

    def match(self, label: Label) -> tuple[str | None, list[tuple[str, str]]]:
        """(join level, [(screen id, element obs id)]) for one label."""
        hits = self.by_key.get((label.url_pattern, label.role, norm_name(label.name), label.ordinal), [])
        exact = [(s.id, eid) for s, eid in hits
                 if (s.url_pattern, tuple(sorted(s.active_tabs)), s.dialog_title or "") == label.state]
        if exact:
            return "state", exact
        if hits:
            return "pattern", [(s.id, eid) for s, eid in hits]
        return None, []


def rank_predictions(links: Iterable[Link], obs_ids: Collection[str],
                     screen_ids: Collection[str]) -> list[Prediction]:
    """IMPLEMENTED_BY links of the matched observations, best per file, ranked by (confidence, method, path).

    A link derived on specific screens counts only when one of them is among `screen_ids`."""
    best: dict[str, Prediction] = {}
    wanted = set(screen_ids)
    for link in links:
        if link.kind != "IMPLEMENTED_BY" or link.src not in obs_ids:
            continue
        if link.screen_ids and not wanted & set(link.screen_ids):
            continue
        cand = Prediction(link.dst, link.confidence, link.method)
        old = best.get(link.dst)
        if old is None or (cand.confidence, METHOD_RANK.get(cand.method, 0)) > (
                old.confidence, METHOD_RANK.get(old.method, 0)):
            best[link.dst] = cand
    return sorted(best.values(), key=lambda p: (-p.confidence, -METHOD_RANK.get(p.method, 0), p.path))


def _group_units(labels: Sequence[Label]) -> dict[tuple[str, str, str, int], list[Label]]:
    groups: dict[tuple[str, str, str, int], list[Label]] = defaultdict(list)
    for label in labels:
        groups[label.unit].append(label)
    return groups


def score_units(labels: Sequence[Label], screens: Sequence[ScreenState], links: Sequence[Link],
                indexed_paths: Collection[str]) -> list[UnitResult]:
    """One UnitResult per distinct labelled element, in a deterministic order."""
    crawl = _CrawlIndex(screens)
    by_src: dict[str, list[Link]] = defaultdict(list)
    for link in links:
        if link.kind == "IMPLEMENTED_BY":
            by_src[link.src].append(link)
    results: list[UnitResult] = []
    for unit, members in sorted(_group_units(labels).items()):
        first = min(members, key=lambda m: (m.owner_hops, m.state))
        res = UnitResult(oracle_file=unit[0], role=unit[1], name=first.name, ordinal=unit[3],
                         states=sorted({_state_label(m.state) for m in members}), owner_hops=first.owner_hops,
                         status="scored", owner_chain=first.owner_chain)
        if unit[0] not in indexed_paths:
            res.status = "file_not_on_main"
            results.append(res)
            continue
        obs_ids: set[str] = set()
        screen_ids: set[str] = set()
        joins: list[str] = []
        for m in members:
            level, hits = crawl.match(m)
            if level is not None:
                joins.append(level)
                obs_ids.update(eid for _, eid in hits)
                screen_ids.update(sid for sid, _ in hits)
        if not joins:
            res.status = "not_in_crawl"
            results.append(res)
            continue
        res.join = "state" if "state" in joins else "pattern"
        pool = [link for oid in sorted(obs_ids) for link in by_src.get(oid, [])]
        res.predicted = rank_predictions(pool, obs_ids, screen_ids)
        results.append(res)
    return results


def metrics(results: Sequence[UnitResult], n_labels: int) -> dict[str, Any]:
    """Counts and intervals. Every rate is a Proportion (k of n, Wilson 95%); n is never omitted."""
    scored = [r for r in results if r.status == "scored"]
    covered = [r for r in scored if r.predicted]
    by_method: dict[str, list[UnitResult]] = defaultdict(list)
    for r in scored:
        by_method[r.top1.method if r.top1 else "none"].append(r)
    return {
        "labels": n_labels,
        "units": len(results),
        "excluded": {
            "not_in_crawl": sum(1 for r in results if r.status == "not_in_crawl"),
            "file_not_on_main": sum(1 for r in results if r.status == "file_not_on_main"),
        },
        "scored": len(scored),
        "join_levels": {lvl: sum(1 for r in scored if r.join == lvl) for lvl in ("state", "pattern")},
        "linker_coverage": Proportion.of(len(covered), len(scored)).as_dict(),
        "top1_precision_of_covered": Proportion.of(sum(r.hit1 for r in covered), len(covered)).as_dict(),
        "top1_accuracy_of_scored": Proportion.of(sum(r.hit1 for r in scored), len(scored)).as_dict(),
        "hit_at_3_of_covered": Proportion.of(sum(r.hit3 for r in covered), len(covered)).as_dict(),
        "hit_at_3_of_scored": Proportion.of(sum(r.hit3 for r in scored), len(scored)).as_dict(),
        "by_top1_method": {
            m: {"n": len(rs), "top1_correct": Proportion.of(sum(r.hit1 for r in rs), len(rs)).as_dict(),
                "hit_at_3": Proportion.of(sum(r.hit3 for r in rs), len(rs)).as_dict()}
            for m, rs in sorted(by_method.items())
        },
        "by_owner_hops": {
            str(h): {"n": len(rs), "top1_correct": Proportion.of(sum(r.hit1 for r in rs), len(rs)).as_dict()}
            for h, rs in sorted(_group_by(scored, lambda r: r.owner_hops).items())
        },
    }


def _group_by(items: Iterable[UnitResult], key: Any) -> dict[Any, list[UnitResult]]:
    out: dict[Any, list[UnitResult]] = defaultdict(list)
    for item in items:
        out[key(item)].append(item)
    return out
