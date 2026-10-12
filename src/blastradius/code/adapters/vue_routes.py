"""File-based routes for vite-plugin-pages with `routeStyle: "nuxt"` (D10; case 29).

`pages/index.vue` -> `/`, `pages/realtime/websocket.vue` -> `/realtime/websocket`, `pages/e/_id.vue` ->
`/e/:id`, `pages/_.vue` -> `/:catchAll`. A parent file next to a same-named directory (`profile.vue` with
`profile/`) is a layout route, so `profile.vue` and `profile/index.vue` share `/profile`. The layout comes
from the page's `<route>` custom block (`meta.layout`) or a literal `definePage({ meta: { layout } })`,
falling back to `layouts/default.vue`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath

import yaml

from ...models import RouteDef
from .vue_sfc import Block

_DEFINE_PAGE = re.compile(r"definePage\s*\(\s*\{")
_LAYOUT_KEY = re.compile(r"\blayout\s*:\s*(['\"])([\w-]+)\1")
_DEFINE_PAGE_WINDOW = 2048      # characters after `definePage({` that can hold its `meta.layout`
_DEFINE_PAGE_CALLS = 16         # a page has one; a hostile file with thousands must not cost as many scans
_PARAM = re.compile(r"^_(.+)$|^\[(.+)\]$")


def route_pattern(rel_path: str) -> str:
    """Route pattern for a path relative to a pages directory, e.g. `view/_id/_version.vue`."""
    segments = list(PurePosixPath(rel_path).with_suffix("").parts)
    out: list[str] = []
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        if last and seg == "index":
            continue
        if seg in ("_", "[...all]", "[...catchAll]"):
            out.append(":catchAll")
            continue
        m = _PARAM.match(seg)
        out.append(f":{m.group(1) or m.group(2)}" if m else seg)
    return "/" + "/".join(out)


def layout_name(blocks: Iterable[Block], script_text: str) -> str | None:
    """`meta.layout` from a `<route>` block (yaml or json) or a literal `definePage` call."""
    for block in blocks:
        if block.tag != "route":
            continue
        try:
            data = json.loads(block.content) if block.lang == "json" else yaml.safe_load(block.content)
        except (ValueError, yaml.YAMLError, RecursionError):    # RecursionError: hostile deep nesting
            continue
        meta = data.get("meta") if isinstance(data, dict) else None
        layout = meta.get("layout") if isinstance(meta, dict) else None
        if isinstance(layout, str):
            return layout
    for n, call in enumerate(_DEFINE_PAGE.finditer(script_text)):
        if n >= _DEFINE_PAGE_CALLS:
            break
        hit = _LAYOUT_KEY.search(script_text, call.end(), call.end() + _DEFINE_PAGE_WINDOW)
        if hit:
            return hit.group(2)
    return None


def build_routes(page_files: Mapping[str, str], layouts: Mapping[str, str], blocks: Mapping[str, list[Block]],
                 scripts: Mapping[str, str], default_layout: str | None) -> list[RouteDef]:
    """`page_files` maps repo path -> path relative to its pages dir; `layouts` maps layout name -> repo path."""
    routes: list[RouteDef] = []
    for path in sorted(page_files):
        name = layout_name(blocks.get(path, ()), scripts.get(path, ""))
        layout = layouts.get(name) if name else default_layout
        routes.append(RouteDef(pattern=route_pattern(page_files[path]), file=path, layout=layout))
    return routes
