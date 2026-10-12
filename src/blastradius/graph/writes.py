"""Cypher for every write the store performs (D28; cases 46-48, 57).

Each statement is a fixed string. Rows and ids arrive as `$rows` / `$ids` / scalar parameters, never as
text. A label or relationship type cannot be a parameter, so there is one literal statement per type.
`PRUNE` lists, per layer, what `GraphStore.prune_stale` removes when a run did not re-assert it, and never
touches a `method = 'human'` link. `DELETE_BY_LABEL` is `clear_target`'s per-label batch delete (served by
the per-label `target` index in schema.cypher, not by a scan of every node).

Statements that belong together (a Gap and its SEARCHED edges, a Flow and its STEP/STARTS_AT/ENDS_AT edges, a
link and its sorted `screen_ids`) are listed by the store as one group and run in ONE transaction, so a
failure part-way leaves the previous state intact. They delete only what the new row set no longer asserts and
MERGE the rest, so an identical rewrite creates and deletes nothing and `first_run_id` survives.
"""

from __future__ import annotations

RUN = """
MERGE (r:Run {target: $target, id: $run_id})
  ON CREATE SET r.first_seen_at = $now
SET r += $props, r.run_id = $run_id, r.updated_at = $now,
    r.layers = [x IN coalesce(r.layers, []) WHERE x <> $layer] + [$layer]
"""

CODE_FILE = """
UNWIND $rows AS row
MERGE (f:CodeFile {target: $target, path: row.path})
  ON CREATE SET f.first_run_id = $run_id
SET f += row.props, f.run_id = $run_id, f.snapshot_id = $snapshot_id
"""

IMPORTS = """
UNWIND $rows AS row
MATCH (a:CodeFile {target: $target, path: row.src})
MATCH (b:CodeFile {target: $target, path: row.dst})
MERGE (a)-[r:IMPORTS]->(b)
  ON CREATE SET r.first_run_id = $run_id
SET r.symbols = row.symbols, r.via = row.via, r.run_id = $run_id, r.snapshot_id = $snapshot_id
"""

RENDERS = """
UNWIND $rows AS row
MATCH (a:CodeFile {target: $target, path: row.src})
MATCH (b:CodeFile {target: $target, path: row.dst})
MERGE (a)-[r:RENDERS]->(b)
  ON CREATE SET r.first_run_id = $run_id
SET r.symbols = row.symbols, r.via = row.via, r.run_id = $run_id, r.snapshot_id = $snapshot_id
"""

I18N_KEY = """
UNWIND $rows AS row
MERGE (k:I18nKey {target: $target, key: row.key})
  ON CREATE SET k.first_run_id = $run_id
SET k.en = row.en, k.run_id = $run_id
"""

USES_KEY = """
UNWIND $rows AS row
MATCH (f:CodeFile {target: $target, path: row.file})
MATCH (k:I18nKey {target: $target, key: row.key})
MERGE (f)-[u:USES_KEY]->(k)
  ON CREATE SET u.first_run_id = $run_id
SET u.dynamic = row.dynamic, u.uses = row.uses, u.run_id = $run_id
"""

ROUTE = """
UNWIND $rows AS row
MERGE (r:Route {target: $target, pattern: row.pattern})
  ON CREATE SET r.first_run_id = $run_id
SET r.file = row.file, r.layout = row.layout, r.run_id = $run_id, r.snapshot_id = $snapshot_id
"""

HANDLED_BY = """
UNWIND $rows AS row
MATCH (r:Route {target: $target, pattern: row.pattern})
MATCH (f:CodeFile {target: $target, path: row.file})
MERGE (r)-[h:HANDLED_BY]->(f)
  ON CREATE SET h.first_run_id = $run_id
SET h.run_id = $run_id, h.snapshot_id = $snapshot_id
"""

SCREEN = """
UNWIND $rows AS row
MERGE (s:Screen {target: $target, id: row.id})
  ON CREATE SET s.first_run_id = $run_id
SET s += row.props, s.run_id = $run_id, s.bundle_hash = $bundle_hash, s.snapshot_id = $snapshot_id
"""

ELEMENT = """
UNWIND $rows AS row
MERGE (e:UIElement {target: $target, id: row.id})
  ON CREATE SET e.first_run_id = $run_id
SET e += row.props, e.run_id = $run_id, e.bundle_hash = $bundle_hash,
    e.obs_ids = [x IN coalesce(e.obs_ids, []) WHERE NOT x IN row.obs_ids] + row.obs_ids
"""

