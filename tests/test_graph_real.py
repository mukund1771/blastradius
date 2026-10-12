"""Graph module on real data: Hoppscotch main (63273f8) and the three demo PRs' changed-file lists.

The code graph fixture is built from the public repo by an independent regex parser (see the sidecar
meta.json), not by the product's VueAdapter, so the closure numbers below check the graph store and its
Cypher against an independent BFS over the same edges and against the measurements recorded in
docs/RECON.md section 5. Needs the Neo4j container; skips when it is unreachable.
"""

from __future__ import annotations

import gzip
import json
import time
import uuid
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_graph import neo4j_settings, obs, screen

from blastradius.graph.store import GraphConnectionError, GraphStore
from blastradius.models import CodeIndex, Gap, GapKind, Link, Requirement

FIXTURES = Path(__file__).parent / "fixtures" / "graph"
META = json.loads((FIXTURES / "hoppscotch_63273f8.meta.json").read_text())
COMMON = "packages/hoppscotch-common/src/"
HUB = "packages/hoppscotch-data/src/index.ts"
SERVICES = [
    COMMON + "services/current-environment-value.service.ts",
    COMMON + "services/secret-environment.service.ts",
]
UI_KINDS = {"page", "layout", "component"}


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


@pytest.fixture(scope="module")
def real_index() -> CodeIndex:
    with gzip.open(FIXTURES / "hoppscotch_63273f8.code_index.json.gz", "rt", encoding="utf-8") as fh:
        return CodeIndex.model_validate_json(fh.read())


def bfs(index: CodeIndex, seeds: list[str], hops: int) -> dict[str, int]:
    """Independent reverse closure over IMPORTS|RENDERS: path -> minimum hop. Seeds absent from the
    snapshot are ignored, exactly as `impact` ignores a changed path with no CodeFile."""
    known = {f.path for f in index.files}
    importers: dict[str, set[str]] = defaultdict(set)
    for e in index.edges:
        importers[e.dst].add(e.src)
    dist = {s: 0 for s in seeds if s in known}
    frontier = list(dist)
    for h in range(1, hops + 1):
        nxt: list[str] = []
        for f in frontier:
            for i in importers[f]:
                if i not in dist:
                    dist[i] = h
                    nxt.append(i)
        frontier = nxt
    return dist


@pytest.fixture
def loaded(store: GraphStore, target: str, real_index: CodeIndex) -> tuple[GraphStore, CodeIndex]:
    index = real_index.model_copy(update={"target": target})
    started = time.monotonic()
    result = store.write_code(index, "real-1")
    assert time.monotonic() - started < 120
    assert result["files"] == len(index.files)
    return store, index


def with_one_element_per_vue_file(store: GraphStore, target: str, index: CodeIndex) -> list[str]:
    """One screen holding one element per .vue file, each IMPLEMENTED_BY its own file."""
    vue = sorted(f.path for f in index.files if f.path.endswith(".vue"))
    store.write_crawl(
        [screen(target, "scr_all", "/all", [obs(f"o{i}", "button", p) for i, p in enumerate(vue)])],
        [],
        "real-1",
        "bundle",
    )
    store.write_links(
        [
            Link(
                kind="IMPLEMENTED_BY",
                src=f"o{i}",
                dst=p,
                confidence=0.9,
                method="i18n_key",
                evidence_id=f"ev{i}",
            )
            for i, p in enumerate(vue)
        ],
        "real-1",
    )
    return vue


