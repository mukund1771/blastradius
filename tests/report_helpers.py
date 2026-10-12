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


# ------------------------------------------------------------------------------------------------- edge
def _ev(n: int) -> str:
    return f"ev_{n:012x}"


def edge_report() -> Report:
    """A hand-built report that walks every optional path of the writers: all four tiers and their caps, every
    requirement status, unsourced and cut rows, matched and unmatched claims, a migration step of the producer's,
    and a complete snapshot. Used by the golden-file tests (tests/fixtures/report/golden_*), not a product output."""
    d1 = item("d1", label="Environments sidebar", screen="Home page", band=Band.CONFIRMED, confidence=0.93,
              what="The Environments sidebar is changed by this PR. Controls involved: “Environments”.",
              evidence=(_ev(1), _ev(2)), flows=["Edit a variable", "flow_0123456789ab", "src/flows/edit.ts"],
              files=["packages/app/src/Editor.vue", "packages/app/src/index.vue"],
              requirement_ids=["req_000000000002", "req_000000000001"])
    d2 = item("d2", label="Quick peek", screen="Quick peek", band=Band.CONFIRMED, confidence=0.85,
              what="The quick peek is changed by this PR.", evidence=(_ev(3),))
    d3 = item("d3", label="Variable selector", screen="Home page", band=Band.CONFIRMED, confidence=0.81,
              what="The variable selector is changed by this PR.", evidence=())
    strengths = (0.79, 0.76, 0.73, 0.70, 0.66, 0.62, 0.58, 0.54, 0.45, 0.30, 0.28, 0.26)
    sections = ("Environments", "Collections", None, "Environments", "Requests", "Collections", None,
                "Environments", "Requests", None, "Collections", "Environments")
    indirect = []
    for n, (conf, section) in enumerate(zip(strengths, sections, strict=True)):
        band = Band.LIKELY if conf >= 0.5 else Band.POSSIBLE
        said = "is probably affected" if band is Band.LIKELY else "might be affected"
        indirect.append(item(
            f"i{n:02d}", tier="indirect", label=f"Screen {n:02d}", screen=f"Page {n:02d}" if n % 3 else None,
            what=f"Screen {n:02d} {said}.", band=band, confidence=conf, section=section,
            via=("the shared current-value service" if n % 2 else "the shared secret-value service" if n % 4 == 0
                 else None),
            evidence=(_ev(10 + n),), flows=[f"Flow {n:02d}"] if n % 5 == 0 else []))
    g1 = item("g1", tier="global", label="The shared page frame", screen=None, band=Band.CONFIRMED,
              confidence=0.85, what="This PR edits the page layout. Every screen is changed by this PR to some "
              "degree.", evidence=(_ev(30),))
    s1 = item("s1", tier="stored_data", label="Saved collection data", screen=None, band=Band.CONFIRMED,
              confidence=0.82, what="The saved collection data format is changed by this PR.", evidence=(_ev(31),))
    steps = [
        ChecklistItem(text="Open Environments in the sidebar and edit a variable's current value.",
                      why="the Environments sidebar is changed by this PR.", item_ids=[d1.id, d2.id]),
        ChecklistItem(text="Open the quick peek and look at it.", why="", item_ids=[d2.id]),
        ChecklistItem(text="Export your saved data and import it again.",
                      why="the saved collection data format is changed by this PR.", item_ids=[s1.id]),
        ChecklistItem(text="Open Screen 11 and look at it.", why="it might be affected.",
                      item_ids=[indirect[11].id, "itm_unknown00000"]),
        ChecklistItem(text="Check the page frame.", why="the page layout is changed by this PR.",
                      item_ids=[g1.id]),
    ]
    requirements = [
        requirement(requirement_id="req_000000000001", statement="User can edit an environment variable.",
                    quote="You can edit “the value” of a variable.", section="Environments", status="at_risk",
                    band=Band.LIKELY, evidence_ids=[_ev(40)]),
        requirement(requirement_id="req_000000000002", statement="User can see the current value.",
                    quote="The current value is shown.", section="Environments",
                    status="loses_only_realization", band=Band.CONFIRMED, evidence_ids=[_ev(41), _ev(40)]),
        requirement(requirement_id="req_000000000003", statement="User can sort collections.",
                    quote="Collections can be sorted.", section="Collections", status="cannot_verify",
                    band=Band.UNKNOWN, evidence_ids=[_ev(42)]),
        requirement(requirement_id="req_000000000004", statement="User can import a collection.",
                    quote="", section="", status="may_close_gap", band=Band.POSSIBLE, evidence_ids=[]),
    ]
    und = [undetermined(code="STALE_BASE", subject="the whole PR", why="The PR base is behind main.",
                        what_to_check="Rebase and run again.", evidence_ids=[_ev(50)])]
    und += [undetermined(code="NEEDS_HUMAN", subject=f"File {n}", why=f"File {n} is outside the snapshot",
                         what_to_check="Look at it by hand.", evidence_ids=[_ev(51 + n)] if n % 2 else [])
            for n in range(6)]
    claims = [
        claim(f"{d1.label}: {d1.what_changed}", Band.CONFIRMED, (_ev(1), _ev(2))),
        Claim(id=stable_id("clm", "d2"), text="Quick peek has a new field.", evidence_ids=[_ev(3)],
              band=Band.CONFIRMED),
        claim("A claim that no sentence shows.", Band.LIKELY, (_ev(60),)),
    ]
    return Report(
        target="hoppscotch", pr_number=6645, pr_title='Edit "environment" values from the sidebar',
        risk_level="HIGH",
        headline='Blast radius: PR #6645 "Edit environment values"\nRisk: HIGH. Touches Environments (sidebar, '
                 "quick peek). 4 screens, 1 user flow, 4 requirements.\nIt also changes code shared across the app.",
        confidence_line="Confidence: 3 items confirmed, 2 probable and 1 possible. 7 things could not be "
                        "determined and are listed below with the same weight as the findings.",
        checklist=steps, direct=[d3, d1, d2], indirect=indirect, global_changes=[g1], stored_data=[s1],
        requirements=requirements, undetermined=und,
        not_checked=["Only the signed-out surface of the web app was observed.", "One viewport size was used",
                     "Only English text (`en`) was compared."],
        snapshot=SnapshotInfo(
            target="hoppscotch", repo="hoppscotch/hoppscotch", indexed_commit="63273f8e599e73f0fb38c0739314e8d46b6bc83d",
            pr_base_sha="ac145e7f758151b41fd46d3e5f513886ce9068ba",
            pr_head_sha="64ba4ff75eff7bef868b010947f50e9b6b326540", base_behind_main_commits=1,
            deployed_vs_indexed="11 of 104 files differ", crawl_bundle_hash="c81a0323d1f5b4f5", crawl_actions=1,
            crawl_screens=25, spec_sha="f895cca9ad176109", doc_version="2026.5.0", app_version="2026.9.0"),
        claims=claims)


