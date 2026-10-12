"""evalkit.oracle_score: joining oracle labels to the crawl and scoring the linker's Link rows."""

from __future__ import annotations

from blastradius.evalkit.oracle_score import Label, metrics, rank_predictions, score_units
from blastradius.models import Link, ScreenState, UIElementObs

F = "packages/app/src/components/"
INDEXED = {F + "Header.vue", F + "Sidebar.vue", F + "Editor.vue", F + "Menu.vue"}


def el(oid: str, role: str, name: str, ordinal: int = 0) -> UIElementObs:
    return UIElementObs(id=oid, role=role, name=name, ordinal=ordinal)


def screen(
    sid: str,
    pattern: str,
    elements: list[UIElementObs],
    tabs: tuple[str, ...] = (),
    dialog: str | None = None,
) -> ScreenState:
    return ScreenState(
        id=sid,
        target="t",
        url=f"https://x.test{pattern}",
        url_pattern=pattern,
        fingerprint=sid,
        capture_id="c",
        elements=elements,
        active_tabs=list(tabs),
        dialog_title=dialog,
        kind="modal" if dialog else "page",
    )


def link(
    src: str, dst: str, conf: float = 0.9, method: str = "i18n_key", screens: list[str] | None = None
) -> Link:
    return Link(
        kind="IMPLEMENTED_BY",
        src=src,
        dst=dst,
        confidence=conf,
        method=method,
        evidence_id="ev_1",
        screen_ids=screens or [],
    )


def label(
    role: str,
    name: str,
    file: str,
    *,
    pattern: str = "/",
    tabs: tuple[str, ...] = (),
    ordinal: int = 0,
    hops: int = 0,
    dialog: str | None = None,
) -> Label:
    return Label(
        url_pattern=pattern,
        active_tabs=tabs,
        dialog_title=dialog,
        role=role,
        name=name,
        ordinal=ordinal,
        oracle_file=file,
        owner_hops=hops,
    )


def test_a_correct_top1_prediction_scores_as_a_hit_and_carries_its_method():
    screens = [screen("s1", "/", [el("o1", "button", "Save")])]
    res = score_units(
        [label("button", "Save", F + "Editor.vue")], screens, [link("o1", F + "Editor.vue")], INDEXED
    )
    assert len(res) == 1 and res[0].status == "scored" and res[0].hit1 and res[0].hit3
    m = metrics(res, 1)
    assert m["scored"] == 1 and m["top1_precision_of_covered"]["k"] == 1
    assert m["by_top1_method"]["i18n_key"]["n"] == 1


def test_hit_at_3_counts_a_second_choice_but_top1_does_not():
    screens = [screen("s1", "/", [el("o1", "button", "Save")])]
    links = [link("o1", F + "Header.vue", 0.9), link("o1", F + "Editor.vue", 0.45, "llm_pick")]
    res = score_units([label("button", "Save", F + "Editor.vue")], screens, links, INDEXED)
    assert not res[0].hit1 and res[0].hit3 and res[0].top1.path == F + "Header.vue"


def test_uncovered_elements_are_scored_as_misses_and_reported_as_coverage():
    screens = [screen("s1", "/", [el("o1", "button", "Save"), el("o2", "button", "Open")])]
    labels = [label("button", "Save", F + "Editor.vue"), label("button", "Open", F + "Menu.vue")]
    res = score_units(labels, screens, [link("o1", F + "Editor.vue")], INDEXED)
    m = metrics(res, 2)
    assert m["scored"] == 2 and m["linker_coverage"]["k"] == 1 and m["linker_coverage"]["n"] == 2
    assert m["top1_precision_of_covered"]["n"] == 1 and m["top1_accuracy_of_scored"]["n"] == 2
    assert m["by_top1_method"]["none"]["n"] == 1


