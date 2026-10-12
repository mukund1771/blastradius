"""evalkit.stability: analysis of N reports, the cache-bypass wrapper, live repetition with a fake runner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from test_eval_support import (
    FakeClient,
    evidence,
    item,
    make_llm,
    make_run,
    report,
    write_evidence,
    write_report,
)

from blastradius.evalkit import stability as st
from blastradius.failures import FailureCode
from blastradius.models import Band
from blastradius.report.model import Report


def rep(*ids: int, band: str = "confirmed", **over: Any) -> Report:
    return Report.model_validate(
        report(
            direct=[item(i, band=band) for i in ids], claims=[], checklist=[], headline="Risk: LOW.", **over
        )
    )


def analyse(reports: list[Report], **kw: Any) -> dict[str, Any]:
    return st.analyse(
        reports,
        min_agreement=kw.pop("min_agreement", 0.6),
        demoted_confidence=kw.pop("demoted_confidence", 0.49),
    )


# ---------------------------------------------------------------------------------------- analysis
def test_identical_runs_are_perfectly_stable_and_nothing_is_demoted():
    out = analyse([rep(1, 2, 3)] * 5)
    assert out["n"] == 5 and out["affected_set_jaccard"] == {"mean": 1.0, "min": 1.0, "pairs": 10}
    assert out["unstable_items"] == [] and out["always_present"] == 3
    assert all(not v["demoted"] and not v["changed"] for v in out["stable_view"]["items"])


def test_jaccard_is_pairwise_over_the_affected_sets():
    out = analyse([rep(1, 2), rep(1, 3)])
    assert out["affected_set_jaccard"]["mean"] == pytest.approx(1 / 3, abs=1e-4)
    out3 = analyse([rep(1, 2), rep(1, 2), rep(1, 3)])
    assert out3["affected_set_jaccard"]["pairs"] == 3 and out3["affected_set_jaccard"][
        "min"
    ] == pytest.approx(1 / 3, abs=1e-4)


def test_items_below_the_agreement_threshold_are_demoted_in_the_stable_view_only():
    reports = [rep(1, 9), rep(1, 9), rep(1), rep(1), rep(1)]  # item 9: 2 of 5 = 0.4 < 0.6; item 1: always
    out = analyse(reports)
    unstable = {r["id"]: r for r in out["items"]}[f"itm_{9:012x}"]
    assert unstable["agreement"] == 0.4 and unstable["unstable"]
    view = {v["item"]["id"]: v for v in out["stable_view"]["items"]}
    shown = view[f"itm_{9:012x}"]
    assert shown["demoted"] and shown["changed"] and shown["original_band"] == "confirmed"
    assert shown["item"]["band"] == "possible" and shown["item"]["confidence"] <= 0.49
    assert view[f"itm_{1:012x}"]["item"]["band"] == "confirmed"
    # the source reports are untouched
    assert reports[0].direct[1].band is Band.CONFIRMED and reports[0].direct[1].confidence == 0.9


def test_agreement_exactly_at_the_threshold_is_not_demoted():
    out = analyse([rep(1, 2), rep(1, 2), rep(1, 2), rep(1), rep(1)])  # item 2: 3 of 5 = 0.6
    row = {r["id"]: r for r in out["items"]}[f"itm_{2:012x}"]
    assert row["agreement"] == 0.6 and not row["unstable"]


def test_demotion_never_raises_a_band():
    low = item(1, band="unknown")
    from blastradius.report.model import ReportItem

    demoted = st.demote(ReportItem.model_validate(low), 0.49)
    assert demoted.band is Band.UNKNOWN and demoted.confidence == 0.1
    likely = st.demote(ReportItem.model_validate(item(2, band="likely")), 0.49)
    assert likely.band is Band.POSSIBLE and likely.confidence == 0.49


def test_band_disagreement_is_reported_per_item():
    out = analyse([rep(1, band="confirmed"), rep(1, band="confirmed"), rep(1, band="likely")])
    row = out["items"][0]
    assert row["modal_band"] == "confirmed" and row["band_agreement"] == pytest.approx(0.6667, abs=1e-4)


def test_a_single_run_has_no_pairs_and_says_so():
    out = analyse([rep(1, 2)])
    assert out["affected_set_jaccard"] == {"mean": None, "min": None, "pairs": 0}
    assert analyse([])["n"] == 0


def test_tiers_are_compared_separately():
    a = Report.model_validate(report(direct=[item(1)], indirect=[item(2, tier="indirect")], claims=[]))
    b = Report.model_validate(report(direct=[item(1)], indirect=[item(3, tier="indirect")], claims=[]))
    out = analyse([a, b])
    assert (
        out["jaccard_by_tier"]["direct"]["mean"] == 1.0 and out["jaccard_by_tier"]["indirect"]["mean"] == 0.0
    )


# ---------------------------------------------------------------------------------------- the wrapper
class Out(BaseModel):
    answer: str


def test_bypass_never_reads_the_cache_and_never_writes_the_product_cache(tmp_path: Path):
    run = make_run(tmp_path)
    client = FakeClient(lambda kw: Out(answer="x"))
    llm = make_llm(run, client, tmp_path)
    product_cache = llm.cache_dir
    scratch = tmp_path / "scratch_cache"
    bypass = st.BypassCacheLLM(llm, scratch)
    for _ in range(3):
        bypass.structured(purpose="t", tier="mid", system="s", user="u", schema=Out)
    assert len(client.calls) == 3  # same prompt, three live calls
    assert not any(product_cache.rglob("*.json")) if product_cache.exists() else True
    assert any(scratch.rglob("*.json")) and llm.cache_dir == product_cache
    # a normal call afterwards is live too (nothing of the bypass leaked into the product cache), then cached
    llm.structured(purpose="t", tier="mid", system="s", user="u", schema=Out)
    llm.structured(purpose="t", tier="mid", system="s", user="u", schema=Out)
    assert len(client.calls) == 4
    assert bypass.run is llm.run  # delegation: spend lands on the same ledger


# -------------------------------------------------------------------------------------- live repetition
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _product_run(tmp_path: Path, client: FakeClient):
    run = make_run(tmp_path)
    write_evidence(run, evidence(3))
    write_report(run, "6645", report())
    run.path("links", "links.jsonl").write_text('{"x": 1}\n')
    run.failures.record(FailureCode.SPEC_STALE, "stale spec")
    run.path("reason", "6645", "pr.json").write_text('{"number": 6645}')
    return run, make_llm(run, client, tmp_path)


def test_live_repetitions_cost_agreement_and_isolation(tmp_path: Path):
    client = FakeClient(lambda kw: Out(answer="x"))
    run, llm = _product_run(tmp_path, client)
    product_report = run.run_dir / "reason" / "6645" / "report.json"
    before = _sha(product_report)
    seen: dict[str, Any] = {}

    def runner(scratch, cfg, store, bypass_llm, pr, i):
        seen.setdefault("pr_json", (scratch.run_dir / "reason" / str(pr) / "pr.json").exists())
        seen.setdefault(
            "old_report_hidden", not (scratch.run_dir / "reason" / str(pr) / "report.json").exists()
        )
        seen.setdefault("links", (scratch.run_dir / "links" / "links.jsonl").exists())
        seen.setdefault("ledger_sees_seed", scratch.evidence.exists("ev_000000000001"))
        seen.setdefault("failures", "SPEC_STALE" in (scratch.run_dir / "failures.jsonl").read_text())
        bypass_llm.structured(purpose="narrate", tier="mid", system="s", user="same prompt", schema=Out)
        ids = [1, 2] if i % 2 == 0 else [1, 2, 3]
        return report(
            direct=[item(k, evidence=["ev_000000000001"]) for k in ids],
            claims=[],
            checklist=[],
            headline="Risk: LOW.",
        )

    from blastradius.targets import TargetConfig

    out = st.run_stability(
        run,
        TargetConfig.load("hoppscotch"),
        None,
        llm,
        n=4,
        min_agreement=0.6,
        demoted_confidence=0.49,
        pr_numbers=[6645],
        runner=runner,
    )
    assert out["status"] == "ok" and out["per_pr"]["6645"]["n_completed"] == 4
    assert len(client.calls) == 4  # cache bypassed: four live calls
    assert out["cost_usd"] > 0 and out["n100"]["n100_usd_lower_bound"] == pytest.approx(
        out["cost_usd"] / 4 * 100, abs=0.01
    )
    assert "x 100 =" in out["n100"]["arithmetic"] and "lower bound" in out["n100"]["note"]
    v = out["per_pr"]["6645"]
    assert v["always_present"] == 2 and v["affected_set_jaccard"]["pairs"] == 6
    assert v["invariants_evaluated"] == 4 and v["invariants_clean"] == 4  # evidence resolves via the seed
    assert seen == {
        "pr_json": True,
        "old_report_hidden": True,
        "links": True,
        "ledger_sees_seed": True,
        "failures": True,
    }
    assert _sha(product_report) == before  # the product artifact is untouched
    assert (run.run_dir / "eval" / "stable_view.json").exists()
    assert len(list((run.run_dir / "eval" / "stability" / "reports" / "6645").glob("rep_*.json"))) == 4
    assert not list((run.run_dir / "eval" / "stability" / "scratch").glob("*-stab-*"))  # scratch removed


def test_a_failing_repetition_is_reported_and_the_rest_still_count(tmp_path: Path):
    client = FakeClient(lambda kw: Out(answer="x"))
    run, llm = _product_run(tmp_path, client)

    def runner(scratch, cfg, store, bypass_llm, pr, i):
        if i == 1:
            raise RuntimeError("model said no\nsecond line")
        if i == 2:
            return {"not": "a report"}
        return report()

    from blastradius.targets import TargetConfig

    out = st.run_stability(
        run,
        TargetConfig.load("hoppscotch"),
        None,
        llm,
        n=4,
        min_agreement=0.6,
        demoted_confidence=0.49,
        pr_numbers=[6645],
        runner=runner,
    )
    v = out["per_pr"]["6645"]
    assert v["n_requested"] == 4 and v["n_completed"] == 2
    assert {e["repetition"] for e in v["errors"]} == {1, 2}
    assert any("RuntimeError: model said no" in e["error"] and "\n" not in e["error"] for e in v["errors"])


def test_skipped_with_a_reason_when_nothing_can_run(tmp_path: Path):
    run = make_run(tmp_path)
    from blastradius.targets import TargetConfig

    cfg = TargetConfig.load("hoppscotch")
    no_pr = st.run_stability(run, cfg, None, None, n=3, min_agreement=0.6, demoted_confidence=0.49)
    assert no_pr["status"] == "skipped" and "no PR numbers" in no_pr["reason"]
    no_llm = st.run_stability(
        run, cfg, None, None, n=3, min_agreement=0.6, demoted_confidence=0.49, pr_numbers=[1]
    )
    assert no_llm["status"] == "skipped" and "no LLM" in no_llm["reason"]


def test_fixture_directory_runs_the_analysis_without_reasoning_or_an_llm(tmp_path: Path):
    for i, ids in enumerate([[1, 2], [1, 2], [1], [1, 2, 3]]):
        d = tmp_path / "fx" / "6645"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"rep_{i}.json").write_text(
            json.dumps(report(direct=[item(k) for k in ids], claims=[], checklist=[], headline="Risk: LOW."))
        )
    run = make_run(tmp_path)
    from blastradius.targets import TargetConfig

    out = st.run_stability(
        run,
        TargetConfig.load("hoppscotch"),
        None,
        None,
        n=4,
        min_agreement=0.6,
        demoted_confidence=0.49,
        fixture_dir=tmp_path / "fx",
    )
    v = out["per_pr"]["6645"]
    assert out["status"] == "ok" and "fixture" in out["source"] and v["n_completed"] == 4
    assert v["unstable_items"] == [f"itm_{3:012x}"] and out["cost_usd"] == 0
    assert "arithmetic" in out["n100"] and out["n100"]["n100_usd_lower_bound"] == 0.0
