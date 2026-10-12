"""Stage 7a: retrieval. Changed files -> the screens and elements they can reach, by templated Cypher (D19, D25).

No LLM and no free-form query: every read is a named, parameterised template (`impact`, `hub_degree`,
`elements_for_keys`, `element_context`, `keys_used_by_files`). For each changed source file (one query per seed,
so each route knows which seed it came from and the per-seed breadth is exact):

 * the reverse closure to `models.MAX_HOPS` (importers and renderers, hop 0 = the elements the file
   implements) and one forward hop. Forward rows are *held in reserve* (`admitted=False`): the grader's EXPAND
   action admits them only when the reverse evidence is not enough (D25: forward edges are a repair, not a
   default);
 * a locale diff reaches elements through the i18n cascade run backwards (case 32): an element that displays
   the old English value of a changed or removed key is a *copy change* (route direction `copy`);
 * a file the PR adds has no baseline node. The head overlay (`code.overlay.run_overlay`, case 39) gives its
   importers at the PR head; the elements those files implement are attached to the added file, in memory only
   (the baseline in Neo4j is never touched).

Path confidence = IMPLEMENTED_BY confidence x HOP_DECAY per hop (code edges are structural, 1.0). Tiers are
decided later by `assemble`; this module only produces facts. Counts anywhere downstream are over distinct
(screen, element) hits, never over routes (case 51).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..code.fileclass import workspace_package
from ..code.index import edge_evidence_id
from ..link.text import normalise_text
from ..models import MAX_HOPS, CodeEdge, CodeIndex, FileClass
from ..run import RunContext
from ..targets import TargetConfig
from .change import LocaleDiff
from .facts import Hit, LocaleResult, Retrieval, Route, SeedCategory, SeedInfo, path_confidence
from .pr import PRData, file_evidence
from .thresholds import Thresholds, load_thresholds

_PRIORITY = {
    FileClass.PAGE: 0, FileClass.LAYOUT: 0, FileClass.COMPONENT: 0, FileClass.SERVICE: 1, FileClass.UTIL: 1,
    FileClass.DATA_SCHEMA: 1, FileClass.STYLE_GLOBAL: 1, FileClass.SOURCE_OTHER: 2,
}


def _under(path: str, root: str) -> bool:
    root = root.strip("/")
    return path == root or path.startswith(root + "/")


def _categories(path: str, file_class: FileClass, cfg: TargetConfig) -> list[SeedCategory]:
    cats: list[SeedCategory] = []
    if file_class is FileClass.LAYOUT or path in cfg.code.global_files:
        cats.append("global_layout")
    if file_class is FileClass.STYLE_GLOBAL:
        cats.append("global_style")
    hub_roots = [r for r in cfg.code.roots if workspace_package(r) in cfg.code.hub_packages]
    if any(_under(path, r) for r in hub_roots):
        cats.append("hub")
    if file_class is FileClass.DATA_SCHEMA:
        cats.append("stored_data")
    return cats or ["normal"]


def build_seeds(run: RunContext, cfg: TargetConfig, pr: PRData, th: Thresholds) -> Retrieval:
    """Changed files -> seeds to analyse, plus what is deliberately not a seed (case 40) and what was cut."""
    out = Retrieval()
    set_aside: dict[str, int] = {}
    seeds: list[SeedInfo] = []
    for f in pr.files:
        if f.set_aside:
            set_aside[f.file_class.value] = set_aside.get(f.file_class.value, 0) + 1
            continue
        if f.file_class is FileClass.LOCALE:
            if f.path not in cfg.code.locale_files:
                out.other_locale_files.append(f.path)
            continue
        if not f.indexed and f.file_class not in (FileClass.DATA_SCHEMA, FileClass.STYLE_GLOBAL):
            out.outside_index.append(f.path)
            continue
        seeds.append(SeedInfo(
            path=f.graph_path, display_path=f.path, file_class=f.file_class, status=f.status, kind=f.kind,
            categories=_categories(f.path, f.file_class, cfg), in_snapshot=bool(f.in_snapshot),
            added=f.status == "added", touched=list(f.touched), added_symbols=list(f.added_symbols),
            removed_symbols=list(f.removed_symbols), symbols_known=f.symbols_known,
            stale_overlap=f.stale_overlap, patch_missing=f.patch_missing,
            evidence_id=file_evidence(run, pr, f)))
    seeds.sort(key=lambda s: (_PRIORITY.get(s.file_class, 3), s.path))
    cap = th.retrieval.max_seeds
    out.not_assessed = max(0, len(seeds) - cap)
    out.seeds = sorted(seeds[:cap], key=lambda s: s.path)
    out.set_aside = dict(sorted(set_aside.items()))
    out.outside_index.sort()
    out.other_locale_files.sort()
    return out


# ------------------------------------------------------------------------------------------ routes
def _edge_evidence(run: RunContext, via: Sequence[str], kinds: Sequence[str], symbols: Sequence[Sequence[str]],
                   specifiers: Sequence[str | None]) -> list[str]:
    ids: list[str] = []
    for i, kind in enumerate(kinds):
        if kind not in ("IMPORTS", "RENDERS") or i + 1 >= len(via):
            continue
        edge = CodeEdge(src=via[i], dst=via[i + 1], kind=kind, symbols=list(symbols[i]) if i < len(symbols) else [],
                        via=specifiers[i] if i < len(specifiers) else None)
        ev = edge_evidence_id(edge)
        if run.evidence.exists(ev):
            ids.append(ev)
    return ids


def _route_from_row(run: RunContext, row: Mapping[str, Any]) -> Route:
    direction = row["direction"]
    hops = int(row["hops"])
    impl = float(row["impl_confidence"] or 0.0)
    symbols = [list(s or []) for s in (row.get("symbols") or [])]
    ids = [row["impl_evidence_id"]] if row.get("impl_evidence_id") else []
    ids += _edge_evidence(run, row["via"], row.get("kinds") or [], symbols, row.get("specifiers") or [])
    conf = path_confidence(impl, hops)
    return Route(
        seed=row["seed"], direction=direction, hops=hops, file=row["file"], via=list(row["via"]),
        kinds=list(row.get("kinds") or []), symbols=symbols, specifiers=list(row.get("specifiers") or []),
        impl_confidence=impl, impl_method=row.get("impl_method"), impl_evidence_id=row.get("impl_evidence_id"),
        confidence=conf, uncapped=conf, admitted=direction != "forward",
        evidence_ids=sorted(dict.fromkeys(ids)))


class _Hits:
    """Hits keyed by (screen, element); routes accumulate in insertion order and are sorted on `done`."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], Hit] = {}

    def add_row(self, run: RunContext, row: Mapping[str, Any], route: Route | None = None) -> Hit:
        key = (row["screen_id"], row["element_id"])
        hit = self._by_key.get(key)
        if hit is None:
            hit = Hit(screen_id=row["screen_id"], element_id=row["element_id"],
                      element_role=row.get("element_role") or "", element_name=row.get("element_name") or "",
                      hidden_on_screen=bool(row.get("hidden_on_screen")),
                      impl_files=sorted({e["file"] for e in row.get("impl_edges") or []}),
                      flows=[{"id": f["id"], "name": f["name"]} for f in row.get("flows") or []],
                      requirements=[dict(q) for q in row.get("requirements") or []])
            self._by_key[key] = hit
        hit.routes.append(route or _route_from_row(run, row))
        return hit

    def done(self) -> list[Hit]:
        out = list(self._by_key.values())
        for h in out:
            h.routes.sort(key=lambda r: (r.seed, r.direction, r.hops, r.file, tuple(r.via)))
        return sorted(out, key=lambda h: h.key)


