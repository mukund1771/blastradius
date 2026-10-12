"""reason.grade: the CRAG-inspired grader (D17, D25; cases 50, 51, 76).

Unit tests drive the grader with hand-built retrievals and a stub store (no database); the integration tests
grade the real demo PRs against a Neo4j target. The properties that matter and are asserted below: the verdict
only ever lowers a confidence (`confidence <= uncapped`), NARROW downgrades and never deletes, and at most two
corrective rounds run.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_reason_support import COMMON, World, new_run, overlay_for, pr_and_retrieval

from blastradius.models import FileClass
from blastradius.reason.change import ChangeKind
from blastradius.reason.facts import Hit, Retrieval, Route, SeedInfo, path_confidence
from blastradius.reason.grade import (
    Signals,
    choose,
    edge_relevant,
    grade,
    route_relevant,
    verdict_for,
)
from blastradius.reason.thresholds import load_thresholds
from blastradius.run import RunContext

UI_TOTAL = 339


# ------------------------------------------------------------------------------------------- unit
class StubStore:
    def __init__(self, importers: list[dict[str, Any]] | None = None):
        self.importers, self.reads = importers or [], []

    def read(self, template: str, **params: Any) -> list[dict[str, Any]]:
        self.reads.append(template)
        if template == "importer_files":
            return self.importers
        return []


def seed(path: str = "a/service.ts", *, kind=ChangeKind.BODY, touched=("Svc",), cls=FileClass.SERVICE, f2=0.0,
         ui2=0, **kw) -> SeedInfo:
    return SeedInfo(path=path, display_path=path, file_class=cls, status="modified", kind=kind,
                    touched=list(touched), symbols_known=bool(touched), in_snapshot=kw.pop("in_snapshot", True),
                    fraction_h2=f2, ui_importers_h2=ui2, ui_total=UI_TOTAL, importers_h2=ui2, **kw)


def route(s: SeedInfo, *, hops=1, impl=0.9, symbols=("Svc",), kind="IMPORTS", direction=None, admitted=True,
          file="ui/A.vue") -> Route:
    direction = direction or ("self" if hops == 0 else "reverse")
    conf = path_confidence(impl, hops)
    via = [file, s.path] if hops else [s.path]
    return Route(seed=s.path, direction=direction, hops=hops, file=file, via=via,
                 kinds=[kind] if hops else [], symbols=[list(symbols)] if hops else [], specifiers=[None] * hops,
                 impl_confidence=impl, confidence=conf, uncapped=conf, admitted=admitted, evidence_ids=["ev_x"])


def retrieval(seeds: list[SeedInfo], hits: list[Hit]) -> Retrieval:
    return Retrieval(seeds=seeds, hits=hits, ui_total=UI_TOTAL, overlay={"status": "applied"})


def hit(i: int, *routes: Route, name: str = "Btn") -> Hit:
    return Hit(screen_id=f"s{i}", element_id=f"e{i}", element_role="button", element_name=f"{name}{i}",
               routes=list(routes))


@pytest.fixture
def run(tmp_path: Path) -> RunContext:
    return new_run(tmp_path / "r", "grade")


def test_verdict_and_action_table():
    sig = {"mapped": True, "evidence_routes": 1, "strong": True, "best_confidence": 0.9,
           "min_edge_confidence": 0.9, "direct_elements": 1, "closure_ui_files_h2": 3,
           "closure_fraction_h2": 0.01, "effective_ui_files_h2": 3, "effective_fraction_h2": 0.01, "wide": False,
           "hub": False, "change_kind": "BODY", "symbols_known": True}
    s = seed()
    ok = Signals(**sig)
    assert verdict_for(ok) == "CORRECT" and choose("CORRECT", ok, s, []) == "ACCEPT"
    wide = Signals(**{**sig, "wide": True, "effective_fraction_h2": 0.3})
    assert verdict_for(wide) == "AMBIGUOUS" and choose("AMBIGUOUS", wide, s, []) == "NARROW"
    assert choose("AMBIGUOUS", wide, s, ["NARROW"]) == "ESCALATE"
    hub = Signals(**{**sig, "hub": True})
    assert verdict_for(hub) == "AMBIGUOUS"
    nothing = Signals(**{**sig, "evidence_routes": 0, "strong": False, "best_confidence": 0.0})
    assert verdict_for(nothing) == "INCORRECT" and choose("INCORRECT", nothing, s, []) == "EXPAND"
    assert choose("INCORRECT", nothing, s, ["EXPAND"]) == "PROBE"
    assert choose("INCORRECT", nothing, s, ["EXPAND", "PROBE"]) == "ESCALATE"
    weak = Signals(**{**sig, "strong": False})
    assert verdict_for(weak) == "AMBIGUOUS" and choose("AMBIGUOUS", weak, s, []) == "EXPAND"
    unmapped = Signals(**{**sig, "mapped": False})
    assert verdict_for(unmapped) == "INCORRECT"
    # a wide seed whose change cannot be narrowed (no symbols, not additive) goes straight to a human
    vue = seed("a/X.vue", cls=FileClass.COMPONENT, touched=())
    assert choose("AMBIGUOUS", wide, vue, []) == "ESCALATE"


def test_named_import_relevance():
    assert edge_relevant("RENDERS", [], {"A"}) is True
    assert edge_relevant("IMPORTS", ["A", "B"], {"B"}) is True
    assert edge_relevant("IMPORTS", ["C"], {"B"}) is False
    assert edge_relevant("IMPORTS", [], {"B"}) is None                     # side-effect / unlisted: cannot tell
    assert edge_relevant("IMPORTS", ["*"], {"B"}) is True
    s = seed()
    two_hops = Route(seed=s.path, direction="reverse", hops=2, via=["x", "y", s.path], kinds=["IMPORTS", "IMPORTS"],
                     symbols=[["Other"], ["Svc"]])
    assert route_relevant(two_hops, {"Svc"}) is False                       # one edge on the path names nothing changed


def test_narrow_downgrades_and_never_deletes_case_76(run: RunContext):
    th = load_thresholds()
    s = seed(kind=ChangeKind.ADDITIVE_API, f2=0.277, ui2=94)
    hits = [hit(i, route(s, file=f"ui/R{i}.vue")) for i in range(6)]
    hits.append(hit(9, route(s, hops=0, direction="self", file="a/service.ts")))
    retr = retrieval([s], hits)
    out = grade(run, StubStore(), retr, base_index=None, th=th)
    g = out.seeds[0]
    assert g.rounds[0].action == "NARROW" and g.rounds[0].verdict_before == "AMBIGUOUS"
    # nothing was deleted: every route is still there, still admitted, with its original evidence
    assert sum(len(h.routes) for h in retr.hits) == 7 and all(r.admitted for h in retr.hits for r in h.routes)
    readers = [r for h in retr.hits for r in h.routes if r.direction == "reverse"]
    assert all(r.confidence == th.caps.additive_reader and r.uncapped == 0.72 for r in readers)
    assert all(r.evidence_ids == ["ev_x"] for r in readers)
    # the seed's own control is untouched, and the verdict is accepted after narrowing
    own = next(r for h in retr.hits for r in h.routes if r.direction == "self")
    assert own.confidence == 0.9 and not own.caps
    assert g.verdict == "CORRECT" and g.signals.effective_ui_files_h2 == 0


def test_accept_still_applies_the_change_kind_cap(run: RunContext):
    th = load_thresholds()
    s = seed(kind=ChangeKind.ADDITIVE_API, f2=0.01, ui2=3)
    retr = retrieval([s], [hit(1, route(s)), hit(2, route(s, hops=0, direction="self"))])
    g = grade(run, StubStore(), retr, th=th).seeds[0]
    assert g.verdict == "CORRECT" and g.rounds == [] and g.action == "ACCEPT"
    reader = retr.hits[0].routes[0]
    assert reader.confidence == th.caps.additive_reader                      # 0.72 -> "possible"


def test_type_only_caps_every_route_including_the_files_own_controls(run: RunContext):
    th = load_thresholds()
    s = seed("ui/Own.vue", kind=ChangeKind.TYPE_ONLY, cls=FileClass.COMPONENT, touched=(), f2=0.01, ui2=2)
    retr = retrieval([s], [hit(1, route(s, hops=0, direction="self", file=s.path)), hit(2, route(s))])
    grade(run, StubStore(), retr, th=th)
    assert all(r.confidence <= th.caps.type_only for h in retr.hits for r in h.routes)
    assert all(r.uncapped > th.caps.type_only for h in retr.hits for r in h.routes)


def test_symbol_filter_keeps_relevant_importers_and_downgrades_the_rest(run: RunContext):
    th = load_thresholds()
    s = seed("pkg/data/index.ts", touched=("HoppCollection",), f2=0.34, ui2=117, cls=FileClass.DATA_SCHEMA,
             categories=["hub", "stored_data"], fraction_h1=0.2)
    hits = [hit(1, route(s, symbols=("HoppCollection",), file="ui/Uses.vue")),
            hit(2, route(s, symbols=("HoppRESTRequest",), file="ui/Other.vue")),
            hit(3, route(s, symbols=("HoppCollection",), kind="RENDERS", file="ui/Renders.vue"))]
    retr = retrieval([s], hits)
    g = grade(run, StubStore(), retr, base_index=None, th=th).seeds[0]
    by_file = {h.routes[0].file: h.routes[0] for h in retr.hits}
    assert by_file["ui/Uses.vue"].confidence == 0.72 and by_file["ui/Renders.vue"].confidence == 0.72
    assert by_file["ui/Other.vue"].confidence == th.caps.symbol_miss
    assert g.rounds[0].action == "NARROW" and g.rounds[0].effect["downgraded"] == 1
    assert g.rounds[0].effect["readers"] == 3 and g.rounds[0].effect["readers_kept"] == 2
    assert g.signals.symbol_hit_rate == pytest.approx(2 / 3, abs=1e-3)


def test_incorrect_seed_gets_expand_then_probe_and_an_unmapped_change_gap(run: RunContext):
    th = load_thresholds()
    s = seed("ui/Orphan.vue", cls=FileClass.COMPONENT, touched=(), kind=ChangeKind.BODY)
    fwd = route(s, hops=1, impl=0.9, direction="forward", admitted=False, file="ui/Dep.vue")
    retr = retrieval([s], [hit(1, fwd)])
    store = StubStore([{"path": s.path, "importer": "helpers/x.ts", "kind": "util", "edge_kind": "IMPORTS",
                        "symbols": []}])
    g = grade(run, store, retr, th=th).seeds[0]
    assert [r.action for r in g.rounds] == ["EXPAND", "PROBE"]              # max_rounds = 2
    assert fwd.admitted and fwd.confidence == th.caps.forward               # reserve admitted, capped
    assert fwd.uncapped == th.caps.forward                                  # the evidence itself is weak
    assert g.verdict == "AMBIGUOUS" and g.signals.best_confidence == th.caps.forward
    probe = run.evidence.get(g.probe_evidence_id)
    assert probe["kind"] == "reason.probe" and "1 file(s)" in probe["summary"]


def test_seed_with_nothing_is_incorrect_and_becomes_could_not_determine(run: RunContext):
    s = seed("ui/Orphan.vue", cls=FileClass.COMPONENT, touched=())
    g = grade(run, StubStore(), retrieval([s], [])).seeds[0]
    assert g.verdict == "INCORRECT" and g.action == "ESCALATE" and len(g.rounds) == 2
    assert g.gap is not None and g.gap.kind.value == "UNMAPPED_CHANGE" and g.gap.subject_id == "ui/Orphan.vue"
    assert g.reviews[0].reason_kind == "NOT_MAPPED_TO_SCREEN" and g.reviews[0].code == "UNMAPPED_CHANGE"
    # a non-UI directory gets the could-not-determine row but no UNMAPPED_CHANGE gap (the kind says UI directory)
    lib = seed("lib/x.ts", cls=FileClass.SOURCE_OTHER, touched=())
    g2 = grade(run, StubStore(), retrieval([lib], [])).seeds[0]
    assert g2.gap is None and g2.reviews and g2.reviews[0].code == "NEEDS_HUMAN"


def test_grader_never_raises_a_confidence_and_is_deterministic(run: RunContext):
    th = load_thresholds()

    def build() -> Retrieval:
        a = seed("a/A.ts", kind=ChangeKind.ADDITIVE_API, f2=0.3, ui2=100)
        b = seed("b/B.ts", kind=ChangeKind.BODY, touched=("B",), f2=0.01, ui2=3)
        c = seed("c/C.vue", cls=FileClass.COMPONENT, touched=(), f2=0.0)
        hits = [hit(i, route(a, file=f"u/{i}.vue"), route(b, symbols=("Z",), file=f"u/{i}.vue")) for i in range(5)]
        hits.append(hit(7, route(c, hops=1, direction="forward", admitted=False, file="u/F.vue")))
        return retrieval([a, b, c], hits)

    r1, r2 = build(), build()
    g1 = grade(run, StubStore(), r1, th=th)
    g2 = grade(run, StubStore(), r2, th=th)
    for h in r1.hits:
        for r in h.routes:
            assert 0.0 <= r.confidence <= r.uncapped
    assert g1.model_dump_json() == g2.model_dump_json()
    assert all(len(sg.rounds) <= th.grade.max_rounds for sg in g1.seeds)


def test_a_wide_component_that_cannot_be_narrowed_is_escalated_and_its_indirect_routes_capped(run: RunContext):
    th = load_thresholds()
    s = seed("ui/Shared.vue", cls=FileClass.COMPONENT, touched=(), f2=0.4, ui2=135)
    hits = [hit(1, route(s, hops=0, direction="self", file=s.path)),
            *[hit(i, route(s, kind="RENDERS", symbols=(), file=f"ui/P{i}.vue")) for i in range(2, 6)]]
    retr = retrieval([s], hits)
    g = grade(run, StubStore(), retr, th=th).seeds[0]
    assert g.verdict == "AMBIGUOUS" and g.action == "ESCALATE" and g.rounds == []
    assert [r.confidence for h in retr.hits[1:] for r in h.routes] == [th.caps.ambiguous_indirect] * 4
    own = retr.hits[0].routes[0]
    assert own.confidence == 0.9 and not own.caps                           # its own controls are not capped
    assert g.reviews[0].reason_kind == "TOO_BROAD"


def test_kind_cap_applies_even_when_the_seed_stays_ambiguous_without_narrowing(run: RunContext):
    th = load_thresholds()
    s = seed(kind=ChangeKind.ADDITIVE_API, f2=0.01, ui2=2)
    weak = route(s, impl=0.5)                                               # 0.4: below the element floor
    retr = retrieval([s], [hit(1, weak)])
    g = grade(run, StubStore(), retr, th=th).seeds[0]
    assert [r.action for r in g.rounds] == ["EXPAND", "PROBE"] and g.verdict == "AMBIGUOUS"
    assert weak.confidence <= th.caps.additive_reader and "additive_reader=0.45" in weak.caps


def test_a_layout_is_global_by_construction_not_an_unmapped_change(run: RunContext):
    s = seed("layouts/default.vue", cls=FileClass.LAYOUT, touched=(), categories=["global_layout"])
    g = grade(run, StubStore(), retrieval([s], [])).seeds[0]
    assert g.verdict == "AMBIGUOUS" and g.rounds == [] and g.gap is None and g.reviews == []
    assert g.signals.hub and "global layout" in g.signals.hub_reasons


# ------------------------------------------------------------------------------------- integration
@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    w = World(tmp_path_factory.mktemp("reason-grade"))
    try:
        yield w
    finally:
        w.close()


def test_6645_services_are_ambiguous_by_breadth_then_narrowed_to_correct(world: World):
    _, retr, _ = pr_and_retrieval(world, 6645, overlay=overlay_for(6645, world.index))
    out = grade(world.run, world.store, retr, base_index=world.index)
    by = {g.display_path.removeprefix(COMMON): g for g in out.seeds}
    for name in ("services/current-environment-value.service.ts", "services/secret-environment.service.ts"):
        g = by[name]
        assert g.rounds[0].verdict_before == "AMBIGUOUS" and g.rounds[0].action == "NARROW"
        assert g.rounds[0].effect["effective_ui_files_h2"] == 0 and g.verdict == "CORRECT"
    sel = by["components/environments/Selector.vue"]
    assert sel.verdict == "CORRECT" and sel.rounds == [] and sel.signals.effective_fraction_h2 < 0.25
    for h in retr.hits:
        for r in h.routes:
            assert r.confidence <= r.uncapped
    svc_routes = [r for h in retr.hits for r in h.routes if r.seed.endswith("current-environment-value.service.ts")]
    assert svc_routes and all(r.confidence <= 0.45 for r in svc_routes)      # readers kept, as "possible"
    assert out.counts["INCORRECT"] == 0                                     # both added files are attached


def test_6658_hub_is_narrowed_by_named_imports_and_the_filter_is_measured(world: World):
    _, retr, _ = pr_and_retrieval(world, 6658, overlay=overlay_for(6658, world.index))
    out = grade(world.run, world.store, retr, base_index=world.index)
    g = next(x for x in out.seeds if x.display_path.endswith("hoppscotch-data/src/collection/index.ts"))
    assert g.rounds[0].action == "NARROW" and g.rounds[0].verdict_before == "AMBIGUOUS"
    assert 0 < g.rounds[0].effect["effective_ui_files_h2"] < 117 and g.rounds[0].effect["downgraded"] > 0
    assert g.signals.effective_fraction_h2 < 0.25 and g.verdict == "CORRECT" and not g.signals.hub


def test_pr_with_unattached_added_files_is_incorrect_for_them_without_a_head_overlay(world: World):
    _, retr, _ = pr_and_retrieval(world, 6641)                              # no overlay: nothing attaches
    out = grade(world.run, world.store, retr, base_index=world.index)
    added = [g for g in out.seeds if g.display_path.endswith("ResponseCompareModal.vue")]
    assert added and all(g.verdict == "INCORRECT" and "OVERLAY_UNAVAILABLE" in g.reasons for g in added)
    assert all(g.gap is not None for g in added)


def test_locale_seed_is_graded_by_whether_the_changed_text_reaches_a_control(world: World):
    _, retr, _ = pr_and_retrieval(world, 6641, overlay=overlay_for(6641, world.index))
    out = grade(world.run, world.store, retr, base_index=world.index)
    loc = next(g for g in out.seeds if g.seed.startswith("locale:"))
    assert loc.verdict == "CORRECT" and loc.signals.change_kind == "COPY_ONLY"     # only added strings
