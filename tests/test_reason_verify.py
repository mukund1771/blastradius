"""reason.verify: the deterministic verifier (D21; cases 54, 55, 57; REPORT_SPEC language rules).

Each rule is triggered by mutating an otherwise valid report, so a rule that stops firing fails here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_reason_support import sample_report

from blastradius.models import Band
from blastradius.reason.risk import REMOVED_TEXT_VIA, derive_risk
from blastradius.reason.verify import verify
from blastradius.report.model import ChecklistItem, ReportItem, RequirementRisk


@pytest.fixture
def bundle(tmp_path: Path):
    report, asm, run = sample_report(tmp_path)
    report.headline = f"Blast radius: PR #6645 “edit”\nRisk: {report.risk_level}. Touches Environments."
    report.checklist = [ChecklistItem(text="On the Home page, click “Select environment”.",
                                      why="the Home page is changed by this PR.", item_ids=[report.direct[0].id])]
    return report, asm, run


def codes(report, asm, run, **kw):
    args = {"basis": asm.basis, "expected_direct": asm.expected_direct, "tier2_cap": 10, "checklist_max": 8}
    args.update(kw)
    return sorted({v.code for v in verify(report, run, **args)})


def test_a_consistent_report_passes(bundle):
    assert codes(*bundle) == []


def test_citation_to_an_unknown_evidence_id_is_rejected(bundle):
    report, asm, run = bundle
    report.direct[0].evidence_ids.append("ev_doesnotexist")
    report.claims[0].evidence_ids.append("ev_alsomissing")
    report.requirements[0].evidence_ids = ["ev_gone"]
    report.undetermined[0].evidence_ids = ["ev_nope"]
    assert codes(report, asm, run) == ["CITATION_MISSING"]


def test_a_claim_may_not_outrun_its_weakest_cited_edge(bundle):
    report, asm, run = bundle
    cid = report.claims[0].id                                              # CONFIRMED claim
    asm.basis[cid] = [{"evidence_id": report.claims[0].evidence_ids[0], "confidence": 0.3}]
    assert codes(report, asm, run) == ["CLAIM_OVERSTATED"]
    asm.basis[cid] = []
    assert "CLAIM_UNCITED" in codes(report, asm, run)


def test_a_conservative_claim_is_fine(bundle):
    report, asm, run = bundle
    asm.basis[report.claims[1].id][0]["confidence"] = 0.95                 # edge stronger than the claim: ok
    assert codes(report, asm, run) == []


@pytest.mark.parametrize(("which", "text"), [
    (0, "This screen is changed by this PR, though it might break."),       # confirmed: 'might'
    (0, "This screen could change."),                                      # confirmed: no allowed phrase + 'could'
    (1, "This screen will change. It is probably affected by this PR."),   # likely: 'will'
    (2, "This screen will be affected by this PR; worth a quick look."),   # possible: 'will'
    (2, "This screen is probably affected by this PR."),                   # possible: 'probably'
])
def test_item_verbs_must_match_the_band(bundle, which, text):
    report, asm, run = bundle
    items = [*report.direct, *report.indirect]
    items[which].what_changed = text
    assert "VERB" in codes(report, asm, run)


def test_names_taken_from_the_page_are_not_policed(bundle):
    report, asm, run = bundle
    report.direct[0].what_changed = "This screen is changed by this PR. Controls: “Might Could Maybe”."
    report.claims[0] = report.claims[0].model_copy(update={"text": "Home page: " + report.direct[0].what_changed})
    assert codes(report, asm, run) == []


def test_checklist_rules(bundle):
    report, asm, run = bundle
    report.checklist.append(ChecklistItem(text="Look.", why="It is changed.", item_ids=["itm_unknown"]))
    assert "CHECKLIST_REF" in codes(report, asm, run)
    report.checklist = [ChecklistItem(text=f"Step {i}", why="the Home page is changed by this PR.",
                                      item_ids=[report.direct[0].id]) for i in range(9)]
    assert "CHECKLIST_LENGTH" in codes(report, asm, run)
    report.checklist = [ChecklistItem(text="Check it.", why="it will certainly break.",
                                      item_ids=[report.indirect[1].id])]
    assert "VERB" in codes(report, asm, run)                                # 'will' on a possible item


def test_completeness_every_tier_one_item_and_a_tier_two_cap(bundle):
    report, asm, run = bundle
    assert "MISSING_TIER1" in codes(report, asm, run, expected_direct=[*asm.expected_direct, "itm_lost"])
    many = [report.indirect[0].model_copy(update={"id": f"itm_{i:012x}"}) for i in range(11)]
    report.indirect = many
    assert "TIER2_UNCAPPED" in codes(report, asm, run)


@pytest.mark.parametrize(("field", "value", "code"), [
    ("what_changed", "This screen is changed by this PR via packages/hoppscotch-common/src/a/B.vue.", "RAW_ID_OR_PATH"),
    ("what_changed", "This screen is changed by this PR (see ev_0123456789ab).", "RAW_ID_OR_PATH"),
    ("what_changed", "This screen is changed by this PR at 63273f8e599e.", "RAW_ID_OR_PATH"),
    ("what_changed", "This screen is changed by this PR; the graph says so.", "CODE_TERM"),
    ("what_changed", "This screen is changed by this PR (two hops away).", "CODE_TERM"),
    ("what_changed", "This screen is changed by this PR <script>alert(1)</script>", "HTML"),
])
def test_body_hygiene(bundle, field, value, code):
    report, asm, run = bundle
    setattr(report.direct[0], field, value)
    assert code in codes(report, asm, run)


def test_appendix_fields_may_hold_paths_and_ids(bundle):
    report, asm, run = bundle
    report.direct[0].files = ["packages/hoppscotch-common/src/components/environments/Selector.vue"]
    assert codes(report, asm, run) == []


def test_risk_level_must_follow_the_items(bundle):
    report, asm, run = bundle
    report.risk_level = "LOW"
    assert "RISK_INCONSISTENT" in codes(report, asm, run)
    report.risk_level = derive_risk(report)
    report.headline = "Blast radius: PR #1 “x”\nNothing to see."
    assert "RISK_NOT_IN_HEADLINE" in codes(report, asm, run)


def test_band_must_match_confidence(bundle):
    report, asm, run = bundle
    report.indirect[0].confidence = 0.95
    assert "BAND_MISMATCH" in codes(report, asm, run)


def test_unknown_risk_needs_a_reason(bundle):
    report, asm, run = bundle
    report.direct, report.indirect, report.claims, report.checklist = [], [], [], []
    report.undetermined = [report.undetermined[0].model_copy(update={"code": "UNMAPPED_CHANGE"})]
    report.risk_level = derive_risk(report)
    report.headline = "Blast radius: PR #6645 “edit”\nRisk: UNKNOWN. No screen could be tied to this change."
    assert report.risk_level == "UNKNOWN" and codes(report, asm, run, expected_direct=[]) == []
    report.undetermined = []
    assert derive_risk(report) == "LOW"                                    # nothing outstanding: LOW, not UNKNOWN


def test_snapshot_must_pin_the_head(bundle):
    report, asm, run = bundle
    report.snapshot.pr_head_sha = None
    assert "SNAPSHOT_UNPINNED" in codes(report, asm, run)


# ---------------------------------------------------------------------------------------- risk rule
def item(tier: str, band: Band, **kw) -> ReportItem:
    conf = {Band.CONFIRMED: 0.9, Band.LIKELY: 0.6, Band.POSSIBLE: 0.4}[band]
    return ReportItem(id=f"itm_{tier}_{band.value}", tier=tier, label="x", what_changed="x",   # type: ignore[arg-type]
                      band=band, confidence=conf, **kw)


def test_risk_rule(tmp_path: Path):
    report, _, _ = sample_report(tmp_path)
    base = report.model_copy(update={"direct": [], "indirect": [], "global_changes": [], "stored_data": [],
                                     "requirements": [], "undetermined": []})
    assert derive_risk(base) == "LOW"                                       # only set-aside files
    cannot = report.undetermined[0].model_copy(update={"code": "UNMAPPED_CHANGE"})
    assert derive_risk(base.model_copy(update={"undetermined": [cannot]})) == "UNKNOWN"
    assert derive_risk(base.model_copy(update={"undetermined": report.undetermined})) == "LOW"     # STALE_BASE only
    risk = RequirementRisk(requirement_id="r1", statement="s", quote="q", section="S", status="at_risk",
                           band=Band.LIKELY)
    confirmed = item("direct", Band.CONFIRMED, flows=["f"], requirement_ids=["r1"])
    assert derive_risk(base.model_copy(update={"direct": [confirmed], "requirements": [risk]})) == "HIGH"
    no_req = item("direct", Band.CONFIRMED, flows=["f"], requirement_ids=["r1"])
    assert derive_risk(base.model_copy(update={"direct": [no_req]})) == "MEDIUM"      # flow without a risk
    unverifiable = risk.model_copy(update={"status": "cannot_verify"})
    assert derive_risk(base.model_copy(update={"direct": [no_req], "requirements": [unverifiable]})) == "MEDIUM"
    assert derive_risk(base.model_copy(update={"indirect": [item("indirect", Band.LIKELY)]})) == "MEDIUM"
    assert derive_risk(base.model_copy(update={"indirect": [item("indirect", Band.POSSIBLE)]})) == "LOW"
    assert derive_risk(base.model_copy(update={"direct": [item("direct", Band.POSSIBLE)]})) == "LOW"
    stored = item("stored_data", Band.CONFIRMED)
    assert derive_risk(base.model_copy(update={"stored_data": [stored]})) == "MEDIUM"
    assert derive_risk(base.model_copy(update={"stored_data": [stored], "direct": [confirmed]})) == "HIGH"
    severe = item("direct", Band.CONFIRMED, via=REMOVED_TEXT_VIA)
    assert derive_risk(base.model_copy(update={"direct": [severe]})) == "HIGH"


# ------------------------------------------------------------------------------- could not determine
def test_could_not_determine_is_aggregated_by_reason_ranked_and_capped_case_61():
    from blastradius.reason.assemble import aggregate_review, not_checked_lines
    from blastradius.reason.facts import Retrieval
    from blastradius.reason.grade import ReviewItem
    from blastradius.reason.pr import PRData

    def row(kind: str, n: int, rank: int) -> ReviewItem:
        return ReviewItem(id=f"rev_{kind}{n}", code="NEEDS_HUMAN", reason_kind=kind, subject=f"the {kind} {n} code",
                          detail="no control we observed is built from this file", what_to_check=f"Check {kind} {n}.",
                          rank=rank, evidence_ids=[f"ev_{kind}{n}"])

    items = [row("A", i, 40) for i in range(12)] + [row(k, 0, r) for k, r in
                                                     (("B", 10), ("C", 20), ("D", 30), ("E", 50), ("F", 60),
                                                      ("G", 70))]
    rows = aggregate_review(items, 5)
    assert len(rows) == 5                                                  # 7 reasons, 5 shown
    assert [r.subject for r in rows][:3] == ["the B 0 code", "the C 0 code", "the D 0 code"]    # ranked
    assert rows[3].subject.startswith("12 items: the A 0 code, the A 1 code, the A 10 code and 9 more")
    assert len(rows[3].evidence_ids) <= 8                                  # a flood of ids never reaches the body
    pr = PRData(number=1, repo="o/r", title="t", base_sha="a" * 40, head_sha="b" * 40)
    lines = not_checked_lines(pr, Retrieval(), {}, overflow=3, more_undetermined=2)
    assert any("3 further screen(s) that share code" in line for line in lines)
    assert any("2 more lower-priority item(s) could not be determined" in line for line in lines)
