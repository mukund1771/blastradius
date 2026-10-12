"""Graph close-out pass: prune shrink guard, dangling per-screen attribution, deterministic reads, bounded
closures, and secret scrubbing.

Every test here failed on the code it was written against. Database tests need the Neo4j container and skip
when it is unreachable; each writes under a unique `__test__<random>` target and removes exactly that target.
"""

from __future__ import annotations

import json
import random
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from neo4j.exceptions import ServiceUnavailable
from test_graph import (
    INCOMPLETE_CRAWLS,
    code_file,
    cypher_rows,
    edge,
    implemented,
    layered_index,
    make_run,
    neo4j_settings,
    obs,
    profile_rows,
    realized,
    requirement,
    rewrite_index,
    screen,
    set_summary,
    write_artifacts,
)

from blastradius.config import Settings
from blastradius.failures import FailureCode
from blastradius.graph import store as store_module
from blastradius.graph.load import run_load
from blastradius.graph.store import GraphConnectionError, GraphStore
from blastradius.models import (
    CodeIndex,
    CrawlSummary,
    FileClass,
    Gap,
    GapKind,
    RouteDef,
    ScreenState,
    Transition,
)
from blastradius.run import RunContext


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
def screens_with(target: str, ids: Any, per_screen: Any = 2) -> list[ScreenState]:
    """One screen per id; `per_screen` is a count or a function of the id. Element names are unique, so
    every observation is its own canonical UIElement."""
    out = []
    for i in ids:
        n = per_screen(i) if callable(per_screen) else per_screen
        elements = [obs(f"o_{i:02d}_{j}", "button", f"Button {i:02d}-{j}") for j in range(n)]
        out.append(screen(target, f"scr_{i:02d}", f"/p{i:02d}", elements))
    return out


def write_crawl_artifacts(
    run: RunContext,
    target: str,
    screens: list[ScreenState],
    *,
    bundle: str = "bundle_A",
    transitions: list[Transition] | None = None,
) -> None:
    transitions = transitions or []
    crawl = run.run_dir / "crawl"
    crawl.mkdir(exist_ok=True)
    (crawl / "screens.jsonl").write_text("".join(s.model_dump_json() + "\n" for s in screens))
    (crawl / "transitions.jsonl").write_text("".join(t.model_dump_json() + "\n" for t in transitions))
    (crawl / "summary.json").write_text(
        CrawlSummary(
            target=target,
            started_at="a",
            finished_at="b",
            screens=len(screens),
            elements=sum(len(s.elements) for s in screens),
            transitions=len(transitions),
            actions_spent=3,
            actions_budget=10,
            wall_clock_s=1.0,
            bundle_hash=bundle,
            bundle_hash_end=bundle,
        ).model_dump_json()
    )


def load_crawl(
    store: GraphStore, target: str, tmp_path: Path, run_id: str, screens: list[ScreenState], **kw: Any
) -> tuple[dict[str, Any], RunContext]:
    run, cfg = make_run(tmp_path, target, run_id)
    write_crawl_artifacts(run, target, screens)
    return run_load(run, cfg, store, **kw), run


def failure_codes(run: RunContext) -> list[str]:
    return [f["code"] for f in run.failures.items]


def prune_notices(out: dict[str, Any], layer: str) -> list[str]:
    return [n for n in out["notices"] if n.startswith(f"{layer}: pruned ")]


# ============================================================ MAJOR 1: shrink guard on the crawl layer
def test_a_crawl_that_would_remove_most_of_the_layer_is_loaded_but_not_pruned(
    store: GraphStore, target: str, tmp_path: Path
):
    """A budget-bounded crawl now prunes, so a crawl that merely saw less (a blocked section, a dead route, a
    smaller budget) would delete the link-stage output of everything it did not reach. 8 of 12 Screens is
    well past half: the layer is loaded, nothing is pruned, and the notice and the failure say why."""
    load_crawl(store, target, tmp_path, "run-A", screens_with(target, range(12)))
    base = store.counts(target)
    assert base["Screen"] == 12 and base["UIElement"] == 24

    out, run = load_crawl(store, target, tmp_path, "run-B", screens_with(target, range(4)))
    crawl = out["layers"]["crawl"]
    assert crawl["pruned"] is False and crawl["screens"] == 4
    assert any(r.startswith("shrink") and "8/12" in r for r in crawl["prune_skipped"]), crawl["prune_skipped"]
    assert any(n.startswith("crawl:") and "not pruned" in n and "shrink" in n for n in out["notices"])
    assert out["pruned"].get("Screen", 0) == 0 and out["pruned"].get("UIElement", 0) == 0
    now = store.counts(target)
    assert now["Screen"] == 12 and now["UIElement"] == 24 and now["APPEARS_ON"] == base["APPEARS_ON"]
    skipped = [f for f in run.failures.items if f["code"] == FailureCode.GRAPH_PRUNE_SKIPPED]
    assert len(skipped) == 1 and skipped[0]["context"]["layer"] == "crawl"
    assert json.loads((run.run_dir / "graph" / "load.json").read_text())["notices"] == out["notices"]


