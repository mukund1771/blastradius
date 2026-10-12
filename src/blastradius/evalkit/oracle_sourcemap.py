"""Source-map oracle (TEST-ONLY, D23; cases 53, 63, 71): exact DOM element -> `.vue` ground truth.

STATEMENT, repeated in every output: this oracle exists only because hoppscotch.io publishes its source maps
(sourcesContent included). It is not available for a target that does not, it cannot label UI that is not in
the deployed build (code introduced by the open demo PRs, case 71), and it is not a product rung: nothing
under link/, reason/ or graph/ may import this package (tests/test_eval_import_lint.py).

Technique (docs/RECON.md section 6; measured there on the main chunk):
  1. Production Vue 3 sets neither `el.__vueParentComponent` nor `type.__file`, but `#app._vnode` is always
     present, so the vnode tree is walkable. Every element vnode carries `ctx`, the component instance whose
     template created it (slot content included), and every instance carries `vnode.ctx`, the instance whose
     template wrote the `<Component>` tag.
  2. `String(type.setup)` (else `render`) of a component is a verbatim substring of exactly one script chunk.
     The generated span of that substring is mapped through the chunk's source map and the plurality of the
     segments in the span names the component's source file. (A first-segment lookup is wrong: it names the
     module that ends just before the span.)
  3. An element's owner is the first instance on its ctx chain that is a repo `.vue` file. 82% of elements on
     hoppscotch.io needed hops through `@hoppscotch/ui` wrappers first, which is why the chain is walked.
  4. Labels are joined to the crawl's elements by (url_pattern, role, name, ordinal) and compared with the
     linker's `Link` rows (oracle_score).

Politeness (D11, D28): at most MAX_VISITS navigations, one at a time, `action_delay_ms` apart, the project's
user agent, robots.txt checked, the browser behind the refusing egress proxy, service workers blocked, no
input typed and nothing submitted (the only click opens a sidebar tab). Each chunk and map is pinned by ETag
and sha256 at harvest time; the entry chunk's ETag is re-checked at the end to catch a deploy mid-harvest.

Not used here and never needed: a local dev server (monorepo install chain), the data-v scope id (circular).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import posixpath
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from ..access.classify import robots_allows
from ..config import CACHE_DIR, Settings
from ..crawl import perceive
from ..crawl.fingerprint import url_pattern
from ..crawl.netguard import Blackhole, Watchdog, bypass_list
from ..crawl.safety import egress_verdict
from ..ids import sha, sha_full
from ..models import AccessVerdict, CodeIndex
from ..targets import TargetConfig
from .oracle_score import Label
from .sourcemap import SourceMap, Text, git_blob_sha
from .stats import Proportion

ORACLE_STATEMENT = (
    "This oracle exists only because this target publishes its source maps. It labels only UI present in the "
    "deployed build (not code added by an open PR), only files that also exist on main are scored, and it is "
    "test-only: it is not a product rung and nothing in link/, reason/ or graph/ may import it."
)
MAX_VISITS = 6
DEFAULT_PATHS: tuple[str, ...] = ("/", "/graphql", "/settings")
ENVIRONMENTS_TAB = "Environments"
MIN_TEXT = 40                      # shorter function texts are too generic to be a unique anchor
MIN_MAJORITY = 0.5                 # a span whose top source holds under half its segments is not decided
TEXT_KINDS = ("setup", "render", "fn")
MAX_ASSET_BYTES = 128 * 1024 * 1024
_SOURCE_MAP_URL = re.compile(r"//# sourceMappingURL=(\S+)\s*$")
_NODE_MODULE = re.compile(r"node_modules/(?:\.pnpm/[^/]+/node_modules/)?((?:@[^/]+/)?[^/]+)/")
_STRIP_QUERY = re.compile(r"[?#].*$")

# Runs in the page. Input: the CSS selectors perceive.collect produced. Output per selector: the component
# types on the element's template-owner chain (innermost first). Reads only the vnode tree and function
# source.
OWNER_JS = r"""
(selectors) => {
  const root = document.querySelector('#app');
  const top = root && root._vnode;
  const out = {has_root: !!top, types: {}, elements: [], vnodes: 0};
  if (!top) {
    out.elements = selectors.map(() => ({found: false, vnode: false, up: 0, chain: []}));
    return out;
  }
  const byEl = new Map();
  const seen = new Set();
  const visit = (v) => {
    if (!v || typeof v !== 'object' || seen.has(v)) return;
    seen.add(v);
    out.vnodes += 1;
    if (v.el && v.el.nodeType === 1 && typeof v.type === 'string' && !byEl.has(v.el)) byEl.set(v.el, v);
    if (v.component && v.component.subTree) visit(v.component.subTree);
    if (v.suspense && v.suspense.activeBranch) visit(v.suspense.activeBranch);
    if (Array.isArray(v.children)) for (const c of v.children) visit(c);
  };
  visit(top);
  const ids = new Map();
  const typeId = (t) => {
    if (!ids.has(t)) {
      const id = 't' + ids.size;
      ids.set(t, id);
      out.types[id] = {
        name: (t && (t.__name || t.name)) || null,
        setup: t && typeof t.setup === 'function' ? String(t.setup) : null,
        render: t && typeof t.render === 'function' ? String(t.render) : null,
        fn: typeof t === 'function' ? String(t) : null,
      };
    }
    return ids.get(t);
  };
  for (const sel of selectors) {
    let el = null;
    try { el = document.querySelector(sel); } catch (e) { el = null; }
    let up = 0;
    let v = null;
    for (let cur = el; cur && !(v = byEl.get(cur)); cur = cur.parentElement) up += 1;
    const chain = [];
    if (v) {
      let inst = v.ctx;
      for (let guard = 0; inst && guard < 40; guard += 1) {
        chain.push(typeId(inst.type));
        const next = inst.vnode && inst.vnode.ctx ? inst.vnode.ctx : inst.parent;
        inst = next === inst ? inst.parent : next;
      }
    }
    out.elements.push({found: !!el, vnode: !!v, up: v ? up : 0, chain});
  }
  return out;
}
"""


# ------------------------------------------------------------------------------------------ data
@dataclass(frozen=True)
class ComponentType:
    """A component definition seen in a page: its display name and the function texts that anchor it."""

    name: str | None
    texts: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SeenElement:
    role: str
    name: str
    ordinal: int
    obs_id: str
    has_vnode: bool
    vnode_up: int                    # DOM ancestors climbed to reach an element vnode (editors have none)
    chain: tuple[str, ...]           # page-local component type ids, template owner first


@dataclass
class State:
    key: str
    url: str
    url_pattern: str
    active_tabs: tuple[str, ...]
    dialog_title: str | None
    elements: list[SeenElement]
    types: dict[str, ComponentType]


@dataclass
class LoadedChunk:
    url: str
    text: Text
    map: SourceMap | None
    asset: dict[str, Any]
    map_asset: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return self.url.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Attribution:
    """Where one component type's code lives."""

    status: str                      # ok | absent | ambiguous | no_text | no_map | no_votes | no_majority
    chunk: str | None = None
    kind: str | None = None          # setup | render | fn
    source: str | None = None        # as written in the map
    path: str | None = None          # resolved repo path, or node_modules/... for a library
    package: str | None = None       # npm package when `path` is inside node_modules
    repo_vue: bool = False
    vote_share: float | None = None
    segments: int = 0


