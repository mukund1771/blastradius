"""evalkit.synthetic: simulated one-file PRs against a fake store, and against real Neo4j (unique target)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from test_graph import neo4j_settings

from blastradius.evalkit.synthetic import (
    CIRCULAR_LABEL,
    candidate_files,
    impact_rows,
    run_synthetic_recall,
)
from blastradius.graph.store import GraphConnectionError, GraphStore
from blastradius.models import CodeEdge, CodeFile, CodeIndex, FileClass, Link, ScreenState, UIElementObs

LEAF, PAGE, LAYOUT, OTHER = (
    "src/components/Leaf.vue",
    "src/pages/Page.vue",
    "src/layouts/default.vue",
    "src/components/Other.vue",
)


def link(src: str, dst: str, conf: float = 0.9, method: str = "i18n_key") -> Link:
    return Link(kind="IMPLEMENTED_BY", src=src, dst=dst, confidence=conf, method=method, evidence_id="ev_1")


class FakeStore:
    """Answers the four templates synthetic recall uses from a toy graph. `drop` makes retrieval lossy."""

    def __init__(self, drop_screens: set[str] | None = None):
        self.importers = {LEAF: [PAGE], OTHER: [PAGE]}
        # element -> (file, method, confidence, screens, flows)
        self.elements = {
            "uie_save": (LEAF, "i18n_key", 0.9, ["s1", "s2"], ["flow_edit"]),
            "uie_open": (PAGE, "route", 0.95, ["s1"], []),
            "uie_chrome": (LAYOUT, "route", 0.95, ["s1", "s2", "s3", "s4"], []),
            "uie_guess": (OTHER, "llm_pick", 0.6, ["s3"], []),
            "uie_weak": (OTHER, "literal_text", 0.7, ["s3"], []),
        }
        self.drop = drop_screens or set()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _closure(self, path: str, hops: int) -> set[str]:
        out, frontier = {path}, {path}
        for _ in range(hops):
            frontier = {i for f in frontier for i in self.importers.get(f, [])} - out
            out |= frontier
        return out

    def read(self, template: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((template, params))
        if template == "screen_url_patterns":
            return [{"id": f"s{i}", "url_pattern": f"/p{i}"} for i in range(1, 5)]
        if template == "element_by_file":
            return [
                {
                    "path": f,
                    "element_id": e,
                    "role": "button",
                    "name": e,
                    "confidence": c,
                    "method": m,
                    "evidence_id": "ev",
                    "screen_ids": s,
                }
                for e, (f, m, c, s, _) in sorted(self.elements.items())
                if f in params["paths"]
            ]
        if template == "element_context":
            return [
                {
                    "element_id": e,
                    "screen_id": s,
                    "flows": [{"id": fl, "name": fl} for fl in flows],
                    "requirements": [],
                }
                for e, (_, _, _, screens, flows) in sorted(self.elements.items())
                if e in params["element_ids"]
                for s in screens
            ]
        if template == "impact":
            reach = self._closure(params["changed"][0], params["hops"])
            rows = []
            for e, (f, _, _, screens, flows) in sorted(self.elements.items()):
                if f in reach:
                    rows += [
                        {"element_id": e, "screen_id": s, "flows": [{"id": fl, "name": fl} for fl in flows]}
                        for s in screens
                        if s not in self.drop
                    ]
            return rows
        raise AssertionError(f"unexpected template {template}")


def test_perfect_retrieval_has_full_recall_and_is_labelled_circular():
    out = run_synthetic_recall(
        FakeStore(),
        "t",
        [link("o1", LEAF), link("o2", PAGE, 0.95, "route"), link("o3", LAYOUT, 0.95, "route")],
        breadth_max=0.25,
    )
    assert out["circular"] is True and out["label"] == CIRCULAR_LABEL and out["label"].startswith("CIRCULAR")
    assert out["n_elements"] == 3 and out["n_files"] == 3
    assert out["recall"]["k"] == 3 and out["recall"]["n"] == 3 and out["miss_count"] == 0
    assert out["flow_recall"]["k"] == out["flow_recall"]["n"] == 2  # flow_edit on s1 and on s2


def test_files_that_report_most_screens_are_trivial_and_recall_is_also_given_without_them():
    out = run_synthetic_recall(
        FakeStore(), "t", [link("o1", LEAF), link("o3", LAYOUT, 0.95, "route")], breadth_max=0.75
    )
    assert out["trivial_files"] == [LAYOUT]  # 4 of 4 screens; the leaf reaches 2 of 4
    assert out["recall_nontrivial"]["n"] == 1 and out["recall"]["n"] == 2


def test_a_lossy_retrieval_is_a_named_miss_with_the_missing_screen():
    out = run_synthetic_recall(FakeStore(drop_screens={"s2"}), "t", [link("o1", LEAF)])
    assert out["recall"]["k"] == 0 and out["recall"]["n"] == 1
    miss = out["misses"][0]
    assert miss["file"] == LEAF and miss["element_id"] == "uie_save" and miss["missing_screens"] == ["s2"]
    assert out["flow_recall"]["k"] == 1 and out["flow_recall"]["n"] == 2  # s1 still reports it, s2 does not


def test_only_deterministic_high_confidence_links_are_expectations():
    links = [
        link("o1", LEAF),
        link("o4", OTHER, 0.6, "llm_pick"),
        link("o5", OTHER, 0.7, "literal_text"),
        Link(
            kind="REALIZED_BY",
            src="req_1",
            dst="uie_save",
            confidence=0.9,
            method="bm25_judge",
            evidence_id="ev_2",
        ),
    ]
    files, available = candidate_files(links, min_confidence=0.8, max_files=10)
    assert files == [LEAF] and available == 1
    out = run_synthetic_recall(FakeStore(), "t", links)
    assert out["n_elements"] == 1 and {m["element_id"] for m in out["misses"]} == set()


def test_with_no_candidate_links_the_result_says_so_instead_of_claiming_recall():
    out = run_synthetic_recall(FakeStore(), "t", [])
    assert out["circular"] is True and out["n_elements"] == 0
    assert out["recall"]["n"] == 0 and out["recall"]["p"] is None


def test_file_cap_is_hash_ordered_and_deterministic():
    links = [link(f"o{i}", f"src/components/F{i:02d}.vue") for i in range(30)]
    a, total = candidate_files(links, min_confidence=0.8, max_files=10)
    b, _ = candidate_files(list(reversed(links)), min_confidence=0.8, max_files=10)
    assert a == b and len(a) == 10 and total == 30 and a == sorted(a)
    assert a != [f"src/components/F{i:02d}.vue" for i in range(10)]  # not simply the first ten


def test_default_retrieval_is_the_product_impact_template_at_its_hop_cap():
    store = FakeStore()
    impact_rows(store, "t", [LEAF])
    assert store.calls[-1] == ("impact", {"target": "t", "changed": [LEAF], "hops": 2, "forward": 0})


# ---------------------------------------------------------------------------------- real Neo4j
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


def test_recall_against_real_cypher_on_a_loaded_graph(store: GraphStore, target: str):
    files = [
        CodeFile(path=p, kind=FileClass.COMPONENT, loc=10, sha=f"sha_{p}")
        for p in (LEAF, PAGE, OTHER, LAYOUT)
    ]
    edges = [CodeEdge(src=PAGE, dst=LEAF, kind="RENDERS"), CodeEdge(src=LAYOUT, dst=PAGE, kind="RENDERS")]
    store.write_code(
        CodeIndex(
            target=target, repo="o/r", ref="main", snapshot_id="s", adapter="vue", files=files, edges=edges
        ),
        "r1",
    )

    def screen(sid: str, pattern: str, els: list[UIElementObs]) -> ScreenState:
        return ScreenState(
            id=sid,
            target=target,
            url=f"https://app.test{pattern}",
            url_pattern=pattern,
            fingerprint=f"fp_{sid}",
            capture_id=f"cap_{sid}",
            elements=els,
        )

    store.write_crawl(
        [
            screen(
                "s1",
                "/a",
                [
                    UIElementObs(id="o_save", role="button", name="Save"),
                    UIElementObs(id="o_open", role="button", name="Open"),
                ],
            ),
            screen("s2", "/b", [UIElementObs(id="o_save2", role="button", name="Save")]),
            screen("s3", "/c", [UIElementObs(id="o_other", role="button", name="Other")]),
        ],
        [],
        "r1",
        "b1",
    )
    links = [
        link("o_save", LEAF),
        link("o_save2", LEAF),
        link("o_open", PAGE, 0.95, "route"),
        link("o_other", OTHER, 0.6, "llm_pick"),
    ]
    store.write_links(links, "r1")
    out = run_synthetic_recall(store, target, links, breadth_max=0.9)
    assert out["n_elements"] == 2 and out["recall"]["k"] == 2 and out["miss_count"] == 0
    assert out["total_screens"] == 3
    # the real template at hop 2 also reports the importers' elements: LEAF reaches PAGE's "Open"
    rows = impact_rows(store, target, [LEAF])
    assert {r["element_name"] for r in rows} == {"Save", "Open"}