APPEARS_ON = """
UNWIND $rows AS row
MATCH (e:UIElement {target: $target, id: row.element})
MATCH (s:Screen {target: $target, id: row.screen})
MERGE (e)-[a:APPEARS_ON {obs_id: row.obs_id}]->(s)
  ON CREATE SET a.first_run_id = $run_id
SET a.ordinal = row.ordinal, a.visible = row.visible, a.bbox = row.bbox, a.selector = row.selector,
    a.run_id = $run_id, a.bundle_hash = $bundle_hash
RETURN count(a) AS matched
"""

LEADS_TO_ELEMENT = """
UNWIND $rows AS row
MATCH (e:UIElement {target: $target, id: row.src})
MATCH (s:Screen {target: $target, id: row.dst})
MERGE (e)-[t:LEADS_TO {tid: row.tid}]->(s)
  ON CREATE SET t.first_run_id = $run_id
SET t.action = row.action, t.observed = row.observed, t.blocked = row.blocked, t.label = row.label,
    t.reason = row.reason, t.from_screen = row.from_screen, t.run_id = $run_id,
    t.bundle_hash = $bundle_hash
RETURN count(t) AS matched
"""

LEADS_TO_SCREEN = """
UNWIND $rows AS row
MATCH (a:Screen {target: $target, id: row.src})
MATCH (s:Screen {target: $target, id: row.dst})
MERGE (a)-[t:LEADS_TO {tid: row.tid}]->(s)
  ON CREATE SET t.first_run_id = $run_id
SET t.action = row.action, t.observed = row.observed, t.blocked = row.blocked, t.label = row.label,
    t.reason = row.reason, t.from_screen = row.from_screen, t.run_id = $run_id,
    t.bundle_hash = $bundle_hash
RETURN count(t) AS matched
"""

REQUIREMENT = """
UNWIND $rows AS row
MERGE (q:Requirement {target: $target, id: row.id})
  ON CREATE SET q.first_run_id = $run_id
SET q += row.props, q.run_id = $run_id
"""

GAP = """
UNWIND $rows AS row
MERGE (g:Gap {target: $target, id: row.id, bundle_hash: row.bundle_hash})
  ON CREATE SET g.first_run_id = $run_id
SET g += row.props, g.run_id = $run_id
"""

HAS_GAP = """
UNWIND $rows AS row
MATCH (g:Gap {target: $target, id: row.id, bundle_hash: row.bundle_hash})
OPTIONAL MATCH (rq:Requirement {target: $target, id: row.subject})
OPTIONAL MATCH (el:UIElement {target: $target, id: row.subject})
OPTIONAL MATCH (sc:Screen {target: $target, id: row.subject})
OPTIONAL MATCH (cf:CodeFile {target: $target, path: row.subject})
WITH g, [x IN [rq, el, sc, cf] WHERE x IS NOT NULL] AS subjects
FOREACH (x IN subjects |
  MERGE (x)-[h:HAS_GAP]->(g)
    ON CREATE SET h.first_run_id = $run_id
  SET h.run_id = $run_id)
RETURN sum(CASE WHEN size(subjects) = 0 THEN 1 ELSE 0 END) AS unresolved
"""

# A Gap's SEARCHED edges: drop those to screens no longer searched, then MERGE the searched ones.
SEARCHED_PRUNE = """
UNWIND $rows AS row
MATCH (g:Gap {target: $target, id: row.id, bundle_hash: row.bundle_hash})-[r:SEARCHED]->(s:Screen)
WHERE NOT s.id IN row.screens
DELETE r
"""

SEARCHED = """
UNWIND $rows AS row
MATCH (g:Gap {target: $target, id: row.id, bundle_hash: row.bundle_hash})
UNWIND row.screens AS sid
MATCH (s:Screen {target: $target, id: sid})
MERGE (g)-[r:SEARCHED]->(s)
  ON CREATE SET r.first_run_id = $run_id
SET r.run_id = $run_id
RETURN count(r) AS matched
"""

