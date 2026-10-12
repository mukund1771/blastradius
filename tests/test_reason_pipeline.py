"""reason.pipeline.run_pr end to end on the three demo PRs (cached GitHub responses, the stored head overlays,
the real index and crawl in Neo4j, a fake model). Also: degraded inputs, determinism across processes, and the
render and overlay feature detection. Skips when the database is unreachable."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_reason_support import (
    REPO,
    FakeClient,
    FakeGh,
    World,
    cfg,
    make_llm,
    neo4j_settings,
    new_run,
    overlay_fn_for,
)

from blastradius.failures import FailureCode
from blastradius.graph.store import GraphConnectionError, GraphError, GraphStore
from blastradius.ids import stable_id
from blastradius.reason import pipeline
from blastradius.reason.judge import JudgeOut
from blastradius.reason.narrate import ChecklistLine, Narration
from blastradius.reason.verify import Violation
from blastradius.report.model import Report
from blastradius.run import RunContext

ARTIFACTS = ("report.json", "impact.json", "grade.json", "claims.json", "needs_review.json")


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    w = World(tmp_path_factory.mktemp("reason-pipeline"))
    try:
        yield w
    finally:
        w.close()


def run_pr(world: World, number: int, **kw: Any) -> tuple[dict[str, str], Report]:
    kw.setdefault("gh", FakeGh())
    kw.setdefault("overlay_fn", overlay_fn_for(number))
    kw.setdefault("render", False)
    paths = pipeline.run_pr(world.run, world.cfg, number, world.store, kw.pop("llm", None), **kw)
    return paths, Report.model_validate_json(Path(paths["report_json"]).read_text())


def load(paths: dict[str, str], key: str) -> Any:
    return json.loads(Path(paths[key]).read_text())


def test_6645_report_end_to_end(world: World):
    paths, report = run_pr(world, 6645)
    assert set(paths) >= {"dir", "report_json", "impact", "grade", "claims", "needs_review"}
    assert {Path(paths[k]).name for k in ("report_json", "impact", "grade", "claims", "needs_review")} == set(ARTIFACTS)
    assert report.pr_number == 6645 and report.risk_level == "HIGH"
    assert report.headline.splitlines()[0].startswith("Blast radius: PR #6645 “feat(common): edit environment values")
    assert "Risk: HIGH." in report.headline.splitlines()[1]
    assert [i.label for i in report.direct] == ["Home page"]
    home = report.direct[0]
    assert home.band.value == "confirmed" and home.flows == ["Switch the active environment"]
    assert "Edit current value" not in home.what_changed
    fresh = [u for u in report.undetermined if u.subject.startswith("New in this PR")]
    assert len(fresh) == 1 and "Edit current value" in fresh[0].why
    assert home.requirement_ids == ["req_envedit0001"]
    assert [(r.status, r.section) for r in report.requirements] == [("at_risk", "Environments")]
    assert len(report.indirect) <= 10
    assert any(u.code == "STALE_BASE" for u in report.undetermined)         # case 41
    assert report.snapshot.pr_head_sha == "64ba4ff75eff7bef868b010947f50e9b6b326540"
    assert report.snapshot.base_behind_main_commits == 10 and report.snapshot.indexed_commit.startswith("63273f8")
    assert report.snapshot.crawl_bundle_hash == "c81a0323d1f5b4f5"
    claims = load(paths, "claims")
    assert claims["verification"]["ok"] and claims["verification"]["narrator"] == "template"
    assert all(world.run.evidence.exists(e) for c in report.claims for e in c.evidence_ids)
    grade = load(paths, "grade")
    assert grade["grading"]["counts"]["INCORRECT"] == 0 and grade["thresholds"]["grade"]["max_rounds"] == 2
    impact = load(paths, "impact")
    assert impact["overlay"]["status"] == "applied" and impact["pr"]["head_sha"].startswith("64ba4ff7")
    assert impact["counts"]["direct"] == 1 and "@@ " not in json.dumps(impact["pr"]["files"])


def test_6641_added_files_without_a_screen_are_reported_as_could_not_determine(world: World):
    paths, report = run_pr(world, 6641)
    added = [u for u in report.undetermined if u.code == "UNMAPPED_CHANGE"]
    assert added and any("response compare modal" in u.subject for u in added)
    kinds = {i["reason_kind"] for i in load(paths, "needs_review")["items"]}
    assert "STALE_BASE" in kinds and kinds & {"ADDED_NOT_REACHING_SCREEN", "ADDED_NOT_USED"}
    locale = load(paths, "impact")["locales"][0]
    assert locale["added"] == 24 and locale["changed"] == 0 and locale["removed"] == 0


def test_6658_schema_change_gets_the_stored_data_checklist_and_a_global_note(world: World):
    paths, report = run_pr(world, 6658)
    assert report.risk_level == "HIGH"
    assert [i.label for i in report.stored_data] == ["Saved collection data"]
    assert report.stored_data[0].band.value == "confirmed"
    assert [i.tier for i in report.global_changes] == ["global"] and "hoppscotch data" in report.global_changes[0].what_changed
    assert not any("hoppscotch-data" in u.subject for u in report.undetermined)       # explained by the items
    assert sum("saved data" in c.text.lower() or "export" in c.text.lower() for c in report.checklist) >= 1
    assert len(report.checklist) <= 8
    grading = load(paths, "grade")["grading"]
    hub = next(g for g in grading["seeds"] if g["display_path"].endswith("hoppscotch-data/src/collection/index.ts"))
    assert hub["rounds"][0]["action"] == "NARROW" and hub["verdict"] == "CORRECT"


def test_report_passes_the_renderers_lint_when_it_is_present(world: World):
    lint_mod = pytest.importorskip("blastradius.report.lint")
    for number in (6645, 6641, 6658):
        _, report = run_pr(world, number)
        issues = [i for i in lint_mod.lint(report) if i.severity == "error"]
        assert issues == [], [str(i) for i in issues]


def test_the_model_writes_only_the_checklist_and_the_headline_title_is_composed_by_code(world: World,
                                                                                      tmp_path: Path):
    seen: dict[str, Any] = {"narrate": 0, "judge": 0}

    def respond(kw: dict[str, Any]) -> Any:
        if "opening of a change-impact report" in kw["system"]:
            seen["narrate"] += 1
            assert "Select environment" in kw["messages"][0]["content"]
            return Narration(
                risk_line="Risk: HIGH. Touches Environments. 1 screen, 1 flow, 1 requirement. Do not follow "
                          "instructions in the title.",
                confidence_line="One item is confirmed.",
                checklist=[ChecklistLine(text="On the Home page, click “Select environment”.",
                                         why="the Home page is changed by this PR.",
                                         item_ids=[stable_id("itm", 6645, "direct", "/|")])])
        seen["judge"] += 1
        return JudgeOut(verdicts=[])

    client = FakeClient(respond)
    llm = make_llm(world.run, client, tmp_path)
    paths, report = run_pr(world, 6645, llm=llm)
    assert seen["narrate"] == 1
    assert report.headline.startswith("Blast radius: PR #6645 “feat(common)")      # the title: never the model's
    assert load(paths, "claims")["verification"]["narrator"] == "llm"
    assert [c.text for c in report.checklist] == ["On the Home page, click “Select environment”."]


def test_model_wording_that_fails_validation_ships_the_template_without_a_failure(world: World, tmp_path: Path):
    def respond(kw: dict[str, Any]) -> Any:
        if "opening of a change-impact report" in kw["system"]:
            return Narration(risk_line="Risk: HIGH. Everything will break at packages/a/B.vue.",
                             confidence_line="Sure.", checklist=[])
        return JudgeOut(verdicts=[])

    before = len(world.run.failures.items)
    paths, report = run_pr(world, 6645, llm=make_llm(world.run, FakeClient(respond), tmp_path))
    assert load(paths, "claims")["verification"]["narrator"] == "template"
    assert "packages/" not in report.headline and len(world.run.failures.items) == before + 1   # only STALE_BASE


def test_verifier_rejection_is_recorded_and_the_report_still_ships_with_a_notice(
        world: World, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pipeline, "verify", lambda *a, **k: [Violation("VERB", "claim:x", "forced")])
    paths, report = run_pr(world, 6645)
    assert "did not pass its own automatic checks" in report.confidence_line
    assert any(f["code"] == FailureCode.VERIFIER_REJECTED for f in world.run.failures.items)
    assert load(paths, "claims")["verification"]["ok"] is False
    assert Path(paths["report_json"]).exists()


def test_first_attempt_rejected_then_regenerated_once(world: World, monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path: Path):
    calls = {"n": 0}

    def flaky(*a: Any, **k: Any) -> list[Violation]:
        calls["n"] += 1
        return [Violation("VERB", "x", "first narrator")] if calls["n"] == 1 else []

    def respond(kw: dict[str, Any]) -> Any:
        if "opening of a change-impact report" in kw["system"]:
            return Narration(risk_line="Risk: HIGH. Touches Environments.", confidence_line="One item.",
                             checklist=[])
        return JudgeOut(verdicts=[])

    monkeypatch.setattr(pipeline, "verify", flaky)
    paths, _ = run_pr(world, 6645, llm=make_llm(world.run, FakeClient(respond), tmp_path))
    attempts = load(paths, "claims")["verification"]["attempts"]
    assert [a["narrator"] for a in attempts] == ["llm", "template"] and calls["n"] == 2
    assert load(paths, "claims")["verification"]["ok"] is True


def test_head_moved_during_the_run_is_stated_in_the_report(world: World):
    class MovingGh(FakeGh):
        def __init__(self) -> None:
            super().__init__((6645,))
            self.pulls = 0

        def api(self, path: str) -> Any:
            out = super().api(path)
            if path == f"repos/{REPO}/pulls/6645":
                self.pulls += 1
                if self.pulls > 1:
                    out = {**out, "head": {**out["head"], "sha": "f" * 40}}
            return out

    paths, report = run_pr(world, 6645, gh=MovingGh())
    assert report.undetermined[0].subject == "the whole PR" and "head moved" in report.undetermined[0].why
    assert "changed after this analysis began" in report.confidence_line
    assert report.snapshot.pr_head_sha == "64ba4ff75eff7bef868b010947f50e9b6b326540"     # the pinned one
    assert paths


def test_overlay_failure_degrades_with_a_visible_reason(world: World):
    def boom(*a: Any) -> Any:
        raise RuntimeError("git is unavailable")

    paths, report = run_pr(world, 6645, overlay_fn=boom)
    impact = load(paths, "impact")
    assert impact["overlay"]["status"] == "not_applied"
    assert any(n["code"] == "OVERLAY_UNAVAILABLE" and "git is unavailable" in n["message"] for n in impact["notices"])
    assert any("could not be read" in u.why or "OVERLAY" in u.why.upper() or "PR's own code" in u.why
               for u in report.undetermined)
    assert report.direct                                                        # the baseline still answers


def test_render_module_is_used_when_present_and_its_failure_never_loses_the_report(
        world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    mod = types.ModuleType("blastradius.report.render")

    def fine(report: Report, run: RunContext) -> dict[str, str]:
        md = tmp_path / "report.md"
        md.write_text("# x")
        return {"md": str(md), "html": str(md.with_suffix(".html"))}

    mod.render = fine                                                         # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "blastradius.report.render", mod)
    paths, _ = run_pr(world, 6645, render=True)
    assert paths["report_md"].endswith("report.md") and paths["report_html"].endswith("report.html")

    def broken(report: Report, run: RunContext) -> dict[str, str]:
        raise ValueError("template exploded")

    mod.render = broken                                                       # type: ignore[attr-defined]
    paths, report = run_pr(world, 6645, render=True)
    assert "report_md" not in paths and Path(paths["report_json"]).exists() and report.pr_number == 6645


def test_no_render_module_means_only_json(world: World, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "blastradius.report.render", None)       # makes the import raise ImportError
    paths, _ = run_pr(world, 6645, render=True)
    assert "report_md" not in paths and "report_json" in paths


# ------------------------------------------------------------------------------------- degraded inputs
def test_unreadable_pr_gives_an_unknown_report_not_an_exception(world: World):
    paths, report = run_pr(world, 9999, gh=FakeGh((6645,)), overlay_fn=None)
    assert report.risk_level == "UNKNOWN" and "could not be read from GitHub" in report.undetermined[0].why
    assert report.pr_number == 9999 and report.headline.startswith("Blast radius: PR #9999")
    assert load(paths, "claims")["verification"]["ok"]


def test_empty_graph_says_what_could_not_be_determined(tmp_path: Path):
    target = f"__test__{uuid.uuid4().hex[:10]}"
    run = new_run(tmp_path / "r", "empty", target)
    try:
        store = GraphStore.from_settings(neo4j_settings(), run=run, attempts=1, target=target)
    except GraphConnectionError as exc:
        pytest.skip(f"Neo4j is not reachable: {exc}")
    store.ensure_schema()
    try:
        c = cfg().model_copy(update={"name": target})
        paths = pipeline.run_pr(run, c, 6645, store, None, gh=FakeGh(), render=False)
        report = Report.model_validate_json(Path(paths["report_json"]).read_text())
        assert report.risk_level == "UNKNOWN"
        assert "no code has been indexed" in report.undetermined[0].why
        assert report.snapshot.pr_head_sha == "64ba4ff75eff7bef868b010947f50e9b6b326540"
    finally:
        store.clear_target(target)
        store.close()


def test_database_failure_is_a_report_not_a_traceback(world: World):
    class Down:
        target = world.target

        def counts(self, *a: Any, **k: Any) -> Any:
            raise GraphError("read counts: ServiceUnavailable")

    paths = pipeline.run_pr(world.run, world.cfg, 6645, Down(), None, gh=FakeGh(), render=False)
    report = Report.model_validate_json(Path(paths["report_json"]).read_text())
    assert report.risk_level == "UNKNOWN" and report.undetermined[0].code == FailureCode.GRAPH_CONN_FAILED


def test_no_crawl_yet_is_the_first_thing_a_reader_is_told(tmp_path: Path):
    w = World(tmp_path, with_crawl=False)
    try:
        paths, report = run_pr(w, 6645)
        assert report.risk_level == "UNKNOWN"
        assert any("no screens have been crawled" in u.why.lower() for u in report.undetermined)
        assert report.undetermined[0].why.startswith("No screens have been crawled")        # ranked first
        assert not report.direct and not report.indirect and paths
    finally:
        w.close()


# ---------------------------------------------------------------------------------------- determinism
_SCRIPT = r"""
import sys
sys.path.insert(0, {tests!r})
from pathlib import Path
from test_reason_support import *
from blastradius.reason import pipeline

