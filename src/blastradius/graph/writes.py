"""Cypher for every write the store performs (D28; cases 46-48, 57).

Each statement is a fixed string. Rows and ids arrive as `$rows` / `$ids` / scalar parameters, never as
text. A label or relationship type cannot be a parameter, so there is one literal statement per type.
`PRUNE` lists, per layer, what `GraphStore.prune_stale` removes when a run did not re-assert it.
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

SEARCHED_RESET = """
UNWIND $rows AS row
MATCH (g:Gap {target: $target, id: row.id, bundle_hash: row.bundle_hash})-[r:SEARCHED]->()
DELETE r
"""

SEARCHED = """
UNWIND $rows AS row
MATCH (g:Gap {target: $target, id: row.id, bundle_hash: row.bundle_hash})
MATCH (s:Screen {target: $target, id: row.screen})
MERGE (g)-[r:SEARCHED]->(s)
  ON CREATE SET r.first_run_id = $run_id
SET r.run_id = $run_id
RETURN count(r) AS matched
"""

IMPLEMENTED_BY = """
UNWIND $rows AS row
OPTIONAL MATCH (e:UIElement {target: $target, id: row.src})
OPTIONAL MATCH (f:CodeFile {target: $target, path: row.dst})
WITH row, e, f
FOREACH (_ IN CASE WHEN e IS NULL OR f IS NULL THEN [] ELSE [1] END |
  MERGE (e)-[r:IMPLEMENTED_BY {method: row.method}]->(f)
    ON CREATE SET r.first_run_id = $run_id
  SET r.confidence = row.confidence, r.evidence_id = row.evidence_id, r.judge = row.judge,
      r.run_id = $run_id, r.snapshot_id = f.snapshot_id, r.bundle_hash = e.bundle_hash)
RETURN sum(CASE WHEN e IS NULL THEN 1 ELSE 0 END) AS missing_src,
       sum(CASE WHEN e IS NOT NULL AND f IS NULL THEN 1 ELSE 0 END) AS missing_dst
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
  SET r.confidence = row.confidence, r.evidence_id = row.evidence_id, r.judge = row.judge,
      r.run_id = $run_id, r.doc_sha = q.spec_sha, r.bundle_hash = d.bundle_hash)
RETURN sum(CASE WHEN q IS NULL THEN 1 ELSE 0 END) AS missing_src,
       sum(CASE WHEN q IS NOT NULL AND size(dsts) = 0 THEN 1 ELSE 0 END) AS missing_dst
"""

OWNER_FILE = """
UNWIND $ids AS id
MATCH (e:UIElement {target: $target, id: id})-[r:IMPLEMENTED_BY]->(f:CodeFile)
WITH e, r, f ORDER BY coalesce(r.confidence, 0.0) DESC, f.path ASC
WITH e, collect(f.path)[0] AS top
SET e.owner_file = top
"""

FLOW_RESET = """
UNWIND $ids AS id
MATCH (fl:Flow {target: $target, id: id})-[r:STEP|STARTS_AT|ENDS_AT]->()
DELETE r
"""

FLOW = """
UNWIND $rows AS row
MERGE (fl:Flow {target: $target, id: row.id})
  ON CREATE SET fl.first_run_id = $run_id
SET fl += row.props, fl.run_id = $run_id
"""

STEP = """
UNWIND $rows AS row
MATCH (fl:Flow {target: $target, id: row.flow})
MATCH (e:UIElement {target: $target, id: row.element})
MERGE (fl)-[r:STEP {order: row.order}]->(e)
  ON CREATE SET r.first_run_id = $run_id
SET r.run_id = $run_id
RETURN count(r) AS matched
"""

STARTS_AT = """
UNWIND $rows AS row
MATCH (fl:Flow {target: $target, id: row.flow})
MATCH (s:Screen {target: $target, id: row.screen})
MERGE (fl)-[r:STARTS_AT]->(s)
  ON CREATE SET r.first_run_id = $run_id
SET r.run_id = $run_id
RETURN count(r) AS matched
"""

ENDS_AT = """
UNWIND $rows AS row
MATCH (fl:Flow {target: $target, id: row.flow})
MATCH (s:Screen {target: $target, id: row.screen})
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

DELETE_BATCH = """
MATCH (n {target: $target})
WITH n LIMIT $batch
DETACH DELETE n
"""

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
    # The linker's output: a re-link replaces it. `owner_file` is derived from IMPLEMENTED_BY, so it is
    # recomputed (and removed when no edge is left) by the last statement.
    "links": (
        (
            "IMPLEMENTED_BY",
            "MATCH (:UIElement {target: $target})-[r:IMPLEMENTED_BY]->() WHERE r.run_id <> $run_id DELETE r",
        ),
        (
            "REALIZED_BY",
            "MATCH (:Requirement {target: $target})-[r:REALIZED_BY]->() WHERE r.run_id <> $run_id DELETE r",
        ),
        (
            "owner_file",
            "MATCH (e:UIElement {target: $target}) WHERE e.owner_file IS NOT NULL "
            "OPTIONAL MATCH (e)-[r:IMPLEMENTED_BY]->(f:CodeFile) "
            "WITH e, r, f ORDER BY coalesce(r.confidence, 0.0) DESC, f.path ASC "
            "WITH e, collect(f.path)[0] AS top SET e.owner_file = top",
        ),
    ),
}
