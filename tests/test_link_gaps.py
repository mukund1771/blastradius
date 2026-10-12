"""Gaps (D08; cases 8, 21, 22, 27, 34, 46, 48): the absence model, effort-derived confidence, access policy,
UNMAPPED_CODE and UNSPECIFIED_UI bookkeeping, and the feature-detected bounded re-crawl."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from link_helpers import (
    CRAWL,
    LINK_FIXTURES,
    FakeClient,
    RecordingStore,
    cfg,
    judge_responder,
    make_llm,
    make_run,
    read_jsonl_dicts,
)

from blastradius.config import Settings
from blastradius.ids import stable_id
from blastradius.link.gaps import (
    NOT_FOUND_CAP,
    UNSPECIFIED_PER_SCREEN,
    UNSPECIFIED_TOTAL,
    absent_confidence,
    effort_score,
    not_found_confidence,
    run_gaps,
)
from blastradius.models import CrawlSummary, Link, ScreenState, UIElementObs
from blastradius.run import RunContext

BUNDLE = "c81a0323d1f5b4f5"


def requirement(text: str, **kw) -> dict[str, Any]:
    base = {"id": stable_id("req", "hoppscotch", text), "target": "hoppscotch", "text": text,
            "quote": text + " (q)", "source_url": "https://docs.hoppscotch.io/x", "section": "Features",
            "edition": "cloud-web", "ui_observable": "yes", "requires_login": False, "polarity": "must_exist",
            "source_kind": "docs", "tags": [], "spec_sha": "f" * 16}
    return {**base, **kw}


def crafted(tmp_path: Path, reqs: list[dict[str, Any]], *, links: list[Link] | None = None,
            judged: dict[str, str] | None = None, stale: bool = True, state: bool = True,
            run_id: str = "gaps") -> RunContext:
    """Real crawl artifacts + the given requirements, links and per-requirement judge state."""
    run = make_run(tmp_path, spec=False, code=False, run_id=run_id)
    run.path("spec", "requirements.json").write_text(json.dumps(reqs))
    run.path("spec", "stats.json").write_text(json.dumps({"stale": stale}))
    (run.path("links", "links.jsonl")).write_text("".join(l.model_dump_json() + "\n" for l in links or []))
    if state:
        run.path("links", "link_state.json").write_text(json.dumps({
            "unresolved": [], "requirements": {
                r["id"]: {"status": (judged or {}).get(r["id"], "judged"), "reason": "", "candidates": 3,
                          "links": 0} for r in reqs}}))
    return run


def gaps_of(run: RunContext) -> list[dict[str, Any]]:
    return read_jsonl_dicts(run.run_dir / "links" / "gaps.jsonl")


def one(run: RunContext, kind: str, subject: str | None = None) -> dict[str, Any]:
    rows = [g for g in gaps_of(run) if g["kind"] == kind and (subject is None or g["subject_id"] == subject)]
    assert len(rows) == 1, rows
    return rows[0]


def realized(req_id: str, dst: str = "el_x") -> Link:
    return Link(kind="REALIZED_BY", src=req_id, dst=dst, confidence=0.8, method="bm25_judge", evidence_id="ev_x")


# ------------------------------------------------------------------------------------------ formula
def test_effort_score_and_confidence_formulas():
    assert effort_score(0, "frontier_empty") == 0.0
    assert effort_score(50, "frontier_empty") == pytest.approx(1 - math.exp(-1))
    assert effort_score(50, "action_budget") == pytest.approx(0.8 * (1 - math.exp(-1)))
    assert not_found_confidence(46, "action_budget") == 0.29
    assert not_found_confidence(10_000, "frontier_empty") == NOT_FOUND_CAP == 0.60     # never above "likely"
    assert absent_confidence(0, "frontier_empty") == 0.40
    assert absent_confidence(10_000, "frontier_empty") == 0.60
    for actions in (0, 5, 46, 150, 1000):                      # a stated negative is never weaker than a miss
        assert absent_confidence(actions, "action_budget") >= not_found_confidence(actions, "action_budget")
    assert not_found_confidence(5, None) < not_found_confidence(40, None) < not_found_confidence(200, None)


# ------------------------------------------------------------------------------------------- policy
def test_requirement_gap_policy_table(tmp_path: Path):
    reqs = [
        requirement("User can use the Cookie Manager", edition="desktop"),
        requirement("User can run collections from the CLI", ui_observable="no"),
        requirement("User can create a workspace", requires_login=True),
        requirement("User can export a collection as OpenAPI"),
        requirement("User can view history", edition="all"),
        requirement("Request chaining is coming soon", polarity="must_not_exist"),
        requirement("Language choice is not available", polarity="must_not_exist"),
        requirement("User can use many languages", ui_observable="unknown"),
        requirement("User can do something in some edition", edition="unknown"),
    ]
    by_text = {r["text"]: r["id"] for r in reqs}
    links = [realized(by_text["User can view history"]), realized(by_text["Language choice is not available"])]
    run = crafted(tmp_path, reqs, links=links)
    out = run_gaps(run, cfg(), RecordingStore(), None)
    kinds = {g["subject_id"]: g for g in gaps_of(run) if g["subject_id"] in by_text.values()}
    g = kinds[by_text["User can use the Cookie Manager"]]
    assert (g["kind"], g["status"], g["reason"], g["confidence"], g["bundle_hash"]) == (
        "NOT_UI_TESTABLE", "CLASSIFIED", "edition:desktop", 0.0, None)
    g = kinds[by_text["User can run collections from the CLI"]]
    assert (g["kind"], g["reason"]) == ("NOT_UI_TESTABLE", "not_ui_observable")
    g = kinds[by_text["User can create a workspace"]]
    assert (g["kind"], g["status"], g["reason"]) == ("ACCESS_RESTRICTED", "SKIPPED_BY_POLICY", "requires_login")
    assert g["screens_searched"] == sorted(s["id"] for s in read_jsonl_dicts(CRAWL / "screens.jsonl"))
    g = kinds[by_text["User can export a collection as OpenAPI"]]
    assert (g["kind"], g["status"], g["reason"], g["bundle_hash"]) == (
        "NO_UI_EVIDENCE", "NOT_FOUND_WITHIN_BUDGET", "SPEC_MAY_BE_STALE", BUNDLE)
    assert g["confidence"] == 0.29 and g["actions_spent"] == 46 and len(g["screens_searched"]) == 25
    g = kinds[by_text["Request chaining is coming soon"]]
    assert (g["kind"], g["status"], g["confidence"]) == ("NO_UI_EVIDENCE", "CONFIRMED_ABSENT", 0.50)
    assert by_text["User can view history"] not in kinds                       # realised: no gap
    assert by_text["Language choice is not available"] not in kinds            # satisfied negative: no gap
    assert by_text["User can use many languages"] not in kinds                 # not known to be UI observable
    assert by_text["User can do something in some edition"] not in kinds
    assert out["negatives_contradicted"] == 1 and out["requirements_not_gapped_unclassified"] == 2


def test_a_goal_directed_crawl_effort_is_cited_in_the_evidence(tmp_path: Path):
    reqs = [requirement("User can export a collection as OpenAPI")]
    run = crafted(tmp_path, reqs)
    run.path("crawl", "goal_effort.json").write_text(json.dumps([{
        "goal": reqs[0]["text"], "screens_visited": 3, "actions_spent": 7, "candidate_names": ["Export"]}]))
    run_gaps(run, cfg(), None, None)
    g = one(run, "NO_UI_EVIDENCE")
    assert "3 matching screens, 7 actions on matching controls" in run.evidence.get(g["evidence_id"])["summary"]


def test_refused_captures_are_named_in_an_absence_claim(tmp_path: Path):
    """case 69: "not found" must say that some pages were never looked at (suspect, blocked or disallowed)."""
    run = crafted(tmp_path, [requirement("User can export a collection as OpenAPI")])
    run_gaps(run, cfg(), None, None)
    summary = run.evidence.get(one(run, "NO_UI_EVIDENCE")["evidence_id"])["summary"]
    assert "0 blocked, 0 disallowed and 1 suspect captures that never became screens" in summary


def test_a_spec_that_is_not_stale_gives_no_stale_reason(tmp_path: Path):
    reqs = [requirement("User can export a collection as OpenAPI")]
    run = crafted(tmp_path, reqs, stale=False)
    run_gaps(run, cfg(), None, None)
    assert one(run, "NO_UI_EVIDENCE")["reason"] == ""


def test_the_judge_not_running_is_never_an_absence_claim(tmp_path: Path):
    reqs = [requirement("User can export a collection as OpenAPI"),
            requirement("Request chaining is coming soon", polarity="must_not_exist")]
    run = crafted(tmp_path, reqs, judged={r["id"]: "unjudged" for r in reqs})
    run_gaps(run, cfg(), None, None)
    rows = [g for g in gaps_of(run) if g["kind"] == "NO_UI_EVIDENCE"]
    assert len(rows) == 2
    assert {(g["status"], g["reason"], g["confidence"]) for g in rows} == {
        ("NOT_FOUND_WITHIN_BUDGET", "JUDGE_UNAVAILABLE", 0.0)}


def test_without_link_state_nothing_is_assumed_judged(tmp_path: Path):
    run = crafted(tmp_path, [requirement("User can export a collection as OpenAPI")], state=False)
    run_gaps(run, cfg(), None, None)
    g = one(run, "NO_UI_EVIDENCE")
    assert g["reason"] == "JUDGE_UNAVAILABLE" and g["confidence"] == 0.0


def test_unrealised_requirement_naming_a_blocked_control_is_restricted_not_absent(tmp_path: Path):
    """The crawl refused to click "Continue with GitHub" and "Connect": absence there is not "not found"."""
    reqs = [requirement("User can continue with GitHub"), requirement("User can connect to an MQTT broker"),
            requirement("User can continue with Slack")]
    run = crafted(tmp_path, reqs)
    run_gaps(run, cfg(), None, None)
    by_text = {r["text"]: r["id"] for r in reqs}
    gh = one(run, "ACCESS_RESTRICTED", by_text["User can continue with GitHub"])
    assert gh["status"] == "SKIPPED_BY_POLICY"
    assert gh["reason"] == "auth+external_link"           # "Continue with GitHub" (auth) and the GitHub link
    mq = one(run, "ACCESS_RESTRICTED", by_text["User can connect to an MQTT broker"])
    assert mq["reason"] == "network_side_effect"
    assert one(run, "NO_UI_EVIDENCE", by_text["User can continue with Slack"])["status"] == "NOT_FOUND_WITHIN_BUDGET"


# ------------------------------------------------------------------------------------------ blocked
def test_blocked_controls_become_one_access_gap_per_canonical_element(tmp_path: Path):
    run = crafted(tmp_path, [])
    out = run_gaps(run, cfg(), None, None)
    rows = [g for g in gaps_of(run) if g["kind"] == "ACCESS_RESTRICTED"]
    transitions = [json.loads(l) for l in (CRAWL / "transitions.jsonl").read_text().splitlines()]
    blocked = [t for t in transitions if t["blocked"]]
    assert len(blocked) == 86 and len(rows) == 33 < len(blocked)         # 18 "Login" observations are one control
    assert {g["status"] for g in rows} == {"SKIPPED_BY_POLICY"}
    assert out["by_reason"]["ACCESS_RESTRICTED/auth"] >= 1
    assert {g["reason"].split("+")[0] for g in rows} <= {
        "auth", "external_link", "destructive", "network_side_effect", "external_widget", "access"}
    assert all(g["bundle_hash"] == BUNDLE and g["confidence"] == 0.0 for g in rows)


# ------------------------------------------------------------------------------------- unmapped code
def test_unmapped_code_rows_come_from_the_link_stage_with_its_reasons(tmp_path: Path):
    run = crafted(tmp_path, [])
    run.path("links", "link_state.json").write_text(json.dumps({"unresolved": [
        {"element_id": "el_a", "obs_ids": ["el_a"], "screen_ids": ["scr_1"], "name": "Frob", "role": "button",
         "reason": "llm_none", "detail": ""},
        {"element_id": "el_b", "obs_ids": ["el_b"], "screen_ids": [], "name": "", "role": "button",
         "reason": "no_text", "detail": "no name"},
        {"element_id": "el_a", "obs_ids": ["el_a"], "screen_ids": [], "name": "Frob", "role": "button",
         "reason": "no_text", "detail": ""}], "requirements": {}}))
    out = run_gaps(run, cfg(), None, None)
    rows = {g["subject_id"]: g for g in gaps_of(run) if g["kind"] == "UNMAPPED_CODE"}
    assert set(rows) == {"el_a", "el_b"}
    assert (rows["el_a"]["reason"], rows["el_a"]["status"], rows["el_a"]["screens_searched"]) == (
        "llm_none", "OBSERVED", ["scr_1"])
    assert rows["el_b"]["reason"] == "no_text" and out["unmapped_merged_shared_obs_id"] == 1


def test_a_link_stage_without_a_code_index_leaves_nothing_unmapped(tmp_path: Path):
    run = crafted(tmp_path, [])
    run.path("links", "link_state.json").write_text(json.dumps({"unresolved": None, "requirements": {}}))
    out = run_gaps(run, cfg(), None, None)
    assert not [g for g in gaps_of(run) if g["kind"] == "UNMAPPED_CODE"] and out["by_kind"].get(
        "UNMAPPED_CODE", 0) == 0


def test_unmapped_code_without_link_state_is_derived_from_links_and_screens(tmp_path: Path):
    screens = [ScreenState.model_validate_json(l) for l in (CRAWL / "screens.jsonl").read_text().splitlines()]
    named = next(e for s in screens for e in s.elements if e.name == "Search and commands")
    linked = Link(kind="IMPLEMENTED_BY", src=named.id, dst="packages/x.vue", confidence=0.9, method="i18n_key",
                  evidence_id="ev_1")
    run = crafted(tmp_path, [], links=[linked], state=False)
    run_gaps(run, cfg(), None, None)
    rows = [g for g in gaps_of(run) if g["kind"] == "UNMAPPED_CODE"]
    assert named.id not in {g["subject_id"] for g in rows}
    assert {g["reason"] for g in rows} == {"no_candidate", "no_text"}


# --------------------------------------------------------------------------------------- unspecified
def synthetic_run(tmp_path: Path, screens: list[ScreenState]) -> RunContext:
    run = RunContext.create("hoppscotch", run_id="synth", runs_dir=tmp_path / "runs",
                            settings=Settings(max_usd=100.0))
    run.path("crawl", "screens.jsonl").write_text("".join(s.model_dump_json() + "\n" for s in screens))
    run.path("crawl", "summary.json").write_text(CrawlSummary(
        target="hoppscotch", started_at="a", finished_at="b", screens=len(screens), elements=0,
        transitions=0, actions_spent=10, actions_budget=50, wall_clock_s=1.0, bundle_hash="b1").model_dump_json())
    run.path("spec", "requirements.json").write_text("[]")
    return run


def page(sid: str, pattern: str, names: list[str], role: str = "button", kind: str = "page") -> ScreenState:
    els = [UIElementObs(id=f"el_{sid}_{i}", role=role, name=n, text=n, ordinal=0) for i, n in enumerate(names)]
    return ScreenState(id=sid, target="hoppscotch", url="https://hoppscotch.io" + pattern, url_pattern=pattern,
                       kind=kind, fingerprint=sid, capture_id="c_" + sid, elements=els, title=sid.upper())


def test_unspecified_ui_is_capped_per_screen_and_grouped(tmp_path: Path):
    run = synthetic_run(tmp_path, [page("s1", "/a", [f"Control {i:02d}" for i in range(20)])])
    out = run_gaps(run, cfg(), None, None)
    rows = [g for g in gaps_of(run) if g["kind"] == "UNSPECIFIED_UI"]
    listed = [g for g in rows if g["reason"] == "no_requirement"]
    grouped = [g for g in rows if g["reason"].startswith("grouped:")]
    assert len(listed) == UNSPECIFIED_PER_SCREEN == 8
    assert [g["reason"] for g in grouped] == ["grouped:12"] and grouped[0]["subject_id"] == "s1"
    assert all(g["status"] == "OBSERVED" for g in rows)
    assert out["unspecified_ui"] == {"listed": 8, "grouped": 12, "screens": 1}


def test_unspecified_ui_total_cap_and_global_and_realised_elements_excluded(tmp_path: Path):
    shared = [f"Shared {i}" for i in range(3)]           # on three url patterns: layout, not page content
    screens = [page(f"s{i}", f"/p{i}", shared + [f"Own {i} {j}" for j in range(12)]) for i in range(8)]
    run = synthetic_run(tmp_path, screens)
    # realise one element: it must not be reported as unspecified
    run.path("links", "links.jsonl").write_text(Link(
        kind="REALIZED_BY", src="req_1", dst="el_s0_5", confidence=0.8, method="bm25_judge",
        evidence_id="ev_1").model_dump_json() + "\n")
    out = run_gaps(run, cfg(), None, None)
    rows = [g for g in gaps_of(run) if g["kind"] == "UNSPECIFIED_UI"]
    listed = [g for g in rows if g["reason"] == "no_requirement"]
    grouped_total = sum(int(g["reason"].split(":")[1]) for g in rows if g["reason"].startswith("grouped:"))
    assert len(listed) == UNSPECIFIED_TOTAL == 60
    assert len(listed) + grouped_total == 8 * 12 - 1      # every non-shared, non-realised control is accounted for
    assert "el_s0_5" not in {g["subject_id"] for g in rows}
    assert not any(g["subject_id"].startswith("el_s") and "Shared" in g["evidence_id"] for g in rows)
    assert out["unspecified_ui"]["listed"] == 60


def test_gap_ids_are_content_hashes_and_stable(tmp_path: Path):
    run1 = crafted(tmp_path / "a", [requirement("User can export a collection")], run_id="a")
    run2 = crafted(tmp_path / "b", [requirement("User can export a collection")], run_id="b")
    run_gaps(run1, cfg(), None, None)
    run_gaps(run2, cfg(), None, None)
    assert [g["id"] for g in gaps_of(run1)] == [g["id"] for g in gaps_of(run2)]
    g = one(run1, "NO_UI_EVIDENCE")
    assert g["id"] == stable_id("gap", "hoppscotch", "NO_UI_EVIDENCE", g["subject_id"])
    assert run1.evidence.get(g["evidence_id"])["kind"] == "gap.no_ui_evidence"


def test_store_receives_exactly_the_written_gaps(tmp_path: Path):
    run = crafted(tmp_path, [requirement("User can export a collection")])
    store = RecordingStore()
    out = run_gaps(run, cfg(), store, None)
    assert [g.id for g in store.gaps] == [g["id"] for g in gaps_of(run)] and store.targets == ["hoppscotch"]
    assert out["gaps"] == len(store.gaps) and out["graph"] == {"gaps": len(store.gaps)}


# --------------------------------------------------------------------------------------------- re-crawl
class FakeCrawl:
    """Stands in for explorer.run_crawl and writes the goal crawl's screens where link.gaps looks for them."""

    def __init__(self, run: RunContext, with_support: set[str] = frozenset(), spent: int = 17):
        self.run, self.with_support, self.spent, self.calls = run, set(with_support), spent, []

    def __call__(self, run, cfg_, llm, *, goals=None, source="live", recorded_dir=None, max_actions=None,
                 artifact_subdir=None):
        self.calls.append({"goals": goals, "max_actions": max_actions, "artifact_subdir": artifact_subdir})
        goal = goals[0]
        names = ["Export OpenAPI collection specification"] if goal in self.with_support else ["Unrelated thing"]
        s = page(f"scr_goal_{len(self.calls)}", f"/goal{len(self.calls)}", names, kind="tab")
        assert "/" not in artifact_subdir and artifact_subdir not in (".", "..")   # one plain component
        path = run.run_dir / "crawl" / artifact_subdir / "screens.jsonl"          # where the real crawl writes
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(s.model_dump_json() + "\n")
        return CrawlSummary(target="hoppscotch", started_at="a", finished_at="b", screens=1, elements=1,
                            transitions=0, actions_spent=self.spent, actions_budget=max_actions or 0,
                            wall_clock_s=1.0)


