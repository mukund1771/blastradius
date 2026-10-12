"""Graph module tests: GraphStore, read templates, canonicalisation, run_load.

Database tests need the Neo4j container from docker-compose.yml (bolt://localhost:7687) and skip with a
clear reason when it is unreachable. Every database test writes under a unique `__test__<random>` target
and removes exactly that target in a finally block; no other target's data is read or modified.
"""

from __future__ import annotations

import ast
import json
import os
import random
import types
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Self

import neo4j
import pytest

from blastradius.config import Settings
from blastradius.failures import FailureCode
from blastradius.graph import queries, writes
from blastradius.graph import store as store_module
from blastradius.graph.canonical import canonicalise, element_id, element_key, match_route
from blastradius.graph.load import run_load
from blastradius.graph.store import GraphConnectionError, GraphError, GraphStore
from blastradius.ids import stable_id
from blastradius.models import (
    CodeEdge,
    CodeFile,
    CodeIndex,
    CrawlSummary,
    FileClass,
    Flow,
    Gap,
    GapKind,
    KeyUse,
    Link,
    Requirement,
    RouteDef,
    ScreenState,
    Transition,
    UIElementObs,
)
from blastradius.run import RunContext
from blastradius.targets import TargetConfig

GRAPH_SRC = Path(__file__).resolve().parents[1] / "src" / "blastradius" / "graph"


