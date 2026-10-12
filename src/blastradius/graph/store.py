"""GraphStore: the only code that talks to Neo4j (D03, D08, D28; cases 46, 47, 48, 51, 57).

What this module guarantees:
  * Writes are idempotent MERGEs keyed by (target, id) / (target, path), batched with UNWIND. Re-writing
    identical input creates and deletes nothing (the returned counters say so) and `SET n += props` never
    drops a property that another stage wrote (owner_file, links), so a rerun cannot lose data (case 47).
  * Every node and relationship carries `run_id` (last writer) and `first_run_id` (never overwritten, also
    by gap and flow rewrites), so two runs are diffable and `prune_stale` can remove what a run no longer
    asserts.
  * Statements that belong together run in ONE transaction per chunk of rows: a Gap with its SEARCHED edges, a
    Flow with its STEP/STARTS_AT/ENDS_AT edges, a link with its sorted `screen_ids`. A failure leaves the
    previous state untouched; a rewrite deletes only the edges the new row set no longer asserts.
  * Gap nodes are keyed by (target, id, bundle_hash) and never reused across bundles (case 48).
  * Reads go through named templates only (queries.py and every queries_*.py) inside one read transaction,
    one attempt: the server rejects a write there, and a memory-pool or timeout error fails fast instead of
    being retried by the driver for tens of seconds. All Cypher in this file is a module constant: values
    reach Neo4j only as parameters.
  * Unresolvable references are skipped, counted in the returned dict and recorded on the run, never silently
    invented. A link with a confidence outside [0, 1], or one that cites evidence the run does not hold, is
    not written. Two links for one (src, dst, method) collapse deterministically (highest confidence, then the
    smallest evidence id; `screen_ids` are unioned).
  * Secrets never reach a message or a failure record: URI userinfo (up to the LAST '@' before the host, so a
    password containing '@' goes whole) and the configured password are scrubbed from every driver error
    text and from the URI itself.
  * A re-crawl mints new Screen ids while canonical UIElement ids and their links survive:
    `reset_dangling_attribution` keeps only the `screen_ids` that still name a Screen ([] = every screen).

Errors: `GraphError` (a failed write, GRAPH_WRITE_FAILED), `GraphReadError` (a failed read), and
`GraphConnectionError` (GRAPH_CONN_FAILED). Each carries the server's code in `.neo4j_code`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlsplit

import neo4j
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, DriverError, Neo4jError, ServiceUnavailable, SessionExpired

from ..config import Settings
from ..failures import META, FailureCode
from ..ids import canonical_json
from ..models import CodeEdge, CodeIndex, Flow, Gap, Link, Requirement, ScreenState, Transition
from ..redact import redact_text
from ..run import RunContext
from ..trace import Tracer, now_iso
from . import queries, writes
from .canonical import canonicalise, match_route

SCHEMA_PATH = Path(__file__).with_name("schema.cypher")
CHUNK_SIZE = 500
READ_TIMEOUT_S = 30.0

LABELS = (
    "Requirement",
    "Screen",
    "UIElement",
    "Flow",
    "CodeFile",
    "I18nKey",
    "Route",
    "PullRequest",
    "Gap",
    "Run",
)
REL_TYPES = (
    "APPEARS_ON",
    "LEADS_TO",
    "STEP",
    "STARTS_AT",
    "ENDS_AT",
    "REALIZED_BY",
    "IMPLEMENTED_BY",
    "SERVED_BY",
    "HANDLED_BY",
    "IMPORTS",
    "RENDERS",
    "USES_KEY",
    "HAS_GAP",
    "SEARCHED",
)
LAYERS = ("code", "crawl", "spec", "links")

# A dedicated read code is a contract request (failures.py is shared). Until it exists a failed read is
# recorded as GRAPH_WRITE_FAILED with a "read <template>" message and is told apart by GraphReadError.
READ_FAILED = getattr(FailureCode, "GRAPH_READ_FAILED", FailureCode.GRAPH_WRITE_FAILED)

_USERINFO = re.compile(r"//[^/?#\s]*@")  # greedy: userinfo ends at the LAST '@' before the host
_SAMPLE = 5  # how many offending ids a failure message names


class GraphError(RuntimeError):
    code: FailureCode = FailureCode.GRAPH_WRITE_FAILED
    neo4j_code: str | None = None  # the server's status code when the driver supplied one


class GraphReadError(GraphError):
    code = READ_FAILED


class GraphConnectionError(GraphError):
    code = FailureCode.GRAPH_CONN_FAILED


# ---------------------------------------------------------------------------------------------- helpers
@dataclass
class _Tally:
    nodes_created: int = 0
    relationships_created: int = 0
    nodes_deleted: int = 0
    relationships_deleted: int = 0

    def add(self, counters: Any) -> None:
        self.nodes_created += counters.nodes_created
        self.relationships_created += counters.relationships_created
        self.nodes_deleted += counters.nodes_deleted
        self.relationships_deleted += counters.relationships_deleted

    def as_dict(self) -> dict[str, int]:
        return {"nodes_created": self.nodes_created, "relationships_created": self.relationships_created}

    def with_deleted(self) -> dict[str, int]:
        return {**self.as_dict(), "relationships_deleted": self.relationships_deleted}


def _safe_uri(uri: str) -> str:
    """`scheme://host[:port]`, no userinfo, path or query. Total: never raises, never echoes the input."""
    try:
        parts = urlsplit(uri)
        host, port = parts.hostname, parts.port
    except ValueError:
        return "<unparseable NEO4J_URI>"
    if not parts.scheme or not host:
        return "<invalid NEO4J_URI>"
    shown = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{shown}" + (f":{port}" if port else "")


def _uri_password(uri: str) -> str:
    try:
        return urlsplit(uri).password or ""
    except ValueError:
        return ""


def _scrub(text: str, *secrets: str) -> str:
    """Remove URI userinfo and known secret values from text bound for a message or a failure record."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return redact_text(_USERINFO.sub("//", text))