def edge_evidence() -> dict[str, dict[str, Any]]:
    """Ledger rows for `edge_report`: every extra field the appendix prints, and ids left out on purpose."""
    rows: dict[str, dict[str, Any]] = {}
    for n in (1, 3, 10, 11, 30, 31, 40, 50, 52, 54):
        rows[_ev(n)] = {"evidence_id": _ev(n), "kind": "pr.file", "ref": f"a/b#6645:packages/app/src/File{n}.vue",
                        "summary": f"modified packages/app/src/File{n}.vue (+{n} -1)", "span_id": f"span{n:04d}"}
    rows[_ev(2)] = {"evidence_id": _ev(2), "kind": "link.edge", "ref": "packages/app/src/index.vue",
                    "summary": "imports the changed editor", "span_id": "", "method": "static-import",
                    "confidence": 0.75, "path": "packages/app/src/index.vue", "file": "index.vue",
                    "lines": "3-9", "line_start": 3, "line_end": 9, "start_line": 3, "end_line": 9,
                    "source_url": "hxxps://docs.example/x", "sha": "abc1234", "commit": "def5678"}
    rows[_ev(41)] = {"evidence_id": _ev(41), "kind": "spec.quote", "ref": "", "summary": "", "span_id": "s41"}
    return rows


# ------------------------------------------------------------------------------------------------ golden
GOLDEN_UPDATE_HINT = ("the output changed; if that is intended, regenerate with BLASTRADIUS_UPDATE_GOLDEN=1 "
                      "pytest tests/test_report_render.py tests/test_report_lint.py and review the diff")


def assert_golden(name: str, actual: str) -> None:
    """Compare `actual` with tests/fixtures/report/<name> (or rewrite it when BLASTRADIUS_UPDATE_GOLDEN=1).

    The writers' text is the product: a golden file makes any change to a heading, a table header, a status
    word, a legend or an appendix sentence a visible, reviewed diff instead of a silent one.
    """
    import os

    from blastradius.config import PACKAGE_VERSION

    actual = actual.replace(PACKAGE_VERSION, "<package version>")      # a release bump is not a text change
    path = FIXTURES / name
    if os.environ.get("BLASTRADIUS_UPDATE_GOLDEN") == "1":
        path.write_text(actual, encoding="utf-8", newline="\n")
        return
    expected = path.read_text(encoding="utf-8")
    assert actual == expected, f"{name}: {GOLDEN_UPDATE_HINT}"


def without_css(page_html: str) -> str:
    """The page without its style sheet (CSS has its own tests; the golden is about the document)."""
    import re

    return re.sub(r"(?s)<style>.*?</style>", "<style>/* css omitted from the golden file */</style>", page_html)