# Per-screen attribution (decision 3): `screen_ids` on the edge lists the observations the link was derived on
# ([] = every screen of the element). Within one run a rewrite unions the sets and "all" absorbs; a different
# run re-asserts from scratch. The SET lists screen_ids first so it still sees the previous run_id.
IMPLEMENTED_BY = """
UNWIND $rows AS row
OPTIONAL MATCH (e:UIElement {target: $target, id: row.src})
OPTIONAL MATCH (f:CodeFile {target: $target, path: row.dst})
WITH row, e, f
FOREACH (_ IN CASE WHEN e IS NULL OR f IS NULL THEN [] ELSE [1] END |
  MERGE (e)-[r:IMPLEMENTED_BY {method: row.method}]->(f)
    ON CREATE SET r.first_run_id = $run_id
  SET r.screen_ids = CASE
        WHEN r.run_id IS NULL OR r.run_id <> $run_id THEN row.screen_ids
        WHEN size(coalesce(r.screen_ids, [])) = 0 OR size(row.screen_ids) = 0 THEN []
        ELSE [x IN r.screen_ids WHERE NOT x IN row.screen_ids] + row.screen_ids END,
      r.confidence = row.confidence, r.evidence_id = row.evidence_id, r.judge = row.judge,
      r.run_id = $run_id, r.snapshot_id = f.snapshot_id, r.bundle_hash = e.bundle_hash)
RETURN sum(CASE WHEN e IS NULL THEN 1 ELSE 0 END) AS missing_src,
       sum(CASE WHEN e IS NOT NULL AND f IS NULL THEN 1 ELSE 0 END) AS missing_dst,
       collect(CASE WHEN e IS NULL OR f IS NULL THEN [row.src, row.dst] END) AS missing
"""

IMPLEMENTED_BY_SORT = """
UNWIND $rows AS row
MATCH (:UIElement {target: $target, id: row.src})-[r:IMPLEMENTED_BY {method: row.method}]->
      (:CodeFile {target: $target, path: row.dst})
UNWIND r.screen_ids AS sid
WITH r, sid ORDER BY sid
WITH r, collect(DISTINCT sid) AS sorted
SET r.screen_ids = sorted
"""

REALIZED_BY = """
UNWIND $rows AS row
OPTIONAL MATCH (q:Requirement {target: $target, id: row.src})
OPTIONAL MATCH (e:UIElement {target: $target, id: row.dst})
OPTIONAL MATCH (s:Screen {target: $target, id: row.dst})
OPTIONAL MATCH (fl:Flow {target: $target, id: row.dst})
WITH row, q, [x IN [e, s, fl] WHERE x IS NOT NULL] AS dsts
FOREACH (d IN CASE WHEN q IS NULL THEN [] ELSE dsts END |
  MERGE (q)-[r:REALIZED_BY {method: row.method}]->(d)
    ON CREATE SET r.first_run_id = $run_id
  SET r.screen_ids = CASE
        WHEN r.run_id IS NULL OR r.run_id <> $run_id THEN row.screen_ids
        WHEN size(coalesce(r.screen_ids, [])) = 0 OR size(row.screen_ids) = 0 THEN []
        ELSE [x IN r.screen_ids WHERE NOT x IN row.screen_ids] + row.screen_ids END,
      r.confidence = row.confidence, r.evidence_id = row.evidence_id, r.judge = row.judge,
      r.run_id = $run_id, r.doc_sha = q.spec_sha, r.bundle_hash = d.bundle_hash)
RETURN sum(CASE WHEN q IS NULL THEN 1 ELSE 0 END) AS missing_src,
       sum(CASE WHEN q IS NOT NULL AND size(dsts) = 0 THEN 1 ELSE 0 END) AS missing_dst,
       collect(CASE WHEN q IS NULL OR size(dsts) = 0 THEN [row.src, row.dst] END) AS missing
"""

REALIZED_BY_SORT = """
UNWIND $rows AS row
MATCH (:Requirement {target: $target, id: row.src})-[r:REALIZED_BY {method: row.method}]->
      ({target: $target, id: row.dst})
UNWIND r.screen_ids AS sid
WITH r, sid ORDER BY sid
WITH r, collect(DISTINCT sid) AS sorted
SET r.screen_ids = sorted
"""

# Screen ids are fingerprint hashes: a re-crawl of a redeployed app mints new ones while the canonical
# UIElement ids (and so their IMPLEMENTED_BY / REALIZED_BY edges) survive. An edge whose `screen_ids` names a
# Screen that no longer exists would silently drop its element from `impact` until the linker ran again, so
# the load keeps only the ids that still exist and falls back to [] (= every screen of the element, the safe
# over-approximating direction) when none do. One literal statement per source label (index seek).
DANGLING_IMPLEMENTED_BY = """
MATCH (:UIElement {target: $target})-[r:IMPLEMENTED_BY]->()
WHERE size(coalesce(r.screen_ids, [])) > 0
WITH r, r.screen_ids AS before,
     [x IN r.screen_ids WHERE EXISTS { (:Screen {target: $target, id: x}) }] AS live
WHERE size(live) < size(before)
SET r.screen_ids = live
RETURN count(r) AS edges, sum(CASE WHEN size(live) = 0 THEN 1 ELSE 0 END) AS reset_to_all,
       sum(size(before) - size(live)) AS ids_dropped
"""