# ----------------------------------------------------------------------------------------- overlay
def _overlay_status(overlay: CodeIndex | None) -> dict[str, object]:
    if overlay is None:
        return {"status": "not_applied", "reason": "no head overlay was available"}
    stats = overlay.stats
    status = str(stats.get("pr_overlay", "unknown"))
    out: dict[str, object] = {"status": status}
    if status == "applied":
        out.update(added=len(stats.get("overlay_added", [])), modified=len(stats.get("overlay_modified", [])),
                   deleted=len(stats.get("overlay_deleted", [])), head_sha=stats.get("overlay_head_sha"))
    else:
        out["reason"] = str(stats.get("pr_overlay_reason", ""))
    return out


def _reverse_paths(edges: Iterable[CodeEdge], start: str, max_hops: int) -> dict[str, tuple[int, list[CodeEdge]]]:
    """file -> (hops, edges from that file down to `start`) for every file reaching `start` in <= max_hops."""
    importers: dict[str, list[CodeEdge]] = {}
    for e in edges:
        importers.setdefault(e.dst, []).append(e)
    found: dict[str, tuple[int, list[CodeEdge]]] = {start: (0, [])}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        hops, path = found[node]
        if hops >= max_hops:
            continue
        for e in sorted(importers.get(node, ()), key=lambda e: (e.src, e.kind)):
            if e.src not in found:
                found[e.src] = (hops + 1, [e, *path])
                queue.append(e.src)
    found.pop(start)
    return found