def test_label_for_a_file_not_on_main_is_excluded_not_a_miss():
    screens = [screen("s1", "/", [el("o1", "button", "Newsletter")])]
    res = score_units(
        [label("button", "Newsletter", "packages/web/src/components/Newsletter.vue")],
        screens,
        [link("o1", F + "Header.vue")],
        INDEXED,
    )
    assert res[0].status == "file_not_on_main"
    m = metrics(res, 1)
    assert m["scored"] == 0 and m["excluded"]["file_not_on_main"] == 1
    assert m["linker_coverage"]["n"] == 0  # no data, not 0%


def test_an_element_the_crawl_never_saw_is_excluded_as_not_in_crawl():
    screens = [screen("s1", "/", [el("o1", "button", "Save")])]
    res = score_units([label("button", "Brand new", F + "Editor.vue")], screens, [], INDEXED)
    assert res[0].status == "not_in_crawl" and metrics(res, 1)["excluded"]["not_in_crawl"] == 1


def test_exact_state_join_beats_a_same_pattern_screen_with_different_ordinals():
    home = screen("home", "/", [el("a", "button", "Add", 0)], tabs=("Collections",))
    envs = screen("envs", "/", [el("b", "button", "Add", 0)], tabs=("Environments",))
    links = [link("a", F + "Sidebar.vue"), link("b", F + "Menu.vue")]
    res = score_units(
        [label("button", "Add", F + "Menu.vue", tabs=("Environments",))], [home, envs], links, INDEXED
    )
    assert res[0].join == "state" and res[0].top1.path == F + "Menu.vue" and res[0].hit1
    pattern_only = score_units(
        [label("button", "Add", F + "Menu.vue", tabs=("History",))], [home, envs], links, INDEXED
    )
    assert pattern_only[0].join == "pattern"  # no screen in that state: falls back and says so


def test_links_derived_on_other_screens_do_not_count():
    s1 = screen("s1", "/", [el("o1", "button", "Save")], tabs=("A",))
    s2 = screen("s2", "/", [el("o1", "button", "Save")], tabs=("B",))
    links = [link("o1", F + "Header.vue", screens=["s2"]), link("o1", F + "Editor.vue", screens=["s1"])]
    res = score_units([label("button", "Save", F + "Editor.vue", tabs=("A",))], [s1, s2], links, INDEXED)
    assert [p.path for p in res[0].predicted] == [F + "Editor.vue"]


def test_a_layout_control_seen_in_two_states_is_one_element():
    s1 = screen("s1", "/", [el("o1", "button", "Menu")], tabs=("A",))
    s2 = screen("s2", "/", [el("o1", "button", "Menu")], tabs=("B",))
    labels = [
        label("button", "Menu", F + "Header.vue", tabs=("A",)),
        label("button", "Menu", F + "Header.vue", tabs=("B",)),
    ]
    res = score_units(labels, [s1, s2], [link("o1", F + "Header.vue")], INDEXED)
    assert len(res) == 1 and len(res[0].states) == 2 and metrics(res, 2)["scored"] == 1


def test_prediction_ranking_is_confidence_then_method_then_path():
    links = [
        link("o", F + "b.vue", 0.6, "llm_pick"),
        link("o", F + "a.vue", 0.6, "llm_pick"),
        link("o", F + "c.vue", 0.6, "i18n_key"),
        link("o", F + "d.vue", 0.9, "literal_text"),
    ]
    ranked = rank_predictions(links, {"o"}, set())
    assert [p.path for p in ranked] == [F + "d.vue", F + "c.vue", F + "a.vue", F + "b.vue"]
    again = rank_predictions(list(reversed(links)), {"o"}, set())
    assert [p.path for p in again] == [p.path for p in ranked]


def test_owner_hops_are_broken_out():
    screens = [screen("s1", "/", [el("o1", "button", "A"), el("o2", "button", "B")])]
    labels = [label("button", "A", F + "Editor.vue", hops=0), label("button", "B", F + "Menu.vue", hops=3)]
    res = score_units(labels, screens, [link("o1", F + "Editor.vue"), link("o2", F + "Header.vue")], INDEXED)
    by = metrics(res, 2)["by_owner_hops"]
    assert by["0"]["top1_correct"]["k"] == 1 and by["3"]["top1_correct"]["k"] == 0
