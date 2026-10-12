"""The deterministic rungs of the UI -> code cascade: text maps, reachability, and text resolution.

Steps 1 to 3 of `ui_to_code` (route + render closure, i18n value -> key -> file, source literals, ambiguity
confidence). Pure functions of a CodeIndex and an element's texts: no run, no LLM, no I/O, so every rule here is
unit-testable on a hand-built index (D07, D09; cases 32, 34). The module docstring of `ui_to_code` is the
specification; the constants below are its numbers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ..code.index import render_closure
from ..graph.canonical import match_route
from ..models import METHOD_PRIOR, CodeIndex, RouteDef, ScreenState, UIElementObs
from .text import normalise_text

CONFIDENCE_FLOOR = 0.30
MIN_LITERAL_CHARS = 3
_ALPHA = re.compile(r"[^\W\d_]")
_KIND_RANK = {"page": 0, "tab": 1, "modal": 2, "embed": 3}


def screen_order(s: ScreenState) -> tuple[int, int, str, str]:
    """The order every link stage visits screens in: pages, then tabs, then dialogs; shallow first; ties by
    url pattern and id. The first screen an element is seen on is therefore its most representative one."""
    return (_KIND_RANK.get(s.kind, 9), s.depth, s.url_pattern, s.id)


# ----------------------------------------------------------------------------------------- text maps
@dataclass(frozen=True)
class TextMaps:
    value_keys: dict[str, tuple[str, ...]]      # normalised English value -> sorted keys
    key_files: dict[str, tuple[str, ...]]       # key -> sorted files (static uses only)
    key_values: dict[str, str]                  # key -> English value
    file_keys: dict[str, tuple[str, ...]]       # file -> sorted keys it uses
    literal_files: dict[str, tuple[str, ...]]   # normalised literal -> sorted files


def _is_noise(text: str, brand_words: frozenset[str]) -> bool:
    return len(text) < MIN_LITERAL_CHARS or not _ALPHA.search(text) or text in brand_words


def _frozen(d: dict[str, set[str]]) -> dict[str, tuple[str, ...]]:
    return {k: tuple(sorted(v)) for k, v in sorted(d.items())}


def build_text_maps(index: CodeIndex, brand_words: Iterable[str] = ()) -> TextMaps:
    brands = frozenset(normalise_text(b) for b in brand_words if b)
    value_keys: dict[str, set[str]] = {}
    for key, value in index.i18n.items():
        for alt in value.split("|"):               # vue-i18n plural forms are alternatives of one key
            norm = normalise_text(alt)
            if norm:
                value_keys.setdefault(norm, set()).add(key)
    key_files: dict[str, set[str]] = {}
    file_keys: dict[str, set[str]] = {}
    for use in index.key_uses:
        if not use.dynamic:
            key_files.setdefault(use.key, set()).add(use.file)
            file_keys.setdefault(use.file, set()).add(use.key)
    literals: dict[str, set[str]] = {}
    for lit in index.literals:
        norm = normalise_text(lit.text)
        if not _is_noise(norm, brands):
            literals.setdefault(norm, set()).add(lit.file)
    return TextMaps(_frozen(value_keys), _frozen(key_files), dict(index.i18n), _frozen(file_keys),
                    _frozen(literals))


# --------------------------------------------------------------------------------------- reachability
@dataclass(frozen=True)
class Reach:
    pattern: str | None             # RouteDef pattern the Screen's url_pattern landed on
    how: str                        # exact | param | catch_all | none
    seeds: tuple[str, ...]
    hops: dict[str, int]            # R(screen): file -> render hops from the nearest seed
    owner: dict[str, str]           # file -> first seed whose closure holds it (the "page" in an explanation)


def _parent_route_files(file: str, route_files: frozenset[str]) -> list[str]:
    name = file.rsplit("/", 1)[-1]
    suffix = name[name.rfind("."):] if "." in name else ""
    parent = file.rsplit("/", 1)[0] if "/" in file else ""
    out = []
    while parent:
        candidate = parent + suffix
        if candidate in route_files and candidate != file:
            out.append(candidate)
        parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
    return out


class Reachability:
    """R(screen) per url_pattern, cached. A pure function of the index and the config's global files."""

    def __init__(self, index: CodeIndex, global_files: Sequence[str] = ()):
        self._edges = index.edges
        self._global = tuple(global_files)
        self._routes: dict[str, list[RouteDef]] = {}
        for r in sorted(index.routes, key=lambda r: (r.pattern, r.file)):
            self._routes.setdefault(r.pattern, []).append(r)
        self._route_files = frozenset(r.file for r in index.routes)
        self._cache: dict[str, Reach] = {}

    def of(self, url_pattern: str) -> Reach:
        if url_pattern not in self._cache:
            self._cache[url_pattern] = self._build(url_pattern)
        return self._cache[url_pattern]

    def _build(self, url_pattern: str) -> Reach:
        matched = match_route(url_pattern, self._routes)
        pattern, how = matched if matched else (None, "none")
        pages: list[str] = []
        layouts: list[str] = []
        for route in self._routes.get(pattern, []) if pattern else []:
            for f in (route.file, *_parent_route_files(route.file, self._route_files)):
                if f not in pages:
                    pages.append(f)
            for f in [route.layout] if route.layout else self._global:
                if f not in layouts:
                    layouts.append(f)
        seeds = tuple(dict.fromkeys([*pages, *(layouts or self._global)]))
        hops: dict[str, int] = {}
        owner: dict[str, str] = {}
        for seed in seeds:
            for f, h in render_closure(self._edges, [seed]).items():
                owner.setdefault(f, seed)
                hops[f] = min(h, hops.get(f, h))
        return Reach(pattern, how, seeds, hops, owner)


