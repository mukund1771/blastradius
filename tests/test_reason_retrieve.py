"""reason.retrieve: changed files -> screens and controls through templated Cypher (D19, D25; cases 32, 39, 50, 51).

A real Neo4j target holds the real Hoppscotch index (63273f8), the real 45-action crawl and nine hand-made
IMPLEMENTED_BY links (tests/test_reason_support.link_plan). Skips when the database is unreachable.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from test_reason_support import (
    COMMON,
    LOCALE,
    FakeGh,
    World,
    cfg,
    new_run,
    overlay_for,
    pr_and_retrieval,
)

from blastradius.code.index import edge_evidence_id
from blastradius.models import MAX_HOPS, CodeEdge
from blastradius.reason.change import LocaleDiff
from blastradius.reason.pr import fetch_pr
from blastradius.reason.retrieve import build_seeds
from blastradius.reason.thresholds import load_thresholds

SELECT = "Select environment"


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    w = World(tmp_path_factory.mktemp("reason-retrieve"))
    try:
        yield w
    finally:
        w.close()


def hits_named(retr, name: str):
    return [h for h in retr.hits if h.element_name == name]


def test_seeds_exclude_what_is_set_aside_and_carry_the_diff_facts(world: World):
    _, retr, _ = pr_and_retrieval(world, 6645)
    names = {s.display_path.removeprefix(COMMON) for s in retr.seeds}
    assert "services/__tests__/current-environment-value.service.spec.ts" not in names
    assert "components.d.ts" not in names and LOCALE not in {s.display_path for s in retr.seeds}
    assert retr.set_aside == {"generated": 1, "test": 2}               # case 40: listed, never looked up
    assert len(retr.seeds) == 10
    svc = retr.seed(COMMON + "services/current-environment-value.service.ts")
    assert svc.kind.value == "ADDITIVE_API" and svc.touched == ["CurrentValueService"] and svc.in_snapshot
    assert (svc.importers_h2, svc.ui_importers_h2) == (162, 94) and svc.fraction_h2 == pytest.approx(0.2773, abs=1e-4)
    added = retr.seed(COMMON + "components/environments/Editor.vue")
    assert added.added and not added.in_snapshot and added.importers_h1 == 0


def test_direct_route_is_the_element_the_changed_file_implements(world: World):
    _, retr, _ = pr_and_retrieval(world, 6645)
    sel = hits_named(retr, SELECT)
    assert sel
    for h in sel:
        own = [r for r in h.routes if r.seed.endswith("environments/Selector.vue")]
        assert [(r.direction, r.hops, r.confidence) for r in own] == [("self", 0, 0.9)]
        assert own[0].impl_evidence_id and world.run.evidence.exists(own[0].impl_evidence_id)


def test_importer_route_decays_per_hop_and_cites_code_edges(world: World):
    _, retr, _ = pr_and_retrieval(world, 6645)
    add_new = hits_named(retr, "Add new")                               # Add.vue renders / imports Selector.vue
    routes = [r for h in add_new for r in h.routes if r.seed.endswith("Selector.vue")]
    assert routes and all(r.direction == "reverse" and r.hops == 1 for r in routes)
    assert {r.confidence for r in routes} == {round(0.9 * 0.8, 4)}
    edge = CodeEdge(src=routes[0].via[0], dst=routes[0].via[1], kind=routes[0].kinds[0],
                    symbols=routes[0].symbols[0], via=routes[0].specifiers[0])
    assert edge_evidence_id(edge) in routes[0].evidence_ids
    assert all(world.run.evidence.exists(e) for e in routes[0].evidence_ids)


def test_hop_cap_is_two(world: World):
    _, retr, _ = pr_and_retrieval(world, 6645)
    assert max(r.hops for h in retr.hits for r in h.routes) <= MAX_HOPS
    assert any(r.hops == MAX_HOPS for h in retr.hits for r in h.routes)


def test_counts_are_over_distinct_screen_element_pairs_not_routes(world: World):
    _, retr, _ = pr_and_retrieval(world, 6645)
    keys = [h.key for h in retr.hits]
    assert len(keys) == len(set(keys))
    multi = [h for h in retr.hits if len(h.routes) > 1]
    assert multi, "an element reached from two changed files must still be one hit"


def test_forward_hop_is_held_in_reserve_not_admitted(world: World):
    _, retr, _ = pr_and_retrieval(world, 6645)
    fwd = [r for h in hits_named(retr, "Switch protocol") for r in h.routes if r.direction == "forward"]
    assert fwd and all(not r.admitted and r.hops == 1 for r in fwd)
    assert all(not r.admitted for h in retr.hits for r in h.routes if r.direction == "forward")


def test_changed_text_reaches_the_control_that_shows_it(world: World):
    diff = LocaleDiff(LOCALE, changed={"environment.select": (SELECT, "Choose environment")})
    _, retr, _ = pr_and_retrieval(world, 6658, diffs=[diff])           # a PR that does not touch Selector.vue
    copy = [r for h in hits_named(retr, SELECT) for r in h.routes if r.direction == "copy"]
    assert copy and all(r.confidence == 0.9 and not r.high_severity for r in copy)
    assert "Choose environment" in copy[0].note and copy[0].old_text == SELECT
    res = retr.locales[0]
    assert res.changed == 1 and res.elements_matched >= 1 and res.exact_matches == res.elements_matched


def test_removed_text_that_is_still_shown_is_high_severity(world: World):
    diff = LocaleDiff(LOCALE, removed={"tab.collections": "Collections"})
    _, retr, _ = pr_and_retrieval(world, 6645, diffs=[diff])
    routes = [r for h in hits_named(retr, "Collections") for r in h.routes if r.direction == "copy"]
    assert routes and all(r.high_severity for r in routes)
    assert retr.locales[0].high_severity_keys == ["tab.collections"]


def test_shared_file_but_different_words_is_capped_not_dropped(world: World):
    # `environment.select` is used by Selector.vue, which also implements other controls; they get the cap
    diff = LocaleDiff(LOCALE, changed={"environment.select": (SELECT, "Choose environment")})
    _, retr, _ = pr_and_retrieval(world, 6658, diffs=[diff])
    other = [r for h in retr.hits if h.element_name != SELECT for r in h.routes if r.direction == "copy"]
    cap = load_thresholds().caps.copy_other_element
    assert all(r.confidence <= cap and r.caps for r in other)


def test_added_files_attach_to_their_importers_through_the_overlay(world: World):
    ov = overlay_for(6645, world.index)
    _, retr, _ = pr_and_retrieval(world, 6645, overlay=ov)
    cvi = retr.seed(COMMON + "components/environments/CurrentValueInput.vue")
    assert cvi.overlay_attached and cvi.overlay_importers == [COMMON + "components/environments/Selector.vue"]
    routes = [r for h in hits_named(retr, SELECT) for r in h.routes if r.direction == "overlay"]
    assert routes and all(r.seed == cvi.path and r.hops == 1 and r.confidence == 0.72 for r in routes)
    assert retr.overlay["status"] == "applied" and retr.overlay["added"] == 2
    assert cvi.keys == ["environment.edit_current_value"]               # head uses, from the overlay


def test_without_an_overlay_added_files_stay_unattached_and_say_why(world: World):
    _, retr, _ = pr_and_retrieval(world, 6645)
    assert retr.overlay["status"] == "not_applied"
    assert not retr.seed(COMMON + "components/environments/Editor.vue").overlay_attached
    assert not [r for h in retr.hits for r in h.routes if r.direction == "overlay"]


def test_hub_package_and_stored_data_categories_and_breadth(world: World):
    _, retr, _ = pr_and_retrieval(world, 6658)
    idx = retr.seed("packages/hoppscotch-data/src/collection/index.ts")
    assert idx.categories == ["hub", "stored_data"] and idx.file_class.value == "data_schema"
    assert idx.fraction_h2 == pytest.approx(0.3451, abs=1e-3) and idx.ui_importers_h2 == 117
    assert retr.seed("packages/hoppscotch-data/src/collection/v/13.ts").added


def test_build_seeds_caps_at_max_seeds_and_reports_the_rest(tmp_path: Path):
    run = new_run(tmp_path / "r", "seeds")
    gh = FakeGh((6645,))
    proto = gh.files[6645][2]
    rows = []
    for i in range(120):
        row = dict(proto)
        row["filename"] = f"packages/hoppscotch-common/src/components/gen/File{i:03d}.vue"
        rows.append(row)
    gh.files[6645] = rows
    gh.prs[6645]["changed_files"] = 120
    pr = fetch_pr(run, cfg(), gh, 6645)
    for f in pr.files:
        f.in_snapshot = True
    retr = build_seeds(run, cfg(), pr, load_thresholds())
    assert len(retr.seeds) == 80 and retr.not_assessed == 40
