"""Flows (D08; cases 12, 46): shortest observed paths from the seed screens, names from titles, bounds."""

from __future__ import annotations

from pathlib import Path

import pytest
from link_helpers import CRAWL, RecordingStore, cfg, make_run, read_jsonl_dicts

from blastradius.ids import stable_id
from blastradius.link.flows import MAX_DEPTH, MAX_FLOWS, build_flows, entry_screens, run_flows
from blastradius.models import ScreenState, Transition, UIElementObs
from blastradius.targets import CodeConfig, CrawlConfig, SpecConfig, TargetConfig


def target(seeds: list[str]) -> TargetConfig:
    return TargetConfig(name="t", display_name="Acme", base_url="https://app.test",
                        crawl=CrawlConfig(seed_urls=seeds, egress_allow=["app.test"]),
                        spec=SpecConfig(kind="llms_full"), code=CodeConfig(repo="a/b", adapter="vue", roots=["s"]))


def scr(sid: str, path: str, kind: str = "page", title: str = "", dialog: str | None = None,
        links: dict[str, str] | None = None, first: str | None = None, depth: int = 0) -> ScreenState:
    els = [UIElementObs(id=f"el_{sid}_{n}", role="link", name=n, href=h) for n, h in (links or {}).items()]
    return ScreenState(id=sid, target="t", url="https://app.test" + path, url_pattern=path, kind=kind,
                       fingerprint=sid, capture_id="c_" + sid, title=title or sid, dialog_title=dialog,
                       elements=els, first_seen_action=first or (f"goto {path}" if kind == "page" else None),
                       depth=depth)


def tr(frm: str, to: str, el: str, label: str, **kw) -> Transition:
    return Transition(id=stable_id("tr", frm, to, el), from_screen=frm, to_screen=to, element_id=el, label=label,
                      **kw)


# ---------------------------------------------------------------------------------------- synthetic graphs
def test_names_come_from_the_entry_link_then_dialog_titles_then_clicked_labels():
    screens = [scr("home", "/", title="Acme • Home", links={"REST": "/"}),
               scr("tab", "/", kind="tab", title="Acme • Home"),
               scr("dlg", "/", kind="modal", dialog="New Collection"),
               scr("menu", "/", kind="modal", dialog="menu")]
    t = [tr("home", "tab", "e1", "Environments"), tr("tab", "dlg", "e2", "New"),
         tr("home", "menu", "e3", "Help & feedback")]
    flows = build_flows(target(["https://app.test/"]), screens, t)
    assert sorted(f.name for f in flows) == ["REST > Environments > New Collection", "REST > Help & feedback"]
    deep = next(f for f in flows if f.name.endswith("New Collection"))
    assert deep.steps == ["e1", "e2"] and deep.screens == ["home", "tab", "dlg"] and deep.goal == "New Collection"
    assert deep.id == stable_id("flow", "e1", "e2")


def test_entry_name_falls_back_to_title_without_the_product_name_then_to_path():
    s = [scr("a", "/", title="GraphQL • Acme"), scr("b", "/x", title="Acme")]
    f = build_flows(target(["https://app.test/", "https://app.test/x"]), [*s, scr("t1", "/", kind="tab"),
                                                                            scr("t2", "/x", kind="tab")],
                    [tr("a", "t1", "e1", "One"), tr("b", "t2", "e2", "Two")])
    assert sorted(x.name for x in f) == ["/x > Two", "GraphQL > One"]


def test_only_observed_unblocked_screen_changing_edges_with_elements_count():
    s = [scr("a", "/"), scr("b", "/", kind="tab"), scr("c", "/", kind="tab"), scr("d", "/", kind="tab"),
         scr("e", "/", kind="tab")]
    t = [tr("a", "b", "e1", "Real"), tr("a", "c", "e2", "Inferred", observed=False),
         tr("a", "d", "e3", "Blocked", blocked=True), tr("a", "a", "e4", "Typed", action="type"),
         Transition(id="tr_nolabel", from_screen="a", to_screen="e", element_id=None, label="goto")]
    flows = build_flows(target(["https://app.test/"]), s, t)
    assert [f.steps for f in flows] == [["e1"]]


def test_bfs_takes_the_shortest_path_to_the_end_screen():
    s = [scr("a", "/"), scr("b", "/", kind="tab"), scr("c", "/", kind="tab"), scr("d", "/", kind="modal", dialog="D")]
    t = [tr("a", "b", "e1", "B"), tr("b", "c", "e2", "C"), tr("c", "d", "e3", "D"), tr("a", "d", "e9", "Direct")]
    flows = build_flows(target(["https://app.test/"]), s, t)
    by_goal = {f.goal: f for f in flows}
    assert by_goal["D"].steps == ["e9"]                  # one edge beats three
    assert by_goal["C"].steps == ["e1", "e2"]