def _one_target(items: Sequence[Any], explicit: str | None, fallback: str | None, what: str) -> str:
    targets = {i.target for i in items if hasattr(i, "target")}
    if len(targets) > 1:
        raise ValueError(f"{what}: rows belong to several targets {sorted(targets)}")
    target = explicit or (targets.pop() if targets else None) or fallback
    if not target:
        raise ValueError(f"{what}: cannot determine the target; pass target=...")
    return target


def _total(rows: Iterable[Mapping[str, Any]], key: str) -> int:
    return sum(int(r[key]) for r in rows)


def _is_package(path: str) -> bool:
    return "." not in path.rsplit("/", 1)[-1]


def _merge_edges(edges: Iterable[CodeEdge]) -> dict[str, list[dict[str, Any]]]:
    """One row per (kind, src, dst): symbols are unioned (sorted) and the smallest `via` kept, so no MERGE
    loses a row and the stored values do not depend on the order edges arrived in."""
    slots: dict[str, dict[tuple[str, str], dict[str, Any]]] = {"IMPORTS": {}, "RENDERS": {}}
    for e in edges:
        slot = slots[e.kind].setdefault(
            (e.src, e.dst), {"src": e.src, "dst": e.dst, "symbols": set(), "via": None}
        )
        slot["symbols"].update(e.symbols)
        slot["via"] = min((v for v in (slot["via"], e.via) if v is not None), default=None)
    return {
        kind: [{**s, "symbols": sorted(s["symbols"])} for _, s in sorted(by_pair.items())]
        for kind, by_pair in slots.items()
    }


