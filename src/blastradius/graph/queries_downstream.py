"""Read templates the link, reason and report stages need on top of `impact` (D19, D28; cases 46, 48, 51, 57).

Auto-discovered by `queries.all_templates()` (any `queries_*.py` that exposes TEMPLATES), so `GraphStore.read`
runs them exactly like the core templates: named, parameterised, constant Cypher, read transactions only.

Every template returns deterministic rows: a total ORDER BY, list columns sorted by the `post` hook, ids and
paths as plain strings. A canonical element that appears on several screens is attributed per screen: an
IMPLEMENTED_BY or REALIZED_BY edge counts on a screen only when its `screen_ids` is empty or contains it.

  files_using_keys(keys)                       CodeFiles that use each i18n key (exact or by dynamic prefix)
  elements_for_keys(keys)                      the UIElements those files implement, with their screens
  requirement_realizations(requirement_ids)    REALIZED_BY edges per requirement (unrealized ones kept)
  unrealized_requirements(bundle_hash)         requirements nothing realizes, with their gaps for a bundle
  screens_overview()                           every Screen with element, transition, flow and route counts
  flows_for_elements(element_ids)              Flows whose steps include the elements (ids or observation ids)
  links_for_screens(screen_ids)                the link edges attributable to each screen
"""

from __future__ import annotations

from .queries import Template, _opt_str, _paths, _str, sorted_lists

TEMPLATES: dict[str, Template] = {}


def _register(tpl: Template) -> None:
    TEMPLATES[tpl.name] = tpl


# A key is used by a file when the file has a USES_KEY edge to it (`exact`), or when the file's dynamic use
# is a prefix of the key ("menu." covers "menu.copy"; case 33 says dynamic keys are known holes, so the
# match is labelled `dynamic_prefix` and the caller can discount it).
_register(
    Template(
        "files_using_keys",
        """
    UNWIND $keys AS wanted
    CALL (wanted) {
      MATCH (k:I18nKey {target: $target, key: wanted})<-[u:USES_KEY]-(f:CodeFile {target: $target})
      RETURN f, u, k, 'exact' AS match
      UNION
      MATCH (k:I18nKey {target: $target})<-[u:USES_KEY]-(f:CodeFile {target: $target})
      WHERE u.dynamic = true AND size(k.key) > 0 AND k.key <> wanted AND wanted STARTS WITH k.key
      RETURN f, u, k, 'dynamic_prefix' AS match
    }
    RETURN wanted AS key, match, k.key AS used_key, f.path AS file, f.kind AS kind, f.package AS package,
           u.dynamic AS dynamic, u.uses AS uses, k.en AS en
    ORDER BY key, match, file, used_key
    """,
        {"target": _str(), "keys": _paths()},
        "Files that use each i18n key: `exact` USES_KEY edges and `dynamic_prefix` uses whose prefix covers "
        "the key. A key with no row has no user in the graph.",
    )
)

_register(
    Template(
        "elements_for_keys",
        """
    UNWIND $keys AS wanted
    CALL (wanted) {
      MATCH (k:I18nKey {target: $target, key: wanted})<-[:USES_KEY]-(f:CodeFile {target: $target})
      RETURN f, 'exact' AS match
      UNION
      MATCH (k:I18nKey {target: $target})<-[u:USES_KEY]-(f:CodeFile {target: $target})
      WHERE u.dynamic = true AND size(k.key) > 0 AND k.key <> wanted AND wanted STARTS WITH k.key
      RETURN f, 'dynamic_prefix' AS match
    }
    MATCH (el:UIElement {target: $target})-[im:IMPLEMENTED_BY]->(f)
    OPTIONAL MATCH (el)-[:APPEARS_ON]->(s:Screen {target: $target})
    WHERE size(coalesce(im.screen_ids, [])) = 0 OR s.id IN im.screen_ids
    WITH wanted, match, f, el, im, collect(DISTINCT s.id) AS screen_ids
    RETURN wanted AS key, match, f.path AS file, el.id AS element_id, el.role AS role, el.name AS name,
           el.owner_file AS owner_file, im.method AS method, im.confidence AS confidence,
           im.evidence_id AS evidence_id, screen_ids
    ORDER BY key, match, file, element_id, method
    """,
        {"target": _str(), "keys": _paths()},
        "Key -> files that use it -> the canonical UIElements those files implement, with the screens each "
        "edge was derived on (per-screen attribution).",
        sorted_lists(screen_ids=str),
    )
)

