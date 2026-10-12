"""The oracle's in-page walk, run in real Chromium on a synthetic page faking Vue 3's production vnode tree.

No network: `set_content` only. The shapes faked are the ones the technique relies on (RECON.md section 6):
`#app._vnode`, element vnodes with `el` and `ctx`, component instances with `type`, `vnode.ctx` and `parent`,
and a DOM subtree (an editor) that no vnode owns. Skipped when Chromium cannot launch."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from test_eval_support import build_chunk

from blastradius.config import Settings
from blastradius.evalkit import oracle_sourcemap as o
from blastradius.targets import TargetConfig

PAGE = """
<!doctype html><html><head><title>Synthetic</title></head><body>
<div id="app">
  <header id="hdr"><button id="save">Save</button>
    <button id="plain" aria-label="Open menu">x</button></header>
  <main id="mn"><button id="open">Open</button>
    <div class="cm-editor"><div class="cm-content" role="textbox" aria-label="Request body">{}</div></div>
  </main>
</div>
<script>
  const page = {__name: 'Page', setup() { return 'page-component-setup-text-that-is-long-enough-to-anchor'
  ; }};
  const wrap = {__name: 'Wrapper', setup() { return 'wrapper-component-setup-text-that-is-long-enough-xx'; }};
  const root = {__name: 'App', setup() { return 'app-component-setup-text-that-is-long-enough-to-anchor-1'
  ; }};
  const iApp = {type: root, vnode: {ctx: null}, parent: null};
  const iPage = {type: page, vnode: {ctx: iApp}, parent: iApp};
  const iWrap = {type: wrap, vnode: {ctx: iPage}, parent: iPage};
  const $ = (id) => document.getElementById(id);
  const ev = (el, ctx, children) => ({type: el.tagName.toLowerCase(), el, ctx, children: children || null});
  $('app')._vnode = {type: 'div', el: $('app'), ctx: iApp, children: [
    ev($('hdr'), iPage, [ev($('save'), iWrap), ev($('plain'), iWrap)]),
    ev($('mn'), iPage, [ev($('open'), iPage)]),
  ]};