@dataclass
class Harvest:
    states: list[State] = field(default_factory=list)
    chunks: list[LoadedChunk] = field(default_factory=list)
    visits: int = 0
    egress_blocked: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    entry_changed: bool = False


# --------------------------------------------------------------------------------- path resolution
def resolve_source(base: str, source: str) -> str:
    """A map `sources` entry -> a repo-relative posix path (query and fragment dropped)."""
    return posixpath.normpath(posixpath.join(base, _STRIP_QUERY.sub("", source)))


def infer_base(sources: Collection[str], indexed: Collection[str], candidates: Sequence[str]) -> str:
    """The map's directory (relative to the repo root) that resolves most `sources` onto indexed files.

    Candidates come from the target's code roots (`<package>/dist/assets`); the data decides, so the target
    config does not have to know where its build output lands. Ties keep the earlier candidate."""
    best, best_hits = candidates[0], -1
    for cand in candidates:
        hits = sum(1 for s in sources if resolve_source(cand, s) in indexed)
        if hits > best_hits:
            best, best_hits = cand, hits
    return best


def base_candidates(roots: Sequence[str]) -> list[str]:
    out: list[str] = []
    for root in roots:
        pkg = posixpath.dirname(root.rstrip("/"))
        cand = f"{pkg}/dist/assets" if pkg else "dist/assets"
        if cand not in out:
            out.append(cand)
    return out or ["dist/assets"]