_register(
    Template(
        "requirement_realizations",
        """
    UNWIND $requirement_ids AS rid
    MATCH (q:Requirement {target: $target, id: rid})
    OPTIONAL MATCH (q)-[rb:REALIZED_BY]->(d)
    RETURN q.id AS requirement_id, q.text AS text, q.section AS section, q.edition AS edition,
           q.spec_sha AS spec_sha, d.id AS target_id, labels(d)[0] AS target_kind,
           coalesce(d.name, d.url_pattern) AS target_name,
           rb.method AS method, rb.confidence AS confidence, rb.evidence_id AS evidence_id,
           rb.judge AS judge, rb.doc_sha AS doc_sha, coalesce(rb.screen_ids, []) AS link_screen_ids
    ORDER BY requirement_id, coalesce(confidence, -1.0) DESC, target_kind, target_id, method
    """,
        {"target": _str(), "requirement_ids": _paths()},
        "REALIZED_BY edges of each requirement (UIElement, Screen or Flow target). A requirement that exists "
        "but is unrealized yields one row with null target columns; an unknown id yields none.",
        sorted_lists(link_screen_ids=str),
    )
)

_register(
    Template(
        "unrealized_requirements",
        """
    MATCH (q:Requirement {target: $target})
    WHERE NOT (q)-[:REALIZED_BY]->()
    OPTIONAL MATCH (q)-[:HAS_GAP]->(g:Gap {target: $target})
    WHERE $bundle_hash IS NULL OR g.bundle_hash IN [$bundle_hash, '']
    RETURN q.id AS requirement_id, q.text AS text, q.section AS section, q.edition AS edition,
           q.polarity AS polarity, q.ui_observable AS ui_observable, q.requires_login AS requires_login,
           q.source_kind AS source_kind, q.doc_version AS doc_version, q.spec_sha AS spec_sha,
           q.source_url AS source_url,
           g.id AS gap_id, g.kind AS gap_kind, g.status AS gap_status, g.reason AS gap_reason,
           g.confidence AS gap_confidence, g.actions_spent AS gap_actions_spent,
           g.bundle_hash AS gap_bundle_hash, g.evidence_id AS gap_evidence_id
    ORDER BY section, requirement_id, gap_kind, gap_id, gap_bundle_hash
    """,
        {"target": _str(), "bundle_hash": _opt_str()},
        "Requirements with no REALIZED_BY edge, one row per gap (or one row with null gap columns). "
        "`bundle_hash` is required (case 48): gaps of that bundle and bundle-independent gaps (bundle '') "
        "are returned; None means gaps from every bundle.",
    )
)

_register(
    Template(
        "screens_overview",
        """
    MATCH (s:Screen {target: $target})
    OPTIONAL MATCH (e:UIElement {target: $target})-[:APPEARS_ON]->(s)
    WITH s, collect(DISTINCT e) AS els
    OPTIONAL MATCH (s)-[:SERVED_BY]->(r:Route {target: $target})
    WITH s, els, collect(DISTINCT r.pattern) AS routes
    RETURN s.id AS screen_id, s.url AS url, s.url_pattern AS url_pattern, s.title AS title, s.kind AS kind,
           s.dialog_title AS dialog_title, s.depth AS depth, s.bundle_hash AS bundle_hash,
           s.snapshot_id AS snapshot_id, size(els) AS elements,
           size([e IN els WHERE NOT (e)-[:IMPLEMENTED_BY]->(:CodeFile)]) AS unmapped_elements,
           COUNT { ()-[:LEADS_TO]->(s) } AS incoming_transitions,
           COUNT { (:Flow)-[:STARTS_AT]->(s) } AS flows_starting,
           COUNT { (:Flow)-[:ENDS_AT]->(s) } AS flows_ending,
           COUNT { (:Gap)-[:SEARCHED]->(s) } AS searched_by_gaps,
           routes
    ORDER BY url_pattern, kind, dialog_title, screen_id
    """,
        {"target": _str()},
        "Every Screen of a target with its element and unmapped-element counts, incoming transitions, the "
        "flows that start and end there, the gaps that searched it and the Routes that serve it.",
        sorted_lists(routes=str),
    )
)