@pytest.mark.parametrize(
    ("existing", "kept", "pruned"),
    [
        (12, 12, True),  # nothing removed
        (12, 8, True),  # a third
        (10, 5, True),  # exactly half is not "more than half"
        (10, 4, False),  # 60%
        (9, 1, True),  # fewer than 10 existing Screens: small graphs are allowed to shrink
    ],
)
def test_shrink_guard_boundaries(
    store: GraphStore, target: str, tmp_path: Path, existing: int, kept: int, pruned: bool
):
    load_crawl(store, target, tmp_path, "run-A", screens_with(target, range(existing)))
    out, run = load_crawl(store, target, tmp_path, "run-B", screens_with(target, range(kept)))
    if pruned:
        assert "prune_skipped" not in out["layers"]["crawl"], out["notices"]
        assert store.counts(target)["Screen"] == kept
        assert FailureCode.GRAPH_PRUNE_SKIPPED not in failure_codes(run)
    else:
        assert out["layers"]["crawl"]["pruned"] is False
        assert store.counts(target)["Screen"] == existing
        assert FailureCode.GRAPH_PRUNE_SKIPPED in failure_codes(run)


def test_losing_most_elements_alone_also_trips_the_guard(store: GraphStore, target: str, tmp_path: Path):
    """Every Screen is still there, but 36 of 48 canonical elements are not (a crawl that never opened the
    menus): the Screens-only reading of the guard would miss it."""
    wide = screens_with(target, range(12), lambda i: 10 if i < 4 else 1)
    load_crawl(store, target, tmp_path, "run-A", wide)
    assert store.counts(target)["UIElement"] == 48
    out, run = load_crawl(store, target, tmp_path, "run-B", screens_with(target, range(12), 1))
    crawl = out["layers"]["crawl"]
    assert crawl["pruned"] is False
    assert any(r.startswith("shrink") and "36/48" in r for r in crawl["prune_skipped"]), crawl[
        "prune_skipped"
    ]
    assert store.counts(target)["UIElement"] == 48
    assert FailureCode.GRAPH_PRUNE_SKIPPED in failure_codes(run)


def test_a_prune_lists_what_it_removed_in_the_notices(store: GraphStore, target: str, tmp_path: Path):
    load_crawl(store, target, tmp_path, "run-A", screens_with(target, range(12)))
    out, run = load_crawl(store, target, tmp_path, "run-B", screens_with(target, range(8)))
    assert out["pruned"]["Screen"] == 4 and out["pruned"]["UIElement"] == 8
    lines = prune_notices(out, "crawl")
    assert len(lines) == 1, out["notices"]
    assert "4 Screen" in lines[0] and "8 UIElement" in lines[0] and "run-B" in lines[0], lines
    assert FailureCode.GRAPH_PRUNE_SKIPPED not in failure_codes(run)
    # a reload prunes nothing, so it says nothing
    again, _ = load_crawl(store, target, tmp_path, "run-B", screens_with(target, range(8)))
    assert prune_notices(again, "crawl") == []


