"""Flows: shortest observed paths from the entry screens, named from what the crawl saw (D08; cases 12, 46).

No LLM. A Flow is one path in the crawl's transition graph:

 * entry screens = the page Screens of `cfg.crawl.seed_urls` (matched on url path, or on the `goto <path>`
   that first reached them, so a seed that redirects, `/realtime` -> `/realtime/websocket`, still matches);
 * edges = Transitions that were observed, not blocked, carry an element id and change the screen (typing
   into a field and inferred-from-layout edges are not steps);
 * BFS from each entry to depth 4 (edges), neighbours in (label, screen, element, transition) order, so the
   path to every screen is the first shortest one and ties never depend on hash order. Another entry reached
   from an entry is a boundary: it ends the flow there and is not expanded, so "REST > Settings" and
   "Settings > Client Certificates" are two flows, not "REST > Settings > Client Certificates" as well;
 * a flow ends at a modal, tab or page with no further observed edge to a screen not yet on the BFS tree
   (a dialog whose only way out leads back to the page is such an end), or at depth 4;
 * name = entry label > one part per step: the dialog title for a dialog, else the label of the control
   that was clicked ("REST > Environments > New Collection"). The entry label is the name of a link that
   points at the entry's path, else the page title without the product name, else the path;
 * Flow.id = stable_id("flow", *steps); at most 30 flows, shortest first, then entry order, name, id.
"""

from __future__ import annotations

import time
from collections import Counter, deque
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..ids import stable_id
from ..models import Flow, ScreenState, Transition
from ..run import RunContext
from ..targets import TargetConfig
from .inputs import load_inputs, write_jsonl

MAX_DEPTH = 4
MAX_FLOWS = 30
GENERIC_DIALOG_TITLES = frozenset({"menu", "dialog", "alert", "alertdialog", "listbox", "tooltip", "popover"})
_TITLE_SEPARATORS = (" • ", " | ", " - ", " – ", " — ")


@dataclass(frozen=True)
class _Edge:
    to: str
    element: str
    label: str
    tid: str


def _path(url: str) -> str:
    return urlsplit(url).path.rstrip("/") or "/"


def entry_screens(cfg: TargetConfig, screens: Sequence[ScreenState]) -> list[ScreenState]:
    """One page Screen per seed URL, in seed order (shallowest, then id, when several qualify)."""
    out: list[ScreenState] = []
    pages = sorted((s for s in screens if s.kind == "page"), key=lambda s: (s.depth, s.id))
    for seed in cfg.crawl.seed_urls:
        want = _path(seed)
        for s in pages:
            if _path(s.url) == want or s.first_seen_action == f"goto {want}":
                if s not in out:
                    out.append(s)
                break
    return out


def _edges(transitions: Sequence[Transition]) -> dict[str, list[_Edge]]:
    best: dict[tuple[str, str], _Edge] = {}
    for t in transitions:
        if not t.observed or t.blocked or not t.element_id or t.from_screen == t.to_screen:
            continue
        edge = _Edge(t.to_screen, t.element_id, t.label, t.id)
        key = (t.from_screen, t.to_screen)
        old = best.get(key)
        if old is None or (edge.label.casefold(), edge.element, edge.tid) < (old.label.casefold(), old.element,
                                                                              old.tid):
            best[key] = edge
    out: dict[str, list[_Edge]] = {}
    for (src, _), edge in best.items():
        out.setdefault(src, []).append(edge)
    for edges in out.values():
        edges.sort(key=lambda e: (e.label.casefold(), e.to, e.element, e.tid))
    return out


def _strip_brand(title: str, brand: str) -> str:
    parts = [title]
    for sep in _TITLE_SEPARATORS:
        if sep in title:
            parts = [p.strip() for p in title.split(sep)]
            break
    kept = [p for p in parts if p and p.casefold() != brand.casefold()]
    return kept[0] if kept else ""