def test_real_snapshot_loads_idempotently(loaded: tuple[GraphStore, CodeIndex], target: str):
    store, index = loaded
    counts = store.counts(target)
    pairs = {(e.kind, e.src, e.dst) for e in index.edges}
    known = {f.path for f in index.files}
    stubs = {p for _, s, d in pairs for p in (s, d)} - known
    assert len(index.i18n) == 2198  # RECON section 1: en.json leaf keys
    assert counts["CodeFile"] == len(known) + len(stubs)
    assert counts["IMPORTS"] == sum(1 for k, *_ in pairs if k == "IMPORTS")
    assert counts["RENDERS"] == sum(1 for k, *_ in pairs if k == "RENDERS")
    assert counts["Route"] == len({r.pattern for r in index.routes})
    assert counts["HANDLED_BY"] == len({(r.pattern, r.file) for r in index.routes})
    assert counts["HANDLED_BY"] > counts["Route"]  # /profile is served by two files
    assert counts["I18nKey"] >= len(index.i18n)
    again = store.write_code(index, "real-1")
    assert again["nodes_created"] == 0 and again["relationships_created"] == 0
    assert store.counts(target) == counts


@pytest.mark.parametrize("pr", sorted(META["prs"]))
def test_real_impact_equals_independent_bfs(loaded: tuple[GraphStore, CodeIndex], target: str, pr: str):
    store, index = loaded
    vue = with_one_element_per_vue_file(store, target, index)
    changed = META["prs"][pr]["files"]
    sizes = []
    for hops in (0, 1, 2, 3):
        expected = {p: h for p, h in bfs(index, changed, hops).items() if p in set(vue)}
        rows = store.read("impact", changed=changed, hops=hops)
        got = {r["file"]: r["hops"] for r in rows}
        assert got == expected, f"PR {pr} hops {hops}"
        assert len(rows) == len(expected) == len({(r["screen_id"], r["element_id"]) for r in rows})
        sizes.append(len(rows))
    assert sizes == sorted(sizes)  # the cap only ever widens the set


def test_real_services_closure_matches_recon(loaded: tuple[GraphStore, CodeIndex], target: str):
    """RECON section 5: the two #6645 services reach 28% of .vue files in 2 hops and 47% in 3."""
    store, index = loaded
    vue = with_one_element_per_vue_file(store, target, index)
    two = store.read("impact", changed=SERVICES, hops=2)
    three = store.read("impact", changed=SERVICES, hops=3)
    assert len(two) == 93 and round(100 * len(two) / len(vue)) == 28
    assert abs(len(three) - 158) <= 2 and round(100 * len(three) / len(vue)) == 47
    assert max(r["hops"] for r in two) == 2 and all(r["direction"] == "reverse" for r in two)


def test_real_pr_paths_without_a_baseline_node(loaded: tuple[GraphStore, CodeIndex], target: str):
    """RECON section 1, #6641: 4 of its 10 code files have no baseline node (PR_PATH_MISSING_AT_HEAD)."""
    store, _ = loaded
    files = META["prs"]["6641"]["files"]
    rows = {r["path"]: r for r in store.read("file_exists", paths=files)}
    code = [
        p
        for p in files
        if p.endswith((".vue", ".ts")) and not p.endswith(("components.d.ts", "vite.config.ts"))
    ]
    assert len(code) == 10
    assert sorted(p for p in code if not rows[p]["exists"]) == [
        COMMON + "components/gql/ResponseCompareModal.vue",
        COMMON + "components/http/ResponseCompareModal.vue",
        COMMON + "helpers/diff.ts",
        COMMON + "newstore/responseHistory.ts",
    ]
    assert rows[COMMON + "components/http/Response.vue"]["kind"] == "component"
    assert rows[COMMON + "components.d.ts"]["kind"] == "generated"


def test_real_hub_degree_matches_bfs(loaded: tuple[GraphStore, CodeIndex], target: str):
    store, index = loaded
    kind = {f.path: f.kind.value for f in index.files}
    ui_total = sum(1 for k in kind.values() if k in UI_KINDS)
    for hops in (1, 2, 3):
        row = store.read("hub_degree", paths=[HUB], hops=hops)[0]
        reach = {p for p, h in bfs(index, [HUB], hops).items() if h > 0}
        assert row["importers"] == len(reach)
        assert row["ui_importers"] == sum(1 for p in reach if kind.get(p) in UI_KINDS)
        assert row["ui_total"] == ui_total and row["fraction"] == pytest.approx(
            row["ui_importers"] / ui_total
        )
    hub = store.read("hub_degree", paths=[HUB, SERVICES[0], COMMON + "layouts/default.vue"], hops=2)
    assert hub[0]["path"] == HUB and hub[0]["fraction"] > 0.5  # @hoppscotch/data lights most of the UI
    assert {r["path"]: r["fraction"] for r in hub}[COMMON + "layouts/default.vue"] < 0.01


