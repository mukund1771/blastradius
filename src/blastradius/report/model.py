"""Report data model: the single contract between reasoning (producer) and rendering (consumer).

A Report is plain data. It contains no HTML and no Markdown; the renderer escapes and formats. Every statement
the report makes is a Claim that cites evidence ids; the verifier checks them against the run's evidence ledger.
Language rules per band are in docs/REPORT_SPEC.md and enforced by reason.verify and report.lint.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..models import Band, Strict

Tier = Literal["direct", "indirect", "global", "stored_data"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
ReqStatus = Literal["at_risk", "loses_only_realization", "cannot_verify", "may_close_gap"]


class Claim(Strict):
    id: str                                   # stable_id("clm", pr, text)
    text: str
    evidence_ids: list[str]
    band: Band                                # lowest-confidence cited edge decides the allowed verbs


class ReportItem(Strict):
    id: str
    tier: Tier
    screen: str | None = None                 # the product's own words: screen / dialog title from the crawl
    label: str                                # what the QA lead will see, no ids or paths
    what_changed: str
    band: Band
    confidence: float
    section: str | None = None                # spec section name used for grouping tier 2
    via: str | None = None                    # plain-language shared piece connecting it ("the variable input")
    flows: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)   # appendix only


class ChecklistItem(Strict):
    text: str                                 # an action a person can take, in product words
    why: str
    item_ids: list[str] = Field(default_factory=list)


class RequirementRisk(Strict):
    requirement_id: str
    statement: str                            # the requirement text
    quote: str                                # verbatim spec quote
    section: str
    status: ReqStatus
    band: Band
    evidence_ids: list[str] = Field(default_factory=list)


class Undetermined(Strict):
    code: str                                 # a FailureCode value or a Gap kind
    subject: str                              # product words where possible
    why: str
    what_to_check: str
    evidence_ids: list[str] = Field(default_factory=list)


class SnapshotInfo(Strict):
    target: str
    repo: str
    indexed_commit: str
    pr_base_sha: str | None = None
    pr_head_sha: str | None = None
    base_behind_main_commits: int | None = None
    deployed_vs_indexed: str | None = None    # "11 of 104 files differ" when known
    crawl_bundle_hash: str | None = None
    crawl_actions: int | None = None
    crawl_screens: int | None = None
    spec_sha: str | None = None
    doc_version: str | None = None
    app_version: str | None = None


class Report(Strict):
    target: str
    pr_number: int
    pr_title: str
    risk_level: RiskLevel
    headline: str                             # 2-3 lines
    confidence_line: str
    checklist: list[ChecklistItem] = Field(default_factory=list)
    direct: list[ReportItem] = Field(default_factory=list)
    indirect: list[ReportItem] = Field(default_factory=list)
    global_changes: list[ReportItem] = Field(default_factory=list)
    stored_data: list[ReportItem] = Field(default_factory=list)
    requirements: list[RequirementRisk] = Field(default_factory=list)
    undetermined: list[Undetermined] = Field(default_factory=list)
    not_checked: list[str] = Field(default_factory=list)
    snapshot: SnapshotInfo
    claims: list[Claim] = Field(default_factory=list)