_register(
    Template(
        "flows_for_elements",
        """
    UNWIND $element_ids AS wanted
    CALL (wanted) {
      MATCH (e:UIElement {target: $target, id: wanted})
      RETURN e
      UNION
      MATCH (e:UIElement {target: $target})
      WHERE e.obs_ids IS NOT NULL AND wanted IN e.obs_ids
      RETURN e
    }
    MATCH (fl:Flow {target: $target})-[st:STEP]->(e)
    OPTIONAL MATCH (fl)-[:STARTS_AT]->(a:Screen {target: $target})
    OPTIONAL MATCH (fl)-[:ENDS_AT]->(b:Screen {target: $target})
    RETURN wanted AS requested_id, e.id AS element_id, e.role AS role, e.name AS name,
           fl.id AS flow_id, fl.name AS flow_name, fl.goal AS goal, fl.n_steps AS n_steps,
           fl.n_screens AS n_screens, st.order AS step_order, a.id AS starts_at, b.id AS ends_at
    ORDER BY element_id, flow_id, step_order, requested_id
    """,
        {"target": _str(), "element_ids": _paths()},
        "Flows with a STEP on each element; an id may be the canonical UIElement id or a crawl observation "
        "id. One row per (element, flow, step order).",
    )
)

_register(
    Template(
        "links_for_screens",
        """
    UNWIND $screen_ids AS sid
    MATCH (s:Screen {target: $target, id: sid})
    CALL (s) {
      MATCH (el:UIElement {target: $target})-[:APPEARS_ON]->(s)
      MATCH (el)-[im:IMPLEMENTED_BY]->(f:CodeFile {target: $target})
      WHERE size(coalesce(im.screen_ids, [])) = 0 OR s.id IN im.screen_ids
      RETURN 'IMPLEMENTED_BY' AS kind, el.id AS src, f.path AS dst, 'element' AS via,
             im.method AS method, im.confidence AS confidence, im.evidence_id AS evidence_id,
             im.judge AS judge, coalesce(im.screen_ids, []) AS link_screen_ids
      UNION
      MATCH (el:UIElement {target: $target})-[:APPEARS_ON]->(s)
      MATCH (q:Requirement {target: $target})-[rb:REALIZED_BY]->(el)
      WHERE size(coalesce(rb.screen_ids, [])) = 0 OR s.id IN rb.screen_ids
      RETURN 'REALIZED_BY' AS kind, q.id AS src, el.id AS dst, 'element' AS via,
             rb.method AS method, rb.confidence AS confidence, rb.evidence_id AS evidence_id,
             rb.judge AS judge, coalesce(rb.screen_ids, []) AS link_screen_ids
      UNION
      MATCH (q:Requirement {target: $target})-[rb:REALIZED_BY]->(s)
      WHERE size(coalesce(rb.screen_ids, [])) = 0 OR s.id IN rb.screen_ids
      RETURN 'REALIZED_BY' AS kind, q.id AS src, s.id AS dst, 'screen' AS via,
             rb.method AS method, rb.confidence AS confidence, rb.evidence_id AS evidence_id,
             rb.judge AS judge, coalesce(rb.screen_ids, []) AS link_screen_ids
      UNION
      MATCH (el:UIElement {target: $target})-[:APPEARS_ON]->(s)
      MATCH (q:Requirement {target: $target})-[rb:REALIZED_BY]->(fl:Flow {target: $target})-[:STEP]->(el)
      WHERE size(coalesce(rb.screen_ids, [])) = 0 OR s.id IN rb.screen_ids
      RETURN 'REALIZED_BY' AS kind, q.id AS src, fl.id AS dst, 'flow' AS via,
             rb.method AS method, rb.confidence AS confidence, rb.evidence_id AS evidence_id,
             rb.judge AS judge, coalesce(rb.screen_ids, []) AS link_screen_ids
    }
    RETURN sid AS screen_id, kind, src, dst, via, method, confidence, evidence_id, judge, link_screen_ids
    ORDER BY screen_id, kind, src, dst, method, via
    """,
        {"target": _str(), "screen_ids": _paths()},
        "IMPLEMENTED_BY and REALIZED_BY edges attributable to each screen (per-screen attribution): via "
        "`element` (the element is on the screen), `screen` (REALIZED_BY the screen itself) or `flow` "
        "(a flow step is on the screen).",
        sorted_lists(link_screen_ids=str),
    )
)