def test_real_routes_serve_screens_by_pattern(loaded: tuple[GraphStore, CodeIndex], target: str):
    store, _ = loaded
    patterns = [
        "/",
        "/graphql",
        "/realtime",
        "/realtime/websocket",
        "/settings",
        "/e/:id",
        "/e/abc123",
        "/profile",
        "/definitely/not/a/page",
    ]
    store.write_crawl([screen(target, f"scr{i}", p, []) for i, p in enumerate(patterns)], [], "real-1", "b")
    res = store.link_routes("real-1", target=target)
    assert res["screens"] == len(patterns) and res["served"] == len(patterns)
    rows = store.driver.execute_query(
        "MATCH (s:Screen {target: $t})-[b:SERVED_BY]->(r:Route) "
        "RETURN s.url_pattern AS u, r.pattern AS r, b.how AS how",
        t=target,
    ).records
    served = {r["u"]: (r["r"], r["how"]) for r in rows}
    assert served["/"] == ("/", "exact") and served["/graphql"] == ("/graphql", "exact")
    assert served["/e/:id"] == ("/e/:id", "exact") and served["/e/abc123"] == ("/e/:id", "param")
    assert served["/definitely/not/a/page"][1] == "catch_all"  # soft-404 lands on the catch-all page
    handler = store.driver.execute_query(
        "MATCH (:Route {target: $t, pattern: '/graphql'})-[:HANDLED_BY]->(f:CodeFile) RETURN f.path AS p",
        t=target,
    )
    assert [r["p"] for r in handler.records] == [COMMON + "pages/graphql.vue"]
    assert store.link_routes("real-1", target=target)["nodes_created"] == 0


def test_real_absence_near_through_the_graphql_page(loaded: tuple[GraphStore, CodeIndex], target: str):
    store, index = loaded
    page = COMMON + "pages/graphql.vue"
    dep = next(e.dst for e in index.edges if e.src == page and e.dst.endswith(".vue"))
    store.write_crawl(
        [screen(target, "scr_gql", "/graphql", []), screen(target, "scr_settings", "/settings", [])],
        [],
        "real-1",
        "bundle_live",
    )
    store.link_routes("real-1", target=target)
    store.write_requirements(
        [
            Requirement(
                id="req_g",
                target=target,
                text="User can run a GraphQL query",
                quote="run a query",
                source_url="u",
                section="GraphQL",
                spec_sha="s",
                edition="cloud-web",
                ui_observable="yes",
            )
        ],
        "real-1",
    )
    store.write_gaps(
        [
            Gap(
                id="gap_g",
                kind=GapKind.NO_UI_EVIDENCE,
                status="NOT_FOUND_WITHIN_BUDGET",
                subject_id="req_g",
                screens_searched=["scr_gql"],
                actions_spent=12,
                confidence=0.4,
                bundle_hash="bundle_live",
                reason="SPEC_MAY_BE_STALE",
            )
        ],
        "real-1",
    )
    rows = store.read("absence_near", changed=[dep], bundle_hash="bundle_live")
    assert [(r["requirement_id"], r["hops"], r["impacted_screen_ids"]) for r in rows] == [
        ("req_g", 1, ["scr_gql"])
    ]
    assert rows[0]["actions_spent"] == 12 and rows[0]["reason"] == "SPEC_MAY_BE_STALE"
    assert store.read("absence_near", changed=[dep], bundle_hash="some_other_bundle") == []
    assert store.read("absence_near", changed=[COMMON + "pages/settings.vue"], hops=0) == []