def library_of(path: str) -> str | None:
    """npm package name of a path inside node_modules (pnpm layout included), else None."""
    at = path.rfind("node_modules/")
    if at < 0:
        return None
    m = _NODE_MODULE.match(path[at:])
    return m.group(1) if m else None


def is_repo_vue(path: str) -> bool:
    return path.endswith(".vue") and "node_modules/" not in path and not path.startswith("..")


# ------------------------------------------------------------------------------------ attribution
def attribute(texts: Sequence[tuple[str, str]], chunks: Sequence[LoadedChunk], base: str) -> Attribution:
    """Find a component's source file from its function texts: the first kind with a unique hit decides."""
    saw: str | None = None
    for kind, text in texts:
        if len(text) < MIN_TEXT:
            continue
        hits = [(c, c.text.occurrences(text)) for c in chunks]
        hits = [(c, occ) for c, occ in hits if occ]
        if not hits:
            saw = saw or "absent"
            continue
        if len(hits) > 1 or len(hits[0][1]) > 1:
            saw = "ambiguous"
            continue
        chunk, (offset,) = hits[0]
        if chunk.map is None:
            return Attribution("no_map", chunk=chunk.name, kind=kind)
        start = chunk.text.position(offset)
        end = chunk.text.position(offset + len(text))
        votes = chunk.map.votes(start, end)
        total = sum(votes.values())
        if total == 0:
            return Attribution("no_votes", chunk=chunk.name, kind=kind)
        top, n = min(votes.items(), key=lambda kv: (-kv[1], kv[0]))
        if n / total < MIN_MAJORITY:
            return Attribution("no_majority", chunk=chunk.name, kind=kind, vote_share=n / total,
                               segments=total)
        source = chunk.map.sources[top]
        path = resolve_source(base, source)
        return Attribution("ok", chunk=chunk.name, kind=kind, source=source, path=path,
                           package=library_of(path), repo_vue=is_repo_vue(path), vote_share=n / total,
                           segments=total)
    return Attribution(saw or "no_text")


def attribute_types(states: Sequence[State], chunks: Sequence[LoadedChunk],
                    base: str) -> dict[str, Attribution]:
    """Attribution per distinct component text (shared across pages and states)."""
    cache: dict[str, Attribution] = {}
    for state in states:
        for ctype in state.types.values():
            key = sha_full("\x1f".join(f"{k}\x1e{t}" for k, t in ctype.texts))
            if key not in cache:
                cache[key] = attribute(ctype.texts, chunks, base)
    return cache


def type_key(ctype: ComponentType) -> str:
    return sha_full("\x1f".join(f"{k}\x1e{t}" for k, t in ctype.texts))


# --------------------------------------------------------------------------------------- labelling
def _chain_label(ctype: ComponentType, att: Attribution) -> str:
    where = att.path if att.repo_vue else (att.package or att.path or att.status)
    return f"{ctype.name or '?'} ({where})"