def test_the_shrink_guard_can_be_overridden_explicitly(store: GraphStore, target: str, tmp_path: Path):
    """Without a way through, an app that really did shrink (or whose Screen fingerprints all churned after a
    redeploy) would keep its stale Screens for ever."""
    load_crawl(store, target, tmp_path, "run-A", screens_with(target, range(12)))
    out, run = load_crawl(store, target, tmp_path, "run-B", screens_with(target, range(4)), allow_shrink=True)
    assert "prune_skipped" not in out["layers"]["crawl"]
    assert out["pruned"]["Screen"] == 8 and store.counts(target)["Screen"] == 4
    assert any("shrink" in n and "allow_shrink" in n for n in out["notices"]), out["notices"]
    assert FailureCode.GRAPH_PRUNE_SKIPPED not in failure_codes(run)


# ===================================== MINOR: every skipped prune is recorded as GRAPH_PRUNE_SKIPPED
@pytest.mark.parametrize("case", sorted(INCOMPLETE_CRAWLS))
def test_an_incomplete_crawl_records_graph_prune_skipped(
    store: GraphStore, target: str, tmp_path: Path, case: str
):
    run1, cfg = make_run(tmp_path, target, "run-full")
    write_artifacts(run1, target)
    run_load(run1, cfg, store)
    run2, _ = make_run(tmp_path, target, f"run-{case}")
    write_artifacts(run2, target, drop_screen=True)
    update, _reason = INCOMPLETE_CRAWLS[case]
    if update is None:
        (run2.run_dir / "crawl" / "summary.json").unlink()
    else:
        set_summary(run2, **update)
    out = run_load(run2, cfg, store)
    skipped = [f for f in run2.failures.items if f["code"] == FailureCode.GRAPH_PRUNE_SKIPPED]
    assert [f["context"]["layer"] for f in skipped] == ["crawl"], out["notices"]
    assert skipped[0]["context"]["reasons"] == out["layers"]["crawl"]["prune_skipped"]


def test_code_and_spec_prune_skips_are_recorded_too(store: GraphStore, target: str, tmp_path: Path):
    run1, cfg = make_run(tmp_path, target, "run-full")
    write_artifacts(run1, target)
    run_load(run1, cfg, store)

    run2, _ = make_run(tmp_path, target, "run-code")
    write_artifacts(run2, target)
    rewrite_index(run2, lambda i: i.model_copy(update={"stats": {"parse_partial": True}}))
    (run2.run_dir / "spec" / "stats.json").write_text(json.dumps({"status": "failed", "complete": True}))
    out = run_load(run2, cfg, store)
    layers = sorted(f["context"]["layer"] for f in run2.failures.items if f["code"] == "GRAPH_PRUNE_SKIPPED")
    assert layers == ["code", "spec"], out["notices"]


# ===================================== MINOR: a degraded code index is partial (edges collapsed, files not)
def test_a_degraded_code_index_never_prunes(store: GraphStore, target: str, tmp_path: Path):
    """The same 11 files with 3 of 10 edges is an indexer that stopped resolving imports (a broken alias, a
    missing tsconfig), not a codebase that lost 70% of its imports: pruning would delete the real edges."""
    run1, cfg = make_run(tmp_path, target, "run-code-full")
    write_artifacts(run1, target)
    run_load(run1, cfg, store)
    base = store.counts(target)
    assert base["CodeFile"] == 11 and base["IMPORTS"] + base["RENDERS"] == 10

    run, _ = make_run(tmp_path, target, "run-degraded")
    write_artifacts(run, target)
    rewrite_index(run, lambda i: i.model_copy(update={"edges": i.edges[:3]}))
    out = run_load(run, cfg, store)
    code = out["layers"]["code"]
    assert code["pruned"] is False
    assert any("edges_dropped" in r for r in code["prune_skipped"]), code["prune_skipped"]
    assert any(n.startswith("code:") and "edges_dropped" in n for n in out["notices"])
    assert FailureCode.GRAPH_PRUNE_SKIPPED in failure_codes(run)
    now = store.counts(target)
    assert (now["IMPORTS"], now["RENDERS"], now["CodeFile"]) == (base["IMPORTS"], base["RENDERS"], 11)