# ---------------------------------------------------------------------------------------- resolution
@dataclass(frozen=True)
class Hit:
    file: str
    method: str                    # i18n_key | literal_text | llm_pick
    confidence: float
    text: str                      # normalised text that matched
    keys: tuple[str, ...] = ()     # i18n keys joining the text to the file
    of: int = 1                    # files that share this text under the page (the ambiguity k)


@dataclass
class Resolution:
    hits: list[Hit] = field(default_factory=list)
    matched_text: bool = False                       # a text equals some i18n value or literal, anywhere
    outside: set[str] = field(default_factory=set)   # files using a matching key/literal outside R


def element_texts(el: UIElementObs) -> list[tuple[str, str]]:
    """Distinct (normalised, original) texts of an element, in attribute priority order."""
    out: dict[str, str] = {}
    for raw in (el.name, el.text, el.aria_label, el.placeholder, el.title_attr):
        norm = normalise_text(raw)
        if norm and norm not in out:
            out[norm] = " ".join((raw or "").split())
    return list(out.items())


def ambiguity(prior: float, k: int) -> float:
    """`prior` for one file, prior / k (floored) for k >= 2: shared copy keeps its files, loses confidence."""
    return prior if k <= 1 else max(CONFIDENCE_FLOOR, round(prior / k, 4))


def _text_files(text: str, maps: TextMaps, reach: Reach, literal: bool,
                res: Resolution) -> dict[str, tuple[str, ...]]:
    """file -> keys for one text, restricted to R; matches outside R are remembered in `res.outside`."""
    files: dict[str, set[str]] = {}
    if literal:
        pairs = [(f, "") for f in maps.literal_files.get(text, ())]
    else:
        pairs = [(f, k) for k in maps.value_keys.get(text, ()) for f in maps.key_files.get(k, ())]
    if text in (maps.literal_files if literal else maps.value_keys):
        res.matched_text = True
    for f, key in pairs:
        if f in reach.hops:
            files.setdefault(f, set()).update([key] if key else [])
        else:
            res.outside.add(f)
    return {f: tuple(sorted(k)) for f, k in files.items()}


def _rung(texts: Sequence[str], maps: TextMaps, reach: Reach, literal: bool,
          res: Resolution) -> dict[str, tuple[str, tuple[str, ...]]]:
    """file -> (text, keys) from the most specific text of the rung (fewest files; ties are unioned)."""
    per_text = [(t, _text_files(t, maps, reach, literal, res)) for t in texts]
    per_text = [(t, m) for t, m in per_text if m]
    if not per_text:
        return {}
    k = min(len(m) for _, m in per_text)
    chosen: dict[str, tuple[str, tuple[str, ...]]] = {}
    for text, m in per_text:
        if len(m) == k:
            for f, keys in m.items():
                chosen.setdefault(f, (text, keys))
    return chosen


def resolve_text(texts: Sequence[str], reach: Reach, maps: TextMaps) -> Resolution:
    """Steps 2 and 3 of the cascade for one element on one screen (see module docstring)."""
    res = Resolution()
    method = "i18n_key"
    chosen = _rung(texts, maps, reach, False, res)
    if not chosen:
        method, chosen = "literal_text", _rung(texts, maps, reach, True, res)
    conf = ambiguity(METHOD_PRIOR[method], len(chosen))
    res.hits = [Hit(f, method, conf, text, keys, len(chosen)) for f, (text, keys) in sorted(chosen.items())]
    return res