</script></body></html>
"""


@pytest.fixture
def page() -> Iterator[Any]:
    """A fresh Chromium page per test (the sync API cannot be entered twice in one thread)."""
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Chromium not available: {exc}")
        pg = browser.new_page()
        pg.set_content(PAGE)
        yield pg
        browser.close()


@pytest.fixture(scope="module")
def cfg() -> TargetConfig:
    return TargetConfig.load("hoppscotch")


def test_owner_chain_walks_template_owners_innermost_first(page: Any, cfg: TargetConfig):
    state = o.observe_state(page, cfg)
    by_name = {e.name: e for e in state.elements}
    save = by_name["Save"]
    names = [state.types[t].name for t in save.chain]
    assert names == ["Wrapper", "Page", "App"]  # ctx, then vnode.ctx of each instance
    assert [state.types[t].name for t in by_name["Open"].chain] == ["Page", "App"]
    assert save.has_vnode and save.vnode_up == 0


def test_function_texts_travel_with_the_types(page: Any, cfg: TargetConfig):
    state = o.observe_state(page, cfg)
    texts = {t.name: dict(t.texts) for t in state.types.values()}
    assert "wrapper-component-setup-text" in texts["Wrapper"]["setup"]
    assert set(texts) == {"Wrapper", "Page", "App"}


def test_dom_no_vnode_owns_is_attributed_to_its_nearest_vnode_ancestor(page: Any, cfg: TargetConfig):
    state = o.observe_state(page, cfg)
    editor = next(e for e in state.elements if e.role == "textbox")
    assert editor.has_vnode and editor.vnode_up >= 1
    assert [state.types[t].name for t in editor.chain] == ["Page", "App"]


def test_elements_have_the_crawlers_identity(page: Any, cfg: TargetConfig):
    """Same perceive.build as the explorer: role, name and ordinal are what the join keys on."""
    state = o.observe_state(page, cfg)
    assert state.url_pattern in ("/", "/blank") or state.url_pattern.startswith("/")
    triples = {(e.role, e.name, e.ordinal) for e in state.elements}
    assert ("button", "Save", 0) in triples and ("button", "Open menu", 0) in triples


def test_a_page_without_a_vue_root_yields_no_owners_not_an_error(page: Any, cfg: TargetConfig):
    page.set_content("<div id='app'><button>Go</button><button>Stop</button></div>")
    state = o.observe_state(page, cfg)
    assert state.elements and all(not e.has_vnode and e.chain == () for e in state.elements)
    labels, why = o.label_state(state, {})
    assert labels == [] and why["no_vnode"] == len(state.elements)


# ------------------------------------------------------------------------------- harvest controls
def test_more_visits_than_the_cap_is_refused_before_anything_launches(cfg: TargetConfig):
    with pytest.raises(ValueError, match="cap is 6"):
        o.harvest(cfg, Settings(), paths=[f"/p{i}" for i in range(7)])


def test_robots_disallow_means_no_navigation_and_a_note(cfg: TargetConfig, tmp_path):
    def fetch(url: str) -> o.Asset:
        if url.endswith("/robots.txt"):
            return o.Asset(200, None, None, b"User-agent: *\nDisallow: /\n")
        return o.Asset(None, None, None, None)

    try:
        h = o.harvest(cfg, Settings(), fetch=fetch, paths=("/", "/graphql"), open_environments=False)
    except PlaywrightError as exc:
        pytest.skip(f"Chromium not available: {exc}")
    assert h.visits == 0 and h.states == [] and h.chunks == []
    assert sum("robots.txt" in n for n in h.notes) == 2


# ----------------------------------------------------------------------------------- fetch and pin
def _asset(body: bytes, etag: str = 'W/"e1"') -> o.Asset:
    return o.Asset(200, etag, "Mon, 12 Oct 2026 02:38:07 GMT", body)


def test_load_chunk_pins_etag_and_sha256_and_parses_the_named_map():
    text, data = build_chunk([("../../src/A.vue", "a" * 40)])
    js = (text + "\n//# sourceMappingURL=chunk.js.map\n").encode()
    files = {
        "https://x.test/assets/chunk.js": _asset(js, 'W/"js"'),
        "https://x.test/assets/chunk.js.map": _asset(json.dumps(data).encode(), 'W/"map"'),
    }
    notes: list[str] = []
    chunk = o.load_chunk("https://x.test/assets/chunk.js", lambda u: files[u], notes)
    assert chunk is not None and chunk.map is not None and not notes
    assert chunk.asset["etag"] == 'W/"js"' and chunk.asset["bytes"] == len(js)
    assert len(chunk.asset["sha256"]) == 64 and chunk.map_asset["etag"] == 'W/"map"'


def test_missing_and_unusable_maps_are_noted_not_fatal():
    js = b"var a=1;\n//# sourceMappingURL=c.js.map\n"
    notes: list[str] = []
    chunk = o.load_chunk(
        "https://x.test/a/c.js",
        lambda u: _asset(js) if u.endswith("c.js") else o.Asset(404, None, None, None),
        notes,
    )
    assert chunk is not None and chunk.map is None and any("not available" in n for n in notes)
    notes.clear()
    chunk = o.load_chunk(
        "https://x.test/a/c.js",
        lambda u: _asset(js) if u.endswith("c.js") else _asset(b'{"version": 2}'),
        notes,
    )
    assert chunk.map is None and any("unusable" in n for n in notes)
    assert o.load_chunk("https://x.test/a/c.js", lambda u: o.Asset(500, None, None, None), []) is None


def test_cached_fetcher_refuses_hosts_outside_the_allowlist_and_caches_by_etag(tmp_path):
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        return httpx.Response(200, content=b"body-bytes", headers={"etag": 'W/"v1"'})

    f = o.CachedFetcher("ua", ["hoppscotch.io"], tmp_path, transport=httpx.MockTransport(handler))
    assert f("https://evil.example/x.js").body is None and calls == []
    first = f("https://hoppscotch.io/assets/a.js")
    assert first.body == b"body-bytes" and not first.cached
    second = f("https://hoppscotch.io/assets/a.js")
    assert second.body == b"body-bytes" and second.cached
    assert [m for m, _ in calls].count("GET") == 1  # the second call was HEAD + disk
    f.close()