def test_an_edge_drop_explained_by_fewer_files_or_under_half_still_prunes(
    store: GraphStore, target: str, tmp_path: Path
):
    run1, cfg = make_run(tmp_path, target, "run-code-full")
    write_artifacts(run1, target)
    run_load(run1, cfg, store)

    run, _ = make_run(tmp_path, target, "run-half")  # exactly half of the edges: not "more than half"
    write_artifacts(run, target)
    rewrite_index(run, lambda i: i.model_copy(update={"edges": i.edges[:5]}))
    out = run_load(run, cfg, store)
    assert "prune_skipped" not in out["layers"]["code"], out["notices"]
    assert store.counts(target)["IMPORTS"] + store.counts(target)["RENDERS"] == 5

    run, _ = make_run(tmp_path, target, "run-fewer-files")  # six files left, so four of ten edges is honest
    write_artifacts(run, target)

    def six_files(index: CodeIndex) -> CodeIndex:
        keep = {f.path for f in index.files[:6]}
        return index.model_copy(
            update={
                "files": [f for f in index.files if f.path in keep],
                "edges": [e for e in index.edges if e.src in keep and e.dst in keep][:2],
            }
        )

    rewrite_index(run, six_files)
    out = run_load(run, cfg, store)
    assert "prune_skipped" not in out["layers"]["code"], out["notices"]


# ====================================== MAJOR 2: dangling per-screen attribution after a re-crawl
HTTP_TABS = "src/components/http/Tabs.vue"
GQL_TABS = "src/components/graphql/Tabs.vue"
ROOT_PAGE = "src/pages/index.vue"
GQL_PAGE = "src/pages/graphql.vue"


def redeployable_world(run: RunContext, target: str, fp: str) -> None:
    """The close-tab world of the review repro. `fp` stands for the app build: Screen and observation ids are
    fingerprint hashes that change with it, while the canonical element id (role, name, scope) does not."""
    index = CodeIndex(
        target=target,
        repo="o/r",
        ref="m",
        snapshot_id=f"snap_{fp}",
        adapter="vue",
        files=[
            code_file(ROOT_PAGE, FileClass.PAGE),
            code_file(GQL_PAGE, FileClass.PAGE),
            code_file(HTTP_TABS),
            code_file(GQL_TABS),
        ],
        edges=[edge(ROOT_PAGE, HTTP_TABS, "RENDERS"), edge(GQL_PAGE, GQL_TABS, "RENDERS")],
    )
    screens = [
        screen(target, f"scr_root_{fp}", "/", [obs(f"c_root_{fp}", "button", "Close tab")]),
        screen(target, f"scr_gql_{fp}", "/graphql", [obs(f"c_gql_{fp}", "button", "Close tab")]),
    ]
    write_crawl_artifacts(run, target, screens, bundle=f"bundle_{fp}")
    (run.run_dir / "code").mkdir(exist_ok=True)
    (run.run_dir / "code" / "index.json").write_text(index.model_dump_json())


def edge_screen_ids(store: GraphStore, target: str, rel: str) -> list[tuple[str, list[str]]]:
    rows = cypher_rows(
        store,
        f"MATCH (a {{target: $t}})-[r:{rel}]->(b) RETURN coalesce(b.path, b.id) AS dst, r.screen_ids AS s "
        "ORDER BY dst, r.method",
        t=target,
    )
    return [(r["dst"], r["s"]) for r in rows]


def test_a_recrawl_does_not_leave_links_pointing_at_screens_that_no_longer_exist(
    store: GraphStore, target: str, tmp_path: Path
):
    run_a, cfg = make_run(tmp_path, target, "runA")
    redeployable_world(run_a, target, "v1")
    run_load(run_a, cfg, store)
    store.write_links(
        [
            implemented("c_root_v1", HTTP_TABS, screens=["scr_root_v1"]),
            implemented("c_gql_v1", GQL_TABS, screens=["scr_gql_v1"]),
        ],
        "runA",
    )
    before = store.read("impact", changed=[HTTP_TABS], hops=0)
    assert [r["screen_id"] for r in before] == ["scr_root_v1"]

    run_b, _ = make_run(tmp_path, target, "runB")
    redeployable_world(run_b, target, "v2")
    out = run_load(run_b, cfg, store)
    assert out["pruned"]["Screen"] == 2  # every fingerprint changed: the old Screens are gone

    # nothing names a Screen that is gone, and the element still reaches the changed file's screens
    assert edge_screen_ids(store, target, "IMPLEMENTED_BY") == [(GQL_TABS, []), (HTTP_TABS, [])]
    after = store.read("impact", changed=[HTTP_TABS], hops=0)
    assert [(r["screen_id"], r["element_name"]) for r in after] == [
        ("scr_gql_v2", "Close tab"),
        ("scr_root_v2", "Close tab"),
    ]
    forward = store.read("impact", changed=[ROOT_PAGE], hops=0, forward=1)  # the page renders the tabs
    assert {r["screen_id"] for r in forward} == {"scr_gql_v2", "scr_root_v2"}
    assert any("screen_ids" in n and "2 " in n for n in out["notices"]), out["notices"]
    assert json.loads((run_b.run_dir / "graph" / "load.json").read_text())["notices"] == out["notices"]

    again = run_load(run_b, cfg, store)  # nothing is dangling any more: nothing to say
    assert not any("screen_ids" in n for n in again["notices"])


