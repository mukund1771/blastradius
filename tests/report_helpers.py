"""Shared builders for the report tests: the PR #6645 fixture, its evidence rows, and small report factories.

`tests/fixtures/report/sample_report_6645.json` is a hand-authored FIXTURE for rendering and lint tests; it is
not a product output (see PROVENANCE.md there).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from blastradius.ids import stable_id
from blastradius.models import Band
from blastradius.report.model import (
    ChecklistItem,
    Claim,
    Report,
    ReportItem,
    RequirementRisk,
    SnapshotInfo,
    Undetermined,
)
from blastradius.run import RunContext

FIXTURES = Path(__file__).parent / "fixtures" / "report"


def load_sample() -> Report:
    return Report.model_validate_json((FIXTURES / "sample_report_6645.json").read_text(encoding="utf-8"))


def load_evidence() -> dict[str, dict[str, Any]]:
    rows = json.loads((FIXTURES / "sample_evidence_6645.json").read_text(encoding="utf-8"))
    return {r["evidence_id"]: r for r in rows}


def sample_run() -> RunContext:
    """An in-memory run whose ledger holds the fixture's evidence rows under the ids the report cites."""
    run = RunContext.ephemeral("hoppscotch")
    for rec in load_evidence().values():
        extra = {k: v for k, v in rec.items() if k not in ("evidence_id", "kind", "ref", "summary", "span_id",
                                                            "run_id")}
        got = run.evidence.add(rec["kind"], rec["ref"], rec["summary"], span_id=rec["span_id"], **extra)
        assert got == rec["evidence_id"], "fixture evidence ids must be content hashes of their rows"
    return run


def item(key: str = "x", *, tier: str = "direct", label: str = "Environments sidebar",
         screen: str | None = "Home page", what: str = "The sidebar is changed by this PR.",
         band: Band = Band.CONFIRMED, confidence: float | None = None, evidence: tuple[str, ...] = ("ev_aaaaaaaaaaaa",),
         **kw: Any) -> ReportItem:
    conf = confidence if confidence is not None else {Band.CONFIRMED: 0.9, Band.LIKELY: 0.65,
                                                       Band.POSSIBLE: 0.4, Band.UNKNOWN: 0.1}[band]
    return ReportItem(id=stable_id("itm", key), tier=tier, screen=screen, label=label, what_changed=what,
                      band=band, confidence=conf, evidence_ids=list(evidence), **kw)  # type: ignore[arg-type]


def minimal(**kw: Any) -> Report:
    """A small valid report that lints clean; tests change one field to break exactly one rule."""
    base = Report(
        target="hoppscotch", pr_number=7, pr_title="Add a thing", risk_level="LOW",
        headline='Blast radius: PR #7 "Add a thing"\nRisk: LOW. Touches the sidebar. 1 screen.',
        confidence_line="Confidence: 1 item confirmed. Nothing is left undetermined.",
        checklist=[ChecklistItem(text="Open the sidebar and look at it.", why="the sidebar is changed.")],
        direct=[item("a")],
        not_checked=["Only the signed-out surface of the web app was observed."],
        snapshot=SnapshotInfo(target="hoppscotch", repo="a/b", indexed_commit="0" * 40),
        claims=[])
    return base.model_copy(update=kw)


def requirement(**kw: Any) -> RequirementRisk:
    data: dict[str, Any] = {"requirement_id": "req_000000000001", "statement": "User can do the thing.",
                            "quote": "You can do the thing.", "section": "Things", "status": "at_risk",
                            "band": Band.LIKELY, "evidence_ids": ["ev_bbbbbbbbbbbb"]}
    data.update(kw)
    return RequirementRisk(**data)


def undetermined(**kw: Any) -> Undetermined:
    data: dict[str, Any] = {"code": "NEEDS_HUMAN", "subject": "Something", "why": "No screen maps to it.",
                            "what_to_check": "Look at it by hand.", "evidence_ids": ["ev_cccccccccccc"]}
    data.update(kw)
    return Undetermined(**data)


def claim(text: str, band: Band = Band.CONFIRMED, evidence: tuple[str, ...] = ("ev_aaaaaaaaaaaa",)) -> Claim:
    return Claim(id=stable_id("clm", text), text=text, evidence_ids=list(evidence), band=band)


def body_of(markdown: str) -> str:
    """The Markdown above the appendix."""
    return markdown.split("\n## Appendix: evidence", 1)[0]


def appendix_of(markdown: str) -> str:
    return markdown.split("\n## Appendix: evidence", 1)[1]