def no_subdir_crawl(run, cfg_, llm, *, goals=None, source="live", recorded_dir=None, max_actions=None):
    raise AssertionError("must not be called: a goal crawl without artifact_subdir would overwrite crawl/")


def test_recrawl_is_skipped_when_run_crawl_has_no_artifact_subdir(tmp_path: Path, monkeypatch):
    from blastradius.crawl import explorer

    monkeypatch.setattr(explorer, "run_crawl", no_subdir_crawl)
    run = crafted(tmp_path, [requirement("User can export a collection as OpenAPI")])
    llm = make_llm(run, FakeClient(judge_responder()), tmp_path)
    before = (run.run_dir / "crawl" / "screens.jsonl").read_bytes()
    out = run_gaps(run, cfg(), None, llm, recrawl=True)
    assert out["recrawl"]["supported"] is False and "artifact_subdir" in out["recrawl"]["skipped"]
    assert (run.run_dir / "crawl" / "screens.jsonl").read_bytes() == before
    assert one(run, "NO_UI_EVIDENCE")["actions_spent"] == 46


def test_recrawl_closes_a_gap_it_finds_and_raises_effort_on_one_it_does_not(tmp_path: Path, monkeypatch):
    from blastradius.crawl import explorer

    reqs = [requirement("User can export a collection as an OpenAPI specification"),
            requirement("User can schedule requests on a timer")]
    run = crafted(tmp_path, reqs)
    fake = FakeCrawl(run, with_support={reqs[0]["text"]})
    monkeypatch.setattr(explorer, "run_crawl", fake)
    llm = make_llm(run, FakeClient(judge_responder()), tmp_path)
    store = RecordingStore()
    run_gaps(run, cfg(), None, None)                                    # the gaps before any re-crawl
    before = {g["subject_id"]: g for g in gaps_of(run) if g["kind"] == "NO_UI_EVIDENCE"}
    out = run_gaps(run, cfg(), store, llm, recrawl=True)

    assert out["recrawl"]["supported"] is True and out["recrawl"]["attempted"] == 2
    assert {c["max_actions"] for c in fake.calls} == {20}
    assert sorted(c["artifact_subdir"] for c in fake.calls) == ["goal-0", "goal-1"]
    assert sorted(c["goals"][0] for c in fake.calls) == sorted(r["text"] for r in reqs)

    after = {g["subject_id"]: g for g in gaps_of(run) if g["kind"] == "NO_UI_EVIDENCE"}
    closed, open_ = reqs[0]["id"], reqs[1]["id"]
    assert closed not in after and open_ in after                       # found -> the gap is gone
    links = [l for l in read_jsonl_dicts(run.run_dir / "links" / "links.jsonl") if l["src"] == closed]
    assert links and links[0]["kind"] == "REALIZED_BY" and store.links and store.links[0].src == closed
    g = after[open_]
    assert g["actions_spent"] == before[open_]["actions_spent"] + 17
    assert g["confidence"] > before[open_]["confidence"] and g["confidence"] <= 0.60
    assert any(s.startswith("scr_goal_") for s in g["screens_searched"])
    assert len(g["screens_searched"]) == len(before[open_]["screens_searched"]) + 1
    assert out["recrawl"]["closed"][0]["requirement"] == closed
    assert out["recrawl"]["still_open"] == [{"requirement": open_, "closed": False, "new_screens": 1,
                                             "actions": 17}]