def entry_label(cfg: TargetConfig, entry: ScreenState, screens: Sequence[ScreenState], seed_path: str) -> str:
    names: Counter[str] = Counter()
    wanted = {seed_path, _path(entry.url)}
    for s in screens:
        for el in s.elements:
            if el.role == "link" and el.href and el.name and _path(el.href) in wanted:
                names[" ".join(el.name.split())] += 1
    if names:
        return min(names, key=lambda n: (-names[n], len(n), n))
    return _strip_brand(entry.title, cfg.display_name) or entry.url_pattern


def _part(dest: ScreenState, label: str) -> str:
    title = (dest.dialog_title or "").strip()
    if title and title.casefold() not in GENERIC_DIALOG_TITLES:
        return title
    return " ".join(label.split()) or _strip_brand(dest.title, "") or dest.url_pattern


def _bfs(entry: ScreenState, edges: dict[str, list[_Edge]],
         boundaries: set[str]) -> dict[str, tuple[str | None, _Edge | None]]:
    """screen id -> (parent screen id, edge taken): the BFS tree to MAX_DEPTH from `entry`."""
    parent: dict[str, tuple[str | None, _Edge | None]] = {entry.id: (None, None)}
    depth = {entry.id: 0}
    queue = deque([entry.id])
    while queue:
        cur = queue.popleft()
        if depth[cur] >= MAX_DEPTH or (cur != entry.id and cur in boundaries):
            continue
        for e in edges.get(cur, ()):
            if e.to not in parent:
                parent[e.to] = (cur, e)
                depth[e.to] = depth[cur] + 1
                queue.append(e.to)
    return parent


def build_flows(cfg: TargetConfig, screens: Sequence[ScreenState], transitions: Sequence[Transition]) -> list[Flow]:
    by_id = {s.id: s for s in screens}
    entries = entry_screens(cfg, screens)
    edges = _edges(transitions)
    boundaries = {e.id for e in entries}
    flows: dict[str, tuple[int, int, Flow]] = {}
    for rank, entry in enumerate(entries):
        seed_path = next((_path(u) for u in cfg.crawl.seed_urls
                          if _path(entry.url) == _path(u) or entry.first_seen_action == f"goto {_path(u)}"),
                         _path(entry.url))
        base = entry_label(cfg, entry, screens, seed_path)
        parent = _bfs(entry, edges, boundaries)
        interior = {p for p, _ in parent.values() if p is not None}
        for node in sorted(parent):
            if node == entry.id or node in interior:
                continue
            chain: list[tuple[str, _Edge]] = []
            cur = node
            while parent[cur][0] is not None:
                prev, edge = parent[cur]
                chain.append((cur, edge))   # type: ignore[arg-type]
                cur = prev                  # type: ignore[assignment]
            chain.reverse()
            steps = [e.element for _, e in chain]
            parts = [_part(by_id[sid], e.label) for sid, e in chain]
            flow = Flow(id=stable_id("flow", *steps), name=" > ".join([base, *parts]), steps=steps,
                        screens=[entry.id, *(sid for sid, _ in chain)], goal=parts[-1])
            flows.setdefault(flow.id, (len(steps), rank, flow))
    ordered = sorted(flows.values(), key=lambda t: (t[0], t[1], t[2].name, t[2].id))
    return [f for _, _, f in ordered[:MAX_FLOWS]]


def run_flows(run: RunContext, cfg: TargetConfig, store) -> list[Flow]:
    t0 = time.monotonic()
    inputs = load_inputs(run, cfg, spec=False, code=False)
    with run.stage("flows.build") as span:
        flows = build_flows(cfg, inputs.screens, inputs.transitions)
        for f in flows:
            run.evidence.add("flow.path", f.id, f"{f.name}: {len(f.steps)} step(s) over {len(f.screens)} screens")
        span.set(flows=len(flows), entries=len(entry_screens(cfg, inputs.screens)))
    write_jsonl(run.path("links", "flows.jsonl"), list(flows))
    if store is not None:
        store.write_flows(flows, run.run_id, target=cfg.name)
    run.tracer.event("flows", flows=len(flows), wall_clock_s=round(time.monotonic() - t0, 3))
    return flows