def test_dangling_screen_ids_are_dropped_and_live_ones_kept_on_both_link_kinds(
    store: GraphStore, target: str, tmp_path: Path
):
    run, cfg = make_run(tmp_path, target, "runA")
    redeployable_world(run, target, "v1")
    run_load(run, cfg, store)
    store.write_requirements([requirement(target, "req_x")], "runA")
    store.write_links(
        [
            implemented("c_root_v1", HTTP_TABS, method="route", screens=["scr_gone", "scr_root_v1"]),
            implemented("c_root_v1", HTTP_TABS, method="testid", screens=["scr_gone"]),
            implemented("c_gql_v1", GQL_TABS, method="route", screens=[]),  # [] = every screen: untouched
            realized("req_x", "c_root_v1", method="bm25_judge", screens=["scr_gone"]),
            realized("req_x", "scr_gql_v1", method="llm_pick", screens=["scr_gone", "scr_gql_v1"]),
        ],
        "runA",
    )
    out = run_load(run, cfg, store)  # same run, same crawl: only the dangling ids are wrong
    assert edge_screen_ids(store, target, "IMPLEMENTED_BY") == [
        (GQL_TABS, []),
        (HTTP_TABS, ["scr_root_v1"]),  # route
        (HTTP_TABS, []),  # testid: no live id left, so every screen
    ]
    realized_ids = {dst: ids for dst, ids in edge_screen_ids(store, target, "REALIZED_BY")}
    assert realized_ids["scr_gql_v1"] == ["scr_gql_v1"]
    assert realized_ids[next(d for d in realized_ids if d != "scr_gql_v1")] == []
    assert out["dangling_attribution"] == {"edges": 4, "reset_to_all": 2, "ids_dropped": 4, "narrowed": 2}


# ============================================================ MINOR: deterministic list columns
def test_absence_near_lists_are_sorted(store: GraphStore, target: str):
    files = [
        code_file("pages/zp.vue", FileClass.PAGE),
        code_file("pages/ap.vue", FileClass.PAGE),
        code_file("comp/z.vue"),
        code_file("comp/a.vue"),
    ]
    store.write_code(
        CodeIndex(
            target=target,
            repo="o/r",
            ref="m",
            snapshot_id="s",
            adapter="vue",
            files=files,
            edges=[
                edge("pages/zp.vue", "comp/z.vue", "RENDERS"),
                edge("pages/ap.vue", "comp/a.vue", "RENDERS"),
            ],
            routes=[
                RouteDef(pattern="/zp", file="pages/zp.vue"),
                RouteDef(pattern="/ap", file="pages/ap.vue"),
            ],
        ),
        "r",
    )
    store.write_crawl([screen(target, "scr_z", "/zp", []), screen(target, "scr_a", "/ap", [])], [], "r", "b")
    store.link_routes("r", target=target)
    store.write_requirements([requirement(target, "rq")], "r")
    store.write_gaps(
        [
            Gap(
                id="g",
                kind=GapKind.NO_UI_EVIDENCE,
                status="NOT_FOUND_WITHIN_BUDGET",
                subject_id="rq",
                screens_searched=["scr_z", "scr_a"],
                bundle_hash="b",
                actions_spent=2,
            )
        ],
        "r",
    )
    for changed in (["comp/z.vue", "comp/a.vue"], ["comp/a.vue", "comp/z.vue"]):
        rows = store.read("absence_near", changed=changed, hops=1, bundle_hash="b")
        assert len(rows) == 1
        assert rows[0]["impacted_screen_ids"] == ["scr_a", "scr_z"]
        assert rows[0]["seeds"] == ["comp/a.vue", "comp/z.vue"]