def schema_statements() -> list[str]:
    """The statements of schema.cypher: `//` comment lines dropped, split on `;`."""
    lines = [ln for ln in SCHEMA_PATH.read_text().splitlines() if not ln.strip().startswith("//")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def _sample(items: Iterable[str]) -> str:
    """A bounded, sorted list of offending ids for a failure message."""
    distinct = sorted(set(items))
    return ", ".join(distinct[:_SAMPLE]) + (" ..." if len(distinct) > _SAMPLE else "")


# ------------------------------------------------------------------------------------------------ store
class GraphStore:
    def __init__(
        self,
        driver: neo4j.Driver,
        *,
        run: RunContext | None = None,
        target: str | None = None,
        database: str | None = None,
        chunk_size: int = CHUNK_SIZE,
        read_timeout_s: float = READ_TIMEOUT_S,
        secrets: Sequence[str] = (),
    ):
        self.driver = driver
        self._secrets = tuple(s for s in secrets if s)  # scrubbed from every driver error text
        self.run = run
        self.target = target or (run.target if run else None)
        self.database = database
        self.chunk_size = max(1, chunk_size)
        self.read_timeout_s = read_timeout_s
        self._tracer = run.tracer if run else Tracer.null()

    # ---- construction ------------------------------------------------------------------------
    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        run: RunContext | None = None,
        *,
        attempts: int = 3,
        backoff_s: float = 0.5,
        chunk_size: int = CHUNK_SIZE,
        target: str | None = None,
        database: str | None = None,
    ) -> GraphStore:
        """Connect with bounded retries. A bad password is not retried. Failure raises
        GraphConnectionError (FailureCode.GRAPH_CONN_FAILED) and is recorded on the run with the number of
        attempts actually made; neither the URI's userinfo nor the password appears in the message."""
        last: Exception | None = None
        made = 0
        for attempt in range(max(1, attempts)):
            made += 1
            driver = None
            try:
                driver = GraphDatabase.driver(
                    settings.neo4j_uri,
                    auth=(settings.neo4j_user, settings.neo4j_password),
                    connection_timeout=5.0,
                    notifications_disabled_classifications=["UNRECOGNIZED"],
                )
                driver.verify_connectivity()
                return cls(
                    driver,
                    run=run,
                    target=target,
                    database=database,
                    chunk_size=chunk_size,
                    secrets=(settings.neo4j_password, _uri_password(settings.neo4j_uri)),
                )
            except AuthError as exc:
                last = exc
                if driver is not None:
                    driver.close()
                break
            except (DriverError, Neo4jError, OSError, ValueError) as exc:
                last = exc
                if driver is not None:
                    driver.close()
                if attempt + 1 < attempts:
                    time.sleep(backoff_s * (2**attempt))
        uri = _safe_uri(settings.neo4j_uri)
        secrets = (settings.neo4j_password, _uri_password(settings.neo4j_uri))
        detail = f"{type(last).__name__}: {_scrub(str(last), *secrets)[:160]}"
        message = f"cannot connect to Neo4j at {uri} ({detail}). {META[FailureCode.GRAPH_CONN_FAILED][2]}"
        if run is not None:
            run.failures.record(FailureCode.GRAPH_CONN_FAILED, message, uri=uri, attempts=made)
        raise GraphConnectionError(message) from last

    def close(self) -> None:
        self.driver.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- plumbing ----------------------------------------------------------------------------
    def _target(self, explicit: str | None) -> str:
        target = explicit or self.target
        if not target:
            raise ValueError("no target: pass target=... or build the store with a RunContext")
        return target

    def _error(self, exc: Exception, what: str, *, read: bool = False) -> GraphError:
        """Map a driver error to our closed vocabulary (not yet recorded: see `_record`)."""
        connection = isinstance(exc, (ServiceUnavailable, SessionExpired))
        neo_code = getattr(exc, "code", None)
        message = f"{what}: {type(exc).__name__}: {_scrub(str(exc), *self._secrets)[:200]}"
        if neo_code and neo_code.endswith("MemoryPoolOutOfMemoryError"):
            message += " (the query exceeded the server's transaction memory limit: fewer seeds or hops)"
        cls = GraphConnectionError if connection else GraphReadError if read else GraphError
        error = cls(message)
        error.neo4j_code = neo_code if isinstance(neo_code, str) else None
        return error

    def _record(self, error: GraphError) -> GraphError:
        if self.run is not None:
            self.run.failures.record(error.code, str(error))
        return error

    def _write(
        self, work: Callable[[neo4j.ManagedTransaction], Any], what: str, *, record: bool = True
    ) -> Any:
        try:
            with self.driver.session(
                database=self.database, default_access_mode=neo4j.WRITE_ACCESS
            ) as session:
                return session.execute_write(work)
        except (DriverError, Neo4jError) as exc:
            error = self._error(exc, what)
            raise (self._record(error) if record else error) from exc

    def _run_group(
        self,
        statements: Sequence[str],
        rows: Sequence[Mapping[str, Any]],
        tally: _Tally,
        what: str,
        **params: Any,
    ) -> list[list[dict[str, Any]]]:
        """Run `statements` over `rows` in chunks, every statement of a chunk in ONE write transaction (all or
        nothing). Returns, per statement, the concatenated result rows."""
        out: list[list[dict[str, Any]]] = [[] for _ in statements]
        for start in range(0, len(rows), self.chunk_size):
            chunk = list(rows[start : start + self.chunk_size])

            def work(tx: neo4j.ManagedTransaction, chunk: list = chunk) -> list[tuple[list, Any]]:
                done = []
                for cypher in statements:
                    result = tx.run(cypher, rows=chunk, **params)
                    done.append((result.data(), result.consume().counters))
                return done

            for index, (data, counters) in enumerate(self._write(work, what)):
                tally.add(counters)
                out[index].extend(data)
        return out

    def _run_rows(
        self, cypher: str, rows: Sequence[Mapping[str, Any]], tally: _Tally, what: str, **params: Any
    ) -> list[dict[str, Any]]:
        """Run one statement over `rows` in chunks; return the concatenated result rows."""
        return self._run_group([cypher], rows, tally, what, **params)[0]

    def _run_once(self, cypher: str, tally: _Tally, what: str, **params: Any) -> list[dict[str, Any]]:
        def work(tx: neo4j.ManagedTransaction) -> tuple[list[dict[str, Any]], Any]:
            result = tx.run(cypher, **params)
            return result.data(), result.consume().counters

        data, counters = self._write(work, what)
        tally.add(counters)
        return data

    def _stamp_run(self, target: str, run_id: str, layer: str, props: Mapping[str, Any]) -> None:
        self._run_once(
            writes.RUN,
            _Tally(),
            "run node",
            target=target,
            run_id=run_id,
            layer=layer,
            now=now_iso(),
            props={k: v for k, v in props.items() if v is not None},
        )

    def _aliases(self, target: str) -> dict[str, str]:
        """Crawl-time observation id -> canonical UIElement id, from the graph."""
        out: dict[str, str] = {}
        for row in self.read("element_aliases", target=target):
            for obs_id in row["obs_ids"]:
                out[obs_id] = row["id"]
        return out

    def _failed(self, message: str, **context: Any) -> None:
        if self.run is not None:
            self.run.failures.record(FailureCode.GRAPH_WRITE_FAILED, message, **context)

    # ---- schema and lifecycle ----------------------------------------------------------------
    def ensure_schema(self) -> int:
        """Apply schema.cypher. Idempotent. Returns the number of statements executed. Two processes applying
        it at once may collide on `AlreadyExists`: that is tolerated and is not a failure of the run."""
        statements = schema_statements()
        with self._tracer.span("graph_write", "ensure_schema", statements=len(statements)):
            for statement in statements:

                def work(tx: neo4j.ManagedTransaction, statement: str = statement) -> None:
                    tx.run(statement).consume()

                try:
                    self._write(work, "ensure_schema", record=False)
                except GraphError as exc:
                    if "AlreadyExists" in (exc.neo4j_code or ""):
                        continue
                    self._record(exc)
                    raise
        return len(statements)

    def clear_target(self, target: str) -> int:
        """Delete every node (and relationship) of one target, in batches per label (each batch is an index
        seek on the label's `target` index). Other targets are untouched. A final label-less sweep removes
        nodes of a label this module does not know; it finds nothing in a normal graph."""
        total = 0
        batch = self.chunk_size * 10
        with self._tracer.span("graph_write", "clear_target", target=target) as sp:
            for label in LABELS:
                while True:
                    tally = _Tally()
                    self._run_once(
                        writes.DELETE_BY_LABEL[label], tally, "clear_target", target=target, batch=batch
                    )
                    total += tally.nodes_deleted
                    if tally.nodes_deleted == 0:
                        break
            while True:
                tally = _Tally()
                self._run_once(writes.DELETE_ANY, tally, "clear_target", target=target, batch=batch)
                total += tally.nodes_deleted
                if tally.nodes_deleted == 0:
                    break
            sp.set(nodes_deleted=total)
        return total

    def counts(self, target: str | None = None) -> dict[str, int]:
        """Node counts keyed by label (CamelCase) and relationship counts keyed by type (UPPER_CASE)."""
        target = self._target(target)
        out = {name: 0 for name in (*LABELS, *REL_TYPES)}
        for row in self.read("counts_by_label", target=target):
            out[row["label"]] = row["n"]
        for row in self.read("counts_by_rel", target=target):
            out[row["type"]] = row["n"]
        return out

    # ---- reads -------------------------------------------------------------------------------
    def read(self, template: str, **params: Any) -> list[dict[str, Any]]:
        """Run a named template (queries.TEMPLATES or any queries_*.py extension) in one read transaction.

        `target` defaults to the store's target. Unknown templates and unknown or invalid parameters raise
        before any I/O. One attempt only: a memory-pool or timeout error raises `GraphReadError` at once
        instead of going through the driver's retry loop."""
        tpl = queries.get(template)
        if "target" in tpl.params and "target" not in params and self.target:
            params["target"] = self.target
        bound = tpl.bind(params)
        with self._tracer.span("cypher_query", template, target=bound.get("target")) as sp:
            try:
                session = self.driver.session(database=self.database, default_access_mode=neo4j.READ_ACCESS)
                with session, session.begin_transaction(timeout=self.read_timeout_s) as tx:
                    rows = tx.run(tpl.cypher, bound).data()
            except (DriverError, Neo4jError) as exc:
                raise self._record(self._error(exc, f"read {template}", read=True)) from exc
            if tpl.post is not None:
                rows = tpl.post(rows)
            sp.set(rows=len(rows))
        return rows

    # ---- writes: code ------------------------------------------------------------------------
    def write_code(self, index: CodeIndex, run_id: str) -> dict[str, Any]:
        """CodeFile, IMPORTS/RENDERS, Route + HANDLED_BY, I18nKey + USES_KEY for one code snapshot.

        Edge or key-use endpoints that are not in `index.files` become stub CodeFile nodes (stub=true,
        kind `external` for a bare package name) so hub edges to packages such as @hoppscotch/data are not
        lost. Literals are not loaded (not in the plan's schema; the linker reads code/index.json).
        """
        target, tally = index.target, _Tally()
        with self._tracer.span(
            "graph_write", "write_code", target=target, files=len(index.files), edges=len(index.edges)
        ) as sp:
            file_rows = {
                f.path: {
                    "path": f.path,
                    "props": {
                        "kind": f.kind.value,
                        "loc": f.loc,
                        "sha": f.sha,
                        "package": f.package,
                        "stub": None,
                    },
                }
                for f in index.files
            }
            merged = _merge_edges(index.edges)
            uses: dict[tuple[str, str], dict[str, Any]] = {}
            for u in index.key_uses:
                slot = uses.setdefault(
                    (u.file, u.key), {"file": u.file, "key": u.key, "dynamic": False, "uses": 0}
                )
                slot["dynamic"] = slot["dynamic"] or u.dynamic
                slot["uses"] += 1
            routes: dict[str, dict[str, Any]] = {}
            for r in index.routes:
                slot = routes.setdefault(
                    r.pattern, {"pattern": r.pattern, "file": r.file, "layout": r.layout, "files": []}
                )
                slot["layout"] = slot["layout"] or r.layout
                if r.file not in slot["files"]:
                    slot["files"].append(r.file)
            referenced = (
                {e["src"] for rows in merged.values() for e in rows}
                | {e["dst"] for rows in merged.values() for e in rows}
                | {k[0] for k in uses}
                | {r["file"] for r in routes.values()}
            )
            stubs = sorted(p for p in referenced if p not in file_rows)
            for path in stubs:
                file_rows[path] = {
                    "path": path,
                    "props": {
                        "kind": "external" if _is_package(path) else "source_other",
                        "loc": 0,
                        "sha": None,
                        "package": None,
                        "stub": True,
                    },
                }
            keys = dict(index.i18n)
            missing_keys = sorted({k[1] for k in uses} - set(keys))
            key_rows = [{"key": k, "en": v} for k, v in keys.items()] + [
                {"key": k, "en": None} for k in missing_keys
            ]
            common = {"target": target, "run_id": run_id, "snapshot_id": index.snapshot_id}
            self._run_rows(writes.CODE_FILE, list(file_rows.values()), tally, "write_code files", **common)
            self._run_rows(writes.IMPORTS, merged["IMPORTS"], tally, "write_code imports", **common)
            self._run_rows(writes.RENDERS, merged["RENDERS"], tally, "write_code renders", **common)
            self._run_rows(writes.ROUTE, list(routes.values()), tally, "write_code routes", **common)
            handled = [{"pattern": r["pattern"], "file": f} for r in routes.values() for f in r["files"]]
            self._run_rows(writes.HANDLED_BY, handled, tally, "write_code handled_by", **common)
            self._run_rows(writes.I18N_KEY, key_rows, tally, "write_code i18n", **common)
            self._run_rows(writes.USES_KEY, list(uses.values()), tally, "write_code uses_key", **common)
            self._stamp_run(
                target,
                run_id,
                "code",
                {
                    "snapshot_id": index.snapshot_id,
                    "repo": index.repo,
                    "ref": index.ref,
                    "adapter": index.adapter,
                    "code_stats": canonical_json(index.stats),
                },
            )
            sp.set(**tally.as_dict())
        return {
            "files": len(index.files),
            "stub_files": len(stubs),
            "imports": len(merged["IMPORTS"]),
            "renders": len(merged["RENDERS"]),
            "routes": len(routes),
            "i18n_keys": len(key_rows),
            "key_uses": len(uses),
            "i18n_keys_without_value": len(missing_keys),
            **tally.as_dict(),
        }

    # ---- writes: crawl -----------------------------------------------------------------------
    def write_crawl(
        self,
        screens: Sequence[ScreenState],
        transitions: Sequence[Transition],
        run_id: str,
        bundle_hash: str | None,
        *,
        target: str | None = None,
        snapshot_id: str | None = None,
        bundle_hash_end: str | None = None,
    ) -> dict[str, Any]:
        """Screens, canonical UIElements (case 46) with APPEARS_ON, and LEADS_TO for transitions.

        A transition whose element_id resolves (through the observation-id alias) becomes
        (UIElement)-[:LEADS_TO]->(Screen); one with no element, or an unresolvable one, becomes
        (Screen)-[:LEADS_TO]->(Screen) from `from_screen`. `from_screen` is kept on the edge.
        """
        target = _one_target(screens, target, self.target, "write_crawl")
        snapshot_id = snapshot_id or bundle_hash or ""
        tally = _Tally()
        with self._tracer.span(
            "graph_write", "write_crawl", target=target, screens=len(screens), transitions=len(transitions)
        ) as sp:
            canon = canonicalise(screens, target)
            screen_rows = [
                {
                    "id": s.id,
                    "props": {
                        "url": s.url,
                        "url_pattern": s.url_pattern,
                        "title": s.title,
                        "kind": s.kind,
                        "fingerprint": s.fingerprint,
                        "dialog_title": s.dialog_title,
                        "active_tabs": list(s.active_tabs),
                        "screenshot": s.screenshot,
                        "dom_path": s.dom_path,
                        "capture_id": s.capture_id,
                        "first_seen_action": s.first_seen_action,
                        "depth": s.depth,
                    },
                }
                for s in sorted(screens, key=lambda s: s.id)
            ]
            common = {
                "target": target,
                "run_id": run_id,
                "bundle_hash": bundle_hash,
                "snapshot_id": snapshot_id,
            }
            self._run_rows(writes.SCREEN, screen_rows, tally, "write_crawl screens", **common)
            self._run_rows(writes.ELEMENT, canon.elements, tally, "write_crawl elements", **common)
            appears = self._run_rows(
                writes.APPEARS_ON, canon.appearances, tally, "write_crawl appears_on", **common
            )
            alias = {**self._aliases(target), **canon.alias}
            by_element: list[dict[str, Any]] = []
            by_screen: list[dict[str, Any]] = []
            unresolved_elements = 0
            for t in sorted(transitions, key=lambda t: t.id):
                row = {
                    "tid": t.id,
                    "dst": t.to_screen,
                    "from_screen": t.from_screen,
                    "action": t.action,
                    "observed": t.observed,
                    "blocked": t.blocked,
                    "label": t.label,
                    "reason": t.reason,
                }
                element = alias.get(t.element_id) if t.element_id else None
                if element is not None:
                    by_element.append({**row, "src": element})
                    continue
                unresolved_elements += 1 if t.element_id else 0
                by_screen.append({**row, "src": t.from_screen})
            via_element = self._run_rows(
                writes.LEADS_TO_ELEMENT, by_element, tally, "write_crawl leads_to", **common
            )
            via_screen = self._run_rows(
                writes.LEADS_TO_SCREEN, by_screen, tally, "write_crawl leads_to", **common
            )
            matched = _total(via_element, "matched") + _total(via_screen, "matched")
            self._stamp_run(
                target,
                run_id,
                "crawl",
                {
                    "bundle_hash": bundle_hash,
                    "bundle_hash_end": bundle_hash_end,
                    "crawl_snapshot_id": snapshot_id,
                },
            )
            sp.set(**tally.as_dict())
        return {
            "screens": len(screens),
            "observations": canon.observations,
            "elements": len(canon.elements),
            "appearances": len(canon.appearances),
            "appearances_matched": _total(appears, "matched"),
            "transitions": len(transitions),
            "transitions_element_level": len(by_element),
            "transitions_screen_level": len(by_screen),
            "transitions_element_unresolved": unresolved_elements,
            "transitions_skipped": len(transitions) - matched,
            **tally.as_dict(),
        }

    def link_routes(self, run_id: str, *, target: str | None = None) -> dict[str, Any]:
        """(Screen)-[:SERVED_BY]->(Route): match each Screen's url_pattern to a Route pattern (exact, then
        parameterised, then catch-all). Computed from what is in the graph, so it is independent of which
        artifact was loaded last. Replaces SERVED_BY edges written by an earlier run."""
        target = self._target(target)
        tally = _Tally()
        with self._tracer.span("graph_write", "link_routes", target=target):
            screens = self.read("screen_url_patterns", target=target)
            patterns = [r["pattern"] for r in self.read("route_patterns", target=target)]
            rows = []
            for s in screens:
                hit = match_route(s["url_pattern"] or "", patterns)
                if hit is not None:
                    rows.append({"screen": s["id"], "pattern": hit[0], "how": hit[1]})
            self._run_rows(writes.SERVED_BY, rows, tally, "link_routes", target=target, run_id=run_id)
            self._run_once(writes.SERVED_BY_STALE, tally, "link_routes prune", target=target, run_id=run_id)
        return {
            "screens": len(screens),
            "served": len(rows),
            "unserved": len(screens) - len(rows),
            **tally.as_dict(),
        }

    # ---- writes: spec ------------------------------------------------------------------------
    def write_requirements(
        self, reqs: Sequence[Requirement], run_id: str, *, target: str | None = None
    ) -> dict[str, Any]:
        target = _one_target(reqs, target, self.target, "write_requirements")
        tally = _Tally()
        with self._tracer.span("graph_write", "write_requirements", target=target, rows=len(reqs)) as sp:
            rows = [
                {
                    "id": r.id,
                    "props": {
                        "text": r.text,
                        "quote": r.quote,
                        "source_url": r.source_url,
                        "section": r.section,
                        "doc_version": r.doc_version,
                        "edition": r.edition,
                        "polarity": r.polarity,
                        "ui_observable": r.ui_observable,
                        "requires_login": r.requires_login,
                        "source_kind": r.source_kind,
                        "tags": list(r.tags),
                        "spec_sha": r.spec_sha,
                    },
                }
                for r in sorted(reqs, key=lambda r: r.id)
            ]
            self._run_rows(
                writes.REQUIREMENT, rows, tally, "write_requirements", target=target, run_id=run_id
            )
            self._stamp_run(target, run_id, "spec", {"spec_shas": sorted({r.spec_sha for r in reqs})})
            sp.set(**tally.as_dict())
        return {"requirements": len(reqs), **tally.as_dict()}

    # ---- writes: links, gaps, flows (wave 2) -------------------------------------------------
    def _link_rows(
        self, links: Sequence[Link], alias: Mapping[str, str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        """Validate and collapse links into IMPLEMENTED_BY and REALIZED_BY rows, order-independently.

        A link is rejected when its confidence is outside [0, 1] (NaN included) or, with a run bound, when its
        evidence id is not in the run's ledger. Links for one (kind, src, dst, method) collapse to the one
        with the highest confidence (ties: smallest evidence id, then judge); their screen_ids are unioned,
        and an empty list ("every screen") absorbs the rest."""
        chosen: dict[tuple[str, str, str, str], Link] = {}
        screens: dict[tuple[str, str, str, str], set[str] | None] = {}
        bad_confidence: list[str] = []
        bad_evidence: list[str] = []
        merged = 0
        for link in links:
            if not 0.0 <= link.confidence <= 1.0:
                bad_confidence.append(f"{link.src}->{link.dst}={link.confidence}")
                continue
            if self.run is not None and not self.run.evidence.exists(link.evidence_id):
                bad_evidence.append(link.evidence_id)
                continue
            implemented = link.kind == "IMPLEMENTED_BY"
            src = alias.get(link.src, link.src) if implemented else link.src
            dst = link.dst if implemented else alias.get(link.dst, link.dst)
            key = (link.kind, src, dst, link.method)
            if key in chosen:
                merged += 1
            incumbent = chosen.get(key)
            rank = (-link.confidence, link.evidence_id, link.judge or "")
            if incumbent is None or rank < (
                -incumbent.confidence,
                incumbent.evidence_id,
                incumbent.judge or "",
            ):
                chosen[key] = link
            if key in screens:
                union = screens[key]
                screens[key] = None if union is None or not link.screen_ids else union | set(link.screen_ids)
            else:
                screens[key] = set(link.screen_ids) if link.screen_ids else None
        impl: list[dict[str, Any]] = []
        real: list[dict[str, Any]] = []
        for key in sorted(chosen):
            link, kind = chosen[key], key[0]
            row = {
                "src": key[1],
                "dst": key[2],
                "method": key[3],
                "confidence": link.confidence,
                "evidence_id": link.evidence_id,
                "judge": link.judge,
                "screen_ids": sorted(screens[key] or ()),
            }
            (impl if kind == "IMPLEMENTED_BY" else real).append(row)
        stats = {
            "duplicates_merged": merged,
            "skipped_invalid_confidence": len(bad_confidence),
            "skipped_unknown_evidence": len(bad_evidence),
        }
        if bad_confidence:
            self._failed(
                f"write_links: {len(bad_confidence)} links not written, confidence outside [0, 1]: "
                f"{_sample(bad_confidence)}",
                skipped=len(bad_confidence),
            )
        if bad_evidence:
            self._failed(
                f"write_links: {len(bad_evidence)} links not written, evidence ids are not in the run's "
                f"ledger: {_sample(bad_evidence)}",
                skipped=len(bad_evidence),
            )
        return impl, real, stats

    def write_links(self, links: Sequence[Link], run_id: str, *, target: str | None = None) -> dict[str, Any]:
        """REALIZED_BY (Requirement -> UIElement|Screen|Flow) and IMPLEMENTED_BY (UIElement -> CodeFile),
        one relationship per (src, dst, method). UIElement ids may be crawl observation ids. After
        IMPLEMENTED_BY writes, `owner_file` is set to the element's highest-confidence file.

        `Link.screen_ids` is stored as a sorted unique list on the edge ([] = every screen of the element):
        within one run a rewrite unions it, a different run re-asserts it. Invalid links are counted
        (`skipped_invalid_confidence`, `skipped_unknown_evidence`), endpoints that are not in the graph in
        `skipped_missing_src` / `skipped_missing_dst`; each is also recorded as a failure on the run."""
        target, tally = self._target(target), _Tally()
        with self._tracer.span("graph_write", "write_links", target=target, rows=len(links)) as sp:
            impl, real, stats = self._link_rows(links, self._aliases(target))
            common = {"target": target, "run_id": run_id}
            impl_res = self._run_group(
                [writes.IMPLEMENTED_BY, writes.IMPLEMENTED_BY_SORT],
                impl,
                tally,
                "write_links implemented_by",
                **common,
            )[0]
            real_res = self._run_group(
                [writes.REALIZED_BY, writes.REALIZED_BY_SORT],
                real,
                tally,
                "write_links realized_by",
                **common,
            )[0]
            ids = sorted({r["src"] for r in impl})
            for start in range(0, len(ids), self.chunk_size):
                self._run_once(
                    writes.OWNER_FILE,
                    tally,
                    "write_links owner_file",
                    target=target,
                    ids=ids[start : start + self.chunk_size],
                )
            sp.set(**tally.as_dict())
        no_src = _total(impl_res, "missing_src") + _total(real_res, "missing_src")
        no_dst = _total(impl_res, "missing_dst") + _total(real_res, "missing_dst")
        if no_src or no_dst:
            pairs = [f"{a} -> {b}" for r in (*impl_res, *real_res) for a, b in r["missing"]]
            self._failed(
                f"write_links: {no_src} sources and {no_dst} destinations are not in the graph, so their "
                f"links were not written: {_sample(pairs)}",
                missing_src=no_src,
                missing_dst=no_dst,
            )
        return {
            "implemented_by": len(impl) - _total(impl_res, "missing_src") - _total(impl_res, "missing_dst"),
            "realized_by": len(real) - _total(real_res, "missing_src") - _total(real_res, "missing_dst"),
            "skipped_missing_src": no_src,
            "skipped_missing_dst": no_dst,
            **stats,
            **tally.as_dict(),
        }

    def write_gaps(self, gaps: Sequence[Gap], run_id: str, *, target: str | None = None) -> dict[str, Any]:
        """Gap nodes keyed by (id, bundle_hash) with HAS_GAP from the subject and SEARCHED to each screen.
        A missing bundle_hash is stored as "" (bundle-independent, e.g. NOT_UI_TESTABLE).

        Each gap and its edges are written in one transaction: SEARCHED edges to screens the gap no longer
        lists are deleted (counted in `relationships_deleted`), the others are merged, so an identical
        rewrite creates and deletes nothing and keeps `first_run_id`."""
        target, tally = self._target(target), _Tally()
        with self._tracer.span("graph_write", "write_gaps", target=target, rows=len(gaps)) as sp:
            alias = self._aliases(target)
            rows: dict[tuple[str, str], dict[str, Any]] = {}
            for g in sorted(gaps, key=lambda g: (g.id, g.bundle_hash or "", g.model_dump_json())):
                bundle = g.bundle_hash or ""
                rows[(g.id, bundle)] = {
                    "id": g.id,
                    "bundle_hash": bundle,
                    "subject": alias.get(g.subject_id, g.subject_id),
                    "screens": list(dict.fromkeys(g.screens_searched)),
                    "props": {
                        "kind": g.kind.value,
                        "status": g.status,
                        "subject_id": g.subject_id,
                        "reason": g.reason,
                        "confidence": g.confidence,
                        "actions_spent": g.actions_spent,
                        "evidence_id": g.evidence_id,
                        "screens_searched": list(g.screens_searched),
                    },
                }
            ordered = [rows[k] for k in sorted(rows)]
            _, attached, _, found = self._run_group(
                [writes.GAP, writes.HAS_GAP, writes.SEARCHED_PRUNE, writes.SEARCHED],
                ordered,
                tally,
                "write_gaps",
                target=target,
                run_id=run_id,
            )
            sp.set(**tally.as_dict())
        wanted = sum(len(r["screens"]) for r in ordered)
        return {
            "gaps": len(gaps),
            "subjects_unresolved": _total(attached, "unresolved"),
            "searched": _total(found, "matched"),
            "searched_unresolved": wanted - _total(found, "matched"),
            **tally.with_deleted(),
        }

    def write_flows(self, flows: Sequence[Flow], run_id: str, *, target: str | None = None) -> dict[str, Any]:
        """Flow nodes, (Flow)-[:STEP {order}]->(UIElement), STARTS_AT/ENDS_AT to the first/last Screen.

        A flow is replaced as a unit and atomically: STEP/STARTS_AT/ENDS_AT edges it no longer asserts are
        deleted (counted in `relationships_deleted`), the others are merged, so an identical rewrite creates
        and deletes nothing and keeps `first_run_id`."""
        target, tally = self._target(target), _Tally()
        with self._tracer.span("graph_write", "write_flows", target=target, rows=len(flows)) as sp:
            alias = self._aliases(target)
            rows: dict[str, dict[str, Any]] = {}
            for f in sorted(flows, key=lambda f: (f.id, f.model_dump_json())):
                steps = [{"order": i, "element": alias.get(e, e)} for i, e in enumerate(f.steps)]
                rows[f.id] = {
                    "id": f.id,
                    "props": {
                        "name": f.name,
                        "goal": f.goal,
                        "n_steps": len(f.steps),
                        "n_screens": len(f.screens),
                    },
                    "steps": steps,
                    "keep": [[s["order"], s["element"]] for s in steps],
                    "start": f.screens[0] if f.screens else None,
                    "end": f.screens[-1] if f.screens else None,
                }
            ordered = [rows[k] for k in sorted(rows)]
            _, _, step_res, _, start_res, end_res = self._run_group(
                [
                    writes.FLOW,
                    writes.STEP_PRUNE,
                    writes.STEP,
                    writes.ENDPOINT_PRUNE,
                    writes.STARTS_AT,
                    writes.ENDS_AT,
                ],
                ordered,
                tally,
                "write_flows",
                target=target,
                run_id=run_id,
            )
            sp.set(**tally.as_dict())
        n_steps = sum(len(r["steps"]) for r in ordered)
        n_ends = sum(1 for r in ordered if r["start"] is not None) + sum(
            1 for r in ordered if r["end"] is not None
        )
        return {
            "flows": len(flows),
            "steps": n_steps,
            "steps_unresolved": n_steps - _total(step_res, "matched"),
            "screens_unresolved": n_ends - _total(start_res, "matched") - _total(end_res, "matched"),
            **tally.with_deleted(),
        }

    # ---- reconcile ---------------------------------------------------------------------------
    def reset_dangling_attribution(self, *, target: str | None = None) -> dict[str, int]:
        """Neutralise per-screen attribution that names Screens which no longer exist.

        For every IMPLEMENTED_BY / REALIZED_BY edge of the target whose `screen_ids` lists a Screen id that is
        not in the graph, keep only the ids that are, and store [] (every screen of the element: the safe,
        over-approximating direction) when none are left. Edges with [] and edges whose ids all exist are not
        touched, `run_id` and every other property are kept, and a second call changes nothing. Returns
        `edges` (changed), `reset_to_all` (left with no live id), `narrowed` (some live ids kept) and
        `ids_dropped`."""
        target = self._target(target)
        total = {"edges": 0, "reset_to_all": 0, "ids_dropped": 0}
        with self._tracer.span("graph_write", "reset_dangling_attribution", target=target) as sp:
            for name, cypher in (
                ("IMPLEMENTED_BY", writes.DANGLING_IMPLEMENTED_BY),
                ("REALIZED_BY", writes.DANGLING_REALIZED_BY),
            ):
                rows = self._run_once(cypher, _Tally(), f"reset_dangling_attribution {name}", target=target)
                for key in total:
                    total[key] += int(rows[0][key] or 0) if rows else 0
            sp.set(**total)
        return {**total, "narrowed": total["edges"] - total["reset_to_all"]}

    def prune_stale(self, run_id: str, layers: Iterable[str], *, target: str | None = None) -> dict[str, int]:
        """Delete nodes and relationships of the given layers (code, crawl, spec, links) that carry another
        run's `run_id`, i.e. what this run did not re-assert. Link-stage edges on a deleted node go with
        it; `method = 'human'` links are never pruned by the `links` layer. Nodes without a run_id are
        never touched. Call after the layer's write succeeded, and only for a layer whose artifact was
        complete (load.py decides)."""
        target, layers = self._target(target), list(layers)
        out: dict[str, int] = {}
        detached = 0
        with self._tracer.span("graph_write", "prune_stale", target=target, layers=layers) as sp:
            for layer in layers:
                if layer not in writes.PRUNE:
                    raise ValueError(f"unknown layer {layer!r}; expected one of {LAYERS}")
                for name, cypher in writes.PRUNE[layer]:
                    tally = _Tally()
                    self._run_once(cypher, tally, f"prune_stale {name}", target=target, run_id=run_id)
                    is_node = name in LABELS
                    out[name] = out.get(name, 0) + (
                        tally.nodes_deleted if is_node else tally.relationships_deleted
                    )
                    if is_node:
                        detached += tally.relationships_deleted
            out["detached_relationships"] = detached
            sp.set(removed=sum(out.values()))
        return out