def label_state(state: State, attributions: Mapping[str, Attribution]) -> tuple[list[Label], Counter[str]]:
    """Labels for one state's elements and a count of why the others have none."""
    labels: list[Label] = []
    why: Counter[str] = Counter()
    for el in state.elements:
        if not el.has_vnode:
            why["no_vnode"] += 1          # no element vnode on or above it (a detached or non-Vue subtree)
            continue
        if not el.chain:
            why["no_owner_instance"] += 1   # a vnode exists but no component instance created it
            continue
        chain = [(state.types[t], attributions[type_key(state.types[t])]) for t in el.chain]
        owner = next((i for i, (_, a) in enumerate(chain) if a.repo_vue), None)
        if owner is None:
            unresolved = any(a.status != "ok" for _, a in chain)
            why["unattributed_chain" if unresolved else "library_only"] += 1
            continue
        if any(a.status != "ok" for _, a in chain[:owner]):
            why["unattributed_before_owner"] += 1   # the real owner may be the unresolved type: do not guess
            continue
        att = chain[owner][1]
        assert att.path is not None
        labels.append(Label(
            url_pattern=state.url_pattern, active_tabs=state.active_tabs, dialog_title=state.dialog_title,
            role=el.role, name=el.name, ordinal=el.ordinal, oracle_file=att.path, owner_hops=owner,
            owner_chain=tuple(_chain_label(t, a) for t, a in chain[: owner + 1])))
    return labels, why


def attribution_checks(states: Sequence[State], attributions: Mapping[str, Attribution]) -> dict[str, Any]:
    """Self-checks on the oracle's own attribution (reported next to the metrics, not hidden)."""
    seen: dict[str, tuple[ComponentType, Attribution]] = {}
    for state in states:
        for ctype in state.types.values():
            seen.setdefault(type_key(ctype), (ctype, attributions[type_key(ctype)]))
    by_status = Counter(a.status for _, a in seen.values())
    repo = [(t, a) for t, a in seen.values() if a.repo_vue]
    named = [(t, a) for t, a in repo if t.name]
    agree = sum(1 for t, a in named if a.path and posixpath.basename(a.path) == f"{t.name}.vue")
    shares = sorted(a.vote_share for _, a in seen.values() if a.vote_share is not None)
    return {
        "component_types": len(seen),
        "attribution_status": dict(sorted(by_status.items())),
        "repo_vue_components": len(repo),
        "basename_equals_type_name": Proportion.of(agree, len(named)).as_dict(),
        "vote_share_min": None if not shares else round(shares[0], 4),
        "vote_share_unanimous": Proportion.of(sum(1 for s in shares if s == 1.0), len(shares)).as_dict(),
    }


def pairing_checks(chunks: Sequence[LoadedChunk]) -> dict[str, Any]:
    """Cheap evidence that each map belongs to the script it was fetched next to (exceptions are listed)."""
    mapped = [c for c in chunks if c.map is not None]
    bad = sorted(c.name for c in mapped if c.map is not None and (
        (c.map.file is not None and posixpath.basename(c.map.file) != c.name)
        or len(c.map.lines) > c.text.lines))
    return {"chunks": len(chunks), "with_map": len(mapped),
            "map_file_matches_and_lines_fit": len(mapped) - len(bad), "mismatched": bad}