def test_unmapped_ui_elements_lists_are_sorted(store: GraphStore, target: str):
    shared = [obs("o_shared_z", "button", "Shared")]
    store.write_crawl(
        [
            screen(target, "scr_z", "/zz", shared),
            screen(target, "scr_a", "/aa", [obs("o_shared_a", "button", "Shared")]),
        ],
        [],
        "r",
        "b",
    )
    element = store.read("unmapped_ui_elements")[0]["id"]
    kinds = (GapKind.UNMAPPED_CODE, GapKind.ACCESS_RESTRICTED, GapKind.NO_UI_EVIDENCE)
    store.write_gaps(
        [
            Gap(id=f"g{n}", kind=k, status="CLASSIFIED", subject_id=element, bundle_hash="b")
            for n, k in zip("zyx", kinds, strict=True)
        ],
        "r",
    )
    rows = store.read("unmapped_ui_elements")
    assert len(rows) == 1
    row = rows[0]
    assert row["screen_ids"] == ["scr_a", "scr_z"] and row["url_patterns"] == ["/aa", "/zz"]
    assert row["gap_kinds"] == sorted(k.value for k in kinds)


# ================================================= MINOR: URI userinfo with a literal '@' in the password
@pytest.mark.parametrize(
    ("text", "forbidden", "kept"),
    [
        ("cannot reach bolt://neo:p@ss@db.internal:7687 now", ["p@ss", "ss@", "neo:"], "db.internal:7687"),
        ("neo4j+s://u:a@b@c@host.example/db", ["a@b", "b@c", "u:"], "host.example/db"),
        ("bolt://u:pw@h/db@keep", ["u:pw"], "h/db@keep"),  # an '@' after the host is not userinfo
        ("see //user:s3cret@example.com/x?y=a@b", ["s3cret", "user:"], "example.com/x?y=a@b"),
    ],
)
def test_userinfo_scrub_takes_the_last_at_sign_before_the_host(text: str, forbidden: list[str], kept: str):
    out = store_module._scrub(text)
    assert all(piece not in out for piece in forbidden), out
    assert kept in out, out


def test_the_configured_password_is_scrubbed_from_driver_errors(monkeypatch):
    """The module promises that the configured password never reaches a failure record; a driver error that
    echoes it outside a `//user:pw@` URI (a bare 'neo:pw@host' pair) used to pass straight through."""
    secret = "pw-from-settings-9"

    class Driver:
        def verify_connectivity(self) -> None:
            return None

        def session(self, **kw: Any) -> Any:
            raise ServiceUnavailable(f"Unable to retrieve routing information for neo:{secret}@db:7687")

        def close(self) -> None:
            return None

    monkeypatch.setattr(store_module.GraphDatabase, "driver", lambda *a, **k: Driver())
    run = RunContext.ephemeral("t")
    settings = Settings(neo4j_uri="bolt://neo:pw-from-settings-9@db:7687", neo4j_password=secret)
    s = GraphStore.from_settings(settings, run, attempts=1)
    with pytest.raises(store_module.GraphError) as info:
        s.counts("t")
    blob = json.dumps([str(info.value), run.failures.items], default=str)
    assert secret not in blob