DANGLING_REALIZED_BY = """
MATCH (:Requirement {target: $target})-[r:REALIZED_BY]->()
WHERE size(coalesce(r.screen_ids, [])) > 0
WITH r, r.screen_ids AS before,
     [x IN r.screen_ids WHERE EXISTS { (:Screen {target: $target, id: x}) }] AS live
WHERE size(live) < size(before)
SET r.screen_ids = live
RETURN count(r) AS edges, sum(CASE WHEN size(live) = 0 THEN 1 ELSE 0 END) AS reset_to_all,
       sum(size(before) - size(live)) AS ids_dropped
"""

OWNER_FILE = """
UNWIND $ids AS id
MATCH (e:UIElement {target: $target, id: id})-[r:IMPLEMENTED_BY]->(f:CodeFile)
WITH e, r, f ORDER BY coalesce(r.confidence, 0.0) DESC, f.path ASC
WITH e, collect(f.path)[0] AS top
SET e.owner_file = top
"""

FLOW = """
UNWIND $rows AS row
MERGE (fl:Flow {target: $target, id: row.id})
  ON CREATE SET fl.first_run_id = $run_id
SET fl += row.props, fl.run_id = $run_id
"""

# `row.keep` lists [order, element id] for every step of the new flow: a STEP edge outside it is stale.
STEP_PRUNE = """
UNWIND $rows AS row
MATCH (fl:Flow {target: $target, id: row.id})-[r:STEP]->(e:UIElement)
WHERE NOT [r.order, e.id] IN row.keep
DELETE r
"""

STEP = """
UNWIND $rows AS row
MATCH (fl:Flow {target: $target, id: row.id})
UNWIND row.steps AS st
MATCH (e:UIElement {target: $target, id: st.element})
MERGE (fl)-[r:STEP {order: st.order}]->(e)
  ON CREATE SET r.first_run_id = $run_id
SET r.run_id = $run_id
RETURN count(r) AS matched
"""

ENDPOINT_PRUNE = """
UNWIND $rows AS row
MATCH (fl:Flow {target: $target, id: row.id})-[r:STARTS_AT|ENDS_AT]->(s:Screen)
WHERE (type(r) = 'STARTS_AT' AND (row.start IS NULL OR s.id <> row.start))
   OR (type(r) = 'ENDS_AT' AND (row.end IS NULL OR s.id <> row.end))
DELETE r
"""

STARTS_AT = """
UNWIND $rows AS row
MATCH (fl:Flow {target: $target, id: row.id})
MATCH (s:Screen {target: $target, id: row.start})
MERGE (fl)-[r:STARTS_AT]->(s)
  ON CREATE SET r.first_run_id = $run_id
SET r.run_id = $run_id
RETURN count(r) AS matched
"""

ENDS_AT = """
UNWIND $rows AS row
MATCH (fl:Flow {target: $target, id: row.id})
MATCH (s:Screen {target: $target, id: row.end})
MERGE (fl)-[r:ENDS_AT]->(s)
  ON CREATE SET r.first_run_id = $run_id
SET r.run_id = $run_id
RETURN count(r) AS matched
"""

SERVED_BY = """
UNWIND $rows AS row
MATCH (s:Screen {target: $target, id: row.screen})
MATCH (r:Route {target: $target, pattern: row.pattern})
MERGE (s)-[b:SERVED_BY]->(r)
  ON CREATE SET b.first_run_id = $run_id
SET b.how = row.how, b.run_id = $run_id
"""

SERVED_BY_STALE = """
MATCH (:Screen {target: $target})-[b:SERVED_BY]->()
WHERE b.run_id <> $run_id
DELETE b
"""

# clear_target: one literal statement per label so each batch is an index seek on (label, target), then one
# label-less sweep for nodes of a label this package does not know (it matches nothing in a normal graph).
DELETE_ANY = "MATCH (n {target: $target}) WITH n LIMIT $batch DETACH DELETE n"