def drift(chunks: Sequence[LoadedChunk], base: str, index: CodeIndex | None) -> dict[str, Any]:
    """Deployed vs indexed: each repo `.vue` in the loaded maps against main's blob sha (case 29)."""
    if index is None:
        return {"status": "no code index"}
    indexed = {f.path: f.sha for f in index.files if f.sha}
    verdict: dict[str, str] = {}
    per_chunk: dict[str, Counter[str]] = {}
    for chunk in chunks:
        if chunk.map is None:
            continue
        for i, source in enumerate(chunk.map.sources):
            path = resolve_source(base, source)
            content = chunk.map.contents[i]
            if not is_repo_vue(path) or content is None:
                continue
            state = ("absent_from_main" if path not in indexed
                     else "same" if indexed[path] == git_blob_sha(content) else "differs")
            per_chunk.setdefault(chunk.name, Counter())[state] += 1
            if verdict.get(path) != "differs":
                verdict[path] = state
    counts = Counter(verdict.values())
    entry = max(per_chunk.items(), key=lambda kv: (sum(kv[1].values()), kv[0]), default=None)
    entry_text = ""
    if entry is not None:
        n_entry, n_off = sum(entry[1].values()), entry[1]["differs"] + entry[1]["absent_from_main"]
        entry_text = f"; the largest chunk {entry[0]}: {n_off} of {n_entry}"
    return {
        "vue_sources": len(verdict), "same": counts["same"], "differs": counts["differs"],
        "absent_from_main": counts["absent_from_main"],
        "summary": f"{counts['differs'] + counts['absent_from_main']} of {len(verdict)} .vue sources in the "
                   f"loaded maps differ from or are absent on the indexed main{entry_text}",
        "by_chunk": {n: {"vue_sources": sum(c.values()), "differs": c["differs"],
                         "absent_from_main": c["absent_from_main"]} for n, c in sorted(per_chunk.items())},
        "differing": sorted(p for p, s in verdict.items() if s == "differs"),
        "absent": sorted(p for p, s in verdict.items() if s == "absent_from_main"),
    }


# ----------------------------------------------------------------------------------------- fetching
@dataclass(frozen=True)
class Asset:
    status: int | None
    etag: str | None
    last_modified: str | None
    body: bytes | None
    cached: bool = False


Fetcher = Callable[[str], Asset]


class CachedFetcher:
    """GET with the project's user agent, the egress rule, no redirects, and an on-disk cache keyed by
    (url, ETag): a re-run on an unchanged deploy costs one HEAD per asset."""

    def __init__(self, user_agent: str, allow: Sequence[str], cache_dir: Path, *, timeout: float = 90.0,
                 transport: httpx.BaseTransport | None = None):
        self.allow = list(allow)
        self.cache_dir = cache_dir
        self.client = httpx.Client(headers={"User-Agent": user_agent}, timeout=timeout,
                                   follow_redirects=False, transport=transport)

    def close(self) -> None:
        self.client.close()

    def __call__(self, url: str) -> Asset:
        if not egress_verdict(url, self.allow).allowed:
            return Asset(None, None, None, None)
        try:
            head = self.client.head(url)
            etag = head.headers.get("etag")
            path = self.cache_dir / sha(url, etag or "")[:2] / f"{sha(url, etag or '', n=24)}.bin"
            if head.status_code == 200 and etag and path.exists():
                return Asset(200, etag, head.headers.get("last-modified"), path.read_bytes(), cached=True)
            resp = self.client.get(url)
        except httpx.HTTPError:
            return Asset(None, None, None, None)
        if resp.status_code != 200 or len(resp.content) > MAX_ASSET_BYTES:
            return Asset(resp.status_code, None, None, None)
        etag = resp.headers.get("etag")
        if etag:
            path = self.cache_dir / sha(url, etag)[:2] / f"{sha(url, etag, n=24)}.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(resp.content)
        return Asset(200, etag, resp.headers.get("last-modified"), resp.content)


def _pin(url: str, asset: Asset) -> dict[str, Any]:
    body = asset.body or b""
    return {"url": url, "etag": asset.etag, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
            "last_modified": asset.last_modified}


def load_chunk(url: str, fetch: Fetcher, notes: list[str]) -> LoadedChunk | None:
    """Fetch one script chunk and, when it names one, its source map. Pinned by ETag and sha256."""
    asset = fetch(url)
    if asset.body is None:
        notes.append(f"{url.rsplit('/', 1)[-1]}: not fetched (status {asset.status})")
        return None
    text = asset.body.decode("utf-8", errors="replace")
    chunk = LoadedChunk(url, Text(text), None, _pin(url, asset))
    m = _SOURCE_MAP_URL.search(text[-400:])
    if not m:
        return chunk
    map_url = urljoin(url, m.group(1))
    map_asset = fetch(map_url)
    if map_asset.body is None:
        notes.append(f"{chunk.name}: source map not available (status {map_asset.status})")
        return chunk
    try:
        chunk.map = SourceMap(json.loads(map_asset.body))
        chunk.map_asset = _pin(map_url, map_asset)
    except (ValueError, KeyError, TypeError) as exc:
        notes.append(f"{chunk.name}: source map unusable ({type(exc).__name__}: {exc})")
    return chunk