def test_recrawl_is_bounded_to_five_gaps_in_the_ingest_order(tmp_path: Path, monkeypatch):
    from blastradius.crawl import explorer

    reqs = [requirement(f"User can export collection format {i}") for i in range(7)]
    run = crafted(tmp_path, reqs)
    fake = FakeCrawl(run)
    monkeypatch.setattr(explorer, "run_crawl", fake)
    llm = make_llm(run, FakeClient(judge_responder()), tmp_path)
    out = run_gaps(run, cfg(), None, llm, recrawl=True)
    assert len(fake.calls) == 5 == out["recrawl"]["attempted"]
    assert [c["goals"][0] for c in fake.calls] == [r["text"] for r in reqs[:5]]    # requirements.json order
    assert [c["artifact_subdir"] for c in fake.calls] == [f"goal-{i}" for i in range(5)]
    rows = [g for g in gaps_of(run) if g["kind"] == "NO_UI_EVIDENCE"]
    assert len(rows) == 7 and sum(1 for g in rows if g["actions_spent"] == 46 + 17) == 5


def test_unjudged_gaps_are_not_recrawled(tmp_path: Path, monkeypatch):
    from blastradius.crawl import explorer

    reqs = [requirement("User can export a collection")]
    run = crafted(tmp_path, reqs, judged={reqs[0]["id"]: "unjudged"})
    fake = FakeCrawl(run)
    monkeypatch.setattr(explorer, "run_crawl", fake)
    llm = make_llm(run, FakeClient(judge_responder()), tmp_path)
    out = run_gaps(run, cfg(), None, llm, recrawl=True)
    assert fake.calls == [] and out["recrawl"]["attempted"] == 0


