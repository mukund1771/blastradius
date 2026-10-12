"""Named, parameterised Cypher read templates (D19, D28; cases 50, 51, 57).

Rules this module exists to enforce:
  * Every query is a fixed string below. Nothing is formatted, concatenated or interpolated: a changed
    path, a requirement id or a hop count only ever reaches Neo4j as a bound parameter.
  * `GraphStore.read(name, **params)` runs a template inside a read transaction; the server rejects
    writes there. There is no entry point that takes free-form Cypher (no text-to-Cypher, D19).
  * Templates are also discovered: every sibling module named `queries_*.py` that exposes a `TEMPLATES`
    dict is merged by `all_templates()` (names must not collide; a collision raises at import). Downstream
    stages add their read templates in such a module and never edit this file.
  * A hop cap cannot be a Cypher parameter (`*0..$n` is a syntax error), so the closure is written as
    explicit 1/2/3-hop levels guarded by `$hops >= k`. The cap is validated to 0..MAX_HOPS_QUERY and
    defaults to models.MAX_HOPS (2).
  * Rows are deterministic: every template ends in a total ORDER BY, the witness of a route is chosen with
    `min()` over a total sort key (never by relying on traversal or insertion order), and list-valued
    columns are sorted in Python by the template's `post` hook.

Closure semantics shared by `impact`, `hub_degree` and `absence_near`: "importers of f within n hops" is
the set of CodeFiles with a path of at most n IMPORTS|RENDERS edges ending at f. Cycles are harmless:
results are aggregated per file with the minimum hop. `impact` computes it breadth-first with one DISTINCT
frontier per hop and one witness per node (work is O(edges), not fan-in^hops: a path-enumerating closure
exhausted the transaction memory pool at 28,000 edges and 3 hops). Needs Neo4j 5.23+ (`CALL (vars) {...}`).

`impact` row contract (one row per distinct (screen, element); counting screens or elements over the rows
is therefore a count of distinct nodes, case 51; confidence is for noisy-OR, never for counts):
  screen_id, url_pattern, screen_title, screen_kind        the Screen
  element_id, element_role, element_name, owner_file       the canonical UIElement
  hidden_on_screen                                         True when every observation there was invisible
  hops, direction, seed, file, via, kinds, symbols,        the best route: minimum hop, then forward last,
  specifiers                                               then highest confidence, then file, seed, via and
                                                           kinds in sort order; `via` lists files from the
                                                           implementing file to the seed in edge order,
                                                           `kinds`/`symbols`/`specifiers` are the edges
                                                           along it (enough to recompute an edge's
                                                           evidence id)
  impl_confidence, impl_method, impl_evidence_id           IMPLEMENTED_BY edge on that route
  impl_edges                                               [{file, confidence, method, evidence_id,
                                                           screen_ids}] every IMPLEMENTED_BY edge that put
                                                           it on this screen, sorted
  seeds                                                    all changed paths that reach it, sorted
  flows                                                    [{id, name}] Flows with a STEP on it, sorted
  requirements                                             [{id, via, confidence, method, evidence_id,
                                                           screen_ids}] via element | screen | flow; ids
                                                           may repeat across `via`, so count distinct ids
Per-screen attribution: an IMPLEMENTED_BY or REALIZED_BY edge carries `screen_ids`, the screens it was
derived on ([] = every screen of the element). A canonical element that appears on several screens counts on
a screen only through edges whose screen_ids is empty or contains that screen, and the witness above is the
best route valid for that screen.
`hops` caps the reverse (importer) distance only. A `forward` row is one edge from the seed and always has
hops = 1, even when hops=0 was requested; use `direction`, not `hops`, to tell it apart.
A changed path with no CodeFile yields no row; ask `file_exists` (PR_PATH_MISSING_AT_HEAD).
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from ..models import MAX_HOPS

MAX_HOPS_QUERY = 3  # 3 exists for measurement ("3 hops = 47%"); the product cap is MAX_HOPS
UI_FILE_KINDS = ["page", "layout", "component"]

_REQUIRED = object()


class UnknownTemplate(KeyError):
    """A name that is not in TEMPLATES. Raised before any I/O."""


@dataclass(frozen=True)
class Param:
    kind: str  # "str" | "str_list" | "int" | "opt_str"
    default: Any = _REQUIRED
    lo: int = 0
    hi: int = 0


def _str() -> Param:
    return Param("str")


def _paths() -> Param:
    return Param("str_list")


def _hops(default: int = MAX_HOPS, hi: int = MAX_HOPS_QUERY) -> Param:
    return Param("int", default, 0, hi)


def _int(default: int, lo: int, hi: int) -> Param:
    return Param("int", default, lo, hi)


Rows = list[dict[str, Any]]


@dataclass(frozen=True)
class Template:
    name: str
    cypher: str
    params: Mapping[str, Param] = field(default_factory=dict)
    doc: str = ""
    post: Callable[[Rows], Rows] | None = None  # deterministic normalisation of list-valued columns

    def bind(self, given: Mapping[str, Any]) -> dict[str, Any]:
        """Validate `given` against the declared parameters and return the full bound parameter map."""
        unknown = sorted(set(given) - set(self.params))
        if unknown:
            raise ValueError(f"{self.name}: unknown parameter(s) {unknown}; expected {sorted(self.params)}")
        bound: dict[str, Any] = {}
        for pname, spec in self.params.items():
            if pname in given:
                value = given[pname]
            elif spec.default is _REQUIRED:
                raise ValueError(f"{self.name}: missing required parameter {pname!r}")
            else:
                value = spec.default
            bound[pname] = _check(self.name, pname, spec, value)
        return bound


def _check(tpl: str, pname: str, spec: Param, value: Any) -> Any:
    if spec.kind == "str":
        ok = isinstance(value, str) and value != ""
    elif spec.kind == "opt_str":
        ok = value is None or isinstance(value, str)
    elif spec.kind == "str_list":
        ok = isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value)
        value = list(value) if ok else value
    else:
        ok = isinstance(value, int) and not isinstance(value, bool) and spec.lo <= value <= spec.hi
    if not ok:
        want = f"int in {spec.lo}..{spec.hi}" if spec.kind == "int" else spec.kind
        raise ValueError(f"{tpl}: parameter {pname!r} must be {want}, got {value!r}")
    return value


TEMPLATES: dict[str, Template] = {}


def _register(tpl: Template) -> None:
    TEMPLATES[tpl.name] = tpl


def _opt_str() -> Param:
    """A string or None, with no default: the caller must say `None` (= any) out loud."""
    return Param("opt_str")


def sorted_lists(**keys: Callable[[Any], Any]) -> Callable[[Rows], Rows]:
    """A `post` hook that sorts the named list-valued columns of every row with the given key functions."""

    def post(rows: Rows) -> Rows:
        for row in rows:
            for column, key in keys.items():
                if isinstance(row.get(column), list):
                    row[column] = sorted(row[column], key=key)
        return rows

    return post


def by(*fields: str) -> Callable[[Mapping[str, Any]], tuple[str, ...]]:
    """Sort key over map fields; a missing or null field sorts as the empty string."""
    return lambda item: tuple("" if item.get(f) is None else str(item[f]) for f in fields)


# ------------------------------------------------------------------------------------ discovery
def extension_modules() -> list[ModuleType]:
    """Every sibling module named `queries_*.py`, imported in name order."""
    package = importlib.import_module(__package__ or "blastradius.graph")
    names = sorted(m.name for m in pkgutil.iter_modules(package.__path__) if m.name.startswith("queries_"))
    return [importlib.import_module(f"{package.__name__}.{n}") for n in names]


def merge_templates(core: Mapping[str, Template], modules: Iterable[Any]) -> dict[str, Template]:
    """`core` plus the TEMPLATES of each module. A name defined twice is an error, not a silent override."""
    merged = dict(core)
    owner = dict.fromkeys(core, "queries")
    for module in modules:
        mod_name = getattr(module, "__name__", repr(module))
        for name, tpl in sorted(getattr(module, "TEMPLATES", {}).items()):
            if name in merged:
                raise ValueError(f"read template {name!r} of {mod_name} is already defined by {owner[name]}")
            merged[name] = tpl
            owner[name] = mod_name
    return merged


def all_templates() -> dict[str, Template]:
    """The core templates merged with every `queries_*.py` extension (live: reflects TEMPLATES edits)."""
    return merge_templates(TEMPLATES, extension_modules())


def get(name: str) -> Template:
    try:
        return TEMPLATES[name] if name in TEMPLATES else all_templates()[name]
    except (KeyError, TypeError) as exc:
        raise UnknownTemplate(
            f"no read template named {name!r}; available: {sorted(all_templates())}"
        ) from exc


# ------------------------------------------------------------------------------------------ bookkeeping
_register(
    Template(
        "counts_by_label",
        """
    CALL () {
      MATCH (n:Requirement {target: $target}) RETURN 'Requirement' AS label, count(n) AS n
      UNION ALL
      MATCH (n:Screen {target: $target}) RETURN 'Screen' AS label, count(n) AS n
      UNION ALL
      MATCH (n:UIElement {target: $target}) RETURN 'UIElement' AS label, count(n) AS n
      UNION ALL
      MATCH (n:Flow {target: $target}) RETURN 'Flow' AS label, count(n) AS n
      UNION ALL
      MATCH (n:CodeFile {target: $target}) RETURN 'CodeFile' AS label, count(n) AS n
      UNION ALL
      MATCH (n:I18nKey {target: $target}) RETURN 'I18nKey' AS label, count(n) AS n
      UNION ALL
      MATCH (n:Route {target: $target}) RETURN 'Route' AS label, count(n) AS n
      UNION ALL
      MATCH (n:PullRequest {target: $target}) RETURN 'PullRequest' AS label, count(n) AS n
      UNION ALL
      MATCH (n:Gap {target: $target}) RETURN 'Gap' AS label, count(n) AS n
      UNION ALL
      MATCH (n:Run {target: $target}) RETURN 'Run' AS label, count(n) AS n
    }
    RETURN label, n
    ORDER BY label
    """,
        {"target": _str()},
        "Node count per label for one target (one label-scoped branch per label, via the target index).",
    )
)

_register(
    Template(
        "counts_by_rel",
        """
    CALL () {
      MATCH (:Requirement {target: $target})-[r]->() RETURN type(r) AS type, count(r) AS n
      UNION ALL
      MATCH (:Screen {target: $target})-[r]->() RETURN type(r) AS type, count(r) AS n
      UNION ALL
      MATCH (:UIElement {target: $target})-[r]->() RETURN type(r) AS type, count(r) AS n
      UNION ALL
      MATCH (:Flow {target: $target})-[r]->() RETURN type(r) AS type, count(r) AS n
      UNION ALL
      MATCH (:CodeFile {target: $target})-[r]->() RETURN type(r) AS type, count(r) AS n
      UNION ALL
      MATCH (:I18nKey {target: $target})-[r]->() RETURN type(r) AS type, count(r) AS n
      UNION ALL
      MATCH (:Route {target: $target})-[r]->() RETURN type(r) AS type, count(r) AS n
      UNION ALL
      MATCH (:PullRequest {target: $target})-[r]->() RETURN type(r) AS type, count(r) AS n
      UNION ALL
      MATCH (:Gap {target: $target})-[r]->() RETURN type(r) AS type, count(r) AS n
      UNION ALL
      MATCH (:Run {target: $target})-[r]->() RETURN type(r) AS type, count(r) AS n
    }
    RETURN type, sum(n) AS n
    ORDER BY type
    """,
        {"target": _str()},
        "Relationship count per type among one target's nodes (one label-scoped branch per source label).",
    )
)

_register(
    Template(
        "runs",
        """
    MATCH (r:Run {target: $target})
    RETURN r.id AS id, r.layers AS layers, r.snapshot_id AS code_snapshot_id,
           r.crawl_snapshot_id AS crawl_snapshot_id, r.bundle_hash AS bundle_hash,
           r.bundle_hash_end AS bundle_hash_end,
           r.first_seen_at AS first_seen_at, r.updated_at AS updated_at
    ORDER BY updated_at DESC, id
    LIMIT $limit
    """,
        {"target": _str(), "limit": _int(20, 1, 1000)},
        "Runs that wrote to a target, newest first, with the snapshot and bundle each layer was pinned to.",
    )
)

_register(
    Template(
        "element_aliases",
        """
    MATCH (e:UIElement {target: $target})
    WHERE e.obs_ids IS NOT NULL
    RETURN e.id AS id, e.obs_ids AS obs_ids
    """,
        {"target": _str()},
        "Canonical UIElement id with the crawl-time observation ids merged into it.",
    )
)

_register(
    Template(
        "screen_url_patterns",
        """
    MATCH (s:Screen {target: $target})
    RETURN s.id AS id, s.url_pattern AS url_pattern
    ORDER BY id
    """,
        {"target": _str()},
        "Every Screen id with its url_pattern (SERVED_BY matching).",
    )
)

_register(
    Template(
        "route_patterns",
        """
    MATCH (r:Route {target: $target})
    RETURN r.pattern AS pattern
    ORDER BY pattern
    """,
        {"target": _str()},
        "Every Route pattern of a target (SERVED_BY matching).",
    )
)

_register(
    Template(
        "transitions",
        """
    CALL () {
      MATCH (src:UIElement {target: $target})-[t:LEADS_TO]->(dst:Screen {target: $target})
      RETURN t, src, dst, 'UIElement' AS source_kind
      UNION ALL
      MATCH (src:Screen {target: $target})-[t:LEADS_TO]->(dst:Screen {target: $target})
      RETURN t, src, dst, 'Screen' AS source_kind
    }
    RETURN t.tid AS id, t.from_screen AS from_screen, src.id AS source_id, source_kind,
           src.name AS source_name, dst.id AS to_screen, t.action AS action, t.label AS label,
           t.observed AS observed, t.blocked AS blocked
    ORDER BY from_screen, id
    """,
        {"target": _str()},
        "All LEADS_TO edges; the source is a canonical UIElement, or a Screen for element-less transitions.",
    )
)

# ----------------------------------------------------------------------------------------- code lookups
_register(
    Template(
        "file_exists",
        """
    UNWIND $paths AS p
    OPTIONAL MATCH (f:CodeFile {target: $target, path: p})
    RETURN p AS path, f IS NOT NULL AS exists, f.kind AS kind, f.sha AS sha, f.package AS package,
           coalesce(f.stub, false) AS stub
    """,
        {"target": _str(), "paths": _paths()},
        "Per path: is it a CodeFile in the indexed snapshot (PR_PATH_MISSING_AT_HEAD), and its class.",
    )
)

_register(
    Template(
        "unmapped_ui_elements",
        """
    MATCH (el:UIElement {target: $target})
    WHERE NOT (el)-[:IMPLEMENTED_BY]->(:CodeFile)
    OPTIONAL MATCH (el)-[:APPEARS_ON]->(s:Screen {target: $target})
    WITH el, collect(DISTINCT s.id) AS screen_ids, collect(DISTINCT s.url_pattern) AS url_patterns
    RETURN el.id AS id, el.role AS role, el.name AS name, el.text AS text, el.tag AS tag,
           el.testid AS testid, el.dom_id AS dom_id, el.href AS href, el.placeholder AS placeholder,
           el.title_attr AS title_attr, el.aria_label AS aria_label,
           size(screen_ids) AS screens, screen_ids, url_patterns,
           [(el)-[:HAS_GAP]->(g:Gap) | g.kind] AS gap_kinds
    ORDER BY screens DESC, name, id
    LIMIT $limit
    """,
        {"target": _str(), "limit": _int(5000, 1, 100000)},
        "Canonical elements with no IMPLEMENTED_BY edge: the linker's work list and the UNMAPPED_CODE input.",
    )
)

_register(
    Template(
        "element_by_file",
        """
    UNWIND $paths AS p
    MATCH (f:CodeFile {target: $target, path: p})<-[im:IMPLEMENTED_BY]-(el:UIElement {target: $target})
    OPTIONAL MATCH (el)-[:APPEARS_ON]->(s:Screen {target: $target})
    WHERE size(coalesce(im.screen_ids, [])) = 0 OR s.id IN im.screen_ids
    RETURN p AS path, el.id AS element_id, el.role AS role, el.name AS name,
           im.confidence AS confidence, im.method AS method, im.evidence_id AS evidence_id,
           collect(DISTINCT s.id) AS screen_ids
    ORDER BY path, confidence DESC, element_id, method
    """,
        {"target": _str(), "paths": _paths()},
        "UI elements directly implemented by each file (no closure), with the edge's confidence and "
        "evidence and the screens the edge was derived on (per-screen attribution).",
        sorted_lists(screen_ids=str),
    )
)

# ------------------------------------------------------------------------------------- impact (core)
# Closure per seed, breadth-first, one level at a time. A level keeps ONE entry per distinct node: `min()`
# over [via, edge type, ...] picks the lexicographically smallest witness whatever order the edges were
# created in. Each level expands every distinct node of the previous level once, so the work per seed is at
# most 3 x edges (a path-enumerating closure is fan-in^hops). A node reached again by a longer walk reappears
# in a later level but is dominated: the `min()` over hops below keeps its shortest route. A level is a list
# of {node, hops, direction, via, kinds, symbols, specifiers}; `$hops` and `$forward` switch levels off.
# The route sort key is [hops, forward?, -confidence, file, seed, via, kinds, payload]: index 7 is the
# payload.
_register(
    Template(
        "impact",
        """
    UNWIND $changed AS changed_path
    MATCH (seed:CodeFile {target: $target, path: changed_path})
    CALL (seed) {
      OPTIONAL MATCH (a:CodeFile {target: $target})-[r:IMPORTS|RENDERS]->(seed)
      WHERE $hops >= 1 AND a <> seed
      WITH a, min([type(r), r.symbols, r.via]) AS pick
      RETURN collect(CASE WHEN a IS NULL THEN null ELSE
             {node: a, hops: 1, direction: 'reverse', via: [a.path, seed.path], kinds: [pick[0]],
              symbols: [pick[1]], specifiers: [pick[2]]} END) AS l1
    }
    CALL (seed, l1) {
      UNWIND l1 AS x
      WITH seed, x, x.node AS via_node
      MATCH (b:CodeFile {target: $target})-[r:IMPORTS|RENDERS]->(via_node)
      WHERE $hops >= 2 AND b <> seed
      WITH b, min([x.via, type(r), r.symbols, r.via, x.kinds, x.symbols, x.specifiers]) AS pick
      RETURN collect({node: b, hops: 2, direction: 'reverse', via: [b.path] + pick[0],
                      kinds: [pick[1]] + pick[4], symbols: [pick[2]] + pick[5],
                      specifiers: [pick[3]] + pick[6]}) AS l2
    }
    CALL (seed, l2) {
      UNWIND l2 AS x
      WITH seed, x, x.node AS via_node
      MATCH (c:CodeFile {target: $target})-[r:IMPORTS|RENDERS]->(via_node)
      WHERE $hops >= 3 AND c <> seed
      WITH c, min([x.via, type(r), r.symbols, r.via, x.kinds, x.symbols, x.specifiers]) AS pick
      RETURN collect({node: c, hops: 3, direction: 'reverse', via: [c.path] + pick[0],
                      kinds: [pick[1]] + pick[4], symbols: [pick[2]] + pick[5],
                      specifiers: [pick[3]] + pick[6]}) AS l3
    }
    CALL (seed) {
      OPTIONAL MATCH (seed)-[r:IMPORTS|RENDERS]->(d:CodeFile {target: $target})
      WHERE $forward >= 1 AND d <> seed
      WITH d, min([type(r), r.symbols, r.via]) AS pick
      RETURN collect(CASE WHEN d IS NULL THEN null ELSE
             {node: d, hops: 1, direction: 'forward', via: [seed.path, d.path], kinds: [pick[0]],
              symbols: [pick[1]], specifiers: [pick[2]]} END) AS lf
    }
    WITH changed_path,
         [{node: seed, hops: 0, direction: 'self', via: [seed.path], kinds: [], symbols: [],
           specifiers: []}] + l1 + l2 + l3 + lf AS routes
    UNWIND routes AS c
    WITH changed_path, c.node AS impl, c.direction AS direction,
         min([c.hops, c.via, c.kinds, c.symbols, c.specifiers]) AS shortest
    WITH changed_path, impl, direction, shortest[0] AS hops, shortest[1] AS via, shortest[2] AS kinds,
         shortest[3] AS symbols, shortest[4] AS specifiers
    MATCH (el:UIElement {target: $target})-[im:IMPLEMENTED_BY]->(impl)
    MATCH (el)-[ap:APPEARS_ON]->(s:Screen {target: $target})
    WHERE size(coalesce(im.screen_ids, [])) = 0 OR s.id IN im.screen_ids
    WITH el, s,
         min(CASE WHEN ap.visible = false THEN 1 ELSE 0 END) AS all_hidden,
         min([hops, CASE direction WHEN 'forward' THEN 1 ELSE 0 END, -coalesce(im.confidence, 0.0),
              impl.path, changed_path, via, kinds,
              {hops: hops, direction: direction, seed: changed_path, file: impl.path, via: via,
               kinds: kinds, symbols: symbols, specifiers: specifiers, confidence: im.confidence,
               method: im.method, evidence_id: im.evidence_id}]) AS best_key,
         collect(DISTINCT {file: impl.path, confidence: im.confidence, method: im.method,
                           evidence_id: im.evidence_id,
                           screen_ids: coalesce(im.screen_ids, [])}) AS impl_edges,
         collect(DISTINCT changed_path) AS seeds
    CALL (el, s) {
      OPTIONAL MATCH (q:Requirement {target: $target})-[rb:REALIZED_BY]->(el)
      WHERE size(coalesce(rb.screen_ids, [])) = 0 OR s.id IN rb.screen_ids
      RETURN q, rb, 'element' AS via
      UNION
      OPTIONAL MATCH (q:Requirement {target: $target})-[rb:REALIZED_BY]->(s)
      WHERE size(coalesce(rb.screen_ids, [])) = 0 OR s.id IN rb.screen_ids
      RETURN q, rb, 'screen' AS via
      UNION
      OPTIONAL MATCH (q:Requirement {target: $target})-[rb:REALIZED_BY]->(:Flow)-[:STEP]->(el)
      WHERE size(coalesce(rb.screen_ids, [])) = 0 OR s.id IN rb.screen_ids
      RETURN q, rb, 'flow' AS via
    }
    WITH el, s, all_hidden, best_key[7] AS best, impl_edges, seeds,
         collect(DISTINCT CASE WHEN q IS NULL THEN null ELSE
                 {id: q.id, via: via, confidence: rb.confidence, method: rb.method,
                  evidence_id: rb.evidence_id, screen_ids: coalesce(rb.screen_ids, [])} END) AS requirements
    OPTIONAL MATCH (fl:Flow {target: $target})-[:STEP]->(el)
    WITH el, s, all_hidden, best, impl_edges, seeds, requirements,
         collect(DISTINCT CASE WHEN fl IS NULL THEN null ELSE {id: fl.id, name: fl.name} END) AS flows
    RETURN s.id AS screen_id, s.url_pattern AS url_pattern, s.title AS screen_title, s.kind AS screen_kind,
           el.id AS element_id, el.role AS element_role, el.name AS element_name,
           el.owner_file AS owner_file, all_hidden = 1 AS hidden_on_screen,
           best.hops AS hops, best.direction AS direction, best.seed AS seed, best.file AS file,
           best.via AS via, best.kinds AS kinds, best.symbols AS symbols, best.specifiers AS specifiers,
           best.confidence AS impl_confidence, best.method AS impl_method,
           best.evidence_id AS impl_evidence_id,
           impl_edges, seeds, flows, requirements
    ORDER BY hops, coalesce(impl_confidence, 0.0) DESC, screen_id, element_id
    """,
        {"target": _str(), "changed": _paths(), "hops": _hops(), "forward": _int(0, 0, 1)},
        "Changed paths -> importer/renderer closure (hop cap) -> UIElement -> Screen, one row per distinct "
        "(screen, element) with the minimum hop, the witness path, and optional flows and requirements. "
        "`hops` caps reverse distance; a forward row always has hops 1.",
        sorted_lists(
            impl_edges=by("file", "method", "evidence_id"),
            seeds=str,
            flows=by("id", "name"),
            requirements=by("id", "via", "method", "evidence_id"),
        ),
    )
)

# ------------------------------------------------------------------------------------- hub damping
_register(
    Template(
        "hub_degree",
        """
    MATCH (u:CodeFile {target: $target})
    WHERE u.kind IN $ui_kinds
    WITH count(u) AS ui_total
    UNWIND $paths AS p
    MATCH (f:CodeFile {target: $target, path: p})
    CALL (f) {
      OPTIONAL MATCH (a:CodeFile {target: $target})-[:IMPORTS|RENDERS]->(f)
      WHERE $hops >= 1
      RETURN a AS imp
      UNION
      MATCH (b:CodeFile {target: $target})-[:IMPORTS|RENDERS]->(:CodeFile {target: $target})
            -[:IMPORTS|RENDERS]->(f)
      WHERE $hops >= 2
      RETURN b AS imp
      UNION
      MATCH (c:CodeFile {target: $target})-[:IMPORTS|RENDERS]->(:CodeFile {target: $target})
            -[:IMPORTS|RENDERS]->(:CodeFile {target: $target})-[:IMPORTS|RENDERS]->(f)
      WHERE $hops >= 3
      RETURN c AS imp
    }
    WITH ui_total, f, collect(DISTINCT imp) AS imps
    WITH ui_total, f, [i IN imps WHERE i <> f] AS imps
    WITH ui_total, f, imps, [i IN imps WHERE i.kind IN $ui_kinds] AS ui_imps
    RETURN f.path AS path, f.kind AS kind, size(imps) AS importers, size(ui_imps) AS ui_importers,
           size([i IN imps WHERE (i)-[:IMPORTS|RENDERS]->(f)]) AS direct_importers,
           ui_total,
           CASE WHEN ui_total = 0 THEN 0.0 ELSE toFloat(size(ui_imps)) / ui_total END AS fraction
    ORDER BY fraction DESC, path
    """,
        {"target": _str(), "paths": _paths(), "hops": _hops(), "ui_kinds": Param("str_list", UI_FILE_KINDS)},
        "Distinct importers of each file within `hops`, and the UI-file share they make up (case 50).",
    )
)

# ------------------------------------------------------------------------------------------- absence
_register(
    Template(
        "absence_near",
        """
    UNWIND $changed AS changed_path
    MATCH (seed:CodeFile {target: $target, path: changed_path})
    CALL (seed) {
      RETURN seed AS impl, 0 AS hops
      UNION
      MATCH (a:CodeFile {target: $target})-[:IMPORTS|RENDERS]->(seed)
      WHERE $hops >= 1
      RETURN a AS impl, 1 AS hops
      UNION
      MATCH (b:CodeFile {target: $target})-[:IMPORTS|RENDERS]->(:CodeFile {target: $target})
            -[:IMPORTS|RENDERS]->(seed)
      WHERE $hops >= 2
      RETURN b AS impl, 2 AS hops
      UNION
      MATCH (c:CodeFile {target: $target})-[:IMPORTS|RENDERS]->(:CodeFile {target: $target})
            -[:IMPORTS|RENDERS]->(:CodeFile {target: $target})-[:IMPORTS|RENDERS]->(seed)
      WHERE $hops >= 3
      RETURN c AS impl, 3 AS hops
    }
    MATCH (route:Route {target: $target})
    WHERE EXISTS { (route)-[:HANDLED_BY]->(impl) } OR route.layout = impl.path
    MATCH (s:Screen {target: $target})-[:SERVED_BY]->(route)
    MATCH (g:Gap {target: $target, kind: 'NO_UI_EVIDENCE'})-[:SEARCHED]->(s)
    WHERE $bundle_hash IS NULL OR g.bundle_hash = $bundle_hash
    MATCH (q:Requirement {target: $target})-[:HAS_GAP]->(g)
    WITH q, g, s, changed_path, min(hops) AS hops
    RETURN q.id AS requirement_id, q.text AS text, q.section AS section, q.edition AS edition,
           q.ui_observable AS ui_observable, g.id AS gap_id, g.status AS status, g.reason AS reason,
           g.confidence AS confidence, g.actions_spent AS actions_spent, g.bundle_hash AS bundle_hash,
           collect(DISTINCT s.id) AS impacted_screen_ids, collect(DISTINCT changed_path) AS seeds,
           min(hops) AS hops
    ORDER BY hops, requirement_id, gap_id, bundle_hash
    """,
        {"target": _str(), "changed": _paths(), "hops": _hops(), "bundle_hash": _opt_str()},
        "Requirements with a NO_UI_EVIDENCE gap whose searched Screens are served by an impacted file: "
        "'cannot verify' rather than 'unaffected'. `bundle_hash` is required (case 48: a gap is never "
        "reused across bundles); pass None explicitly to mean gaps from any bundle.",
    )
)


all_templates()  # a name defined twice across queries_*.py raises here, at import