# -------------------------------------------------------------------------------------- the browser
def _types_of(raw: Mapping[str, Any]) -> dict[str, ComponentType]:
    out: dict[str, ComponentType] = {}
    for tid, t in raw.items():
        texts = tuple((k, t[k]) for k in TEXT_KINDS if isinstance(t.get(k), str) and t[k])
        out[tid] = ComponentType(t.get("name"), texts)
    return out


def observe_state(page: Any, cfg: TargetConfig, *, key: str | None = None) -> State:
    """Perceive the page exactly as the crawler does, then ask the page who owns each element."""
    raw = perceive.collect(page, cfg.crawl.widget_selectors)
    pattern = url_pattern(raw.get("url", ""))
    perc = perceive.build(raw, target=cfg.name, pattern=pattern)
    els = perc.reachable()
    owners = page.evaluate(OWNER_JS, [e.obs.selector for e in els])
    if len(owners["elements"]) != len(els):
        raise ValueError(f"owner walk answered {len(owners['elements'])} rows for {len(els)} elements")
    seen = [SeenElement(e.obs.role, e.obs.name, e.obs.ordinal, e.obs.id, bool(o["vnode"]), int(o["up"]),
                        tuple(o["chain"])) for e, o in zip(els, owners["elements"])]
    tabs = tuple(sorted(perc.active_tabs))
    label = f"{pattern} [{', '.join(tabs)}]"
    return State(key or label, raw.get("url", ""), pattern, tabs, perc.dialog_title, seen,
                 _types_of(owners.get("types", {})))


def _hydrate(page: Any, cfg: TargetConfig, sleep: Callable[[float], None], cap_s: float = 9.0) -> None:
    need, waited = cfg.crawl.anchor.min_interactive, 0.0
    while waited < cap_s:
        with contextlib.suppress(Exception):   # a navigation destroyed the context: retry after a pause
            if perceive.collect(page, cfg.crawl.widget_selectors)["interactive"] >= need:
                break
        sleep(0.4)
        waited += 0.4
    sleep(1.0)


def _environments_selector(state: State, page: Any, cfg: TargetConfig) -> str | None:
    raw = perceive.collect(page, cfg.crawl.widget_selectors)
    perc = perceive.build(raw, target=cfg.name, pattern=state.url_pattern)
    for el in perc.reachable():
        if el.obs.name == ENVIRONMENTS_TAB and el.obs.role in ("tab", "button") and not el.disabled:
            return el.obs.selector
    return None