def test_real_requirement_fixture_gap_summary(tmp_path: Path):
    """The 19 hand-written requirements over the real crawl, with the heuristic judge's link results."""
    from blastradius.link.link import run_link

    run = make_run(tmp_path)
    llm = make_llm(run, FakeClient(judge_responder()), tmp_path)
    run_link(run, cfg(), None, llm)
    out = run_gaps(run, cfg(), None, None)
    reqs = {r["text"]: r for r in json.loads((LINK_FIXTURES / "requirements.json").read_text())}
    rows = {g["subject_id"]: g for g in gaps_of(run) if g["kind"] in ("NO_UI_EVIDENCE", "NOT_UI_TESTABLE")
            or (g["kind"] == "ACCESS_RESTRICTED" and g["subject_id"] in {r["id"] for r in reqs.values()})}
    sched = rows[reqs["User can schedule a request to run on a timer."]["id"]]
    assert (sched["kind"], sched["status"], sched["reason"]) == (
        "NO_UI_EVIDENCE", "NOT_FOUND_WITHIN_BUDGET", "SPEC_MAY_BE_STALE")
    assert 0.0 < sched["confidence"] <= 0.60
    assert rows[reqs["User can invite users to a workspace."]["id"]]["kind"] == "ACCESS_RESTRICTED"
    assert rows[reqs["User can run a collection from the command line."]["id"]]["kind"] == "NOT_UI_TESTABLE"
    assert out["by_kind"]["NOT_UI_TESTABLE"] == 3
    assert all(0.0 <= g["confidence"] <= 0.6 for g in rows.values())


def test_the_subdirs_gaps_passes_are_accepted_by_the_real_crawl_module(tmp_path: Path):
    """No crawl is started: `scoped_run` is the validation run_crawl applies to artifact_subdir."""
    import inspect

    from blastradius.crawl import explorer

    assert "artifact_subdir" in inspect.signature(explorer.run_crawl).parameters
    run = make_run(tmp_path, spec=False, code=False)
    for i in range(5):
        scoped = explorer.scoped_run(run, f"goal-{i}")
        assert scoped.path("crawl", "screens.jsonl") == run.run_dir / "crawl" / f"goal-{i}" / "screens.jsonl"
