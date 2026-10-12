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
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from blastradius.config import Settings
from blastradius.failures import FailureCode
from blastradius.graph import queries
from blastradius.graph.canonical import canonicalise, element_id, element_key, match_route
from blastradius.graph.load import run_load
from blastradius.graph.store import GraphConnectionError, GraphError, GraphStore
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
    assert element_key(obs("c1", "button", "Close"), a)[3] == "dialog:add environment"


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
def test_every_cypher_string_is_a_constant():
    """D28: no string-built Cypher. Constants are plain literals and are passed to the driver by name."""
    for module in ("queries.py", "store.py", "writes.py"):
        tree = ast.parse((GRAPH_SRC / module).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if any(n.lstrip("_").isupper() for n in names) and isinstance(
                    node.value, (ast.JoinedStr, ast.BinOp)
                ):
                    pytest.fail(f"{module}:{node.lineno} builds a constant by formatting")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"_run_once", "_run_rows"}:
                    arg = node.args[0]
                    constant = isinstance(arg, ast.Name) or (
                        isinstance(arg, ast.Attribute) and getattr(arg.value, "id", "") == "writes"
                    )
                    assert constant, f"{module}:{node.lineno} non-constant Cypher"
                assert not (module == "queries.py" and node.func.attr in {"format", "replace"}), (
                    f"{module}:{node.lineno} formats a template"
                )
    for tpl in queries.TEMPLATES.values():
        assert isinstance(tpl.cypher, str) and "{{" not in tpl.cypher and "%s" not in tpl.cypher


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
    assert sym[0]["s"] == ["Hub", "s1", "Other"] and sym[0]["run"] == "run1" == sym[0]["first"]
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
    anyb = hub.read("absence_near", changed=["src/pages/B1.vue"])
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
    assert store.read("absence_near", changed=["comp/c.vue"], hops=0) == []
    via_layout = store.read("absence_near", changed=["layouts/default.vue"], hops=0)
    assert [r["gap_id"] for r in via_layout] == ["gp"]  # NOT_UI_TESTABLE is not absence
    assert store.read("absence_near", changed=["pages/other.vue"]) == []


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