def harvest(cfg: TargetConfig, settings: Settings, *, fetch: Fetcher | None = None,
            cache_dir: Path | None = None, paths: Sequence[str] = DEFAULT_PATHS,
            open_environments: bool = True, max_visits: int = MAX_VISITS, deadline_s: float = 240.0,
            sleep: Callable[[float], None] = time.sleep) -> Harvest:
    """Visit `paths` (and the Environments sidebar tab) once each, then fetch and pin the loaded chunks."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    if len(paths) > max_visits:
        raise ValueError(f"{len(paths)} page visits requested, the cap is {max_visits}")
    out = Harvest()
    ua = settings.user_agent()
    allow = cfg.crawl.egress_allow
    own_fetcher = fetch is None
    fetcher = fetch or CachedFetcher(ua, allow, cache_dir or CACHE_DIR / "oracle")
    robots = fetcher(urljoin(cfg.base_url, "/robots.txt"))
    robots_text = robots.body.decode("utf-8", "replace") if robots.status == 200 and robots.body else (
        "" if robots.status is not None and 400 <= robots.status < 500 else None)
    scripts: set[str] = set()
    token, blackhole = uuid.uuid4().hex, Blackhole()
    dog = Watchdog(token, deadline_s)
    dog.start()
    blocked: Counter[str] = Counter()

    def on_route(route: Any) -> None:
        verdict = egress_verdict(route.request.url, allow)
        if verdict.allowed:
            route.continue_()
        else:
            blocked[verdict.host] += 1
            route.abort("blockedbyclient")

    def on_response(resp: Any) -> None:
        is_script = urlsplit(resp.url).path.endswith(".js")
        if resp.status == 200 and is_script and egress_verdict(resp.url, allow).allowed:
            scripts.add(resp.url.split("?", 1)[0])

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=[f"--br-watchdog={token}"],
                                         proxy={"server": blackhole.server, "bypass": bypass_list(allow)})
            width, height = cfg.crawl.viewport
            context = browser.new_context(viewport={"width": width, "height": height},
                                          locale=cfg.crawl.locale, timezone_id=cfg.crawl.timezone,
                                          color_scheme="light", accept_downloads=False,
                                          service_workers="block", user_agent=ua, permissions=[])
            context.route("**/*", on_route)
            context.route_web_socket("**/*", lambda ws: None)
            page = context.new_page()
            page.on("response", on_response)
            page.on("dialog", lambda d: d.dismiss())
            try:
                for i, path in enumerate(paths):
                    url = urljoin(cfg.base_url, path)
                    if robots_allows(robots_text, ua, url) is not AccessVerdict.OK:
                        out.notes.append(f"{path}: skipped, robots.txt disallows or is unreadable")
                        continue
                    if i:
                        sleep(cfg.crawl.action_delay_ms / 1000)
                    page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                    out.visits += 1
                    _hydrate(page, cfg, sleep)
                    state = observe_state(page, cfg)
                    out.states.append(state)
                    if open_environments and path == paths[0]:
                        sel = _environments_selector(state, page, cfg)
                        if sel is None:
                            out.notes.append("Environments tab not found on the first page")
                        else:
                            sleep(cfg.crawl.action_delay_ms / 1000)
                            page.locator(sel).click(timeout=3000)
                            sleep(1.0)
                            out.states.append(observe_state(page, cfg))
            except PlaywrightError as exc:
                out.notes.append(f"browser error: {str(exc).splitlines()[0][:160]}")
            finally:
                browser.close()
    finally:
        dog.stop()
        blackhole.close()
    out.egress_blocked = dict(sorted(blocked.items()))
    try:
        for url in sorted(scripts):
            chunk = load_chunk(url, fetcher, out.notes)
            if chunk is not None:
                out.chunks.append(chunk)
        out.entry_changed = _entry_changed(out.chunks, fetcher)
    finally:
        if own_fetcher and isinstance(fetcher, CachedFetcher):
            fetcher.close()
    return out


def _entry_changed(chunks: Sequence[LoadedChunk], fetch: Fetcher) -> bool:
    """True when the largest chunk's ETag differs from the one pinned at fetch time (a deploy mid-harvest)."""
    if not chunks:
        return False
    entry = max(chunks, key=lambda c: c.asset["bytes"])
    now = fetch(entry.url)
    return now.etag is not None and entry.asset["etag"] is not None and now.etag != entry.asset["etag"]


def _squash(text: str) -> str:
    return " ".join(text.split()).casefold()