def _attach_added_files(run: RunContext, store: Any, retr: Retrieval, hits: _Hits,
                        overlay: CodeIndex | None) -> None:
    """Route elements of the files that render or import an ADDED file back to it (case 39). An importer is
    usually another changed file, but any file of the baseline can import it (a re-resolved component tag)."""
    added = [s for s in retr.seeds if s.added]
    if not added or overlay is None or overlay.stats.get("pr_overlay") != "applied":
        return
    new_files = set(overlay.stats.get("overlay_added", []))
    wanted: dict[str, list[tuple[SeedInfo, int, list[CodeEdge]]]] = {}
    for seed in added:
        paths = _reverse_paths(overlay.edges, seed.path, MAX_HOPS)
        seed.overlay_importers = sorted(f for f, (h, _) in paths.items() if h == 1)
        seed.overlay_attached = bool(paths)
        for f, (h, edges) in sorted(paths.items()):
            if f not in new_files:          # an added file has no baseline node, so no controls to attach
                wanted.setdefault(f, []).append((seed, h, edges))
    if not wanted:
        return
    rows = store.read("impact", changed=sorted(wanted), hops=0, forward=0)
    for row in rows:
        if row["direction"] != "self":
            continue
        for seed, hops, edges in wanted.get(row["file"], []):
            via = [row["file"], *[e.dst for e in edges]]
            kinds = [e.kind for e in edges]
            symbols = [list(e.symbols) for e in edges]
            specifiers = [e.via for e in edges]
            impl = float(row["impl_confidence"] or 0.0)
            ids = [row["impl_evidence_id"]] if row.get("impl_evidence_id") else []
            ids += _edge_evidence(run, via, kinds, symbols, specifiers)
            conf = path_confidence(impl, hops)
            route = Route(seed=seed.path, direction="overlay", hops=hops, file=row["file"], via=via, kinds=kinds,
                          symbols=symbols, specifiers=specifiers, impl_confidence=impl,
                          impl_method=row.get("impl_method"), impl_evidence_id=row.get("impl_evidence_id"),
                          confidence=conf, uncapped=conf, evidence_ids=sorted(dict.fromkeys(ids)))
            hits.add_row(run, row, route)


# ------------------------------------------------------------------------------------------ locale
def _locale_routes(run: RunContext, store: Any, diffs: Sequence[LocaleDiff], hits: _Hits, retr: Retrieval,
                   base_index: CodeIndex | None, overlay: CodeIndex | None, th: Thresholds) -> None:
    """Changed and removed message keys -> elements that display the old string (case 32, run backwards)."""
    head_keys = {u.key for u in (overlay.key_uses if overlay is not None else [])}
    for diff in diffs:
        res = LocaleResult(path=diff.path, added=len(diff.added), changed=len(diff.changed),
                           removed=len(diff.removed), eol_only=diff.eol_only, problem=diff.problem,
                           changed_keys=diff.keys[:50])
        retr.locales.append(res)
        if diff.problem:
            continue
        keys = sorted({*diff.changed, *diff.removed})
        if not keys:
            continue
        rows = store.read("elements_for_keys", keys=keys)
        ctx = _element_context(store, sorted({r["element_id"] for r in rows}))
        matched: set[tuple[str, str]] = set()
        exact: set[tuple[str, str]] = set()
        severe: set[str] = set()
        for row in rows:
            key = row["key"]
            removed = key in diff.removed
            old = (base_index.i18n.get(key) if base_index is not None else None) or \
                (diff.removed[key] if removed else diff.changed[key][0])
            is_exact = row["match"] == "exact" and normalise_text(row["name"]) == normalise_text(old)
            impl = float(row["confidence"] or 0.0)
            still_shown = (key in head_keys) if overlay is not None else True
            severity = removed and is_exact and still_shown
            if removed:
                note = f"the text “{old}” is removed from the interface texts"
            else:
                note = f"the text “{old}” becomes “{diff.changed[key][1]}”"
            for screen_id in row["screen_ids"]:
                ev = row.get("evidence_id")
                route = Route(
                    seed=diff.path, direction="copy", hops=0, file=row["file"], via=[row["file"]],
                    impl_confidence=impl, impl_method=row.get("method"), impl_evidence_id=ev,
                    confidence=path_confidence(impl, 0), uncapped=path_confidence(impl, 0),
                    note=note, old_text=old, high_severity=severity, evidence_ids=[ev] if ev else [])
                if not is_exact:
                    route.restrict("copy_other_element", th.caps.copy_other_element)
                    route.note = f"{note} (this control shares a file with that text but shows other words)"
                base = {"screen_id": screen_id, "element_id": row["element_id"],
                        "element_role": row["role"], "element_name": row["name"]}
                c = ctx.get((screen_id, row["element_id"]), {})
                hits.add_row(run, {**base, "flows": c.get("flows", []), "requirements": c.get("requirements", [])},
                             route)
                matched.add((screen_id, row["element_id"]))
                if is_exact:
                    exact.add((screen_id, row["element_id"]))
                if severity:
                    severe.add(key)
        res.elements_matched, res.exact_matches = len(matched), len(exact)
        res.high_severity_keys = sorted(severe)
        res.evidence_id = run.evidence.add(
            "reason.locale", f"{diff.path}:{len(keys)} keys",
            f"{len(keys)} changed or removed text(s) reach {len(matched)} control(s), {len(exact)} showing the "
            f"old text exactly")