def test_flows_end_where_nothing_new_is_reachable_and_cycles_terminate():
    s = [scr("a", "/"), scr("m", "/", kind="modal", dialog="New Collection")]
    t = [tr("a", "m", "e1", "New"), tr("m", "a", "e2", "Save")]      # Save returns to the entry
    flows = build_flows(target(["https://app.test/"]), s, t)
    assert [(f.name, f.steps) for f in flows] == [("a > New Collection", ["e1"])]


def test_depth_is_capped_at_four_edges():
    chain = [scr("n0", "/")] + [scr(f"n{i}", "/", kind="tab") for i in range(1, 8)]
    t = [tr(f"n{i}", f"n{i + 1}", f"e{i}", f"Step {i}") for i in range(7)]
    [flow] = build_flows(target(["https://app.test/"]), chain, t)
    assert len(flow.steps) == MAX_DEPTH == 4 and flow.screens[-1] == "n4"


def test_another_entry_is_a_boundary_not_a_waypoint():
    s = [scr("home", "/", links={"Home": "/"}), scr("set", "/settings", links={"Settings": "/settings"}),
         scr("cert", "/settings", kind="modal", dialog="Certificates")]
    t = [tr("home", "set", "e1", "Settings"), tr("set", "cert", "e2", "Certificates")]
    flows = build_flows(target(["https://app.test/", "https://app.test/settings"]), s, t)
    assert sorted((f.name, tuple(f.steps)) for f in flows) == [
        ("Home > Settings", ("e1",)), ("Settings > Certificates", ("e2",))]


def test_at_most_thirty_flows_shortest_first_and_deterministic():
    s = [scr("a", "/")] + [scr(f"t{i:02d}", "/", kind="tab") for i in range(40)]
    s += [scr("deep", "/", kind="tab")]
    t = [tr("a", f"t{i:02d}", f"e{i:02d}", f"Tab {i:02d}") for i in range(40)] + [tr("t00", "deep", "ez", "Deep")]
    flows = build_flows(target(["https://app.test/"]), s, t)
    assert len(flows) == MAX_FLOWS == 30
    assert all(len(f.steps) == 1 for f in flows) and len({f.id for f in flows}) == 30   # the deep one is cut
    assert [f.name for f in flows] == [f.name for f in build_flows(target(["https://app.test/"]),
                                                                    list(reversed(s)), list(reversed(t)))]


def test_a_seed_that_redirects_still_finds_its_screen_by_the_goto_that_reached_it():
    s = [scr("ws", "/realtime/websocket", first="goto /realtime", title="WebSocket • Acme"),
         scr("probe", "/*", first="goto /__missing__"), scr("t", "/realtime/websocket", kind="tab")]
    entries = entry_screens(target(["https://app.test/realtime"]), s)
    assert [e.id for e in entries] == ["ws"]


# ----------------------------------------------------------------------------------------- real crawl
@pytest.fixture(scope="module")
def real_flows():
    screens = [ScreenState.model_validate_json(l) for l in (CRAWL / "screens.jsonl").read_text().splitlines()]
    trans = [Transition.model_validate_json(l) for l in (CRAWL / "transitions.jsonl").read_text().splitlines()]
    return screens, trans, build_flows(cfg(), screens, trans)


def test_real_crawl_flows(real_flows):
    screens, trans, flows = real_flows
    names = {f.name for f in flows}
    assert len(flows) == 19 <= MAX_FLOWS
    assert {"REST > Environments", "REST > New Collection", "GraphQL > Schema", "Realtime > Protocols",
            "Settings > Client Certificates", "REST > Settings", "GraphQL > Login to Hoppscotch"} <= names
    by_id = {s.id: s for s in screens}
    ok = {(t.from_screen, t.to_screen, t.element_id) for t in trans if t.observed and not t.blocked}
    for f in flows:
        assert 1 <= len(f.steps) <= MAX_DEPTH and len(f.screens) == len(f.steps) + 1
        assert all(sid in by_id for sid in f.screens)
        # every step is a transition the crawl observed, element for element
        assert all((a, b, e) in ok for a, b, e in zip(f.screens[:-1], f.screens[1:], f.steps, strict=True))
        assert f.id == stable_id("flow", *f.steps)
    entries = {e.id for e in entry_screens(cfg(), screens)}
    assert {f.screens[0] for f in flows} <= entries and len(entries) == 4
    assert all(f.name.split(" > ")[0] in {"REST", "GraphQL", "Realtime", "Settings"} for f in flows)


def test_run_flows_writes_jsonl_and_the_store_in_flow_order(tmp_path: Path):
    run = make_run(tmp_path, spec=False, code=False)
    store = RecordingStore()
    flows = run_flows(run, cfg(), store)
    rows = read_jsonl_dicts(run.run_dir / "links" / "flows.jsonl")
    assert [r["id"] for r in rows] == [f.id for f in flows] == [f.id for f in store.flows]
    assert store.targets == ["hoppscotch"] and len(flows) == 19
    assert all(run.evidence.exists(e) for e in run.evidence.ids())
    assert any(run.evidence.get(e)["kind"] == "flow.path" for e in run.evidence.ids())