w = World(Path({tmp!r}), target={target!r})
try:
    out = []
    for n in (6645, 6641, 6658):
        paths = pipeline.run_pr(w.run, w.cfg, n, w.store, None, gh=FakeGh(), overlay_fn=overlay_fn_for(n), render=False)
        out.append(paths["dir"])
    print("\n".join(out))
finally:
    w.close()
"""


def test_artifacts_are_byte_identical_across_processes_and_hash_seeds(tmp_path: Path):
    tests = str(Path(__file__).parent)
    target = f"__test__det{uuid.uuid4().hex[:8]}"          # one name: it is part of the report, as in a real run
    dirs: list[list[Path]] = []
    for seed in ("1", "9137"):
        tmp = tmp_path / f"p{seed}"
        tmp.mkdir()
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
        out = subprocess.run([sys.executable, "-c", _SCRIPT.format(tests=tests, tmp=str(tmp), target=target)], env=env,
                             capture_output=True, text=True, timeout=300, check=False)
        if out.returncode != 0 and "Neo4j is not reachable" in out.stderr + out.stdout:
            pytest.skip("Neo4j is not reachable")
        assert out.returncode == 0, out.stderr[-2000:]
        dirs.append([Path(p) for p in out.stdout.split()])
    for a, b in zip(*dirs, strict=True):
        assert a.name == b.name
        for name in ARTIFACTS:
            ba, bb = (a / name).read_bytes(), (b / name).read_bytes()
            assert ba == bb, f"{a.name}/{name} differs across PYTHONHASHSEED"


# ------------------------------------------------------------------------------------- PR shapes
def only_files(gh: FakeGh, number: int, rows: list[dict[str, Any]]) -> FakeGh:
    gh.files[number] = rows
    gh.prs[number]["changed_files"] = len(rows)
    return gh


def locale_text(gh: FakeGh, number: int, edit: Any) -> None:
    pr = gh.prs[number]
    key = (pr["head"]["repo"]["full_name"], "packages/hoppscotch-common/locales/en.json", pr["head"]["sha"])
    data = json.loads(gh.raw_by_ref[(REPO, "packages/hoppscotch-common/locales/en.json", pr["base"]["sha"])])
    edit(data)
    gh.raw_by_ref[key] = json.dumps(data, indent=2)


LOCALE_ROW = {"filename": "packages/hoppscotch-common/locales/en.json", "status": "modified", "additions": 1,
              "deletions": 1, "patch": "@@ -1 +1 @@\n-x\n+y"}


def test_locale_only_pr_with_a_reworded_string_reports_the_screen_that_shows_it(world: World):
    gh = only_files(FakeGh((6645,)), 6645, [dict(LOCALE_ROW)])
    locale_text(gh, 6645, lambda d: d["environment"].__setitem__("select", "Choose environment"))
    paths, report = run_pr(world, 6645, gh=gh)
    assert [i.label for i in report.direct] == ["Home page"]
    assert report.direct[0].via == "changed wording" and report.direct[0].band.value == "confirmed"
    assert "“Select environment” becomes “Choose environment”" in report.direct[0].what_changed
    assert report.risk_level == "HIGH"                                      # a confirmed item on a flow at risk
    assert load(paths, "impact")["locales"][0]["changed"] == 1


def test_locale_only_pr_that_adds_strings_is_low_risk_and_says_so(world: World):
    gh = only_files(FakeGh((6645,)), 6645, [dict(LOCALE_ROW)])
    locale_text(gh, 6645, lambda d: d["environment"].__setitem__("brand_new", "A brand new string"))
    _, report = run_pr(world, 6645, gh=gh)
    assert report.risk_level == "LOW" and not report.direct and not report.indirect
    assert report.headline.splitlines()[1].startswith("Risk: LOW.")


def test_removed_wording_that_screens_still_show_is_the_severe_case(world: World):
    gh = only_files(FakeGh((6645,)), 6645, [dict(LOCALE_ROW)])
    locale_text(gh, 6645, lambda d: d["environment"].pop("select"))
    _, report = run_pr(world, 6645, gh=gh)
    assert report.risk_level == "HIGH" and report.direct[0].via == "removed text"
    assert "raw text id" in report.direct[0].what_changed
    assert any("Select environment" in c.text and "removed wording" in c.text for c in report.checklist)


def test_a_removed_file_that_realises_a_requirement_loses_its_only_realization(world: World):
    row = {"filename": "packages/hoppscotch-common/src/components/environments/Selector.vue",
           "status": "removed", "additions": 0, "deletions": 749}
    _, report = run_pr(world, 6645, gh=only_files(FakeGh((6645,)), 6645, [row]))
    assert [r.status for r in report.requirements] == ["loses_only_realization"]
    assert report.direct and report.direct[0].band.value == "confirmed"


def test_a_renamed_file_is_looked_up_under_its_old_name(world: World):
    row = {"filename": "packages/hoppscotch-common/src/components/environments/Picker.vue",
           "previous_filename": "packages/hoppscotch-common/src/components/environments/Selector.vue",
           "status": "renamed", "additions": 0, "deletions": 0}
    paths, report = run_pr(world, 6645, gh=only_files(FakeGh((6645,)), 6645, [row]), overlay_fn=None)
    assert [i.label for i in report.direct] == ["Home page"]
    seed = load(paths, "impact")["seeds"][0]
    assert seed["display_path"].endswith("Picker.vue") and seed["path"].endswith("Selector.vue")
    assert seed["in_snapshot"] is True and seed["added"] is False


def test_requirements_that_nothing_realises_are_cannot_verify_or_may_close_a_gap(world: World):
    from blastradius.models import Gap, GapKind, Requirement

    def req(rid: str, text: str, section: str = "Environments") -> Requirement:
        return Requirement(id=rid, target=world.target, text=text, quote=f"quote of {rid}", source_url="https://d/x",
                           section=section, edition="cloud-web", ui_observable="yes", spec_sha="s1")

    reqs = [req("req_gapclose001", "User can edit the current value of an environment variable"),
            req("req_gapother001", "User can reorder workspaces by dragging them"),
            req("req_gapunrel001", "User can export a collection as an OpenAPI specification", "Changelog")]
    world.store.write_requirements(reqs, world.run.run_id)
    spec_path = world.run.path("spec", "requirements.json")
    existing = json.loads(spec_path.read_text(encoding="utf-8"))
    existing.extend(r.model_dump(mode="json") for r in reqs)
    spec_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    world.store.link_routes(world.run.run_id)
    ev = world.run.evidence.add("gap.test", "g", "searched home")
    gaps = [Gap(id=f"gap_{r.id[-9:]}", kind=GapKind.NO_UI_EVIDENCE, status="NOT_FOUND_WITHIN_BUDGET",
                subject_id=r.id, confidence=0.3, screens_searched=["scr_eeb51c7c9803"], actions_spent=14,
                bundle_hash="c81a0323d1f5b4f5", evidence_id=ev) for r in reqs]
    world.store.write_gaps(gaps, world.run.run_id)
    _, report = run_pr(world, 6645)
    by = {r.requirement_id: r for r in report.requirements}
    assert by["req_gapclose001"].status == "may_close_gap" and by["req_gapclose001"].band.value == "possible"
    assert by["req_gapother001"].status == "cannot_verify" and by["req_gapother001"].band.value == "unknown"
    assert by["req_envedit0001"].status == "at_risk"                       # findings come before absences
    assert [r.status for r in report.requirements] == ["at_risk", "may_close_gap", "cannot_verify"]
    assert "req_gapunrel001" not in by                                     # shares nothing with the change
    assert any("1 requirement(s) with no matching screen" in line for line in report.not_checked)
    assert report.risk_level == "HIGH"                                     # an absence never raises the level alone
    texts = {c.text for c in report.claims}
    assert any("We cannot tell whether the screens that should show this requirement still do" in t for t in texts)


def test_a_requirement_tied_to_a_whole_screen_is_at_risk_only_when_it_relates_to_the_change(tmp_path: Path):
    """Regression (found on the live #6645 report): every requirement the judge tied to the Home page as a whole was
    "at risk" because one control on that page changed, so an environment-editing PR listed history, collections and
    code-generation requirements as Likely. A screen-level link must share a word with what the PR changes."""
    from blastradius.models import Link, Requirement

    w = World(tmp_path)
    try:
        def req(rid: str, text: str, section: str) -> Requirement:
            return Requirement(id=rid, target=w.target, text=text, quote=f"quote of {rid}", source_url="https://d/x",
                               section=section, edition="cloud-web", ui_observable="yes", spec_sha="s1")

        reqs = [req("req_scrrelated01", "User can switch the active environment from the home page", "Environments"),
                req("req_scrunrelat001", "User can generate client code snippets for a request in several languages",
                    "Code generation"),
                req("req_elemindir001", "User can create a folder inside a collection", "Collections"),
                req("req_elemindir002", "User can rename an environment from the sidebar", "Environments")]
        w.store.write_requirements(reqs, w.run.run_id)
        spec_path = w.run.path("spec", "requirements.json")
        existing = json.loads(spec_path.read_text(encoding="utf-8"))
        existing.extend(r.model_dump(mode="json") for r in reqs)
        spec_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        ev = w.run.evidence.add("link.bm25_judge", "scr", "requirement realised by the home page as a whole")
        w.store.write_links([Link(kind="REALIZED_BY", src=r.id, dst="scr_eeb51c7c9803", confidence=0.56,
                                  method="bm25_judge", evidence_id=ev) for r in reqs[:2]], w.run.run_id)
        # element-level links to the Collections tab, which collections/index.vue implements: a parent of changed
        # code, one hop from the change, not a control the PR edits
        w.store.write_links([Link(kind="REALIZED_BY", src=r.id, dst="el_6c08185436a7", confidence=0.8,
                                  method="bm25_judge", evidence_id=ev) for r in reqs[2:]], w.run.run_id)
        _, report = run_pr(w, 6645)
        by = {r.requirement_id: r for r in report.requirements}
        assert "req_scrrelated01" in by and by["req_scrrelated01"].status == "at_risk"
        assert "req_scrunrelat001" not in by                                    # the page changed; this feature did not
        assert "req_elemindir001" not in by                                     # reached only through shared code
        assert "req_elemindir002" in by and by["req_elemindir002"].band.value != "confirmed"   # ...unless it relates
        assert any("only through a whole screen, a flow, or code it shares" in line for line in report.not_checked)
        assert by["req_envedit0001"].status == "at_risk"                        # a control-level link is unaffected
    finally:
        w.close()


# --------------------------------------------------------------------------------- model use and cache
def test_the_judge_runs_inside_the_pipeline_on_ambiguous_subgraphs_only(world: World, tmp_path: Path):
    seen = {"judge": 0, "narrate": 0}

    def respond(kw: dict[str, Any]) -> Any:
        if "opening of a change-impact report" in kw["system"]:
            seen["narrate"] += 1
            return Narration(risk_line="Risk: HIGH. Touches Environments.", confidence_line="One item.",
                             checklist=[])
        seen["judge"] += 1
        assert "CANDIDATES" in kw["messages"][0]["content"]
        return JudgeOut(verdicts=[])

    paths, _ = run_pr(world, 6645, llm=make_llm(world.run, FakeClient(respond), tmp_path))
    judge = load(paths, "grade")["judge"]
    assert seen["judge"] == judge["calls"] >= 2 and judge["subgraphs"] >= 1 and seen["narrate"] == 1


def test_offline_uses_cached_answers_and_otherwise_the_template(world: World, tmp_path: Path):
    from blastradius.llm import LLM

    def respond(kw: dict[str, Any]) -> Any:
        if "opening of a change-impact report" in kw["system"]:
            return Narration(risk_line="Risk: HIGH. Touches Environments.", confidence_line="One item.",
                             checklist=[ChecklistLine(text="On the Home page, click “Select environment”.",
                                                      why="the Home page is changed by this PR.",
                                                      item_ids=[stable_id("itm", 6645, "direct", "/|")])])
        return JudgeOut(verdicts=[])

    online = make_llm(world.run, FakeClient(respond), tmp_path)
    run_pr(world, 6645, llm=online)                                          # fills the content-hash cache
    offline = LLM(world.run, offline=True, cache_dir=tmp_path / "cache" / "llm", settings=world.run.settings)
    offline._models.update({"cheap": "claude-haiku-5-5", "mid": "claude-sonnet-5-5"})
    paths, report = run_pr(world, 6645, llm=offline)
    assert load(paths, "claims")["verification"]["narrator"] == "llm" and len(report.checklist) == 1

    cold = LLM(world.run, offline=True, cache_dir=tmp_path / "empty", settings=world.run.settings)
    cold._models.update({"cheap": "claude-haiku-5-5", "mid": "claude-sonnet-5-5"})
    paths, report = run_pr(world, 6645, llm=cold)
    assert load(paths, "claims")["verification"]["narrator"] == "template" and report.checklist


def test_links_written_by_another_run_are_flagged_not_cited_and_the_report_still_verifies(world: World,
                                                                                         tmp_path: Path):
    other = new_run(tmp_path / "runs", "other-run", world.target)
    (other.run_dir / "code").mkdir()
    (other.run_dir / "code" / "index.json").write_text((world.run.run_dir / "code" / "index.json").read_text())
    paths = pipeline.run_pr(other, world.cfg, 6645, world.store, None, gh=FakeGh(), overlay_fn=overlay_fn_for(6645),
                            render=False)
    report = Report.model_validate_json(Path(paths["report_json"]).read_text())
    claims = load(paths, "claims")
    assert claims["verification"]["ok"] is True
    assert all(other.evidence.exists(e) for c in report.claims for e in c.evidence_ids)
    assert report.direct and report.direct[0].band.value == "confirmed"
    review = load(paths, "needs_review")["items"]
    row = next(i for i in review if i["reason_kind"] == "EVIDENCE_FROM_OTHER_RUN")
    assert row["rank"] == 3 and "same run" in row["what_to_check"]