def owner_text_check(labels: Sequence[Label], chunks: Sequence[LoadedChunk], base: str,
                     index: CodeIndex | None) -> dict[str, Any]:
    """Plausibility of the labels themselves: does the element's visible name occur in the file the oracle
    names, as a literal in its source or as the English value of a key that file uses?

    This is a sanity check of the oracle, not a score and not independent of the linker's own text rungs
    (they look for the same thing); a miss is expected when the text arrives as a prop or from data, so only
    the hit rate is informative. Nameless elements cannot be checked and are counted separately."""
    source: dict[str, str] = {}
    for chunk in chunks:
        if chunk.map is None:
            continue
        for i, src in enumerate(chunk.map.sources):
            if chunk.map.contents[i] is not None:
                source.setdefault(resolve_source(base, src), _squash(chunk.map.contents[i] or ""))
    values: dict[str, set[str]] = {}
    if index is not None:
        for use in index.key_uses:
            if not use.dynamic and use.key in index.i18n:
                values.setdefault(use.file, set()).add(_squash(index.i18n[use.key]))
    named = [lbl for lbl in labels if lbl.name.strip()]
    hits = [lbl for lbl in named
            if _squash(lbl.name) in source.get(lbl.oracle_file, "")
            or _squash(lbl.name) in values.get(lbl.oracle_file, ())]
    by_hops: dict[int, list[int]] = {}
    for lbl in named:
        got = by_hops.setdefault(lbl.owner_hops, [0, 0])
        got[1] += 1
        got[0] += lbl in hits
    return {
        "checked": len(named), "nameless_unchecked": len(labels) - len(named),
        "name_found_in_owner_file": Proportion.of(len(hits), len(named)).as_dict(),
        "by_owner_hops": {str(h): Proportion.of(k, n).as_dict() for h, (k, n) in sorted(by_hops.items())},
        "not_found_sample": sorted({f"{lbl.role} {lbl.name!r} -> {lbl.oracle_file}" for lbl in named
                                    if lbl not in hits})[:10],
        "note": "plausibility only: a miss is expected when text arrives as a prop; correlated with the "
                "linker's text rungs, so never used for scoring",
    }


# ----------------------------------------------------------------------------------------- assembly
def build_labels(h: Harvest, index: CodeIndex | None, cfg: TargetConfig) -> dict[str, Any]:
    """Attribute every component, label every element, and collect the oracle's own self-checks."""
    indexed = {f.path for f in index.files if f.sha} if index else set()
    sources = {s for c in h.chunks if c.map for s in c.map.sources}
    base = infer_base(sources, indexed, base_candidates(cfg.code.roots))
    attributions = attribute_types(h.states, h.chunks, base)
    labels: list[Label] = []
    why: Counter[str] = Counter()
    for state in h.states:
        got, missing = label_state(state, attributions)
        labels.extend(got)
        why.update(missing)
    seen = sum(len(s.elements) for s in h.states)
    return {
        "labels": labels,
        "map_base": base,
        "observed_elements": seen,
        "labelled_elements": len(labels),
        "oracle_coverage": Proportion.of(len(labels), seen).as_dict(),
        "unlabelled_reasons": dict(sorted(why.items())),
        "owner_via_ancestor_vnode": sum(1 for st in h.states for e in st.elements
                                        if e.has_vnode and e.vnode_up),
        "attribution": attribution_checks(h.states, attributions),
        "owner_text_check": owner_text_check(labels, h.chunks, base, index),
        "pairing": pairing_checks(h.chunks),
        "drift": drift(h.chunks, base, index),
    }


def harvest_summary(h: Harvest) -> dict[str, Any]:
    """The reproducibility record: what was visited and the hash-pinned assets (no timestamps)."""
    return {
        "page_visits": h.visits,
        "states": [s.key for s in h.states],
        "egress_blocked_hosts": h.egress_blocked,
        "entry_chunk_changed_during_harvest": h.entry_changed,
        "pinned": sorted(
            ({"url": c.asset["url"], "etag": c.asset["etag"], "sha256": c.asset["sha256"],
              "bytes": c.asset["bytes"], **({"map_url": c.map_asset["url"], "map_etag": c.map_asset["etag"],
                                             "map_sha256": c.map_asset["sha256"],
                                             "map_bytes": c.map_asset["bytes"]} if c.map_asset else {})}
             for c in h.chunks), key=lambda r: r["url"]),
        "chunks_without_map": sorted(c.name for c in h.chunks if c.map is None),
        "notes": sorted(h.notes),
    }