DELETE_BY_LABEL: dict[str, str] = {
    "Requirement": "MATCH (n:Requirement {target: $target}) WITH n LIMIT $batch DETACH DELETE n",
    "Screen": "MATCH (n:Screen {target: $target}) WITH n LIMIT $batch DETACH DELETE n",
    "UIElement": "MATCH (n:UIElement {target: $target}) WITH n LIMIT $batch DETACH DELETE n",
    "Flow": "MATCH (n:Flow {target: $target}) WITH n LIMIT $batch DETACH DELETE n",
    "CodeFile": "MATCH (n:CodeFile {target: $target}) WITH n LIMIT $batch DETACH DELETE n",
    "I18nKey": "MATCH (n:I18nKey {target: $target}) WITH n LIMIT $batch DETACH DELETE n",
    "Route": "MATCH (n:Route {target: $target}) WITH n LIMIT $batch DETACH DELETE n",
    "PullRequest": "MATCH (n:PullRequest {target: $target}) WITH n LIMIT $batch DETACH DELETE n",
    "Gap": "MATCH (n:Gap {target: $target}) WITH n LIMIT $batch DETACH DELETE n",
    "Run": "MATCH (n:Run {target: $target}) WITH n LIMIT $batch DETACH DELETE n",
}

# Prune statements per layer: relationships first, then nodes (DETACH DELETE takes link edges with them).
# One literal statement per label / type because neither can be a Cypher parameter.
PRUNE: dict[str, tuple[tuple[str, str], ...]] = {
    "code": (
        ("IMPORTS", "MATCH (:CodeFile {target: $target})-[r:IMPORTS]->() WHERE r.run_id <> $run_id DELETE r"),
        ("RENDERS", "MATCH (:CodeFile {target: $target})-[r:RENDERS]->() WHERE r.run_id <> $run_id DELETE r"),
        (
            "HANDLED_BY",
            "MATCH (:Route {target: $target})-[r:HANDLED_BY]->() WHERE r.run_id <> $run_id DELETE r",
        ),
        (
            "USES_KEY",
            "MATCH (:CodeFile {target: $target})-[r:USES_KEY]->() WHERE r.run_id <> $run_id DELETE r",
        ),
        ("Route", "MATCH (n:Route {target: $target}) WHERE n.run_id <> $run_id DETACH DELETE n"),
        ("I18nKey", "MATCH (n:I18nKey {target: $target}) WHERE n.run_id <> $run_id DETACH DELETE n"),
        ("CodeFile", "MATCH (n:CodeFile {target: $target}) WHERE n.run_id <> $run_id DETACH DELETE n"),
    ),
    "crawl": (
        (
            "APPEARS_ON",
            "MATCH (:UIElement {target: $target})-[r:APPEARS_ON]->() WHERE r.run_id <> $run_id DELETE r",
        ),
        (
            "LEADS_TO",
            "MATCH (:UIElement {target: $target})-[r:LEADS_TO]->() WHERE r.run_id <> $run_id DELETE r",
        ),
        ("LEADS_TO", "MATCH (:Screen {target: $target})-[r:LEADS_TO]->() WHERE r.run_id <> $run_id DELETE r"),
        ("UIElement", "MATCH (n:UIElement {target: $target}) WHERE n.run_id <> $run_id DETACH DELETE n"),
        ("Screen", "MATCH (n:Screen {target: $target}) WHERE n.run_id <> $run_id DETACH DELETE n"),
    ),
    "spec": (
        ("Requirement", "MATCH (n:Requirement {target: $target}) WHERE n.run_id <> $run_id DETACH DELETE n"),
    ),
    # The linker's output: a re-link replaces it, except what a person asserted (`method = 'human'` is never
    # pruned, D09). `owner_file` is derived from the IMPLEMENTED_BY edges that remain, human ones included, so
    # it is recomputed (and removed when no edge is left) by the last statement.
    "links": (
        (
            "IMPLEMENTED_BY",
            (
                "MATCH (:UIElement {target: $target})-[r:IMPLEMENTED_BY]->() "
                "WHERE r.run_id <> $run_id AND r.method <> 'human' DELETE r"
            ),
        ),
        (
            "REALIZED_BY",
            (
                "MATCH (:Requirement {target: $target})-[r:REALIZED_BY]->() "
                "WHERE r.run_id <> $run_id AND r.method <> 'human' DELETE r"
            ),
        ),
        (
            "owner_file",
            (
                "MATCH (e:UIElement {target: $target}) WHERE e.owner_file IS NOT NULL "
                "OPTIONAL MATCH (e)-[r:IMPLEMENTED_BY]->(f:CodeFile) "
                "WITH e, r, f ORDER BY coalesce(r.confidence, 0.0) DESC, f.path ASC "
                "WITH e, collect(f.path)[0] AS top SET e.owner_file = top"
            ),
        ),
    ),
}
