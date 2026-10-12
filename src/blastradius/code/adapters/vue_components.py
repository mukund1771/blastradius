"""Template tag -> component resolution (D10; cases 33, 34, 35).

Order, first hit wins: an explicit import binding in the SFC's own script; the committed
`components.d.ts` table that `unplugin-vue-components` generated; the plugin's `directoryAsNamespace` rule
applied to the `components/` directory next to that table. Tags that resolve to a package export (`Hopp*`
from `@hoppscotch/ui`, `Tippy`, `RouterView`) are external leaves: counted, never edged.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

_DTS_ENTRY = re.compile(
    r"""^\s*['"]?([A-Za-z_$][\w$-]*)['"]?\s*:\s*typeof\s+import\(\s*(['"])(.+?)\2\s*\)(?:\[\s*(['"])(.+?)\4\s*\])?""",
    re.MULTILINE)

_HTML = [
    "a", "abbr", "address", "area", "article", "aside", "audio", "b", "base", "bdi", "bdo", "blockquote",
    "body", "br", "button", "canvas", "caption", "cite", "code", "col", "colgroup", "data", "datalist",
    "dd", "del", "details", "dfn", "dialog", "div", "dl", "dt", "em", "embed", "fieldset", "figcaption",
    "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hgroup", "hr",
    "html", "i", "iframe", "img", "input", "ins", "kbd", "label", "legend", "li", "link", "main", "map",
    "mark", "menu", "meta", "meter", "nav", "noscript", "object", "ol", "optgroup", "option", "output", "p",
    "param", "picture", "pre", "progress", "q", "rp", "rt", "ruby", "s", "samp", "script", "search",
    "section", "select", "small", "source", "span", "strong", "style", "sub", "summary", "sup", "table",
    "tbody", "td", "template", "textarea", "tfoot", "th", "thead", "time", "title", "tr", "track", "u",
    "ul", "var", "video", "wbr", "slot",
]
_SVG = [
    "svg", "g", "path", "circle", "ellipse", "line", "polygon", "polyline", "rect", "text", "tspan", "defs",
    "use", "symbol", "marker", "mask", "pattern", "clipPath", "linearGradient", "radialGradient", "stop",
    "filter", "image", "foreignObject", "switch", "desc", "animate", "animateMotion", "animateTransform",
    "set", "mpath", "textPath", "view", "feBlend", "feColorMatrix", "feComponentTransfer", "feComposite",
    "feConvolveMatrix", "feDiffuseLighting", "feDisplacementMap", "feDistantLight", "feDropShadow",
    "feFlood", "feFuncA", "feFuncB", "feFuncG", "feFuncR", "feGaussianBlur", "feImage", "feMerge",
    "feMergeNode", "feMorphology", "feOffset", "fePointLight", "feSpecularLighting", "feSpotLight",
    "feTile", "feTurbulence",
]
NATIVE_TAGS: frozenset[str] = frozenset(_HTML + _SVG)

# Vue core built-ins that are never components of the app, and vue-router's global components.
BUILTIN_TAGS = frozenset({"Transition", "TransitionGroup", "KeepAlive", "Teleport", "Suspense", "Component"})
ROUTER_TAGS = frozenset({"RouterView", "RouterLink"})


def pascal_case(name: str) -> str:
    """Vue's tag normalisation: `app-header` and `appHeader` both become `AppHeader`."""
    camel = re.sub(r"-(\w)", lambda m: m.group(1).upper(), name)
    return camel[:1].upper() + camel[1:]


def namespaced_name(rel_path: str) -> str:
    """`directoryAsNamespace` name for a path relative to the components directory.

    `accessTokens/GenerateModal.vue` -> `AccessTokensGenerateModal`; `accessTokens/index.vue` ->
    `AccessTokens`. Same-prefix collapsing is off: the committed table for this target lists both
    `HttpTestResult` (http/TestResult.vue) and `HttpTestTestResult` (http/test/TestResult.vue).
    """
    path = PurePosixPath(rel_path)
    stem = "" if path.stem.lower() == "index" else path.stem
    return pascal_case("-".join(part for part in (*path.parent.parts, stem) if part))


@dataclass(frozen=True)
class DtsEntry:
    module: str                 # as written: "./components/http/Headers.vue" or "@hoppscotch/ui"
    export: str                 # "default" or a named export
    path: str | None            # repo path for a relative module, else None (external package)


def parse_components_dts(text: str, dts_path: str) -> dict[str, DtsEntry]:
    """`Name: typeof import('./components/x/Y.vue')['default']` lines -> {Name: DtsEntry}."""
    base = posixpath.dirname(dts_path)
    out: dict[str, DtsEntry] = {}
    for m in _DTS_ENTRY.finditer(text):
        name, module, export = m.group(1), m.group(3), m.group(5) or "default"
        path = posixpath.normpath(posixpath.join(base, module)) if module.startswith(".") else None
        out[name] = DtsEntry(module, export, path)
    return out


@dataclass(frozen=True)
class TagResolution:
    kind: str                   # file | external | unresolved | builtin
    target: str = ""            # repo path, or package name for external
    how: str = ""               # import | dts | namespace | self | router | lazy_import
    reason: str = ""


class ComponentResolver:
    """Resolves tag names against one `components.d.ts` table and its `components/` directories."""

    def __init__(self, files: Iterable[str], dts: Mapping[str, DtsEntry], components_dirs: Iterable[str]):
        self.files = set(files)
        self.dts = dict(dts)
        self.namespace: dict[str, list[str]] = {}
        for directory in components_dirs:
            prefix = directory.rstrip("/") + "/"
            for path in sorted(self.files):
                if path.startswith(prefix) and path.endswith(".vue"):
                    self.namespace.setdefault(namespaced_name(path[len(prefix):]), []).append(path)

    def resolve(self, tag: str, *, importer: str, bindings: Mapping[str, tuple[str, str, str]]) -> TagResolution:
        """`bindings` maps PascalCase local binding -> (kind, target, how) already resolved by the caller."""
        name = pascal_case(tag)
        if name in BUILTIN_TAGS:
            return TagResolution("builtin")
        hit = bindings.get(name)
        if hit is not None:
            return TagResolution(hit[0], hit[1], hit[2])
        if name in ROUTER_TAGS:
            return TagResolution("external", "vue-router", "router")
        if PurePosixPath(importer).stem == name or pascal_case(PurePosixPath(importer).stem) == name:
            return TagResolution("file", importer, "self")
        entry = self.dts.get(name)
        if entry is not None:
            if entry.path is None:
                return TagResolution("external", entry.module, "dts")
            if entry.path in self.files:
                return TagResolution("file", entry.path, "dts")
            return TagResolution("unresolved", reason=f"components.d.ts points at missing file {entry.path}")
        candidates = self.namespace.get(name, [])
        if len(candidates) == 1:
            return TagResolution("file", candidates[0], "namespace")
        if candidates:
            return TagResolution("unresolved", reason="ambiguous directoryAsNamespace name: " + ", ".join(candidates))
        return TagResolution("unresolved", reason="no import, not in components.d.ts, no directory-namespace match")