def _element_context(store: Any, element_ids: Sequence[str]) -> dict[tuple[str, str], dict[str, Any]]:
    if not element_ids:
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in store.read("element_context", element_ids=list(element_ids)):
        out[(row["screen_id"], row["element_id"])] = row
    return out


# ------------------------------------------------------------------------------------------- stage
def retrieve(run: RunContext, cfg: TargetConfig, store: Any, pr: PRData, locale_diffs: Sequence[LocaleDiff], *,
             base_index: CodeIndex | None = None, overlay: CodeIndex | None = None,
             th: Thresholds | None = None) -> Retrieval:
    """Seeds, per-seed breadth, routes and hits (module docstring)."""
    th = th or load_thresholds()
    with run.stage("reason.retrieve", pr=pr.number) as span:
        retr = build_seeds(run, cfg, pr, th)
        retr.overlay = _overlay_status(overlay)
        known = {s.path for s in retr.seeds if s.in_snapshot}
        _breadth(store, retr, th)
        hits = _Hits()
        for seed in retr.seeds:
            if seed.path not in known or "global_style" in seed.categories:
                continue
            mapping = 0 if "global_layout" in seed.categories else MAX_HOPS
            rows = store.read("impact", changed=[seed.path], hops=mapping,
                              forward=0 if mapping == 0 else th.retrieval.forward_hops)
            for row in rows:
                hits.add_row(run, row)
        _seed_keys(store, retr, known, overlay)
        _attach_added_files(run, store, retr, hits, overlay)
        _locale_routes(run, store, locale_diffs, hits, retr, base_index, overlay, th)
        retr.hits = hits.done()
        span.set(seeds=len(retr.seeds), hits=len(retr.hits), locales=len(retr.locales))
    return retr


def _breadth(store: Any, retr: Retrieval, th: Thresholds) -> None:
    """Per-seed closure size at hop 1 and hop 2 as a share of all UI files (the grader's breadth signal)."""
    paths = sorted(s.path for s in retr.seeds if s.in_snapshot)
    if not paths:
        return
    by_h: dict[int, dict[str, Mapping[str, Any]]] = {}
    for hops in (1, MAX_HOPS):
        by_h[hops] = {r["path"]: r for r in store.read("hub_degree", paths=paths, hops=hops)}
    for seed in retr.seeds:
        r1, r2 = by_h[1].get(seed.path), by_h[MAX_HOPS].get(seed.path)
        if r1:
            seed.importers_h1, seed.ui_importers_h1 = int(r1["importers"]), int(r1["ui_importers"])
            seed.fraction_h1 = round(float(r1["fraction"]), 4)
            retr.ui_total = seed.ui_total = int(r1["ui_total"])
        if r2:
            seed.importers_h2, seed.ui_importers_h2 = int(r2["importers"]), int(r2["ui_importers"])
            seed.fraction_h2 = round(float(r2["fraction"]), 4)
            retr.ui_total = seed.ui_total = int(r2["ui_total"])


def _seed_keys(store: Any, retr: Retrieval, known: set[str], overlay: CodeIndex | None) -> None:
    """Static i18n keys each seed uses (for the locale route of EXPAND); head uses win for changed files."""
    head: dict[str, set[str]] = {}
    if overlay is not None and overlay.stats.get("pr_overlay") == "applied":
        for u in overlay.key_uses:
            if not u.dynamic:
                head.setdefault(u.file, set()).add(u.key)
    paths = sorted(known)
    graph: dict[str, set[str]] = {}
    if paths:
        for row in store.read("keys_used_by_files", paths=paths):
            if not row["dynamic"]:
                graph.setdefault(row["path"], set()).add(row["key"])
    for seed in retr.seeds:
        keys = head.get(seed.display_path) or head.get(seed.path) or graph.get(seed.path) or set()
        seed.keys = sorted(keys)
