"""Read templates the reasoning stage needs on top of `impact` (D19, D28; cases 46, 48, 51, 57).

Auto-discovered by `queries.all_templates()` (any `queries_*.py` that exposes TEMPLATES), so `GraphStore.read`
runs them exactly like the core templates: named, parameterised, constant Cypher, read transactions only. Every
template returns deterministic rows (a total ORDER BY, list columns sorted by the `post` hook).

  requirements_by_id(requirement_ids)   the spec statement, verbatim quote and section of each requirement
  screens_by_id(screen_ids)             title, dialog title, kind, tabs and url pattern of each Screen
  keys_used_by_files(paths)             i18n keys a CodeFile uses (static and dynamic prefix) with the English value
  element_context(element_ids)          per (element, screen): flows with a STEP on it and the requirements
                                        realised by it, its flow or its screen (same shape as `impact`)
  importer_files(paths)                 direct importers/renderers of each file with their file class
"""

from __future__ import annotations

from .queries import Template, _paths, _str, by, sorted_lists

TEMPLATES: dict[str, Template] = {}


def _register(tpl: Template) -> None:
    TEMPLATES[tpl.name] = tpl


_register(
    Template(
        "requirements_by_id",
        """
    UNWIND $requirement_ids AS rid
    MATCH (q:Requirement {target: $target, id: rid})
    RETURN q.id AS requirement_id, q.text AS text, q.quote AS quote, q.section AS section,
           q.source_url AS source_url, q.edition AS edition, q.polarity AS polarity,
           q.ui_observable AS ui_observable, q.doc_version AS doc_version, q.spec_sha AS spec_sha
    ORDER BY requirement_id
    """,
        {"target": _str(), "requirement_ids": _paths()},
        "The statement, verbatim quote, section and edition of each requirement id (an unknown id yields no row).",
    )
)

_register(
    Template(
        "screens_by_id",
        """
    UNWIND $screen_ids AS sid
    MATCH (s:Screen {target: $target, id: sid})
    RETURN s.id AS screen_id, s.url AS url, s.url_pattern AS url_pattern, s.title AS title,
           s.dialog_title AS dialog_title, s.kind AS kind, coalesce(s.active_tabs, []) AS active_tabs,
           s.depth AS depth
    ORDER BY screen_id
    """,
        {"target": _str(), "screen_ids": _paths()},
        "Presentation facts of each Screen (the product's own words: title, dialog title, tabs).",
    )
)

_register(
    Template(
        "keys_used_by_files",
        """
    UNWIND $paths AS p
    MATCH (f:CodeFile {target: $target, path: p})-[u:USES_KEY]->(k:I18nKey {target: $target})
    RETURN p AS path, k.key AS key, k.en AS en, coalesce(u.dynamic, false) AS dynamic
    ORDER BY path, key, dynamic
    """,
        {"target": _str(), "paths": _paths()},
        "The i18n keys a file uses (static keys and dynamic prefixes) with their English value.",
    )
)

_register(
    Template(
        "element_context",
        """
    UNWIND $element_ids AS eid
    MATCH (el:UIElement {target: $target, id: eid})-[ap:APPEARS_ON]->(s:Screen {target: $target})
    WITH el, s, min(CASE WHEN ap.visible = false THEN 1 ELSE 0 END) AS all_hidden
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
    WITH el, s, all_hidden,
         collect(DISTINCT CASE WHEN q IS NULL THEN null ELSE
                 {id: q.id, via: via, confidence: rb.confidence, method: rb.method,
                  evidence_id: rb.evidence_id, screen_ids: coalesce(rb.screen_ids, [])} END) AS requirements
    OPTIONAL MATCH (fl:Flow {target: $target})-[:STEP]->(el)
    WITH el, s, all_hidden, requirements,
         collect(DISTINCT CASE WHEN fl IS NULL THEN null ELSE {id: fl.id, name: fl.name} END) AS flows
    RETURN s.id AS screen_id, el.id AS element_id, el.role AS element_role, el.name AS element_name,
           el.owner_file AS owner_file, all_hidden = 1 AS hidden_on_screen, flows, requirements
    ORDER BY element_id, screen_id
    """,
        {"target": _str(), "element_ids": _paths()},
        "Per (element, screen): the flows with a STEP on the element and the requirements realised through "
        "the element, its screen or one of its flows (per-screen attribution, as in `impact`).",
        sorted_lists(flows=by("id", "name"), requirements=by("id", "via", "method", "evidence_id")),
    )
)

_register(
    Template(
        "importer_files",
        """
    UNWIND $paths AS p
    MATCH (f:CodeFile {target: $target, path: p})
    OPTIONAL MATCH (a:CodeFile {target: $target})-[r:IMPORTS|RENDERS]->(f)
    WHERE a <> f
    RETURN p AS path, a.path AS importer, a.kind AS kind, type(r) AS edge_kind, r.symbols AS symbols
    ORDER BY path, importer, edge_kind
    """,
        {"target": _str(), "paths": _paths()},
        "Direct importers and renderers of each file with their file class (the PROBE action's census). A file "
        "nothing imports yields one row with a null importer.",
    )
)