# ============================================ MINOR: closures use a per-hop distinct frontier
def test_hub_degree_and_absence_near_work_is_per_node_not_per_path(store: GraphStore, target: str):
    """`impact` was fixed to a distinct frontier per hop; hub_degree and absence_near still enumerated every
    walk (fan-in^hops rows, the same transaction-memory exhaustion at 3 hops on a big repo)."""
    index = layered_index(target, layers=5, width=40, fan=18)
    store.write_code(index, "r")
    seeds = [f"L0/f{i}.vue" for i in range(3)]
    n_edges = len(index.edges)
    degree = profile_rows(store, "hub_degree", paths=seeds, hops=3)
    absence = profile_rows(store, "absence_near", changed=seeds, hops=3, bundle_hash=None)
    assert degree < 5 * n_edges, (degree, n_edges)
    assert absence < 5 * n_edges, (absence, n_edges)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_hub_degree_and_absence_near_match_a_reference_closure(store: GraphStore, target: str, seed: int):
    """The rewrite must keep the closure semantics (a walk of at most `hops` edges, cycles harmless, the file
    itself never its own importer) that hub_degree and absence_near documented before."""
    rnd = random.Random(seed)
    n = 24
    paths = [f"f{i:02d}.vue" for i in range(n)]
    kinds = [
        rnd.choice([FileClass.PAGE, FileClass.COMPONENT, FileClass.UTIL, FileClass.LAYOUT]) for _ in paths
    ]
    pairs = sorted(
        {(rnd.randrange(n), rnd.randrange(n)) for _ in range(70)}
    )  # self loops and cycles included
    edges = [edge(paths[a], paths[b], rnd.choice(["IMPORTS", "RENDERS"])) for a, b in pairs]
    handlers = rnd.sample(range(n), 6)
    layouts = {handlers[0]: rnd.randrange(n), handlers[1]: rnd.randrange(n)}
    routes = [
        RouteDef(pattern=f"/r{k}", file=paths[h], layout=paths[layouts[h]] if h in layouts else None)
        for k, h in enumerate(handlers)
    ]
    index = CodeIndex(
        target=target,
        repo="o/r",
        ref="m",
        snapshot_id="s",
        adapter="vue",
        files=[code_file(p, k) for p, k in zip(paths, kinds, strict=True)],
        edges=edges,
        routes=routes,
    )
    store.write_code(index, "r")
    store.write_crawl([screen(target, f"s{k}", f"/r{k}", []) for k in range(len(routes))], [], "r", "b")
    store.link_routes("r", target=target)
    store.write_requirements([requirement(target, "rq")], "r")
    store.write_gaps(
        [
            Gap(
                id="g",
                kind=GapKind.NO_UI_EVIDENCE,
                status="NOT_FOUND_WITHIN_BUDGET",
                subject_id="rq",
                screens_searched=[f"s{k}" for k in range(len(routes))],
                bundle_hash="b",
            )
        ],
        "r",
    )

    importers: dict[str, set[str]] = {p: set() for p in paths}
    for a, b in pairs:
        importers[paths[b]].add(paths[a])

    def dist_from(start: str, hops: int) -> dict[str, int]:
        """Reverse walk distance (minimum edge count) to every file within `hops`."""
        out, frontier = {start: 0}, [start]
        for h in range(1, hops + 1):
            nxt = []
            for f in frontier:
                for i in sorted(importers[f]):
                    if i not in out:
                        out[i] = h
                        nxt.append(i)
            frontier = nxt
        return out

    ui = {"page", "layout", "component"}
    ui_total = sum(1 for k in kinds if k.value in ui)
    for hops in (0, 1, 2, 3):
        rows = {r["path"]: r for r in store.read("hub_degree", paths=paths, hops=hops)}
        for p, k in zip(paths, kinds, strict=True):
            reach = {f for f in dist_from(p, hops) if f != p}
            row = rows[p]
            assert row["importers"] == len(reach), (p, hops)
            assert row["ui_importers"] == len([f for f in reach if kinds[paths.index(f)].value in ui]), (
                p,
                hops,
            )
            assert row["direct_importers"] == len(importers[p] - {p}) * (1 if hops >= 1 else 0), (p, hops)
            assert row["ui_total"] == ui_total and row["kind"] == k.value

        seeds = rnd.sample(paths, 3)
        got = store.read("absence_near", changed=seeds, hops=hops, bundle_hash="b")
        best: dict[int, int] = {}  # screen index -> minimum hop over seeds
        reaching: set[str] = set()
        for s in seeds:
            d = dist_from(s, hops)
            for k, h in enumerate(handlers):
                files = [paths[h]] + ([routes[k].layout] if routes[k].layout else [])
                hits = [d[f] for f in files if f in d]
                if hits:
                    best[k] = min(best.get(k, 99), min(hits))
                    reaching.add(s)
        if not best:
            assert got == [], (seed, hops)
            continue
        assert len(got) == 1
        assert got[0]["impacted_screen_ids"] == sorted(f"s{k}" for k in best), (seed, hops)
        assert got[0]["seeds"] == sorted(reaching) and got[0]["hops"] == min(best.values())