def neo4j_settings() -> Settings:
    """Connection settings from the environment, without reading any .env file."""
    env = os.environ.get
    return Settings(
        neo4j_uri=env("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=env("NEO4J_USER", "neo4j"),
        neo4j_password=env("NEO4J_PASSWORD", "blastradius-local"),
    )


@pytest.fixture
def target() -> str:
    return f"__test__{uuid.uuid4().hex[:10]}"


@pytest.fixture
def store(target: str) -> Iterator[GraphStore]:
    try:
        s = GraphStore.from_settings(neo4j_settings(), attempts=1, target=target)
    except GraphConnectionError as exc:
        pytest.skip(f"Neo4j is not reachable: {exc}")
    s.ensure_schema()
    try:
        yield s
    finally:
        s.clear_target(target)
        s.close()


# ------------------------------------------------------------------------------------------ builders
def obs(oid: str, role: str, name: str, ordinal: int = 0, **kw: Any) -> UIElementObs:
    return UIElementObs(id=oid, role=role, name=name, ordinal=ordinal, **kw)


def screen(target: str, sid: str, pattern: str, elements: list[UIElementObs], **kw: Any) -> ScreenState:
    return ScreenState(
        id=sid,
        target=target,
        url=f"https://app.test{pattern}",
        url_pattern=pattern,
        fingerprint=f"fp_{sid}",
        capture_id=f"cap_{sid}",
        elements=elements,
        **kw,
    )


def code_file(path: str, kind: FileClass = FileClass.COMPONENT) -> CodeFile:
    return CodeFile(path=path, kind=kind, loc=10, sha=f"sha_{path}")


def edge(src: str, dst: str, kind: str = "IMPORTS", symbols: list[str] | None = None) -> CodeEdge:
    return CodeEdge(src=src, dst=dst, kind=kind, symbols=symbols or [])  # type: ignore[arg-type]


HUB = "src/data/hub.ts"


def hub_index(target: str) -> CodeIndex:
    """hub.ts <- A1..A5 (hop 1) <- B1..B3 (hop 2, B1 reaches hub through A1 AND A2) <- C1 (hop 3)."""
    files = [code_file(HUB, FileClass.DATA_SCHEMA), code_file("src/lib/unrelated.ts", FileClass.UTIL)]
    edges: list[CodeEdge] = []
    for i in range(1, 6):
        files.append(code_file(f"src/components/A{i}.vue"))
        edges.append(edge(f"src/components/A{i}.vue", HUB, symbols=["Hub", f"s{i}"]))
    for i in range(1, 4):
        files.append(code_file(f"src/pages/B{i}.vue", FileClass.PAGE))
    edges += [
        edge("src/pages/B1.vue", "src/components/A1.vue", "RENDERS"),
        edge("src/pages/B1.vue", "src/components/A2.vue", "RENDERS"),
        edge("src/pages/B2.vue", "src/components/A3.vue", "RENDERS"),
        edge("src/pages/B3.vue", "src/lib/unrelated.ts"),
    ]
    files.append(code_file("src/pages/C1.vue", FileClass.PAGE))
    edges.append(edge("src/pages/C1.vue", "src/pages/B1.vue", "RENDERS"))
    return CodeIndex(
        target=target,
        repo="o/r",
        ref="main",
        snapshot_id="snap1",
        adapter="vue",
        files=files,
        edges=edges,
        routes=[RouteDef(pattern="/one", file="src/pages/B1.vue")],
    )


def hub_screens(target: str) -> list[ScreenState]:
    one = screen(
        target,
        "scr_one",
        "/one",
        [
            obs("o_hub", "button", "Hub control"),
            obs("o_a1", "button", "A1"),
            obs("o_a2", "button", "A2"),
            obs("o_b1", "heading", "B1 title"),
        ],
    )
    two = screen(
        target,
        "scr_two",
        "/two",
        [obs("o_a2b", "button", "A2"), obs("o_c1", "heading", "C1 header"), obs("o_a3", "button", "A3")],
    )
    return [one, two]


def hub_links() -> list[Link]:
    def impl(src: str, path: str) -> Link:
        return Link(
            kind="IMPLEMENTED_BY",
            src=src,
            dst=path,
            confidence=0.9,
            method="i18n_key",
            evidence_id=f"ev_{src}",
        )

    return [
        impl("o_hub", HUB),
        impl("o_a1", "src/components/A1.vue"),
        impl("o_a2", "src/components/A2.vue"),
        impl("o_a3", "src/components/A3.vue"),
        impl("o_b1", "src/pages/B1.vue"),
        impl("o_c1", "src/pages/C1.vue"),
    ]


@pytest.fixture
def hub(store: GraphStore, target: str) -> GraphStore:
    store.write_code(hub_index(target), "run_hub")
    store.write_crawl(hub_screens(target), [], "run_hub", "bundle_1")
    store.write_links(hub_links(), "run_hub")
    return store


def names(rows: list[dict[str, Any]]) -> set[str]:
    return {r["element_name"] for r in rows}


# ================================================================================== unit: canonical
def test_layout_elements_collapse_to_one_canonical_element(target: str):
    """Case 46: a header link on every screen is one element with one APPEARS_ON per screen."""
    screens = [
        screen(
            target,
            f"s{i}",
            f"/p{i}",
            [obs(f"hdr{i}", "link", "Settings"), obs(f"own{i}", "button", f"Only {i}")],
        )
        for i in range(5)
    ]
    canon = canonicalise(screens, target)
    assert canon.observations == 10 and len(canon.elements) == 6
    header = [e for e in canon.elements if e["props"]["name"] == "Settings"]
    assert len(header) == 1
    on = [a for a in canon.appearances if a["element"] == header[0]["id"]]
    assert {a["screen"] for a in on} == {f"s{i}" for i in range(5)}
    assert header[0]["obs_ids"] == sorted(f"hdr{i}" for i in range(5))
    assert {canon.alias[f"hdr{i}"] for i in range(5)} == {header[0]["id"]}


def test_ordinal_separates_duplicates_and_nameless_are_not_merged_across_pages(target: str):
    a = screen(
        target,
        "a",
        "/a",
        [obs("a0", "button", "Copy", 0), obs("a1", "button", "Copy", 1), obs("an", "button", "", 0)],
    )
    b = screen(target, "b", "/b", [obs("b0", "button", "Copy", 0), obs("bn", "button", "", 0)])
    canon = canonicalise([a, b], target)
    copies = [e for e in canon.elements if e["props"]["name"] == "Copy"]
    assert len(copies) == 2  # ordinal 0 merged across pages, ordinal 1 apart
    assert canon.alias["a0"] == canon.alias["b0"] != canon.alias["a1"]
    assert canon.alias["an"] != canon.alias["bn"]  # nameless: only the crawler's own id may merge
    twice = canonicalise([screen(target, "c", "/c", [obs("an", "button", "", 0)]), a], target)
    assert twice.alias["an"] == canon.alias["an"]  # same crawl id -> same canonical id


def test_modal_elements_are_scoped_by_dialog_title(target: str):
    """Two dialogs' generic "Close" buttons are different elements; the same dialog reached from two pages
    is one. Page-level controls still merge across pages."""

    def modal(sid: str, pattern: str, title: str, oid: str) -> ScreenState:
        return screen(target, sid, pattern, [obs(oid, "button", "Close")], kind="modal", dialog_title=title)

    a, b = modal("m1", "/", "Add environment", "c1"), modal("m2", "/graphql", "Add environment", "c2")
    c = modal("m3", "/", "Share request", "c3")
    page = screen(target, "p1", "/", [obs("c4", "button", "Close")])
    canon = canonicalise([a, b, c, page], target)
    assert canon.alias["c1"] == canon.alias["c2"]
    assert len({canon.alias[o] for o in ("c1", "c3", "c4")}) == 3
    assert element_key(obs("c1", "button", "Close"), a)[4] == "dialog:add environment"


def test_canonical_id_is_stable_under_input_order_and_added_screens(target: str):
    screens = hub_screens(target)
    forward = canonicalise(screens, target)
    shuffled = list(screens)
    random.Random(7).shuffle(shuffled)
    for s in shuffled:
        random.Random(3).shuffle(s.elements)
    again = canonicalise(shuffled, target)
    assert forward.alias == again.alias and forward.elements == again.elements
    extra = [*screens, screen(target, "scr_three", "/three", [obs("o_a1_again", "button", "A1")])]
    grown = canonicalise(extra, target)
    assert grown.alias["o_a1"] == forward.alias["o_a1"] == grown.alias["o_a1_again"]
    assert element_id(target, element_key(obs("x", "button", "  a1 "))) == forward.alias["o_a1"]


def test_match_route_orders_exact_param_catch_all():
    routes = ["/", "/graphql", "/realtime", "/realtime/websocket", "/e/:id", "/:catchAll(.*)*"]
    assert match_route("/graphql", routes) == ("/graphql", "exact")
    assert match_route("/graphql/", routes) == ("/graphql", "exact")
    assert match_route("/e/:id", routes) == ("/e/:id", "exact")
    assert match_route("/e/abc123", routes) == ("/e/:id", "param")
    assert match_route("/realtime/mqtt?x=1#top", routes) == ("/:catchAll(.*)*", "catch_all")
    assert match_route("/no/such/page", routes) == ("/:catchAll(.*)*", "catch_all")
    assert match_route("/no/such/page", ["/", "/graphql"]) is None
    assert match_route("/a/b", ["/:x/:y", "/a/:y", "/a/b/:z"]) == ("/a/:y", "param")  # most static wins


# ================================================================================= unit: templates
PLUMBING = {"_run_once", "_run_rows", "_run_group"}  # GraphStore helpers whose arguments are Cypher
DRIVER_CALLS = {"run", "execute_query"}  # driver / transaction / session entry points taking Cypher text
TX_FUNCTIONS = {
    "execute_read",
    "execute_write",
}  # take a unit of work; the Cypher inside is checked at tx.run


def cypher_violations(source: str, filename: str) -> list[str]:
    """Every place where text that reaches Neo4j (or a Template) is not a module constant (D28, case 57).

    A Cypher argument is acceptable only as a string literal, a module-level constant (also `writes.NAME`,
    `writes.TABLE[key]` and a template's own `.cypher`), a parameter of a plumbing helper (whose call sites
    are checked in turn), or the loop variable of a `for` over `writes.<TABLE>`, over a plumbing parameter or
    over `schema_statements()` (the packaged schema.cypher). An f-string, a concatenation, `%`, `.format`
    or `.replace` is a violation.
    """
    tree = ast.parse(source)
    module_names = {
        t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)
    } | {a.asname or a.name for n in tree.body if isinstance(n, ast.ImportFrom) for a in n.names}
    out: list[str] = []

    def literal_ok(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return isinstance(node.value, str)
        if isinstance(node, ast.Attribute):  # writes.NAME, queries.NAME, template.cypher
            return node.attr == "cypher" or (
                isinstance(node.value, ast.Name) and node.value.id in {"writes", "queries"}
            )
        if isinstance(node, ast.Subscript):  # writes.PRUNE[layer]
            return literal_ok(node.value)
        return isinstance(node, ast.Name) and node.id in module_names  # JoinedStr, BinOp, Call: no

    def trusted(node: ast.AST, scope: list[ast.FunctionDef], loops: set[str]) -> bool:
        if isinstance(node, ast.Name) and node.id not in module_names:
            params = {a.arg for fn in scope for a in fn.args.args if fn.name in PLUMBING}
            return node.id in params or node.id in loops
        return literal_ok(node)

    def loop_names(node: ast.For, scope: list[ast.FunctionDef], derived: set[str]) -> set[str]:
        source_of_loop = ast.unparse(node.iter)
        params = {a.arg for fn in scope for a in fn.args.args if fn.name in PLUMBING}
        fixed = "writes." in source_of_loop or "schema_statements" in source_of_loop
        if fixed or source_of_loop in params or source_of_loop in derived:
            return {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
        return set()

    def walk(node: ast.AST, scope: list[ast.FunctionDef], loops: set[str], derived: set[str]) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            scope = [*scope, node]
        if isinstance(node, ast.Assign) and "schema_statements" in ast.unparse(node.value):
            derived.update(t.id for t in node.targets if isinstance(t, ast.Name))  # shared on purpose
        if isinstance(node, ast.For):
            loops = loops | loop_names(node, scope, derived)
        if isinstance(node, ast.Call) and node.args:
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            checked: list[ast.AST] = []
            if name == "Template" and len(node.args) > 1:
                checked = [node.args[1]]
            elif name == "_run_group" and isinstance(node.args[0], ast.List | ast.Tuple):
                checked = list(node.args[0].elts)  # statements arrive as a list of constants
            elif name in DRIVER_CALLS | PLUMBING:
                checked = [node.args[0]]
            elif name in TX_FUNCTIONS and not isinstance(node.args[0], ast.Name | ast.Lambda | ast.Attribute):
                shown = ast.unparse(node.args[0])[:40]
                out.append(f"{filename}:{node.lineno} {name} takes a function, not text: {shown}")
            for arg in checked:
                if not trusted(arg, scope, loops):
                    out.append(f"{filename}:{node.lineno} non-constant Cypher: {ast.unparse(arg)[:60]}")
        formatted = isinstance(node, ast.Assign) and isinstance(node.value, ast.JoinedStr | ast.BinOp)
        if formatted and any(isinstance(t, ast.Name) and t.id.lstrip("_").isupper() for t in node.targets):
            out.append(f"{filename}:{node.lineno} builds a constant by formatting")
        for child in ast.iter_child_nodes(node):
            walk(child, scope, loops, derived)

    walk(tree, [], set(), set())
    return out


def test_every_cypher_string_is_a_constant():
    """D28: no string-built Cypher anywhere in graph/, including every queries_*.py extension module and
    every driver entry point (tx.run, session.run, execute_query), not only the store's own helpers."""
    modules = sorted(GRAPH_SRC.glob("*.py"))
    assert {"store.py", "writes.py", "queries.py", "queries_downstream.py"} <= {m.name for m in modules}
    found = [v for m in modules for v in cypher_violations(m.read_text(), m.name)]
    assert not found, found
    for tpl in queries.all_templates().values():
        assert isinstance(tpl.cypher, str) and "{{" not in tpl.cypher and "%s" not in tpl.cypher


def test_the_cypher_checker_catches_what_the_old_test_missed():
    bad = {
        "tx_run_fstring": "def f(tx, label):\n    tx.run(f'MATCH (n:{label}) RETURN n')\n",
        "execute_query_concat": "def f(d, t):\n    d.execute_query('MATCH (n {t: ' + t + '})')\n",
        "session_run_format": "def f(s, t):\n    s.run('MATCH (n:{}) RETURN n'.format(t))\n",
        "template_fstring": "TPL = Template('x', f'MATCH (n:{LBL})')\n",
        "run_once_computed": "class S:\n    def g(self, t):\n        self._run_once(self.f(t), None, 'w')\n",
        "name_not_constant": "def f(tx, t):\n    cypher = 'MATCH (n)' + t\n    tx.run(cypher)\n",
        "execute_write_text": "def f(s, t):\n    s.execute_write(f'MATCH (n:{t}) RETURN n')\n",
        "execute_read_text": "def f(s):\n    s.execute_read('MATCH (n) RETURN n')\n",
    }
    for name, source in bad.items():
        assert cypher_violations(source, name), name
    good = (
        "Q = 'MATCH (n) RETURN n'\n"
        "def f(tx, tpl):\n    tx.run(Q)\n    tx.run(writes.Q2)\n    tx.run('MATCH (m) RETURN m')\n"
        "    tx.run(tpl.cypher)\n"
        "class S:\n    def _run_once(self, cypher, tally, what, **p):\n        tx.run(cypher, **p)\n"
        "    def _run_group(self, statements, rows):\n        for cypher in statements:\n"
        "            tx.run(cypher)\n"
        "    def g(self):\n        for name, cypher in writes.PRUNE['x']:\n"
        "            self._run_once(cypher, 1, name)\n"
        "        self._run_group([writes.A, writes.B], [])\n"
        "    def h(self):\n        statements = schema_statements()\n        for s in statements:\n"
        "            self._run_once(s, 1, 'x')\n"
    )
    assert cypher_violations(good, "good") == []


def test_template_binding_validates_before_any_io():
    with pytest.raises(queries.UnknownTemplate):
        queries.get("MATCH (n) DETACH DELETE n")
    impact = queries.get("impact")
    with pytest.raises(ValueError, match="missing required parameter 'changed'"):
        impact.bind({"target": "t"})
    with pytest.raises(ValueError, match="unknown parameter"):
        impact.bind({"target": "t", "changed": [], "limit": 5})
    with pytest.raises(ValueError, match="hops"):
        impact.bind({"target": "t", "changed": [], "hops": 4})
    with pytest.raises(ValueError, match="hops"):
        impact.bind({"target": "t", "changed": [], "hops": True})
    with pytest.raises(ValueError, match="changed"):
        impact.bind({"target": "t", "changed": "a.vue"})
    with pytest.raises(ValueError, match="changed"):
        impact.bind({"target": "t", "changed": [1]})
    bound = impact.bind({"target": "t", "changed": ("a",)})
    assert bound == {"target": "t", "changed": ["a"], "hops": 2, "forward": 0}


def test_connection_failure_is_graph_conn_failed_and_recorded():
    run = RunContext.ephemeral("t")
    settings = Settings(neo4j_uri="bolt://127.0.0.1:1", neo4j_password="s3cret-pass-do-not-leak")
    with pytest.raises(GraphConnectionError) as info:
        GraphStore.from_settings(settings, run, attempts=2, backoff_s=0)
    assert info.value.code is FailureCode.GRAPH_CONN_FAILED
    assert "s3cret" not in str(info.value) and "127.0.0.1:1" in str(info.value)
    assert [f["code"] for f in run.failures.items] == ["GRAPH_CONN_FAILED"]


# ============================================================================== db: schema and reads
def test_schema_is_idempotent(store: GraphStore):
    first = store.ensure_schema()
    before = store.driver.execute_query("SHOW CONSTRAINTS YIELD name RETURN name ORDER BY name").records
    assert store.ensure_schema() == first
    after = store.driver.execute_query("SHOW CONSTRAINTS YIELD name RETURN name ORDER BY name").records
    assert [r["name"] for r in before] == [r["name"] for r in after]
    assert {
        "requirement_key",
        "screen_key",
        "ui_element_key",
        "flow_key",
        "code_file_key",
        "i18n_key_key",
        "route_key",
        "pull_request_key",
        "gap_key",
        "run_key",
    } <= {r["name"] for r in after}


def test_read_refuses_writes_and_free_form_cypher(store: GraphStore, target: str, monkeypatch):
    store.write_code(hub_index(target), "r1")
    with pytest.raises(queries.UnknownTemplate):
        store.read("MATCH (n) DETACH DELETE n")
    evil = queries.Template(
        "evil", "MATCH (n:CodeFile {target: $target}) DETACH DELETE n", {"target": queries._str()}
    )
    monkeypatch.setitem(queries.TEMPLATES, "evil", evil)
    before = store.counts(target)["CodeFile"]
    with pytest.raises(GraphError, match=r"AccessMode|read access"):
        store.read("evil", target=target)
    creator = queries.Template(
        "creator", "CREATE (n:Hack {target: $target}) RETURN n", {"target": queries._str()}
    )
    monkeypatch.setitem(queries.TEMPLATES, "creator", creator)
    with pytest.raises(GraphError):
        store.read("creator", target=target)
    assert store.counts(target)["CodeFile"] == before > 0
    assert store.read("counts_by_label", target=target)  # reads still work afterwards


def test_parameters_are_inert(hub: GraphStore, target: str):
    nasty = [
        "x' }) DETACH DELETE n //",
        'a" OR 1=1 //',
        "}) MATCH (n) DETACH DELETE n RETURN (",
        "${jndi}",
        "\\'; --",
        "src/data/hub.ts' OR path <> '",
        "💥\n\x00",
    ]
    before = hub.counts(target)
    assert hub.read("impact", changed=nasty, hops=2) == []
    assert hub.read("hub_degree", paths=nasty) == []
    assert hub.read("element_by_file", paths=nasty) == []
    assert hub.read("absence_near", changed=nasty, bundle_hash="b' OR true //") == []
    assert [r["exists"] for r in hub.read("file_exists", paths=nasty)] == [False] * len(nasty)
    assert hub.read("impact", target="x' }) DETACH DELETE n //", changed=[HUB]) == []
    assert hub.counts(target) == before
    # a path with metacharacters is stored and found as data, not interpreted
    weird = 'src/we\'ird/"na}me" //.vue'
    idx = CodeIndex(
        target=target,
        repo="o/r",
        ref="main",
        snapshot_id="s",
        adapter="vue",
        files=[code_file(weird), code_file("src/ok.vue")],
        edges=[edge("src/ok.vue", weird)],
    )
    hub.write_code(idx, "r2")
    found = hub.read("file_exists", paths=[weird, "src/ok.vue"])
    assert [(r["path"], r["exists"]) for r in found] == [(weird, True), ("src/ok.vue", True)]


# ============================================================================ db: writes and rounds
def test_code_round_trip_and_idempotent_rewrite(store: GraphStore, target: str):
    index = hub_index(target)
    index.i18n = {"action.send": "Send", "app.name": "Hub"}
    index.key_uses = [
        KeyUse(file="src/components/A1.vue", key="action.send"),
        KeyUse(file="src/components/A1.vue", key="action.send"),
        KeyUse(file="src/components/A2.vue", key="not.in.en.json"),
        KeyUse(file="src/components/A3.vue", key="menu.", dynamic=True),
    ]
    index.edges.append(edge("src/components/A1.vue", "@hoppscotch/ui", "RENDERS"))  # endpoint not in files
    index.edges.append(edge("src/components/A1.vue", HUB, symbols=["Other", "Hub"]))  # duplicate pair
    first = store.write_code(index, "run1")
    assert first["files"] == len(index.files) and first["stub_files"] == 1
    assert first["imports"] == 6 and first["renders"] == 5  # duplicate A1 -> hub pair merged
    counts = store.counts(target)
    assert counts["CodeFile"] == len(index.files) + 1 and counts["Route"] == 1 and counts["HANDLED_BY"] == 1
    assert counts["I18nKey"] == 4 and counts["USES_KEY"] == 3  # the repeated use is one edge, uses=2
    uses = store.driver.execute_query(
        "MATCH (:CodeFile {target: $t, path: 'src/components/A1.vue'})"
        "-[u:USES_KEY]->(k:I18nKey {key: 'action.send'}) "
        "RETURN u.uses AS n, u.dynamic AS d, k.en AS en",
        t=target,
    ).records[0]
    assert (uses["n"], uses["d"], uses["en"]) == (2, False, "Send")
    missing = store.driver.execute_query(
        "MATCH (k:I18nKey {target: $t, key: 'not.in.en.json'}) RETURN k.en AS en", t=target
    ).records[0]
    assert missing["en"] is None
    exists = {r["path"]: r for r in store.read("file_exists", paths=["@hoppscotch/ui", HUB])}
    assert exists["@hoppscotch/ui"]["stub"] is True and exists["@hoppscotch/ui"]["kind"] == "external"
    assert exists[HUB]["kind"] == "data_schema" and exists[HUB]["sha"] == f"sha_{HUB}"
    sym = store.driver.execute_query(
        "MATCH (:CodeFile {target: $t, path: 'src/components/A1.vue'})-[r:IMPORTS]->(:CodeFile {path: $h}) "
        "RETURN r.symbols AS s, r.run_id AS run, r.first_run_id AS first",
        t=target,
        h=HUB,
    ).records
    assert sym[0]["s"] == ["Hub", "Other", "s1"]  # unioned and sorted: independent of edge arrival order
    assert sym[0]["run"] == "run1" == sym[0]["first"]
    again = store.write_code(index, "run1")
    assert again["nodes_created"] == 0 and again["relationships_created"] == 0
    assert store.counts(target) == counts


def test_crawl_canonicalises_elements_and_resolves_transitions(store: GraphStore, target: str):
    screens = [
        screen(
            target,
            f"s{i}",
            f"/p{i}",
            [obs(f"hdr{i}", "link", "Settings"), obs(f"own{i}", "button", f"Only {i}")],
        )
        for i in range(4)
    ]
    trans = [
        Transition(id="t_el", from_screen="s0", to_screen="s1", element_id="hdr0", label="Settings"),
        Transition(id="t_goto", from_screen="s1", to_screen="s2", action="goto"),
        Transition(id="t_gone", from_screen="s2", to_screen="s3", element_id="no_such_obs"),
        Transition(
            id="t_blocked",
            from_screen="s3",
            to_screen="s_not_captured",
            blocked=True,
            observed=False,
            reason="policy",
        ),
    ]
    res = store.write_crawl(screens, trans, "run1", "bundle_x")
    assert res["observations"] == 8 and res["elements"] == 5 and res["appearances"] == 8
    assert res["transitions_element_level"] == 1 and res["transitions_screen_level"] == 3
    assert res["transitions_element_unresolved"] == 1 and res["transitions_skipped"] == 1
    counts = store.counts(target)
    assert counts["Screen"] == 4 and counts["UIElement"] == 5 and counts["APPEARS_ON"] == 8
    assert counts["LEADS_TO"] == 3
    header = next(r for r in store.read("unmapped_ui_elements") if r["name"] == "Settings")
    assert header["screens"] == 4 and set(header["screen_ids"]) == {f"s{i}" for i in range(4)}
    rows = {r["id"]: r for r in store.read("transitions")}
    assert rows["t_el"]["source_kind"] == "UIElement" and rows["t_el"]["source_id"] == header["id"]
    assert rows["t_el"]["from_screen"] == "s0" and rows["t_gone"]["source_kind"] == "Screen"
    assert "t_blocked" not in rows
    shot = store.driver.execute_query("MATCH (s:Screen {target: $t, id: 's0'}) RETURN s", t=target).records[
        0
    ]["s"]
    assert (
        shot["bundle_hash"] == "bundle_x" and shot["snapshot_id"] == "bundle_x" and shot["run_id"] == "run1"
    )
    assert store.write_crawl(screens, trans, "run1", "bundle_x")["nodes_created"] == 0


def test_requirements_links_gaps_flows_round_trip(hub: GraphStore, target: str):
    req = Requirement(
        id="req_a",
        target=target,
        text="User can use A1",
        quote="use A1",
        source_url="u",
        section="Hub",
        spec_sha="sha",
        tags=["x"],
        edition="cloud-web",
        ui_observable="yes",
    )
    other = Requirement(
        id="req_b",
        target=target,
        text="User can use C1",
        quote="use C1",
        source_url="u",
        section="Hub",
        spec_sha="sha",
    )
    assert hub.write_requirements([req, other], "run2")["requirements"] == 2
    links = [
        Link(
            kind="REALIZED_BY",
            src="req_a",
            dst="o_a1",
            confidence=0.7,
            method="bm25_judge",
            evidence_id="ev_r1",
            judge="j1",
        ),
        Link(
            kind="REALIZED_BY",
            src="req_a",
            dst="scr_two",
            confidence=0.5,
            method="llm_pick",
            evidence_id="ev_r2",
        ),
        Link(
            kind="REALIZED_BY",
            src="req_a",
            dst="flow_x",
            confidence=0.4,
            method="llm_pick",
            evidence_id="ev_r3",
        ),
        Link(
            kind="REALIZED_BY", src="req_missing", dst="o_a1", confidence=0.4, method="human", evidence_id="e"
        ),
        Link(kind="REALIZED_BY", src="req_b", dst="nowhere", confidence=0.4, method="human", evidence_id="e"),
        Link(
            kind="IMPLEMENTED_BY",
            src="o_a1",
            dst="src/components/A4.vue",
            confidence=0.95,
            method="route",
            evidence_id="ev_i2",
        ),
        Link(
            kind="IMPLEMENTED_BY",
            src="o_a1",
            dst="src/gone.vue",
            confidence=0.9,
            method="route",
            evidence_id="ev_i3",
        ),
    ]
    flow = Flow(
        id="flow_x", name="use A", steps=["o_a1", "o_a2", "o_c1"], screens=["scr_one", "scr_two"], goal="g"
    )
    res = hub.write_flows([flow], "run2")
    assert res["flows"] == 1 and res["steps"] == 3 and res["steps_unresolved"] == 0
    assert res["screens_unresolved"] == 0

    got = hub.write_links(links, "run2")
    assert got["realized_by"] == 3 and got["implemented_by"] == 1
    assert got["skipped_missing_src"] == 1 and got["skipped_missing_dst"] == 2
    owner = hub.driver.execute_query(
        "MATCH (e:UIElement {target: $t}) WHERE 'o_a1' IN e.obs_ids RETURN e.owner_file AS f", t=target
    ).records
    assert owner[0]["f"] == "src/components/A4.vue"  # highest-confidence IMPLEMENTED_BY wins
    rows = hub.read("impact", changed=["src/components/A4.vue"], hops=0)
    assert [r["element_name"] for r in rows] == ["A1"] and rows[0]["impl_method"] == "route"
    both = hub.read("impact", changed=["src/components/A4.vue", "src/components/A1.vue"], hops=0)
    a1 = next(r for r in both if r["element_name"] == "A1")
    assert a1["file"] == "src/components/A4.vue" and a1["impl_confidence"] == 0.95
    assert sorted((e["file"], e["confidence"]) for e in a1["impl_edges"]) == [
        ("src/components/A1.vue", 0.9),
        ("src/components/A4.vue", 0.95),
    ]
    assert set(a1["seeds"]) == {"src/components/A4.vue", "src/components/A1.vue"}
    edges = hub.driver.execute_query(
        "MATCH (:Requirement {target: $t, id: 'req_a'})-[r:REALIZED_BY]->(:UIElement) "
        "RETURN r.doc_sha AS doc, r.bundle_hash AS b, r.judge AS j",
        t=target,
    ).records
    assert [(r["doc"], r["b"], r["j"]) for r in edges] == [("sha", "bundle_1", "j1")]
    impl = hub.driver.execute_query(
        "MATCH (:UIElement {target: $t})-[r:IMPLEMENTED_BY]->(f:CodeFile {path: 'src/components/A4.vue'}) "
        "RETURN r.snapshot_id AS s, r.bundle_hash AS b",
        t=target,
    ).records
    assert [(r["s"], r["b"]) for r in impl] == [("snap1", "bundle_1")]
    reqs = hub.read("impact", changed=["src/components/A1.vue"], hops=0)[0]["requirements"]
    assert sorted((r["id"], r["via"]) for r in reqs) == [("req_a", "element"), ("req_a", "flow")]
    assert {f["id"] for f in hub.read("impact", changed=["src/components/A1.vue"], hops=0)[0]["flows"]} == {
        "flow_x"
    }
    two = [
        r
        for r in hub.read("impact", changed=["src/components/A3.vue"], hops=0)
        if r["screen_id"] == "scr_two"
    ]
    assert [(q["id"], q["via"]) for q in two[0]["requirements"]] == [("req_a", "screen")]
    flows = hub.driver.execute_query(
        "MATCH (f:Flow {target: $t, id: 'flow_x'}) OPTIONAL MATCH (f)-[:STARTS_AT]->(a) "
        "OPTIONAL MATCH (f)-[:ENDS_AT]->(b) RETURN a.id AS a, b.id AS b, f.n_steps AS n",
        t=target,
    ).records[0]
    assert (flows["a"], flows["b"], flows["n"]) == ("scr_one", "scr_two", 3)
    # a flow is replaced as a unit
    hub.write_flows([Flow(id="flow_x", name="use A", steps=["o_c1"], screens=["scr_two"], goal="g")], "run3")
    steps = hub.driver.execute_query(
        "MATCH (:Flow {target: $t})-[s:STEP]->(e) RETURN count(s) AS n", t=target
    )
    assert steps.records[0]["n"] == 1
    assert hub.write_links(links, "run2")["relationships_created"] == 0  # idempotent
    odd = hub.write_flows(
        [Flow(id="flow_odd", name="odd", steps=["o_a1", "nope"], screens=["scr_one", "scr_nope"])], "run2"
    )
    assert odd["steps_unresolved"] == 1 and odd["screens_unresolved"] == 1


def test_gaps_are_never_reused_across_bundles(hub: GraphStore, target: str):
    """Case 48: the same gap id under a new bundle hash is a new node; the old one is untouched."""
    req = Requirement(
        id="req_a", target=target, text="t", quote="q", source_url="u", section="s", spec_sha="x"
    )
    hub.write_requirements([req], "r")

    def gap(bundle: str | None, status: str, screens: list[str]) -> Gap:
        return Gap(
            id="gap_a",
            kind=GapKind.NO_UI_EVIDENCE,
            status=status,
            subject_id="req_a",
            confidence=0.3,
            screens_searched=screens,
            actions_spent=11,
            bundle_hash=bundle,
            reason="SPEC_MAY_BE_STALE",
        )

    hub.write_gaps([gap("b1", "NOT_FOUND_WITHIN_BUDGET", ["scr_one"])], "r1")
    res = hub.write_gaps([gap("b2", "CONFIRMED_ABSENT", ["scr_two"])], "r2")
    assert res["gaps"] == 1 and hub.counts(target)["Gap"] == 2 and hub.counts(target)["HAS_GAP"] == 2
    # only scr_one is served by a route (/one -> B1.vue): the b1 gap is "near" B1, the b2 gap is not
    hub.link_routes("r2", target=target)
    old = hub.read("absence_near", changed=["src/pages/B1.vue"], bundle_hash="b1")
    new = hub.read("absence_near", changed=["src/pages/B1.vue"], bundle_hash="b2")
    anyb = hub.read("absence_near", changed=["src/pages/B1.vue"], bundle_hash=None)  # None: explicitly "any"
    with pytest.raises(ValueError, match="bundle_hash"):  # case 48: the safe behaviour is opt-out, not opt-in
        hub.read("absence_near", changed=["src/pages/B1.vue"])
    assert [r["status"] for r in old] == ["NOT_FOUND_WITHIN_BUDGET"] and old[0]["impacted_screen_ids"] == [
        "scr_one"
    ]
    assert new == []
    assert [r["bundle_hash"] for r in anyb] == ["b1"]
    # re-asserting a gap under the same bundle updates it in place and replaces its SEARCHED set
    hub.write_gaps([gap("b1", "CONFIRMED_ABSENT", [])], "r3")
    assert hub.counts(target)["Gap"] == 2
    assert hub.read("absence_near", changed=["src/pages/B1.vue"], bundle_hash="b1") == []
    # a gap without a bundle hash is bundle-independent and distinct from both
    hub.write_gaps([gap(None, "CLASSIFIED", [])], "r4")
    assert hub.counts(target)["Gap"] == 3


def test_unresolvable_gap_subjects_and_screens_are_counted(hub: GraphStore, target: str):
    g = Gap(
        id="g_free",
        kind=GapKind.UNMAPPED_CHANGE,
        status="OBSERVED",
        subject_id="src/new/File.vue",
        screens_searched=["scr_one", "scr_nope"],
        bundle_hash="b",
    )
    res = hub.write_gaps([g], "r")
    assert res["subjects_unresolved"] == 1 and res["searched"] == 1 and res["searched_unresolved"] == 1
    attached = hub.write_gaps([g.model_copy(update={"id": "g_known", "subject_id": HUB})], "r")
    assert attached["subjects_unresolved"] == 0


# ================================================================================== db: impact query
def test_impact_respects_hop_cap_and_counts_distinct(hub: GraphStore):
    by_hops = {h: hub.read("impact", changed=[HUB], hops=h) for h in (0, 1, 2, 3)}
    assert names(by_hops[0]) == {"Hub control"}
    assert names(by_hops[1]) == {"Hub control", "A1", "A2", "A3"}
    assert names(by_hops[2]) == {"Hub control", "A1", "A2", "A3", "B1 title"}
    assert names(by_hops[3]) == {"Hub control", "A1", "A2", "A3", "B1 title", "C1 header"}
    assert hub.read("impact", changed=[HUB]) == by_hops[2]  # default cap is MAX_HOPS
    for rows in by_hops.values():
        pairs = [(r["screen_id"], r["element_id"]) for r in rows]
        assert len(pairs) == len(set(pairs))  # DISTINCT (screen, element)
    two = by_hops[2]
    minhop = {r["element_name"]: r["hops"] for r in two}
    assert minhop == {"Hub control": 0, "A1": 1, "A2": 1, "A3": 1, "B1 title": 2}
    # B1 reaches the hub through A1 and through A2: still exactly one row
    assert [r["element_name"] for r in two].count("B1 title") == 1
    # A2 (one element) is on two screens: two rows, one element id, screens counted once each
    a2 = [r for r in two if r["element_name"] == "A2"]
    assert len(a2) == 2 and len({r["element_id"] for r in a2}) == 1
    assert {r["screen_id"] for r in two} == {"scr_one", "scr_two"}
    b1 = next(r for r in two if r["element_name"] == "B1 title")
    assert b1["via"][0] == "src/pages/B1.vue" and b1["via"][-1] == HUB and len(b1["via"]) == 3
    assert b1["kinds"] == ["RENDERS", "IMPORTS"] and b1["direction"] == "reverse" and b1["seed"] == HUB
    assert len(b1["specifiers"]) == 2 and len(b1["symbols"]) == 2
    assert b1["symbols"][1] in (["Hub", "s1"], ["Hub", "s2"])
    assert b1["impl_confidence"] == 0.9 and b1["impl_method"] == "i18n_key" and b1["impl_evidence_id"]


def test_impact_cycles_multiple_seeds_and_forward_hop(store: GraphStore, target: str):
    files = [code_file(p) for p in ("a.vue", "b.vue", "c.vue", "d.vue", "e.vue")]
    edges = [
        edge("a.vue", "b.vue"),
        edge("b.vue", "a.vue"),  # cycle a <-> b
        edge("c.vue", "a.vue"),
        edge("c.vue", "d.vue", "RENDERS"),
        edge("e.vue", "c.vue"),
    ]
    store.write_code(
        CodeIndex(
            target=target, repo="o/r", ref="m", snapshot_id="s", adapter="vue", files=files, edges=edges
        ),
        "r",
    )
    screens = [screen(target, "s1", "/", [obs(f"o_{n}", "button", n) for n in "abcde"])]
    store.write_crawl(screens, [], "r", None)
    store.write_links(
        [
            Link(
                kind="IMPLEMENTED_BY",
                src=f"o_{n}",
                dst=f"{n}.vue",
                confidence=0.8,
                method="route",
                evidence_id=f"e{n}",
            )
            for n in "abcde"
        ],
        "r",
    )
    rows = store.read("impact", changed=["a.vue", "b.vue"], hops=2)
    got = {r["element_name"]: (r["hops"], r["seed"]) for r in rows}
    assert got["a"][0] == 0 and got["b"][0] == 0 and got["c"][0] == 1 and got["e"][0] == 2
    assert "d" not in got
    assert {r["element_name"]: set(r["seeds"]) for r in rows}["c"] == {"a.vue", "b.vue"}
    fwd = store.read("impact", changed=["c.vue"], hops=0, forward=1)
    assert {r["element_name"]: (r["hops"], r["direction"]) for r in fwd} == {
        "c": (0, "self"),
        "a": (1, "forward"),
        "d": (1, "forward"),
    }
    assert [r["via"] for r in fwd if r["element_name"] == "d"] == [["c.vue", "d.vue"]]
    assert store.read("impact", changed=["c.vue"], hops=0) != fwd
    assert store.read("impact", changed=[], hops=2) == []
    assert store.read("impact", changed=["not/in/graph.vue"], hops=2) == []


def test_hub_degree_is_fraction_of_ui_files(hub: GraphStore):
    rows = {
        r["path"]: r
        for r in hub.read("hub_degree", paths=[HUB, "src/pages/C1.vue", "src/lib/unrelated.ts"], hops=2)
    }
    h = rows[HUB]
    assert h["importers"] == 5 + 2 and h["ui_importers"] == 7 and h["direct_importers"] == 5
    assert h["ui_total"] == 5 + 4 and h["fraction"] == pytest.approx(7 / 9)
    assert rows["src/pages/C1.vue"]["importers"] == 0 and rows["src/pages/C1.vue"]["fraction"] == 0.0
    assert rows["src/lib/unrelated.ts"]["importers"] == 1  # B3 only; A-files do not import it
    assert hub.read("hub_degree", paths=[HUB], hops=1)[0]["importers"] == 5
    assert hub.read("hub_degree", paths=[HUB], hops=3)[0]["importers"] == 8


def test_unmapped_elements_and_element_by_file(hub: GraphStore, target: str):
    extra = [screen(target, "scr_x", "/x", [obs("o_orphan", "textbox", "Search", placeholder="Search...")])]
    hub.write_crawl(extra, [], "run_hub", "bundle_1")
    unmapped = hub.read("unmapped_ui_elements")
    assert [(r["name"], r["placeholder"]) for r in unmapped] == [("Search", "Search...")]
    assert unmapped[0]["url_patterns"] == ["/x"] and unmapped[0]["gap_kinds"] == []
    assert hub.read("unmapped_ui_elements", limit=1) == unmapped
    by_file = hub.read("element_by_file", paths=["src/components/A2.vue", HUB, "src/lib/unrelated.ts"])
    assert [(r["path"], r["name"]) for r in by_file] == [
        ("src/components/A2.vue", "A2"),
        (HUB, "Hub control"),
    ]
    assert set(by_file[0]["screen_ids"]) == {"scr_one", "scr_two"}


def test_absence_near_follows_route_handler_and_layout(store: GraphStore, target: str):
    files = [
        code_file("pages/p.vue", FileClass.PAGE),
        code_file("comp/c.vue"),
        code_file("layouts/default.vue", FileClass.LAYOUT),
        code_file("pages/other.vue", FileClass.PAGE),
    ]
    idx = CodeIndex(
        target=target,
        repo="o/r",
        ref="m",
        snapshot_id="s",
        adapter="vue",
        files=files,
        edges=[edge("pages/p.vue", "comp/c.vue", "RENDERS")],
        routes=[
            RouteDef(pattern="/p", file="pages/p.vue", layout="layouts/default.vue"),
            RouteDef(pattern="/o", file="pages/other.vue"),
        ],
    )
    store.write_code(idx, "r")
    store.write_crawl([screen(target, "sp", "/p", []), screen(target, "so", "/o", [])], [], "r", "b")
    assert store.link_routes("r", target=target)["served"] == 2
    store.write_requirements(
        [Requirement(id="rq", target=target, text="t", quote="q", source_url="u", section="s", spec_sha="x")],
        "r",
    )
    store.write_gaps(
        [
            Gap(
                id="gp",
                kind=GapKind.NO_UI_EVIDENCE,
                status="NOT_FOUND_WITHIN_BUDGET",
                subject_id="rq",
                screens_searched=["sp"],
                bundle_hash="b",
                actions_spent=4,
            ),
            Gap(
                id="go",
                kind=GapKind.NOT_UI_TESTABLE,
                status="CLASSIFIED",
                subject_id="rq",
                screens_searched=["sp"],
                bundle_hash="b",
            ),
        ],
        "r",
    )
    via_component = store.read("absence_near", changed=["comp/c.vue"], bundle_hash="b")
    assert [(r["gap_id"], r["hops"], r["actions_spent"]) for r in via_component] == [("gp", 1, 4)]
    assert store.read("absence_near", changed=["comp/c.vue"], hops=0, bundle_hash="b") == []
    via_layout = store.read("absence_near", changed=["layouts/default.vue"], hops=0, bundle_hash="b")
    assert [r["gap_id"] for r in via_layout] == ["gp"]  # NOT_UI_TESTABLE is not absence
    assert store.read("absence_near", changed=["pages/other.vue"], bundle_hash="b") == []


def test_chunked_writes_match_unchunked(target: str):
    try:
        small = GraphStore.from_settings(neo4j_settings(), attempts=1, chunk_size=3, target=target)
    except GraphConnectionError as exc:
        pytest.skip(str(exc))
    try:
        small.ensure_schema()
        res = small.write_code(hub_index(target), "r")
        small.write_crawl(hub_screens(target), [], "r", "b")
        small.write_links(hub_links(), "r")
        counts = small.counts(target)
        assert counts["CodeFile"] == res["files"] == 11 and counts["IMPORTS"] == 6 and counts["RENDERS"] == 4
        assert counts["IMPLEMENTED_BY"] == 6 and counts["APPEARS_ON"] == 7
        assert names(small.read("impact", changed=[HUB], hops=3)) == {
            "Hub control",
            "A1",
            "A2",
            "A3",
            "B1 title",
            "C1 header",
        }
    finally:
        small.clear_target(target)
        small.close()


def test_clear_target_leaves_other_targets_alone(store: GraphStore, target: str):
    other = f"__test__{uuid.uuid4().hex[:10]}"
    try:
        store.write_code(hub_index(target), "r")
        store.write_crawl(hub_screens(target), [], "r", "b")
        store.write_code(hub_index(other), "r")
        store.write_requirements(
            [
                Requirement(
                    id="rq", target=other, text="t", quote="q", source_url="u", section="s", spec_sha="x"
                )
            ],
            "r",
            target=other,
        )
        mine, theirs = store.counts(target), store.counts(other)
        assert mine["CodeFile"] == theirs["CodeFile"] == 11 and theirs["Requirement"] == 1
        deleted = store.clear_target(target)
        assert deleted == sum(v for k, v in mine.items() if k[0].isupper() and not k.isupper())
        assert sum(store.counts(target).values()) == 0
        assert store.counts(other) == theirs
        assert (
            names(store.read("impact", target=other, changed=[HUB], hops=1)) == set()
        )  # no elements in `other`
        assert store.read("file_exists", target=other, paths=[HUB])[0]["exists"] is True
        assert store.clear_target("__test__nonexistent") == 0
    finally:
        store.clear_target(other)


# ================================================================================= db: run_load
def make_run(tmp_path: Path, target: str, run_id: str) -> tuple[RunContext, TargetConfig]:
    settings = neo4j_settings()
    run = RunContext.create(target, run_id=run_id, settings=settings, runs_dir=tmp_path)
    cfg = TargetConfig.load("hoppscotch").model_copy(update={"name": target})
    return run, cfg


def write_artifacts(
    run: RunContext, target: str, *, drop_screen: bool = False, bundle: str = "bundle_A"
) -> None:
    index = hub_index(target)
    index.i18n = {"action.send": "Send"}
    index.key_uses = [KeyUse(file="src/components/A1.vue", key="action.send")]
    screens = hub_screens(target)
    if drop_screen:
        screens = screens[:1]
    reqs = [
        Requirement(
            id="req_a",
            target=target,
            text="User can use A1",
            quote="use A1",
            source_url="u",
            section="Hub",
            spec_sha="s1",
        )
    ]
    trans = [Transition(id="t1", from_screen="scr_one", to_screen="scr_two", element_id="o_a1", label="A1")]
    if drop_screen:
        trans = []
    (run.run_dir / "crawl").mkdir(exist_ok=True)
    (run.run_dir / "crawl" / "screens.jsonl").write_text(
        "\n".join(s.model_dump_json() for s in screens) + "\n"
    )
    (run.run_dir / "crawl" / "transitions.jsonl").write_text(
        "\n".join(t.model_dump_json() for t in trans) + "\n"
    )
    summary = CrawlSummary(
        target=target,
        started_at="a",
        finished_at="b",
        screens=len(screens),
        elements=7,
        transitions=len(trans),
        actions_spent=3,
        actions_budget=10,
        wall_clock_s=1.0,
        bundle_hash=bundle,
        bundle_hash_end=bundle,
        app_version="2026.9.0",
    )
    (run.run_dir / "crawl" / "summary.json").write_text(summary.model_dump_json())
    (run.run_dir / "spec").mkdir(exist_ok=True)
    (run.run_dir / "spec" / "requirements.json").write_text(json.dumps([r.model_dump() for r in reqs]))
    (run.run_dir / "code").mkdir(exist_ok=True)
    (run.run_dir / "code" / "index.json").write_text(index.model_dump_json())


def test_run_load_round_trip(store: GraphStore, target: str, tmp_path: Path):
    run, cfg = make_run(tmp_path, target, "run-load-1")
    write_artifacts(run, target)
    out = run_load(run, cfg, store)
    assert (run.run_dir / "graph" / "load.json").exists()
    assert json.loads((run.run_dir / "graph" / "load.json").read_text()) == out
    assert out["layers"]["code"]["files"] == 11 and out["layers"]["crawl"]["elements"] == 6
    assert out["layers"]["spec"]["requirements"] == 1
    assert (
        out["code_snapshot_id"] == "snap1"
        and out["bundle_hash"] == "bundle_A"
        and out["bundle_drift"] is False
    )
    assert out["served_by"]["served"] == 1 and out["served_by"]["unserved"] == 1
    counts = out["counts"]
    assert counts["Screen"] == 2 and counts["UIElement"] == 6 and counts["Run"] == 1
    assert counts["SERVED_BY"] == 1 and counts["LEADS_TO"] == 1 and counts["Requirement"] == 1
    assert store.read("runs", target=target)[0]["layers"] == ["spec", "code", "crawl"]
    assert store.read("runs", target=target)[0]["crawl_snapshot_id"] == out["crawl_snapshot_id"]
    assert store.read("hub_degree", paths=[HUB])[0]["ui_importers"] == 7


def test_rerun_is_idempotent_and_new_run_replaces_state_without_losing_links(
    store: GraphStore, target: str, tmp_path: Path
):
    run1, cfg = make_run(tmp_path, target, "run-A")
    write_artifacts(run1, target)
    first = run_load(run1, cfg, store)
    store.write_links(hub_links(), "run-A")  # link stage output, written after load
    base = store.counts(target)
    again = run_load(run1, cfg, store)
    assert store.counts(target) == base and again["pruned"] == {k: 0 for k in again["pruned"]}
    assert all(v["nodes_created"] == 0 and v["relationships_created"] == 0 for v in again["layers"].values())
    # a second run: one screen and its transition vanished, and the bundle changed
    run2, _ = make_run(tmp_path, target, "run-B")
    write_artifacts(run2, target, drop_screen=True, bundle="bundle_B")
    second = run_load(run2, cfg, store)
    assert second["other_runs"] == ["run-A"]
    assert second["pruned"]["Screen"] == 1 and second["pruned"]["LEADS_TO"] == 1
    counts = store.counts(target)
    assert counts["Screen"] == 1 and counts["Run"] == 2 and counts["CodeFile"] == base["CodeFile"]
    # surviving nodes are re-stamped (run_id) but remember when they first appeared; links survive the reload
    row = store.driver.execute_query(
        "MATCH (s:Screen {target: $t, id: 'scr_one'}) "
        "RETURN s.run_id AS r, s.first_run_id AS f, s.bundle_hash AS b",
        t=target,
    ).records[0]
    assert (row["r"], row["f"], row["b"]) == ("run-B", "run-A", "bundle_B")
    owner = store.driver.execute_query(
        "MATCH (e:UIElement {target: $t}) WHERE 'o_a1' IN e.obs_ids RETURN e.owner_file AS f", t=target
    ).records
    assert owner[0]["f"] == "src/components/A1.vue"  # owner_file written by links is not lost
    assert (
        counts["IMPLEMENTED_BY"] == base["IMPLEMENTED_BY"] - 2
    )  # the two elements only on the dropped screen
    assert first["crawl_snapshot_id"] != second["crawl_snapshot_id"]


def test_run_load_with_nothing_to_load_fails_loudly(store: GraphStore, target: str, tmp_path: Path):
    run, cfg = make_run(tmp_path, target, "run-empty")
    with pytest.raises(FileNotFoundError, match="nothing to load"):
        run_load(run, cfg, store)


def test_run_load_skips_rows_of_another_target_and_invalid_rows(
    store: GraphStore, target: str, tmp_path: Path
):
    run, cfg = make_run(tmp_path, target, "run-bad")
    write_artifacts(run, target)
    stray = screen("some_other_app", "scr_stray", "/", [obs("x", "button", "X")])
    path = run.run_dir / "crawl" / "screens.jsonl"
    path.write_text(path.read_text() + stray.model_dump_json() + "\nnot json at all\n")
    out = run_load(run, cfg, store)
    assert out["layers"]["crawl"]["foreign_target_rows"] == 1 and out["layers"]["crawl"]["invalid_rows"] == 1
    assert out["counts"]["Screen"] == 2
    assert [f["code"] for f in run.failures.items].count("GRAPH_WRITE_FAILED") == 2


# ============================================================================ db: provenance, trace
def test_every_node_and_relationship_carries_run_and_first_run(hub: GraphStore, target: str):
    """Case 47: whatever the stage, nothing is written without the run that wrote it."""
    hub.write_requirements(
        [
            Requirement(
                id="req_a", target=target, text="t", quote="q", source_url="u", section="s", spec_sha="x"
            )
        ],
        "run_hub",
    )
    hub.write_links(
        [
            Link(
                kind="REALIZED_BY",
                src="req_a",
                dst="o_a1",
                confidence=0.7,
                method="bm25_judge",
                evidence_id="e",
            )
        ],
        "run_hub",
    )
    hub.write_gaps(
        [
            Gap(
                id="g",
                kind=GapKind.NO_UI_EVIDENCE,
                status="OBSERVED",
                subject_id="req_a",
                screens_searched=["scr_one"],
                bundle_hash="b",
            )
        ],
        "run_hub",
    )
    hub.write_flows([Flow(id="f", name="n", steps=["o_a1"], screens=["scr_one"])], "run_hub")
    hub.link_routes("run_hub", target=target)
    nodes = hub.driver.execute_query(
        "MATCH (n {target: $t}) WHERE n.run_id IS NULL OR (NOT n:Run AND n.first_run_id IS NULL) "
        "RETURN labels(n)[0] AS l, count(*) AS c",
        t=target,
    ).records
    rels = hub.driver.execute_query(
        "MATCH (a {target: $t})-[r]->() WHERE r.run_id IS NULL OR r.first_run_id IS NULL "
        "RETURN type(r) AS t, count(*) AS c",
        t=target,
    ).records
    assert [(r["l"], r["c"]) for r in nodes] == [] and [(r["t"], r["c"]) for r in rels] == []
    kinds = {r["type"] for r in hub.read("counts_by_rel", target=target)}
    assert {
        "IMPORTS",
        "RENDERS",
        "HANDLED_BY",
        "APPEARS_ON",
        "IMPLEMENTED_BY",
        "REALIZED_BY",
        "HAS_GAP",
        "SEARCHED",
        "STEP",
        "STARTS_AT",
        "ENDS_AT",
        "SERVED_BY",
    } <= kinds


def test_store_with_a_run_emits_graph_spans(store: GraphStore, target: str, tmp_path: Path):
    run, cfg = make_run(tmp_path, target, "run-trace")
    write_artifacts(run, target)
    traced = GraphStore.from_settings(run.settings, run, attempts=1)
    try:
        assert traced.target == target
        run_load(run, cfg, traced)
    finally:
        traced.close()
    lines = [json.loads(ln) for ln in (run.run_dir / "trace.jsonl").read_text().splitlines()]
    starts = [r for r in lines if r["event"] == "start"]
    kinds = {r["kind"] for r in starts}
    assert {"graph_write", "cypher_query"} <= kinds
    assert {
        "write_code",
        "write_crawl",
        "write_requirements",
        "link_routes",
        "prune_stale",
        "counts_by_label",
    } <= {r["name"] for r in starts}
    assert all(r["status"] == "ok" for r in lines if r["event"] == "end")
    assert not (run.run_dir / "failures.jsonl").read_text().strip()


def test_truncated_or_partly_invalid_artifact_never_prunes(store: GraphStore, target: str, tmp_path: Path):
    """A crawl that died early must not delete what an earlier complete run put in the graph."""
    run1, cfg = make_run(tmp_path, target, "run-full")
    write_artifacts(run1, target)
    run_load(run1, cfg, store)
    base = store.counts(target)
    assert base["Screen"] == 2 and base["UIElement"] == 6

    run2, _ = make_run(tmp_path, target, "run-died")
    write_artifacts(run2, target)
    (run2.run_dir / "crawl" / "screens.jsonl").write_text("")
    out = run_load(run2, cfg, store)
    assert out["layers"]["crawl"]["pruned"] is False and out["layers"]["crawl"]["screens"] == 0
    assert out["layers"]["code"].get("pruned") is None  # the other layers were read cleanly
    assert store.counts(target)["Screen"] == 2 and store.counts(target)["UIElement"] == 6

    run3, _ = make_run(tmp_path, target, "run-partial")
    write_artifacts(run3, target)
    good = (run1.run_dir / "crawl" / "screens.jsonl").read_text().splitlines()[0]
    (run3.run_dir / "crawl" / "screens.jsonl").write_text(good + '\n{"id": 7}\n')
    out = run_load(run3, cfg, store)
    assert out["layers"]["crawl"]["pruned"] is False and out["layers"]["crawl"]["invalid_rows"] == 1
    assert store.counts(target)["Screen"] == 2 and out["pruned"].get("Screen", 0) == 0


def test_bundle_drift_is_recorded(store: GraphStore, target: str, tmp_path: Path):
    run, cfg = make_run(tmp_path, target, "run-drift")
    write_artifacts(run, target, bundle="bundle_A")
    summary_path = run.run_dir / "crawl" / "summary.json"
    summary = CrawlSummary.model_validate_json(summary_path.read_text())
    summary_path.write_text(summary.model_copy(update={"bundle_hash_end": "bundle_B"}).model_dump_json())
    out = run_load(run, cfg, store)
    assert out["bundle_drift"] is True and out["bundle_hash"] == "bundle_A"
    row = store.read("runs", target=target)[0]
    assert (row["bundle_hash"], row["bundle_hash_end"]) == ("bundle_A", "bundle_B")


def test_relinking_replaces_links_and_recomputes_owner_file(hub: GraphStore, target: str):
    """The linker's layer is replaceable: links a newer run did not re-assert go, owner_file follows."""
    hub.write_requirements(
        [
            Requirement(
                id="req_a", target=target, text="t", quote="q", source_url="u", section="s", spec_sha="x"
            )
        ],
        "run_hub",
    )
    first = [
        Link(
            kind="IMPLEMENTED_BY",
            src="o_a1",
            dst="src/components/A4.vue",
            confidence=0.95,
            method="route",
            evidence_id="e1",
        ),
        Link(
            kind="REALIZED_BY", src="req_a", dst="o_a1", confidence=0.7, method="bm25_judge", evidence_id="e2"
        ),
        Link(
            kind="REALIZED_BY", src="req_a", dst="o_a2", confidence=0.6, method="bm25_judge", evidence_id="e3"
        ),
    ]
    hub.write_links(first, "link-1")
    hub.write_links([first[2]], "link-2")  # a re-link that no longer finds the others
    removed = hub.prune_stale("link-2", ["links"], target=target)
    assert removed["REALIZED_BY"] == 1
    assert removed["IMPLEMENTED_BY"] == 7  # the six from the fixture's first link run, plus A4
    owners = hub.driver.execute_query(
        "MATCH (e:UIElement {target: $t}) WHERE e.owner_file IS NOT NULL RETURN count(e) AS n", t=target
    ).records[0]["n"]
    assert owners == 0
    counts = hub.counts(target)
    assert counts["REALIZED_BY"] == 1 and counts["IMPLEMENTED_BY"] == 0
    assert hub.prune_stale("link-2", ["links"], target=target)["REALIZED_BY"] == 0  # idempotent
    with pytest.raises(ValueError, match="unknown layer"):
        hub.prune_stale("link-2", ["gaps"], target=target)


# ============================================================================================ wave 2
# Fix-wave tests. Each of these failed against the wave-1 module before the fix (see the module report).
HTTP_TABS = "src/components/http/Tabs.vue"
GQL_TABS = "src/components/graphql/Tabs.vue"
ROOT_PAGE = "src/pages/index.vue"
GQL_PAGE = "src/pages/graphql.vue"


def implemented(
    src: str,
    dst: str,
    *,
    conf: float = 0.9,
    method: str = "i18n_key",
    ev: str | None = None,
    screens: list[str] | None = None,
) -> Link:
    return Link(
        kind="IMPLEMENTED_BY",
        src=src,
        dst=dst,
        confidence=conf,
        method=method,
        evidence_id=ev or f"ev_{src}_{method}",
        screen_ids=screens or [],
    )


def realized(
    src: str,
    dst: str,
    *,
    conf: float = 0.7,
    method: str = "bm25_judge",
    ev: str | None = None,
    screens: list[str] | None = None,
) -> Link:
    return Link(
        kind="REALIZED_BY",
        src=src,
        dst=dst,
        confidence=conf,
        method=method,
        evidence_id=ev or f"ev_{src}_{dst}_{method}",
        screen_ids=screens or [],
    )


def requirement(target: str, rid: str, **kw: Any) -> Requirement:
    return Requirement(
        id=rid, target=target, text=f"text {rid}", quote=f"quote {rid}", source_url="u", section="s",
        spec_sha="x", **kw,
    )  # fmt: skip


def cypher_rows(store: GraphStore, cypher: str, **params: Any) -> list[dict[str, Any]]:
    return [r.data() for r in store.driver.execute_query(cypher, **params).records]


@pytest.fixture
def run_store(target: str) -> Iterator[tuple[GraphStore, RunContext]]:
    """A store bound to an in-memory RunContext, so failures and evidence checks are live."""
    run = RunContext.ephemeral(target)
    try:
        s = GraphStore.from_settings(neo4j_settings(), run, attempts=1)
    except GraphConnectionError as exc:
        pytest.skip(f"Neo4j is not reachable: {exc}")
    s.ensure_schema()
    try:
        yield s, run
    finally:
        s.clear_target(target)
        s.close()


def close_tab_world(store: GraphStore, target: str) -> None:
    """Two pages, each rendering its own 'Close tab' button: one canonical element on two screens."""
    files = [
        code_file(ROOT_PAGE, FileClass.PAGE),
        code_file(GQL_PAGE, FileClass.PAGE),
        code_file(HTTP_TABS),
        code_file(GQL_TABS),
    ]
    edges = [edge(ROOT_PAGE, HTTP_TABS, "RENDERS"), edge(GQL_PAGE, GQL_TABS, "RENDERS")]
    store.write_code(
        CodeIndex(
            target=target, repo="o/r", ref="m", snapshot_id="s", adapter="vue", files=files, edges=edges
        ),
        "r",
    )
    store.write_crawl(
        [
            screen(target, "scr_root", "/", [obs("c_root", "button", "Close tab")]),
            screen(target, "scr_gql", "/graphql", [obs("c_gql", "button", "Close tab")]),
        ],
        [],
        "r",
        "b",
    )


# ------------------------------------------------------------------------ canonical: owner_hint (case 46)
def test_owner_hint_is_the_first_key_part_and_keeps_legacy_ids(target: str):
    """Decision 2: key = (owner_hint or '', role, casefold(name), ordinal, scope). Same-named controls owned
    by different components stay apart; the same owner on two pages is one element; no owner merges as before,
    with the same id the wave-1 key produced (no id churn for owner-less crawls)."""

    def close(oid: str, owner: str | None) -> UIElementObs:
        return obs(oid, "button", "Close tab", owner_hint=owner)

    http_a = screen(target, "s_root", "/", [close("r", "components/http/Tabs.vue")])
    http_b = screen(target, "s_two", "/two", [close("r2", "components/http/Tabs.vue")])
    gql = screen(target, "s_gql", "/graphql", [close("g", "components/graphql/Tabs.vue")])
    canon = canonicalise([http_a, http_b, gql], target)
    assert canon.alias["r"] == canon.alias["r2"] != canon.alias["g"]
    assert len(canon.elements) == 2
    free = canonicalise(
        [
            screen(target, "s1", "/", [close("x", None)]),
            screen(target, "s2", "/graphql", [close("y", None)]),
        ],
        target,
    )
    assert free.alias["x"] == free.alias["y"]  # documented residual risk, now attributed per screen
    assert element_key(close("x", "O"))[0] == "O" and element_key(close("x", None))[0] == ""
    legacy = stable_id("uie", target, "button", "close tab", 0, "")
    assert free.alias["x"] == legacy == element_id(target, element_key(close("z", None)))
    assert canon.alias["r"] != legacy
    named = canonicalise([screen(target, "s1", "/", [close("n", "O")])], target)
    assert named.elements[0]["props"]["owner_hint"] == "O"


def test_owner_hint_splits_elements_in_the_graph(store: GraphStore, target: str):
    shot = [
        screen(target, "s_root", "/", [obs("r", "button", "Close tab", owner_hint="http/Tabs.vue")]),
        screen(target, "s_gql", "/graphql", [obs("g", "button", "Close tab", owner_hint="graphql/Tabs.vue")]),
    ]
    res = store.write_crawl(shot, [], "r", "b")
    assert res["elements"] == 2 and res["appearances"] == 2
    owners = cypher_rows(
        store,
        "MATCH (e:UIElement {target: $t}) RETURN e.owner_hint AS o ORDER BY o",
        t=target,
    )
    assert [r["o"] for r in owners] == ["graphql/Tabs.vue", "http/Tabs.vue"]


# ------------------------------------------------------------- per-screen link attribution (decision 3)
def test_link_screen_ids_limit_which_screens_an_impact_counts(store: GraphStore, target: str):
    """A 'Close tab' button on / and /graphql is one canonical element. Only / is derived from the changed
    file, so /graphql must not be reported (the over-approximation case 44 warns about)."""
    close_tab_world(store, target)
    res = store.write_links(
        [
            implemented("c_root", HTTP_TABS, screens=["scr_root"]),
            implemented("c_gql", GQL_TABS, screens=["scr_gql"]),
        ],
        "r",
    )
    assert res["implemented_by"] == 2
    http = store.read("impact", changed=[HTTP_TABS], hops=0)
    assert [(r["screen_id"], r["element_name"]) for r in http] == [("scr_root", "Close tab")]
    assert http[0]["seeds"] == [HTTP_TABS] and [e["file"] for e in http[0]["impl_edges"]] == [HTTP_TABS]
    gql = store.read("impact", changed=[GQL_TABS], hops=0)
    assert [r["screen_id"] for r in gql] == ["scr_gql"]
    both = store.read("impact", changed=[HTTP_TABS, GQL_TABS], hops=0)
    assert [r["screen_id"] for r in both] == ["scr_gql", "scr_root"]
    assert {r["element_id"] for r in both} == {http[0]["element_id"]}  # still one canonical element
    # through the page that renders it (hop 1 from the component's importer is not the element: no row)
    assert store.read("impact", changed=[ROOT_PAGE], hops=1) == []
    owners = store.read("element_by_file", paths=[HTTP_TABS, GQL_TABS])
    assert [(r["path"], r["screen_ids"]) for r in owners] == [
        (GQL_TABS, ["scr_gql"]),
        (HTTP_TABS, ["scr_root"]),
    ]


def test_links_without_screen_ids_apply_to_every_screen_of_the_element(store: GraphStore, target: str):
    close_tab_world(store, target)
    store.write_links([implemented("c_root", HTTP_TABS)], "r")
    rows = store.read("impact", changed=[HTTP_TABS], hops=0)
    assert [r["screen_id"] for r in rows] == ["scr_gql", "scr_root"]


def test_screen_ids_are_stored_sorted_unique_and_union_within_a_run(store: GraphStore, target: str):
    close_tab_world(store, target)

    def stored() -> list[str] | None:
        return cypher_rows(
            store,
            "MATCH (:UIElement {target: $t})-[r:IMPLEMENTED_BY]->(:CodeFile {path: $p}) "
            "RETURN r.screen_ids AS s",
            t=target,
            p=HTTP_TABS,
        )[0]["s"]

    store.write_links([implemented("c_root", HTTP_TABS, screens=["scr_z", "scr_b", "scr_b"])], "r1")
    assert stored() == ["scr_b", "scr_z"]
    store.write_links([implemented("c_root", HTTP_TABS, screens=["scr_a", "scr_z"])], "r1")
    assert stored() == ["scr_a", "scr_b", "scr_z"]  # union on rewrite within one run
    store.write_links([implemented("c_root", HTTP_TABS)], "r1")
    assert stored() == []  # "all screens" absorbs
    store.write_links([implemented("c_root", HTTP_TABS, screens=["scr_a"])], "r1")
    assert stored() == []  # and is never narrowed by a later partial claim of the same run
    store.write_links([implemented("c_root", HTTP_TABS, screens=["scr_a"])], "r2")
    assert stored() == ["scr_a"]  # a new run re-asserts the attribution from scratch
    both = [
        implemented("c_root", HTTP_TABS, screens=["scr_b"]),
        implemented("c_root", HTTP_TABS, screens=["scr_c", "scr_b"]),
    ]
    store.write_links(both, "r3")
    assert stored() == ["scr_b", "scr_c"]  # duplicates inside one call union too


def test_realized_by_screen_ids_scope_requirements_per_screen(store: GraphStore, target: str):
    close_tab_world(store, target)
    store.write_requirements([requirement(target, "req_a"), requirement(target, "req_b")], "r")
    store.write_links(
        [
            implemented("c_root", HTTP_TABS),
            implemented("c_gql", GQL_TABS),
            realized("req_a", "c_root", screens=["scr_root"]),
            realized("req_b", "c_root"),
        ],
        "r",
    )
    rows = {r["screen_id"]: r for r in store.read("impact", changed=[HTTP_TABS, GQL_TABS], hops=0)}
    assert [q["id"] for q in rows["scr_root"]["requirements"]] == ["req_a", "req_b"]
    assert [q["id"] for q in rows["scr_gql"]["requirements"]] == ["req_b"]


# --------------------------------------------------------------------------------- template discovery
def test_queries_modules_are_discovered_and_names_cannot_collide():
    """Decision 4: every blastradius.graph.queries_*.py exposing TEMPLATES is merged; a collision raises."""
    merged = queries.all_templates()
    downstream = {
        "files_using_keys",
        "elements_for_keys",
        "requirement_realizations",
        "unrealized_requirements",
        "screens_overview",
        "flows_for_elements",
        "links_for_screens",
    }
    assert downstream <= set(merged) and set(queries.TEMPLATES) <= set(merged)
    assert downstream.isdisjoint(queries.TEMPLATES)  # the core dict stays core-only
    names = [m.__name__ for m in queries.extension_modules()]
    assert "blastradius.graph.queries_downstream" in names
    assert all(n.startswith("blastradius.graph.queries_") for n in names) and names == sorted(names)
    assert queries.get("files_using_keys") is merged["files_using_keys"]
    clash = types.SimpleNamespace(
        __name__="blastradius.graph.queries_clash", TEMPLATES={"impact": queries.get("impact")}
    )
    with pytest.raises(ValueError, match=r"'impact' of .*queries_clash"):
        queries.merge_templates(queries.TEMPLATES, [clash])
    twin = types.SimpleNamespace(__name__="queries_twin", TEMPLATES={"only_here": queries.get("impact")})
    with pytest.raises(ValueError, match="only_here"):
        queries.merge_templates({}, [twin, twin])
    assert queries.merge_templates({}, [types.SimpleNamespace(__name__="no_templates")]) == {}


def test_extension_templates_validate_like_core_ones():
    tpl = queries.get("files_using_keys")
    with pytest.raises(ValueError, match="keys"):
        tpl.bind({"target": "t", "keys": "action.send"})
    with pytest.raises(ValueError, match="missing required parameter 'keys'"):
        tpl.bind({"target": "t"})
    assert tpl.bind({"target": "t", "keys": ("a",)}) == {"target": "t", "keys": ["a"]}
    with pytest.raises(ValueError, match="bundle_hash"):  # explicit None means any, omission is an error
        queries.get("unrealized_requirements").bind({"target": "t"})
    assert (
        queries.get("unrealized_requirements").bind({"target": "t", "bundle_hash": None})["bundle_hash"]
        is None
    )
    with pytest.raises(ValueError, match="bundle_hash"):
        queries.get("absence_near").bind({"target": "t", "changed": []})


# ------------------------------------------------------------------------------ prune guard (decision 1)
def set_summary(run: RunContext, **update: Any) -> None:
    path = run.run_dir / "crawl" / "summary.json"
    path.write_text(
        CrawlSummary.model_validate_json(path.read_text()).model_copy(update=update).model_dump_json()
    )


INCOMPLETE_CRAWLS: dict[str, tuple[dict[str, Any] | None, str]] = {
    "no_summary": (None, "summary_missing"),
    "circuit_breaker": ({"stop_reason": "circuit_breaker"}, "stop_reason:circuit_breaker"),
    "error": ({"stop_reason": "error"}, "stop_reason:error"),
    "screens_mismatch": ({"screens": 7}, "screens_mismatch"),
    "transitions_mismatch": ({"transitions": 3}, "transitions_mismatch"),
}


@pytest.mark.parametrize("stop", ["frontier_empty", "action_budget", "time_budget"])
def test_a_budget_bounded_crawl_is_a_complete_observation_and_prunes(
    store: GraphStore, target: str, tmp_path: Path, stop: str
):
    """A crawl that stopped on its configured budget IS the observation the target asked for; refusing to prune it
    would let screens from earlier runs accumulate forever (found on the real 150-action crawl). Goal-directed
    re-crawls write to their own artifact_subdir and never reach this guard."""
    run1, cfg = make_run(tmp_path, target, "run-full")
    write_artifacts(run1, target)
    run_load(run1, cfg, store)
    assert store.counts(target)["Screen"] == 2
    run2, _ = make_run(tmp_path, target, f"run-{stop}")
    write_artifacts(run2, target, drop_screen=True)
    set_summary(run2, stop_reason=stop)
    out = run_load(run2, cfg, store)
    assert "prune_skipped" not in out["layers"]["crawl"], out["notices"]
    assert out["pruned"]["Screen"] == 1 and store.counts(target)["Screen"] == 1


@pytest.mark.parametrize("case", sorted(INCOMPLETE_CRAWLS))
def test_an_incomplete_crawl_never_prunes_and_says_so(
    store: GraphStore, target: str, tmp_path: Path, case: str
):
    """Decision 1: a crawl that stopped early (budget, error, breaker), has no summary, or whose summary
    disagrees with its rows is a partial observation (a goal-directed re-crawl is partial by design)."""
    run1, cfg = make_run(tmp_path, target, "run-full")
    write_artifacts(run1, target)
    run_load(run1, cfg, store)
    store.write_links(hub_links(), "run-full")
    base = store.counts(target)
    assert base["Screen"] == 2 and base["UIElement"] == 6 and base["IMPLEMENTED_BY"] == 6

    run2, _ = make_run(tmp_path, target, f"run-{case}")
    write_artifacts(run2, target, drop_screen=True)
    update, reason = INCOMPLETE_CRAWLS[case]
    if update is None:
        (run2.run_dir / "crawl" / "summary.json").unlink()
    else:
        set_summary(run2, **update)
    out = run_load(run2, cfg, store)
    crawl = out["layers"]["crawl"]
    assert crawl["pruned"] is False
    assert any(r.startswith(reason) for r in crawl["prune_skipped"]), crawl["prune_skipped"]
    assert any(reason in n and "crawl" in n for n in out["notices"]), out["notices"]
    assert json.loads((run2.run_dir / "graph" / "load.json").read_text())["notices"] == out["notices"]
    now = store.counts(target)
    for key in ("Screen", "UIElement", "IMPLEMENTED_BY", "APPEARS_ON"):
        assert now[key] == base[key], key
    assert out["pruned"].get("Screen", 0) == 0 and out["pruned"].get("UIElement", 0) == 0
    # the rows it did read are loaded, and the other layers (read cleanly) still reconcile
    assert crawl["screens"] == 1 and "code" not in str(out["notices"])


def test_a_complete_crawl_still_prunes(store: GraphStore, target: str, tmp_path: Path):
    """The guard must not turn pruning off: frontier_empty, matching counts, clean rows replace the layer."""
    run1, cfg = make_run(tmp_path, target, "run-A")
    write_artifacts(run1, target)
    run_load(run1, cfg, store)
    run2, _ = make_run(tmp_path, target, "run-B")
    write_artifacts(run2, target, drop_screen=True)
    out = run_load(run2, cfg, store)
    assert (
        out["layers"]["crawl"].get("pruned") is None and out["layers"]["crawl"].get("prune_skipped") is None
    )
    assert out["pruned"]["Screen"] == 1 and out["notices"] == []
    assert store.counts(target)["Screen"] == 1


def rewrite_index(run: RunContext, mutate: Any) -> None:
    path = run.run_dir / "code" / "index.json"
    index = CodeIndex.model_validate_json(path.read_text())
    path.write_text(mutate(index).model_dump_json())


def test_a_partial_or_edgeless_code_index_never_prunes(store: GraphStore, target: str, tmp_path: Path):
    run1, cfg = make_run(tmp_path, target, "run-code-full")
    write_artifacts(run1, target)
    run_load(run1, cfg, store)
    base = store.counts(target)
    assert base["CodeFile"] == 11 and base["IMPORTS"] == 6 and base["RENDERS"] == 4

    def fewer(index: CodeIndex, **stats: Any) -> CodeIndex:
        keep = {f.path for f in index.files[:4]}
        return index.model_copy(
            update={
                "files": [f for f in index.files if f.path in keep],
                "edges": [e for e in index.edges if e.src in keep and e.dst in keep],
                "stats": stats,
            }
        )

    for name, stats in (
        ("parse-partial", {"parse_partial": True}),
        ("failed-files", {"files_failed": 3}),
        ("i18n-parse-error", {"i18n_parse_error": True}),
    ):
        run, _ = make_run(tmp_path, target, f"run-{name}")
        write_artifacts(run, target)
        rewrite_index(run, lambda i, s=stats: fewer(i, **s))
        out = run_load(run, cfg, store)
        code = out["layers"]["code"]
        assert code["pruned"] is False and any("parse_partial" in r for r in code["prune_skipped"]), name
        assert store.counts(target)["CodeFile"] == base["CodeFile"], name
        assert out["pruned"].get("CodeFile", 0) == 0 and out["pruned"].get("IMPORTS", 0) == 0, name

    run, _ = make_run(tmp_path, target, "run-edgeless")
    write_artifacts(run, target)
    rewrite_index(run, lambda i: i.model_copy(update={"edges": []}))
    out = run_load(run, cfg, store)
    assert out["layers"]["code"]["pruned"] is False
    assert any("edges_empty" in r for r in out["layers"]["code"]["prune_skipped"])
    counts = store.counts(target)
    assert counts["IMPORTS"] == base["IMPORTS"] and counts["RENDERS"] == base["RENDERS"]

    run, _ = make_run(tmp_path, target, "run-clean-shrink")  # complete, parsed cleanly: pruning is allowed
    write_artifacts(run, target)
    rewrite_index(run, fewer)
    out = run_load(run, cfg, store)
    assert out["layers"]["code"].get("pruned") is None
    kept = 4 + 1  # four files, plus the route's file as a stub (the route handler is referenced, not listed)
    assert out["pruned"]["CodeFile"] == base["CodeFile"] - kept and store.counts(target)["CodeFile"] == kept


def test_an_edgeless_first_load_is_not_blocked(store: GraphStore, target: str, tmp_path: Path):
    """No previous edges means nothing could be lost, so an edge-free index is a legitimate first state."""
    run, cfg = make_run(tmp_path, target, "run-first")
    write_artifacts(run, target)
    rewrite_index(run, lambda i: i.model_copy(update={"edges": []}))
    out = run_load(run, cfg, store)
    assert out["layers"]["code"].get("pruned") is None and store.counts(target)["IMPORTS"] == 0


# ------------------------------------------------------------------------------- secrets in connect errors
def test_uri_userinfo_never_reaches_messages_or_failures():
    run = RunContext.ephemeral("t")
    settings = Settings(neo4j_uri="bolt://neo:Sup3rS3cret@127.0.0.1:1", neo4j_password="pw-from-settings")
    with pytest.raises(GraphConnectionError) as info:
        GraphStore.from_settings(settings, run, attempts=2, backoff_s=0)
    blob = json.dumps([str(info.value), run.failures.items], default=str)
    assert "Sup3rS3cret" not in blob and "pw-from-settings" not in blob
    assert "127.0.0.1:1" in str(info.value)
    assert run.failures.items[0]["context"] == {"uri": "bolt://127.0.0.1:1", "attempts": 2}


def test_unparseable_uri_is_a_connection_failure_not_a_value_error():
    run = RunContext.ephemeral("t")
    for uri in (
        "bolt://localhost:notaport",
        "bolt://u:Sup3rS3cret@localhost:notaport",
        "nope",
        "bolt://[::1",
    ):
        with pytest.raises(GraphConnectionError) as info:
            GraphStore.from_settings(Settings(neo4j_uri=uri), run, attempts=1)
        assert info.value.code is FailureCode.GRAPH_CONN_FAILED
        assert "Sup3rS3cret" not in str(info.value)
    assert [f["code"] for f in run.failures.items] == ["GRAPH_CONN_FAILED"] * 4


def test_safe_uri_is_total():
    for uri in ("", "x", "bolt://", "bolt://[::1", "bolt://h:99999", "bolt://u:p4ss@h:abc", "://u:p4ss@h"):
        out = store_module._safe_uri(uri)
        assert isinstance(out, str) and "p4ss" not in out and "@" not in out, uri
    assert store_module._safe_uri("neo4j+s://u:pw@db.example.com:7687/x") == "neo4j+s://db.example.com:7687"
    assert store_module._safe_uri("bolt://[::1]:7687") == "bolt://[::1]:7687"


def test_auth_failure_is_not_retried_and_records_the_real_attempt_count(monkeypatch):
    from neo4j.exceptions import AuthError

    store_mod = store_module
    calls: list[int] = []

    def refuse(*a: Any, **k: Any) -> Any:
        calls.append(1)
        raise AuthError("The client is unauthorized due to authentication failure.")

    monkeypatch.setattr(store_mod.GraphDatabase, "driver", refuse)
    run = RunContext.ephemeral("t")
    with pytest.raises(GraphConnectionError):
        GraphStore.from_settings(Settings(), run, attempts=3, backoff_s=0)
    assert len(calls) == 1
    assert run.failures.items[0]["context"]["attempts"] == 1


# ------------------------------------------------------------------------------------ determinism (rule 4)
def diamond_index(target: str, seed: int) -> CodeIndex:
    """hub <- A1, A2, A3 <- B1 (three equal-length witnesses), a parallel IMPORTS+RENDERS pair, a seed-
    independent logical graph loaded with a seed-dependent file and edge insertion order."""
    files = [code_file(p) for p in ("hub.ts", "A1.vue", "A2.vue", "A3.vue", "B1.vue", "C1.vue")]
    edges = [
        edge("A1.vue", "hub.ts", "IMPORTS", ["x"]),
        edge("A2.vue", "hub.ts", "IMPORTS", ["y"]),
        edge("A3.vue", "hub.ts", "RENDERS", ["z"]),
        edge("B1.vue", "A1.vue", "RENDERS"),
        edge("B1.vue", "A2.vue", "IMPORTS"),
        edge("B1.vue", "A3.vue", "RENDERS"),
        edge("B1.vue", "A3.vue", "IMPORTS"),
        edge("C1.vue", "B1.vue", "RENDERS"),
    ]
    rnd = random.Random(seed)
    rnd.shuffle(files)
    rnd.shuffle(edges)
    return CodeIndex(
        target=target, repo="o/r", ref="m", snapshot_id="s", adapter="vue", files=files, edges=edges
    )


def test_impact_rows_do_not_depend_on_edge_insertion_order(target: str):
    """Review wave 1: the witness (via, kinds, symbols, specifiers) and the evidence ids recomputed from it
    used to follow relationship insertion order. One logical graph must give one answer."""
    seen: dict[str, int] = {}
    for variant in range(10):
        t = target  # one target name: element ids hash the target, and each pass clears it
        try:
            s = GraphStore.from_settings(neo4j_settings(), attempts=1, target=t)
        except GraphConnectionError as exc:
            pytest.skip(str(exc))
        try:
            s.ensure_schema()
            s.write_code(diamond_index(t, variant), "r")
            s.write_crawl(
                [
                    screen(
                        t,
                        "s0",
                        "/",
                        [obs("o_b1", "button", "B1"), obs("o_c1", "button", "C1"), obs("o_h", "button", "H")],
                    )
                ],
                [],
                "r",
                "b",
            )
            s.write_links(
                [
                    implemented("o_b1", "B1.vue"),
                    implemented("o_c1", "C1.vue"),
                    implemented("o_h", "hub.ts"),
                ],
                "r",
            )
            rows = s.read("impact", changed=["hub.ts"], hops=3)
            key = json.dumps(rows, sort_keys=True)
            seen[key] = seen.get(key, 0) + 1
        finally:
            s.clear_target(t)
            s.close()
    assert len(seen) == 1, f"{len(seen)} different answers for one graph"
    rows = json.loads(next(iter(seen)))
    b1 = next(r for r in rows if r["element_name"] == "B1")
    assert b1["via"] == ["B1.vue", "A1.vue", "hub.ts"]  # lexicographically smallest witness
    assert b1["kinds"] == ["RENDERS", "IMPORTS"] and b1["symbols"][1] == ["x"]


def test_impact_lists_are_sorted(hub: GraphStore, target: str):
    reqs = [requirement(target, f"req_{c}") for c in "cab"]
    hub.write_requirements(reqs, "run_hub")
    hub.write_flows(
        [Flow(id=f"flow_{c}", name=f"n {c}", steps=["o_a1"], screens=["scr_one"]) for c in "cab"], "run_hub"
    )
    hub.write_links(
        [realized(f"req_{c}", "o_a1", method=m) for c in "cab" for m in ("llm_pick", "human")], "run_hub"
    )
    row = hub.read("impact", changed=["src/components/A1.vue"], hops=0)[0]
    assert [f["id"] for f in row["flows"]] == ["flow_a", "flow_b", "flow_c"]
    got = [(q["id"], q["via"], q["method"]) for q in row["requirements"]]
    assert got == sorted(got) and len(got) == 6
    assert row["seeds"] == sorted(row["seeds"])
    edges = [(e["file"], e["method"]) for e in row["impl_edges"]]
    assert edges == sorted(edges)


def test_graph_state_and_reads_are_identical_across_hash_seeds(target: str):
    """Rule 4 for the graph: the same inputs give the same nodes, relationships, properties and read rows in
    processes with different string-hash seeds (target names are masked; run ids and clocks are not read)."""
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    snippet = """
import hashlib, json, os, sys
sys.path.insert(0, "tests")
import test_graph as tg
from blastradius.graph.store import GraphStore
from blastradius.models import *
T = os.environ["GRAPH_TEST_TARGET"]
s = GraphStore.from_settings(tg.neo4j_settings(), attempts=1, target=T)
try:
    s.ensure_schema()
    idx = tg.hub_index(T)
    idx.i18n = {"action.send": "Send", "menu.copy": "Copy"}
    idx.key_uses = [KeyUse(file="src/components/A1.vue", key="action.send"),
                    KeyUse(file="src/components/A2.vue", key="action.send"),
                    KeyUse(file="src/components/A3.vue", key="menu.", dynamic=True)]
    idx.edges.append(tg.edge("src/components/A1.vue", tg.HUB, symbols=["Other", "Hub"]))
    s.write_code(idx, "r1")
    shots = tg.hub_screens(T)
    trans = [Transition(id="t1", from_screen="scr_one", to_screen="scr_two", element_id="o_a1", label="A1")]
    s.write_crawl(shots, trans, "r1", "b1")
    s.write_requirements([tg.requirement(T, "req_a"), tg.requirement(T, "req_b")], "r1")
    links = tg.hub_links() + [tg.realized("req_a", "o_a1", screens=["scr_two", "scr_one"]),
                              tg.realized("req_b", "scr_two")]
    s.write_links(links, "r1")
    s.write_gaps([Gap(id="g", kind=GapKind.NO_UI_EVIDENCE, status="OBSERVED", subject_id="req_a",
                      screens_searched=["scr_two", "scr_one"], bundle_hash="b1")], "r1")
    s.write_flows([Flow(id="f", name="n", steps=["o_a1", "o_a2"], screens=["scr_one", "scr_two"])], "r1")
    s.link_routes("r1", target=T)
    nodes = s.driver.execute_query(
        "MATCH (n {target: $t}) RETURN labels(n)[0] AS l, properties(n) AS p "
        "ORDER BY l, p.id, p.path, p.key, p.pattern",
        t=T).records
    rels = s.driver.execute_query(
        "MATCH (a {target: $t})-[r]->(b) RETURN labels(a)[0] AS la, "
        "coalesce(a.id, a.path, a.key, a.pattern) AS a, type(r) AS ty, properties(r) AS p, "
        "labels(b)[0] AS lb, coalesce(b.id, b.path, b.key, b.pattern) AS b "
        "ORDER BY ty, a, b, p.method, p.order, p.obs_id, p.tid", t=T).records
    reads = {name: s.read(name, **kw) for name, kw in {
        "impact": {"changed": [tg.HUB, "src/components/A1.vue"], "hops": 3, "forward": 1},
        "hub_degree": {"paths": [tg.HUB], "hops": 3},
        "element_by_file": {"paths": [tg.HUB, "src/components/A2.vue"]},
        "unmapped_ui_elements": {},
        "transitions": {},
        "absence_near": {"changed": ["src/pages/B1.vue"], "bundle_hash": None},
        "files_using_keys": {"keys": ["action.send", "menu.copy"]},
        "links_for_screens": {"screen_ids": ["scr_one", "scr_two"]},
    }.items()}
    def scrub(o):
        return json.loads(json.dumps(o, sort_keys=True, default=str).replace(T, "T"))
    dump = scrub({"nodes": [r.data() for r in nodes], "rels": [r.data() for r in rels], "reads": reads})
    for n in dump["nodes"]:
        for k in ("first_seen_at", "updated_at"):
            n["p"].pop(k, None)
    print(hashlib.sha256(json.dumps(dump, sort_keys=True).encode()).hexdigest())
finally:
    s.clear_target(T)
    s.close()
"""
    digests = []
    for seed in (0, 1, 7):
        env = {
            **os.environ,
            "PYTHONHASHSEED": str(seed),
            "PYTHONPATH": str(repo / "src"),
            "GRAPH_TEST_TARGET": target,
        }
        out = subprocess.run(
            [sys.executable, "-c", snippet],
            check=False,
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert out.returncode == 0, out.stderr[-1500:]
        digests.append(out.stdout.strip().splitlines()[-1])
    assert len(set(digests)) == 1, digests


# ------------------------------------------------------------------- closure cost and read failures
def layered_index(target: str, layers: int, width: int, fan: int, seed: int = 3) -> CodeIndex:
    """`layers` x `width` files; every file in layer l imports `fan` files of layer l-1."""
    rnd = random.Random(seed)
    names = [[f"L{layer}/f{i}.vue" for i in range(width)] for layer in range(layers)]
    files = [code_file(p) for layer in names for p in layer]
    edges = [
        edge(p, d, "IMPORTS" if rnd.random() < 0.7 else "RENDERS", ["a", "b"])
        for layer in range(1, layers)
        for p in names[layer]
        for d in rnd.sample(names[layer - 1], fan)
    ]
    return CodeIndex(
        target=target, repo="o/r", ref="m", snapshot_id="s", adapter="vue", files=files, edges=edges
    )


def profile_rows(store: GraphStore, template: str, **params: Any) -> int:
    """Total rows flowing through every operator of the template's executed plan (PROFILE)."""
    tpl = queries.get(template)
    bound = tpl.bind({"target": store.target, **params})
    with store.driver.session(default_access_mode=neo4j.READ_ACCESS) as session:
        plan = session.run("PROFILE " + tpl.cypher, bound).consume().profile

    def total(node: dict[str, Any]) -> int:
        return int(node.get("rows", 0)) + sum(total(c) for c in node.get("children", []))

    return total(plan)


def test_closure_work_is_per_node_not_per_path(store: GraphStore, target: str):
    """Review wave 1: path enumeration made rows grow like fan-in^hops (the transaction memory pool was
    exhausted at 28,000 edges and hops=3). With a distinct frontier per hop the work is O(edges)."""
    index = layered_index(target, layers=5, width=40, fan=18)
    store.write_code(index, "r")
    store.write_crawl(
        [screen(target, "s0", "/", [obs(f"o{i}", "button", f"e{i}") for i in range(len(index.files))])],
        [],
        "r",
        "b",
    )
    store.write_links([implemented(f"o{i}", f.path) for i, f in enumerate(index.files)], "r")
    seeds = [f"L0/f{i}.vue" for i in range(3)]
    rows = store.read("impact", changed=seeds, hops=3)
    importers: dict[str, set[str]] = {}
    for e in index.edges:
        importers.setdefault(e.dst, set()).add(e.src)
    dist = {s: 0 for s in seeds}
    frontier = list(seeds)
    for hop in (1, 2, 3):
        nxt = []
        for f in frontier:
            for i in sorted(importers.get(f, ())):
                if i not in dist:
                    dist[i] = hop
                    nxt.append(i)
        frontier = nxt
    assert {r["file"]: r["hops"] for r in rows} == dist
    n_edges = len(index.edges)
    assert (
        profile_rows(store, "impact", changed=seeds, hops=3) < 20 * n_edges
    )  # paths would be ~ 18**3 * 3 * 9


def test_read_fails_fast_on_a_memory_pool_error_without_the_driver_retry_loop():
    """Review wave 1: execute_read retried MemoryPoolOutOfMemoryError for ~45 s while stressing the shared
    database. read() must make one attempt and raise a read-specific error carrying a hint."""
    from neo4j.exceptions import TransientError

    class MemoryPool(TransientError):
        code = "Neo.TransientError.General.MemoryPoolOutOfMemoryError"

    attempts: list[int] = []

    class Tx:
        def run(self, *a: Any, **k: Any) -> Any:
            attempts.append(1)
            raise MemoryPool("limit 716.8 MiB reached")

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    class Session(Tx):
        def begin_transaction(self, **kw: Any) -> Tx:
            return Tx()

        def execute_read(self, work: Any, **kw: Any) -> Any:  # the real driver's behaviour: retry transients
            for _ in range(5):
                try:
                    return work(Tx())
                except TransientError as exc:
                    if not exc.is_retryable():
                        raise
            raise MemoryPool("retried out")

    class Driver:
        def session(self, **kw: Any) -> Session:
            return Session()

    run = RunContext.ephemeral("t")
    fake = GraphStore(Driver(), run=run, target="t")  # type: ignore[arg-type]
    with pytest.raises(store_module.GraphReadError) as info:
        fake.read("counts_by_label")
    assert len(attempts) == 1
    assert "memory" in str(info.value).lower() and "read counts_by_label" in str(info.value)
    assert not isinstance(info.value, GraphConnectionError)
    assert run.failures.items[0]["message"].startswith("read counts_by_label")


def test_read_and_write_failures_are_distinguishable(store: GraphStore, target: str, monkeypatch):
    monkeypatch.setitem(
        queries.TEMPLATES,
        "broken",
        queries.Template("broken", "MATCH (n RETURN n", {"target": queries._str()}),
    )
    with pytest.raises(store_module.GraphReadError) as read_error:
        store.read("broken", target=target)
    assert isinstance(read_error.value, GraphError) and "read broken" in str(read_error.value)
    monkeypatch.setattr(writes, "RUN", "THIS IS NOT CYPHER")
    with pytest.raises(GraphError) as write_error:
        store.write_requirements([requirement(target, "r")], "r1")
    assert not isinstance(write_error.value, store_module.GraphReadError)


# ------------------------------------------------------------------- gaps and flows: atomic, idempotent
def run_props(store: GraphStore, target: str, rel: str) -> list[tuple[Any, ...]]:
    """(from, to, first_run_id, run_id) of every relationship of one type, in a fixed order."""
    rows = cypher_rows(
        store,
        f"MATCH (a {{target: $t}})-[r:{rel}]->(b) "
        "RETURN coalesce(a.id, a.path) AS src, coalesce(b.id, b.path) AS dst, "
        "r.first_run_id AS first_run, r.run_id AS last_run, r.order AS step "
        "ORDER BY src, dst, step",
        t=target,
    )
    return [(x["src"], x["dst"], x["first_run"], x["last_run"]) for x in rows]


def test_identical_gap_and_flow_rewrites_change_nothing_and_keep_first_run(hub: GraphStore, target: str):
    """Review wave 1: reset-then-create made every rewrite report new relationships and move first_run_id."""
    hub.write_requirements([requirement(target, "req_a")], "r1")
    gap = Gap(
        id="g",
        kind=GapKind.NO_UI_EVIDENCE,
        status="OBSERVED",
        subject_id="req_a",
        screens_searched=["scr_one", "scr_two"],
        bundle_hash="b1",
    )
    flow = Flow(id="f", name="n", steps=["o_a1", "o_a2"], screens=["scr_one", "scr_two"])
    first_g, first_f = hub.write_gaps([gap], "r1"), hub.write_flows([flow], "r1")
    assert first_g["relationships_created"] == 3 and first_f["relationships_created"] == 4
    searched = run_props(hub, target, "SEARCHED")
    steps = run_props(hub, target, "STEP")
    assert {x[2] for x in searched + steps} == {"r1"}

    again_g, again_f = hub.write_gaps([gap], "r2"), hub.write_flows([flow], "r2")
    for res in (again_g, again_f):
        assert res["nodes_created"] == 0 and res["relationships_created"] == 0
        assert res["relationships_deleted"] == 0
    for rel in ("SEARCHED", "STEP", "STARTS_AT", "ENDS_AT", "HAS_GAP"):
        rows = run_props(hub, target, rel)
        assert rows and {x[2] for x in rows} == {"r1"} and {x[3] for x in rows} == {"r2"}, rel
    assert again_g["searched"] == 2 and again_f["steps"] == 2


def test_changed_gap_and_flow_replace_only_what_differs(hub: GraphStore, target: str):
    hub.write_requirements([requirement(target, "req_a")], "r1")
    gap = Gap(
        id="g", kind=GapKind.NO_UI_EVIDENCE, status="OBSERVED", subject_id="req_a",
        screens_searched=["scr_one", "scr_two"], bundle_hash="b1",
    )  # fmt: skip
    hub.write_gaps([gap], "r1")
    hub.write_flows(
        [Flow(id="f", name="n", steps=["o_a1", "o_a2", "o_c1"], screens=["scr_one", "scr_two"])], "r1"
    )
    shrunk = hub.write_gaps([gap.model_copy(update={"screens_searched": ["scr_two"]})], "r2")
    assert shrunk["relationships_deleted"] == 1 and shrunk["relationships_created"] == 0
    assert run_props(hub, target, "SEARCHED") == [("g", "scr_two", "r1", "r2")]
    grown = hub.write_gaps([gap], "r3")
    assert grown["relationships_created"] == 1 and grown["relationships_deleted"] == 0
    # the flow loses a step, swaps the element at order 1 and ends elsewhere
    res = hub.write_flows(
        [Flow(id="f", name="n", steps=["o_a1", "o_c1"], screens=["scr_one", "scr_one"])], "r4"
    )
    assert res["relationships_deleted"] == 3  # STEP 1->a2, STEP 2->c1, ENDS_AT two
    assert res["relationships_created"] == 2  # STEP 1->c1, ENDS_AT one
    step_rows = cypher_rows(
        hub,
        "MATCH (:Flow {target: $t, id: 'f'})-[r:STEP]->(e) RETURN r.order AS o, e.name AS n ORDER BY o",
        t=target,
    )
    assert [(r["o"], r["n"]) for r in step_rows] == [(0, "A1"), (1, "C1 header")]
    ends = cypher_rows(hub, "MATCH (:Flow {target: $t})-[:ENDS_AT]->(s) RETURN s.id AS s", t=target)
    assert [r["s"] for r in ends] == ["scr_one"]
    empty = hub.write_flows([Flow(id="f", name="n", steps=[], screens=[])], "r5")
    assert empty["relationships_deleted"] == 4
    assert hub.counts(target)["STEP"] == 0 and hub.counts(target)["STARTS_AT"] == 0


def test_a_failed_gap_or_flow_rewrite_leaves_the_old_state_intact(hub: GraphStore, target: str, monkeypatch):
    """Delete-then-create across transactions left a Gap with no SEARCHED edges after a crash."""
    hub.write_requirements([requirement(target, "req_a")], "r1")
    gap = Gap(
        id="g", kind=GapKind.NO_UI_EVIDENCE, status="OBSERVED", subject_id="req_a",
        screens_searched=["scr_one", "scr_two"], bundle_hash="b1",
    )  # fmt: skip
    flow = Flow(id="f", name="n", steps=["o_a1", "o_a2"], screens=["scr_one", "scr_two"])
    hub.write_gaps([gap], "r1")
    hub.write_flows([flow], "r1")
    before = {rel: run_props(hub, target, rel) for rel in ("SEARCHED", "STEP", "STARTS_AT", "ENDS_AT")}
    monkeypatch.setattr(writes, "SEARCHED", "THIS IS NOT CYPHER")
    monkeypatch.setattr(writes, "ENDS_AT", "THIS IS NOT CYPHER")
    changed_gap = gap.model_copy(update={"status": "CONFIRMED_ABSENT", "screens_searched": ["scr_one"]})
    with pytest.raises(GraphError):
        hub.write_gaps([changed_gap], "r2")
    with pytest.raises(GraphError):
        hub.write_flows([Flow(id="f", name="renamed", steps=["o_c1"], screens=["scr_two"])], "r2")
    after = {rel: run_props(hub, target, rel) for rel in before}
    assert after == before
    status = cypher_rows(hub, "MATCH (g:Gap {target: $t}) RETURN g.status AS s", t=target)[0]["s"]
    assert status == "OBSERVED"
    name = cypher_rows(hub, "MATCH (f:Flow {target: $t}) RETURN f.name AS n", t=target)[0]["n"]
    assert name == "n"


# ------------------------------------------------------------------------------------- write_links
def test_write_links_dedupes_deterministically(run_store: tuple[GraphStore, RunContext], target: str):
    store, run = run_store
    store.write_code(hub_index(target), "r")
    store.write_crawl(hub_screens(target), [], "r", "b")

    def ev(text: str) -> str:
        return run.evidence.add("link.test", text, text)

    hi = implemented("o_a1", "src/components/A4.vue", conf=0.95, method="route", ev=ev("hi"))
    lo = implemented("o_a1", "src/components/A4.vue", conf=0.10, method="route", ev=ev("lo"))
    tie_a = implemented(
        "o_a2", "src/components/A4.vue", conf=0.5, method="route", ev=ev("a-ev"), screens=["s1"]
    )
    tie_b = implemented(
        "o_a2", "src/components/A4.vue", conf=0.5, method="route", ev=ev("b-ev"), screens=["s2"]
    )

    def stored() -> list[tuple[Any, ...]]:
        rows = cypher_rows(
            store,
            "MATCH (e:UIElement {target: $t})-[r:IMPLEMENTED_BY]->"
            "(:CodeFile {path: 'src/components/A4.vue'}) "
            "RETURN e.name AS n, r.confidence AS c, r.evidence_id AS ev, r.screen_ids AS s ORDER BY n",
            t=target,
        )
        return [(r["n"], r["c"], r["ev"], r["s"]) for r in rows]

    first = store.write_links([lo, hi, tie_b, tie_a], "r1")
    one = stored()
    store.write_links([hi, lo, tie_a, tie_b], "r2")
    two = stored()
    assert one == two
    assert one[0][:3] == ("A1", 0.95, ev("hi")) and one[1][:3] == ("A2", 0.5, min(ev("a-ev"), ev("b-ev")))
    assert one[1][3] == ["s1", "s2"]
    assert first["implemented_by"] == 2 and first["duplicates_merged"] == 2


def test_write_links_rejects_bad_confidence_unknown_evidence_and_records_missing_endpoints(
    run_store: tuple[GraphStore, RunContext], target: str
):
    store, run = run_store
    store.write_code(hub_index(target), "r")
    store.write_crawl(hub_screens(target), [], "r", "b")
    store.write_requirements([requirement(target, "req_a")], "r")
    good_ev = run.evidence.add("link.test", "ok", "ok")
    links = [
        implemented("o_a1", "src/components/A1.vue", ev=good_ev),
        implemented("o_a2", "src/components/A2.vue", conf=float("nan"), ev=good_ev),
        implemented("o_a3", "src/components/A3.vue", conf=1.5, ev=good_ev),
        implemented("o_b1", "src/pages/B1.vue", conf=-0.1, ev=good_ev),
        implemented("o_c1", "src/pages/C1.vue", ev="ev_never_written"),
        implemented("o_ghost", "src/pages/B1.vue", ev=good_ev),
        implemented("o_hub", "src/gone.vue", ev=good_ev),
        realized("req_nope", "o_a1", ev=good_ev),
    ]
    res = store.write_links(links, "r")
    assert res["implemented_by"] == 1 and res["skipped_invalid_confidence"] == 3
    assert res["skipped_unknown_evidence"] == 1
    assert res["skipped_missing_src"] == 2 and res["skipped_missing_dst"] == 1
    assert store.counts(target)["IMPLEMENTED_BY"] == 1
    messages = [f["message"] for f in run.failures.items]
    assert all(f["code"] == "GRAPH_WRITE_FAILED" for f in run.failures.items)
    assert any("confidence" in m and "3" in m for m in messages)
    assert any("evidence" in m for m in messages)
    assert any("not in the graph" in m and "o_ghost" in m for m in messages)
    assert len(messages) == 3


def test_prune_never_removes_human_links(hub: GraphStore, target: str):
    hub.write_requirements([requirement(target, "req_a")], "run_hub")
    hub.write_links(
        [
            implemented("o_a1", "src/components/A2.vue", conf=1.0, method="human", ev="ev_h"),
            realized("req_a", "o_a1", conf=1.0, method="human", ev="ev_h2"),
            realized("req_a", "o_a2", conf=0.6, method="llm_pick", ev="ev_l"),
        ],
        "link-1",
    )
    hub.write_links([implemented("o_a3", "src/components/A3.vue", method="route", ev="ev_r")], "link-2")
    removed = hub.prune_stale("link-2", ["links"], target=target)
    assert removed["REALIZED_BY"] == 1  # the llm_pick edge only
    assert removed["IMPLEMENTED_BY"] == 6  # fixture's i18n_key edges; the human one stays
    left = cypher_rows(
        hub,
        "MATCH (a {target: $t})-[r:IMPLEMENTED_BY|REALIZED_BY]->(b) "
        "RETURN type(r) AS t, r.method AS m ORDER BY t, m",
        t=target,
    )
    assert [(r["t"], r["m"]) for r in left] == [
        ("IMPLEMENTED_BY", "human"),
        ("IMPLEMENTED_BY", "route"),
        ("REALIZED_BY", "human"),
    ]
    owner = cypher_rows(
        hub, "MATCH (e:UIElement {target: $t}) WHERE 'o_a1' IN e.obs_ids RETURN e.owner_file AS f", t=target
    )
    assert owner[0]["f"] == "src/components/A2.vue"  # derived from what survived, human edge included


# ------------------------------------------------------------------------------------------ schema
def test_ensure_schema_does_not_record_a_tolerated_race(target: str):
    from neo4j.exceptions import ClientError

    class AlreadyExists(ClientError):
        code = "Neo.ClientError.Schema.EquivalentSchemaRuleAlreadyExists"

    class Syntax(ClientError):
        code = "Neo.ClientError.Statement.SyntaxError"

    def fake_driver(error: type[Exception]) -> Any:
        class Session:
            def __enter__(self) -> Self:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def execute_write(self, work: Any, **kw: Any) -> Any:
                raise error("boom")

        class Driver:
            def session(self, **kw: Any) -> Session:
                return Session()

        return Driver()

    run = RunContext.ephemeral(target)
    raced = GraphStore(fake_driver(AlreadyExists), run=run, target=target)
    assert raced.ensure_schema() > 0
    assert run.failures.items == []
    broken = GraphStore(fake_driver(Syntax), run=run, target=target)
    with pytest.raises(GraphError):
        broken.ensure_schema()
    assert [f["code"] for f in run.failures.items] == ["GRAPH_WRITE_FAILED"]


# ------------------------------------------------------------------------------ cost of bookkeeping reads
def plan_operators(store: GraphStore, cypher: str, **params: Any) -> list[str]:
    with store.driver.session(default_access_mode=neo4j.READ_ACCESS) as session:
        plan = session.run("EXPLAIN " + cypher, params).consume().plan
    ops: list[str] = []

    def walk(node: Any) -> None:
        ops.append(node["operatorType"] if isinstance(node, dict) else node.operator_type)
        for child in (node.get("children") if isinstance(node, dict) else node.children) or []:
            walk(child)

    walk(plan)
    return ops


def test_bookkeeping_reads_use_label_scoped_plans_and_keep_their_results(hub: GraphStore, target: str):
    """Review wave 1: label-less `MATCH (n {target: $t})` is an AllNodesScan per call and per delete batch."""
    for name in ("counts_by_label", "counts_by_rel", "transitions"):
        ops = plan_operators(hub, queries.get(name).cypher, target=target)
        assert not any("AllNodesScan" in op for op in ops), (name, ops)
        assert any("IndexSeek" in op for op in ops), (name, ops)  # served by the per-label `target` index
    counts = hub.counts(target)
    nodes = {k: v for k, v in counts.items() if v and not k.isupper()}
    rels = {k: v for k, v in counts.items() if v and k.isupper()}
    assert nodes == {"CodeFile": 11, "Route": 1, "Screen": 2, "UIElement": 6, "Run": 1}
    assert rels == {"IMPORTS": 6, "RENDERS": 4, "HANDLED_BY": 1, "APPEARS_ON": 7, "IMPLEMENTED_BY": 6}
    assert hub.clear_target(target) == 21 and sum(hub.counts(target).values()) == 0
    ops = plan_operators(hub, writes.DELETE_BY_LABEL["CodeFile"], target=target, batch=10)
    assert not any("AllNodesScan" in op for op in ops) and any("IndexSeek" in op for op in ops), ops


def test_forward_rows_are_exempt_from_the_reverse_hop_cap(hub: GraphStore):
    """Documented contract: `hops` caps reverse distance only; a forward row always has hops == 1."""
    rows = hub.read("impact", changed=["src/pages/B1.vue"], hops=0, forward=1)
    assert {r["direction"] for r in rows} == {"self", "forward"}
    assert all(r["hops"] == 0 for r in rows if r["direction"] == "self")
    assert all(r["hops"] == 1 for r in rows if r["direction"] == "forward")
    capped = hub.read("impact", changed=["src/pages/B1.vue"], hops=1, forward=0)
    assert all(r["hops"] <= 1 and r["direction"] != "forward" for r in capped)


# ------------------------------------------------------------- downstream read templates (decision 6)
@pytest.fixture
def rich(hub: GraphStore, target: str) -> GraphStore:
    """The hub world plus i18n keys, requirements, a flow, a transition, routes and gaps."""
    index = hub_index(target)
    index.i18n = {"action.send": "Send", "menu.copy": "Copy", "menu.paste": "Paste"}
    index.key_uses = [
        KeyUse(file="src/components/A1.vue", key="action.send"),
        KeyUse(file="src/components/A2.vue", key="action.send"),
        KeyUse(file="src/components/A3.vue", key="menu.", dynamic=True),
        KeyUse(file="src/pages/B1.vue", key="menu.copy"),
    ]
    hub.write_code(index, "run_hub")
    trans = [Transition(id="t1", from_screen="scr_one", to_screen="scr_two", element_id="o_a1", label="A1")]
    hub.write_crawl(hub_screens(target), trans, "run_hub", "bundle_1")
    hub.write_requirements([requirement(target, f"req_{c}") for c in "abcd"], "run_hub")
    hub.write_flows(
        [Flow(id="flow_x", name="use A", steps=["o_a1", "o_a2", "o_c1"], screens=["scr_one", "scr_two"])],
        "run_hub",
    )
    hub.write_links(
        [
            realized("req_a", "o_a1", conf=0.7),
            realized("req_a", "scr_two", conf=0.5, method="llm_pick"),
            realized("req_a", "flow_x", conf=0.4, method="llm_pick"),
            realized("req_b", "o_c1", conf=0.8),
            implemented("o_a2", "src/components/A2.vue", screens=["scr_two"]),
        ],
        "run_rich",
    )
    hub.link_routes("run_hub", target=target)

    def gap(gid: str, kind: GapKind, bundle: str | None, status: str) -> Gap:
        return Gap(
            id=gid,
            kind=kind,
            status=status,
            subject_id="req_c",
            reason="SPEC_MAY_BE_STALE",
            confidence=0.3,
            screens_searched=["scr_one"],
            actions_spent=5,
            bundle_hash=bundle,
        )

    hub.write_gaps(
        [
            gap("g1", GapKind.NO_UI_EVIDENCE, "b1", "NOT_FOUND_WITHIN_BUDGET"),
            gap("g2", GapKind.NOT_UI_TESTABLE, None, "CLASSIFIED"),
            gap("g3", GapKind.NO_UI_EVIDENCE, "b2", "NOT_FOUND_WITHIN_BUDGET"),
        ],
        "run_rich",
    )
    return hub


def canon_id(target: str, obs_id: str) -> str:
    return canonicalise(hub_screens(target), target).alias[obs_id]


def test_files_using_keys(rich: GraphStore):
    rows = rich.read("files_using_keys", keys=["menu.paste", "unknown.key", "action.send", "menu.copy"])
    got = [(r["key"], r["match"], r["file"], r["used_key"], r["dynamic"], r["uses"], r["en"]) for r in rows]
    assert got == [
        ("action.send", "exact", "src/components/A1.vue", "action.send", False, 1, "Send"),
        ("action.send", "exact", "src/components/A2.vue", "action.send", False, 1, "Send"),
        ("menu.copy", "dynamic_prefix", "src/components/A3.vue", "menu.", True, 1, None),
        ("menu.copy", "exact", "src/pages/B1.vue", "menu.copy", False, 1, "Copy"),
        ("menu.paste", "dynamic_prefix", "src/components/A3.vue", "menu.", True, 1, None),
    ]
    assert rows[0]["kind"] == "component" and rows[3]["kind"] == "page"
    assert rich.read("files_using_keys", keys=[]) == []
    literal = rich.read("files_using_keys", keys=["menu.", "app.name"])  # a prefix is its own exact key
    assert [(r["key"], r["match"], r["file"]) for r in literal] == [
        ("menu.", "exact", "src/components/A3.vue")
    ]


def test_elements_for_keys_follow_files_to_elements_with_screen_attribution(rich: GraphStore, target: str):
    rows = rich.read("elements_for_keys", keys=["menu.paste", "action.send"])
    got = [(r["key"], r["match"], r["file"], r["name"], r["screen_ids"], r["method"]) for r in rows]
    assert got == [
        ("action.send", "exact", "src/components/A1.vue", "A1", ["scr_one"], "i18n_key"),
        ("action.send", "exact", "src/components/A2.vue", "A2", ["scr_two"], "i18n_key"),
        ("menu.paste", "dynamic_prefix", "src/components/A3.vue", "A3", ["scr_two"], "i18n_key"),
    ]
    assert rows[0]["element_id"] == canon_id(target, "o_a1") and rows[0]["confidence"] == 0.9
    assert rows[0]["evidence_id"] == "ev_o_a1" and rows[0]["role"] == "button"
    assert rows[0]["owner_file"] == "src/components/A1.vue"


def test_requirement_realizations_keep_unrealized_and_drop_unknown_ids(rich: GraphStore, target: str):
    rows = rich.read("requirement_realizations", requirement_ids=["req_c", "req_a", "req_missing", "req_b"])
    got = [(r["requirement_id"], r["target_kind"], r["method"], r["confidence"]) for r in rows]
    assert got == [
        ("req_a", "UIElement", "bm25_judge", 0.7),
        ("req_a", "Screen", "llm_pick", 0.5),
        ("req_a", "Flow", "llm_pick", 0.4),
        ("req_b", "UIElement", "bm25_judge", 0.8),
        ("req_c", None, None, None),  # the requirement exists and nothing realizes it
    ]
    first = rows[0]
    assert first["target_id"] == canon_id(target, "o_a1") and first["target_name"] == "A1"
    assert first["doc_sha"] == "x" and first["evidence_id"] == "ev_req_a_o_a1_bm25_judge"
    assert rows[1]["target_name"] == "/two" and rows[2]["target_name"] == "use A"


def test_unrealized_requirements_are_scoped_to_a_bundle(rich: GraphStore):
    def view(bundle: str | None) -> list[tuple[str, str | None, str | None]]:
        rows = rich.read("unrealized_requirements", bundle_hash=bundle)
        return [(r["requirement_id"], r["gap_id"], r["gap_bundle_hash"]) for r in rows]

    # rows sort by requirement, then gap kind (NOT_UI_TESTABLE < NO_UI_EVIDENCE), then gap id
    assert view("b1") == [("req_c", "g2", ""), ("req_c", "g1", "b1"), ("req_d", None, None)]
    assert view("b2") == [("req_c", "g2", ""), ("req_c", "g3", "b2"), ("req_d", None, None)]
    assert view("other") == [("req_c", "g2", ""), ("req_d", None, None)]
    assert view(None) == [
        ("req_c", "g2", ""),
        ("req_c", "g1", "b1"),
        ("req_c", "g3", "b2"),
        ("req_d", None, None),
    ]
    row = next(r for r in rich.read("unrealized_requirements", bundle_hash="b1") if r["gap_id"] == "g1")
    assert row["gap_kind"] == "NO_UI_EVIDENCE" and row["gap_status"] == "NOT_FOUND_WITHIN_BUDGET"
    assert row["gap_actions_spent"] == 5 and row["gap_reason"] == "SPEC_MAY_BE_STALE"
    assert row["section"] == "s" and row["ui_observable"] == "unknown"


def test_screens_overview(rich: GraphStore):
    rows = rich.read("screens_overview")
    got = [
        (
            r["screen_id"],
            r["url_pattern"],
            r["elements"],
            r["unmapped_elements"],
            r["incoming_transitions"],
            r["flows_starting"],
            r["flows_ending"],
            r["searched_by_gaps"],
            r["routes"],
        )
        for r in rows
    ]
    assert got == [
        ("scr_one", "/one", 4, 0, 0, 1, 0, 3, ["/one"]),
        ("scr_two", "/two", 3, 0, 1, 0, 1, 0, []),
    ]
    assert rows[0]["kind"] == "page" and rows[0]["bundle_hash"] == "bundle_1"


def test_flows_for_elements_resolve_observation_ids(rich: GraphStore, target: str):
    rows = rich.read("flows_for_elements", element_ids=["o_c1", "o_a1", "o_nope"])
    got = [(r["requested_id"], r["flow_id"], r["step_order"], r["starts_at"], r["ends_at"]) for r in rows]
    assert [r["element_id"] for r in rows] == sorted([canon_id(target, "o_a1"), canon_id(target, "o_c1")])
    assert sorted(got) == [
        ("o_a1", "flow_x", 0, "scr_one", "scr_two"),
        ("o_c1", "flow_x", 2, "scr_one", "scr_two"),
    ]
    assert {r["element_id"] for r in rows} == {canon_id(target, "o_a1"), canon_id(target, "o_c1")}
    canonical = rich.read("flows_for_elements", element_ids=[canon_id(target, "o_a2")])
    assert [(r["flow_id"], r["step_order"], r["flow_name"], r["n_steps"]) for r in canonical] == [
        ("flow_x", 1, "use A", 3)
    ]


def test_links_for_screens_respect_attribution(rich: GraphStore, target: str):
    rows = rich.read("links_for_screens", screen_ids=["scr_two", "scr_nope"])
    assert rows == sorted(
        rows,
        key=lambda r: (r["screen_id"], r["kind"], r["src"], r["dst"], r["method"], r["via"]),
    )
    got = {(r["kind"], r["dst"], r["via"], r["method"]) for r in rows}
    assert got == {
        ("IMPLEMENTED_BY", "src/components/A2.vue", "element", "i18n_key"),
        ("IMPLEMENTED_BY", "src/pages/C1.vue", "element", "i18n_key"),
        ("IMPLEMENTED_BY", "src/components/A3.vue", "element", "i18n_key"),
        ("REALIZED_BY", canon_id(target, "o_c1"), "element", "bm25_judge"),
        ("REALIZED_BY", "scr_two", "screen", "llm_pick"),
        ("REALIZED_BY", "flow_x", "flow", "llm_pick"),
    }
    assert {r["screen_id"] for r in rows} == {"scr_two"}
    a2 = next(r for r in rows if r["dst"] == "src/components/A2.vue")
    assert a2["link_screen_ids"] == ["scr_two"] and a2["src"] == canon_id(target, "o_a2")
    one = {(r["kind"], r["dst"]) for r in rich.read("links_for_screens", screen_ids=["scr_one"])}
    assert ("IMPLEMENTED_BY", "src/components/A2.vue") not in one  # attributed to scr_two only
    assert ("IMPLEMENTED_BY", "src/components/A1.vue") in one


def test_an_incomplete_spec_extraction_never_prunes(store: GraphStore, target: str, tmp_path: Path):
    """spec/stats.json says `complete: false` when chunks failed: the requirements are a partial set."""
    run1, cfg = make_run(tmp_path, target, "run-spec-full")
    write_artifacts(run1, target)
    run_load(run1, cfg, store)
    store.write_requirements([requirement(target, "req_old")], "run-spec-full")
    base = store.counts(target)["Requirement"]
    assert base == 2

    for name, stats in (
        ("failed-chunks", {"status": "ok", "complete": False, "chunks_failed": 2, "chunks_skipped": 0}),
        ("fetch-failed", {"status": "failed", "complete": True}),
    ):
        run, _ = make_run(tmp_path, target, f"run-spec-{name}")
        write_artifacts(run, target)
        (run.run_dir / "spec" / "stats.json").write_text(json.dumps(stats))
        out = run_load(run, cfg, store)
        spec = out["layers"]["spec"]
        assert spec["pruned"] is False and spec["prune_skipped"], name
        assert any(n.startswith("spec:") for n in out["notices"]), name
        assert store.counts(target)["Requirement"] == base, name

    run, _ = make_run(tmp_path, target, "run-spec-complete")
    write_artifacts(run, target)
    (run.run_dir / "spec" / "stats.json").write_text(json.dumps({"status": "ok", "complete": True}))
    out = run_load(run, cfg, store)
    assert out["layers"]["spec"].get("pruned") is None and out["pruned"]["Requirement"] == 1
    assert store.counts(target)["Requirement"] == 1


# --------------------------------------------------------------------- differential test (impact closure)
def reference_impact(
    edges: list[tuple[str, str, str]], seeds: list[str], hops: int, forward: int
) -> tuple[dict[str, tuple[Any, ...]], dict[str, set[str]]]:
    """Brute force over every walk of at most `hops` reverse steps: per file the minimum of
    (hops, forward?, seed, via, kinds), and the set of seeds that reach it."""
    reverse: dict[str, list[tuple[str, str]]] = {}
    forward_of: dict[str, list[tuple[str, str]]] = {}
    for kind, src, dst in edges:
        reverse.setdefault(dst, []).append((src, kind))
        forward_of.setdefault(src, []).append((dst, kind))
    best: dict[str, tuple[Any, ...]] = {}
    reach: dict[str, set[str]] = {}

    def offer(path: str, key: tuple[Any, ...], seed: str) -> None:
        reach.setdefault(path, set()).add(seed)
        if path not in best or key < best[path]:
            best[path] = key

    for seed in seeds:
        offer(seed, (0, 0, seed, (seed,), ()), seed)
        walks: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [(seed, (seed,), ())]
        for hop in range(1, hops + 1):
            walks = [
                (src, (src, *via), (kind, *kinds))
                for node, via, kinds in walks
                for src, kind in reverse.get(node, [])
            ]
            for node, via, kinds in walks:
                if node != seed:
                    offer(node, (hop, 0, seed, via, kinds), seed)
        if forward:
            for dst, kind in forward_of.get(seed, []):
                if dst != seed:
                    offer(dst, (1, 1, seed, (seed, dst), (kind,)), seed)
    return best, reach


@pytest.mark.parametrize("graph_seed", range(14))
def test_impact_matches_a_brute_force_reference_on_random_graphs(
    store: GraphStore, target: str, graph_seed: int
):
    """Random small graphs with cycles, parallel IMPORTS+RENDERS pairs, several seeds, forward hops and
    per-screen attribution: every row's witness, symbols, specifiers and seeds equal a walk enumeration."""
    rnd = random.Random(graph_seed)
    paths = [f"f{i}.vue" for i in range(9)]
    triples = sorted(
        {
            (rnd.choice(("IMPORTS", "RENDERS")), rnd.choice(paths), rnd.choice(paths))
            for _ in range(rnd.randint(8, 22))
        }
    )
    triples = [t for t in triples if t[1] != t[2]]
    edges = [
        CodeEdge(src=s, dst=d, kind=k, symbols=[f"{s}>{d}:{k}"], via=f"spec:{s}>{d}")  # type: ignore
        for k, s, d in triples
    ]
    rnd.shuffle(edges)
    index = CodeIndex(
        target=target,
        repo="o/r",
        ref="m",
        snapshot_id="s",
        adapter="vue",
        files=[code_file(p) for p in paths],
        edges=edges,
    )
    store.write_code(index, "r")
    shots = [
        screen(target, f"s{j}", f"/{j}", [obs(f"o{i}_{j}", "button", f"E{i}") for i in range(len(paths))])
        for j in (0, 1)
    ]
    store.write_crawl(shots, [], "r", "b")
    attribution = {p: rnd.choice(([], ["s0"], ["s1"], ["s0", "s1"])) for p in paths}
    links = [
        Link(
            kind="IMPLEMENTED_BY",
            src=f"o{i}_0",
            dst=p,
            confidence=0.9,
            method="route",
            evidence_id="e",
            screen_ids=attribution[p],
        )
        for i, p in enumerate(paths)
    ]
    store.write_links(links, "r")
    for trial in range(3):
        seeds = rnd.sample([*paths, "not/in/graph.vue"], rnd.randint(1, 3))
        hops, fwd = rnd.randint(0, 3), rnd.randint(0, 1)
        best, reach = reference_impact(triples, [s for s in seeds if s in paths], hops, fwd)
        rows = store.read("impact", changed=seeds, hops=hops, forward=fwd)
        expected_rows = {
            (s, p) for p in best for s in ("s0", "s1") if not attribution[p] or s in attribution[p]
        }
        assert {(r["screen_id"], r["file"]) for r in rows} == expected_rows, (graph_seed, trial)
        assert len(rows) == len(expected_rows)
        for r in rows:
            hop, fwd_rank, seed, via, kinds = best[r["file"]]
            ctx = (graph_seed, trial, r["file"])
            direction = "self" if hop == 0 else "forward" if fwd_rank else "reverse"
            assert (r["hops"], r["direction"], r["seed"]) == (hop, direction, seed), ctx
            assert (tuple(r["via"]), tuple(r["kinds"])) == (via, kinds), ctx
            assert r["symbols"] == [[f"{via[i]}>{via[i + 1]}:{kinds[i]}"] for i in range(len(kinds))], ctx
            assert r["specifiers"] == [f"spec:{via[i]}>{via[i + 1]}" for i in range(len(kinds))], ctx
            assert r["seeds"] == sorted(reach[r["file"]]), ctx


def test_ensure_schema_tolerates_the_servers_real_already_exists_errors(
    run_store: tuple[GraphStore, RunContext], monkeypatch
):
    """The tolerated race, end to end: the same statements without IF NOT EXISTS hit the real server's
    Equivalent...AlreadyExists / ConstraintAlreadyExists errors on a database that already has the schema."""
    store, run = run_store
    racing = [
        "CREATE CONSTRAINT requirement_key FOR (n:Requirement) REQUIRE (n.target, n.id) IS UNIQUE",
        "CREATE CONSTRAINT some_other_name FOR (n:Requirement) REQUIRE (n.target, n.id) IS UNIQUE",
        "CREATE INDEX requirement_target FOR (n:Requirement) ON (n.target)",
    ]
    monkeypatch.setattr(store_module, "schema_statements", lambda: racing)
    assert store.ensure_schema() == 3
    assert run.failures.items == []


def test_read_timeout_is_enforced_and_raised_as_a_read_error(store: GraphStore, target: str, monkeypatch):
    """One transaction with the configured timeout, not the driver's 30 s retry loop around it."""
    import time

    monkeypatch.setitem(
        queries.TEMPLATES,
        "slow",
        queries.Template(
            "slow",
            "UNWIND range(1, 2000000000) AS x WITH x WHERE x % 7 = 0 RETURN count(x) AS n",
            {"target": queries._str()},
        ),
    )
    store.read_timeout_s = 0.3
    started = time.monotonic()
    with pytest.raises(store_module.GraphReadError) as info:
        store.read("slow", target=target)
    assert time.monotonic() - started < 10
    assert info.value.neo4j_code and "Timed" in info.value.neo4j_code


def test_bookkeeping_statements_cover_every_label():
    for label in store_module.LABELS:
        assert f"(n:{label} {{target: $target}})" in queries.get("counts_by_label").cypher
        assert f"(:{label} {{target: $target}})-[r]->()" in queries.get("counts_by_rel").cypher
        assert f"(n:{label} {{target: $target}})" in writes.DELETE_BY_LABEL[label]
    assert set(writes.DELETE_BY_LABEL) == set(store_module.LABELS)


def test_parse_errors_are_judged_as_a_ratio_not_as_any(store: GraphStore, target: str, tmp_path: Path):
    """Two valid files a grammar cannot fully parse (the real Hoppscotch index) must not freeze the layer; a
    large share of unparsed files must."""
    from blastradius.models import CodeFile, FileClass

    run1, cfg = make_run(tmp_path, target, "run-ratio-full")
    write_artifacts(run1, target)
    run_load(run1, cfg, store)

    def padded(errors: int, pad: int):
        extra = [CodeFile(path=f"pad/{i}.ts", kind=FileClass.UTIL) for i in range(pad)]
        return lambda i: i.model_copy(update={"files": [*i.files, *extra], "stats": {"parse_errors": errors}})

    run, _ = make_run(tmp_path, target, "run-ratio-low")
    write_artifacts(run, target)
    rewrite_index(run, padded(errors=2, pad=300))
    out = run_load(run, cfg, store)
    assert "prune_skipped" not in out["layers"]["code"], out["notices"]

    run, _ = make_run(tmp_path, target, "run-ratio-high")
    write_artifacts(run, target)
    rewrite_index(run, padded(errors=40, pad=100))
    out = run_load(run, cfg, store)
    assert out["layers"]["code"]["pruned"] is False
    assert any("parse_errors=40" in r for r in out["layers"]["code"]["prune_skipped"])
