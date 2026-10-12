"""The decisions both renderers share, made once (D29; REPORT_SPEC sections 1-9; cases 57, 61, 62).

`build_view` turns a `Report` (plain data) and the run's evidence records into a `View`: cleaned text, the order
of every list, the Tier 2 and could-not-determine caps, and one numbered appendix entry per cited sentence. The
Markdown and HTML writers only format a `View`, so the two files cannot disagree about order, numbering or
what was cut. Nothing here depends on set or dict iteration order, a clock or a run id.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..ids import stable_id
from ..models import Band
from .escape import clean, sentence
from .model import Claim, Report, ReportItem, RequirementRisk, Undetermined
from .vocab import (
    BAND_RANK,
    FILE_PATH,
    MIGRATION_STEPS,
    RAW_ID,
    STATUS_RANK,
    STATUS_WORD,
    TIER2_CAP,
    UNDETERMINED_TOP,
)

EVIDENCE_EXTRAS = ("method", "confidence", "path", "file", "lines", "line_start", "line_end", "start_line",
                   "end_line", "source_url", "sha", "commit")
_RISK_PREFIX = re.compile(r"^\s*risk\s*:\s*(?:low|medium|high|unknown)\b[\s.:;,-]*", re.IGNORECASE)
_TITLE_PREFIX = re.compile(r"^\s*blast radius\b", re.IGNORECASE)
_OTHER_SCREENS = "Other screens"


@dataclass(frozen=True)
class EvidenceView:
    id: str
    found: bool
    kind: str = ""
    ref: str = ""
    summary: str = ""
    span: str = ""
    extras: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Unit:
    """One cited sentence = one footnote = one appendix entry."""

    number: int
    section: str                       # key in vocab.SECTION_TITLES, or "claim"
    title: str
    text: str
    band: Band | None
    confidence: float | None
    claim_id: str | None
    claim_text: str | None
    evidence: tuple[EvidenceView, ...]
    files: tuple[str, ...] = ()
    requirement_ids: tuple[str, ...] = ()
    flows: tuple[str, ...] = ()
    code: str | None = None            # failure code or gap kind of a could-not-determine row
    overflow: bool = False             # cut from the body by a cap, listed here instead


@dataclass(frozen=True)
class FindingRow:
    title: str
    where: str | None
    text: str
    band: Band
    flows: tuple[str, ...]
    marker: int | None


@dataclass(frozen=True)
class Group:
    title: str
    via: tuple[str, ...]
    rows: tuple[FindingRow, ...]


@dataclass(frozen=True)
class CheckRow:
    text: str
    why: str
    markers: tuple[int, ...]


@dataclass(frozen=True)
class RequirementRow:
    statement: str
    quote: str
    section: str
    status: str
    band: Band
    marker: int | None


@dataclass(frozen=True)
class UndeterminedRow:
    subject: str
    why: str
    check: str
    marker: int | None


@dataclass(frozen=True)
class View:
    pr_number: int
    title: str
    target: str
    risk: str
    summary: str
    confidence_line: str
    note: str
    checklist: tuple[CheckRow, ...]
    direct: tuple[FindingRow, ...]
    indirect: tuple[Group, ...]
    indirect_total: int
    indirect_shown: int
    global_rows: tuple[FindingRow, ...]
    stored_rows: tuple[FindingRow, ...]
    requirements: tuple[RequirementRow, ...]
    undetermined: tuple[UndeterminedRow, ...]
    undetermined_total: int
    not_checked: tuple[str, ...]
    migration_steps: tuple[str, ...]
    snapshot: tuple[tuple[str, str, bool], ...]       # (label, value, value is an identifier)
    units: tuple[Unit, ...]


# ------------------------------------------------------------------------------------------- helpers
def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _item_key(item: ReportItem) -> tuple[float, str, str]:
    return (-item.confidence, clean(item.label).casefold(), item.id)


def _is_plain(text: str) -> bool:
    """A flow name safe for the body: not an id and not a path."""
    return not (RAW_ID.search(text) or FILE_PATH.search(text))


def _headline_parts(report: Report) -> str:
    """The summary sentence(s) of the headline without the title line and without a leading `Risk: X.`.

    The title and the risk label are always rebuilt from `pr_number`, `pr_title` and `risk_level`, so a headline
    that disagrees with `risk_level` can never put two different levels on the page (the lint flags it).
    """
    lines = [clean(line) for line in report.headline.splitlines() if line.strip()]
    if lines and _TITLE_PREFIX.match(lines[0]):
        lines = lines[1:]
    if lines:
        lines[0] = _RISK_PREFIX.sub("", lines[0])
    return " ".join(line for line in lines if line)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _snapshot_fields(report: Report) -> tuple[tuple[str, str, bool], ...]:
    s = report.snapshot
    crawl = None
    if s.crawl_actions is not None or s.crawl_screens is not None:
        crawl = " and ".join(p for p in (
            None if s.crawl_actions is None else _plural(s.crawl_actions, "action"),
            None if s.crawl_screens is None else _plural(s.crawl_screens, "screen")) if p)
    behind = None if s.base_behind_main_commits is None else _plural(s.base_behind_main_commits, "commit")
    pairs: list[tuple[str, str | None, bool]] = [
        ("Target", s.target, True), ("Repository", s.repo, True), ("Indexed commit", s.indexed_commit, True),
        ("PR base", s.pr_base_sha, True), ("PR head", s.pr_head_sha, True),
        ("PR base behind main", behind, False), ("Deployed vs indexed", s.deployed_vs_indexed, False),
        ("Crawl bundle", s.crawl_bundle_hash, True), ("Crawl size", crawl, False),
        ("Spec sha", s.spec_sha, True), ("Documentation version", s.doc_version, False),
        ("App version", s.app_version, False),
    ]
    return tuple((k, clean(v), code) for k, v, code in pairs if v not in (None, ""))


def _note(report: Report) -> str:
    s = report.snapshot
    parts = []
    if s.deployed_vs_indexed:
        parts.append(sentence(f"Deployed app compared with the indexed code: {clean(s.deployed_vs_indexed)}"))
    if s.base_behind_main_commits:
        parts.append(f"The PR branch is {_plural(s.base_behind_main_commits, 'commit')} behind main, so its diff "
                     "may not apply as shown.")
    return " ".join(parts)


def _evidence_view(evidence_id: str, evidence: Mapping[str, Mapping[str, Any]]) -> EvidenceView:
    rec = evidence.get(evidence_id)
    if not rec:
        return EvidenceView(id=evidence_id, found=False)
    extras = tuple((k, clean(rec[k])) for k in EVIDENCE_EXTRAS if rec.get(k) not in (None, ""))
    return EvidenceView(id=evidence_id, found=True, kind=clean(rec.get("kind")), ref=clean(rec.get("ref")),
                        summary=clean(rec.get("summary")), span=clean(rec.get("span_id")), extras=extras)


class _Builder:
    def __init__(self, report: Report, evidence: Mapping[str, Mapping[str, Any]]):
        self.report = report
        self.evidence = evidence
        self.units: dict[str, Unit] = {}
        self.claims = sorted(report.claims, key=lambda c: c.id)
        self.matched_claims: set[str] = set()

    def _evidence_for(self, ids: list[str], claim_ids: list[str]) -> tuple[EvidenceView, ...]:
        return tuple(_evidence_view(e, self.evidence) for e in sorted({*ids, *claim_ids}))

    def _match(self, candidates: set[str], prefix: str | None, evidence_ids: list[str], band: Band) -> list[Claim]:
        """Claims that state this item's sentence: same text, the producer's `<label>: <text>` form, a
        quoted-statement prefix, or the same band citing only evidence the item cites."""
        own = set(evidence_ids)
        found = []
        for claim in self.claims:
            text = _norm(clean(claim.text))
            by_text = text in candidates or (prefix is not None and text.startswith(prefix))
            by_evidence = bool(claim.evidence_ids) and claim.band is band and set(claim.evidence_ids) <= own
            if by_text or by_evidence:
                found.append(claim)
        self.matched_claims.update(c.id for c in found)
        return found

    def _register(self, key: str, build_unit: Callable[[int], Unit], has_evidence: bool, *,
                  force: bool = False) -> int | None:
        unit = self.units.get(key)
        if unit is None:
            if not has_evidence and not force:
                return None
            unit = build_unit(len(self.units) + 1)
            self.units[key] = unit
        return unit.number

    # ---- items
    def item_marker(self, item: ReportItem, section: str, *, overflow: bool = False) -> int | None:
        text = clean(item.what_changed)
        candidates = {_norm(text)} | {_norm(f"{clean(name)}: {text}") for name in (item.label, item.screen) if name}
        claims = self._match(candidates, None, item.evidence_ids, item.band)
        claim_evidence = [e for c in claims for e in c.evidence_ids]

        def build(number: int) -> Unit:
            return Unit(
                number=number, section=section, title=_item_title(item), text=text, band=item.band,
                confidence=item.confidence, claim_id=claims[0].id if claims else None,
                claim_text=clean(claims[0].text) if claims else None,
                evidence=self._evidence_for(list(item.evidence_ids), claim_evidence),
                files=tuple(sorted({clean(f) for f in item.files})),
                requirement_ids=tuple(sorted({clean(r) for r in item.requirement_ids})),
                flows=tuple(clean(f) for f in item.flows), overflow=overflow)

        return self._register(f"item:{item.id}", build, bool(item.evidence_ids or claim_evidence),
                              force=overflow)

    def finding_row(self, item: ReportItem, section: str, *, overflow: bool = False) -> FindingRow:
        label = clean(item.label) or clean(item.screen) or "(unnamed)"
        screen = clean(item.screen)
        where = screen if screen and screen.casefold() != label.casefold() else None
        flows = tuple(f for f in (clean(x) for x in item.flows) if f and _is_plain(f))
        return FindingRow(title=label, where=where, text=clean(item.what_changed), band=item.band, flows=flows,
                          marker=self.item_marker(item, section, overflow=overflow))

    # ---- requirements and undetermined
    def requirement_row(self, req: RequirementRisk) -> RequirementRow:
        statement = clean(req.statement)
        quoted = "“" + _norm(statement.replace("“", '"').replace("”", '"')) + "”"
        claims = self._match({_norm(statement)}, quoted, req.evidence_ids, req.band)
        claim_evidence = [e for c in claims for e in c.evidence_ids]
        key = f"req:{req.requirement_id}:{req.status}"

        def build(number: int) -> Unit:
            return Unit(
                number=number, section="requirements", title=clean(req.section) or "Requirement",
                text=statement, band=req.band, confidence=None, claim_id=claims[0].id if claims else None,
                claim_text=clean(claims[0].text) if claims else None,
                evidence=self._evidence_for(list(req.evidence_ids), claim_evidence),
                requirement_ids=(clean(req.requirement_id),))

        marker = self._register(key, build, bool(req.evidence_ids or claim_evidence))
        return RequirementRow(statement=statement, quote=clean(req.quote).replace("“", '"').replace("”", '"'),
                              section=clean(req.section), status=STATUS_WORD[req.status], band=req.band,
                              marker=marker)

    def undetermined_row(self, und: Undetermined, *, overflow: bool = False) -> UndeterminedRow:
        key = "und:" + stable_id("und", und.code, und.subject, und.why, *sorted(und.evidence_ids))
        why = clean(und.why)

        def build(number: int) -> Unit:
            return Unit(
                number=number, section="undetermined", title=clean(und.subject), text=why, band=None,
                confidence=None, claim_id=None, claim_text=None,
                evidence=self._evidence_for(list(und.evidence_ids), []),
                code=clean(und.code), overflow=overflow)

        marker = self._register(key, build, bool(und.evidence_ids), force=overflow)
        return UndeterminedRow(subject=clean(und.subject), why=why, check=clean(und.what_to_check),
                               marker=marker)

    def orphan_claims(self) -> None:
        for claim in sorted(self.report.claims, key=lambda c: c.id):
            if claim.id in self.matched_claims:
                continue
            key = f"claim:{claim.id}"
            self._register(key, lambda n, c=claim: Unit(
                number=n, section="claim", title="Claim", text=clean(c.text), band=c.band, confidence=None,
                claim_id=c.id, claim_text=None, evidence=self._evidence_for(list(c.evidence_ids), [])),
                True)                    # an unmatched claim is listed even when it cites nothing


def _item_title(item: ReportItem) -> str:
    label = clean(item.label)
    screen = clean(item.screen)
    if label and screen and screen.casefold() != label.casefold():
        return f"{label} (on {screen})"
    return label or screen or "(unnamed)"


# -------------------------------------------------------------------------------------------- build
def _index_items(report: Report) -> tuple[dict[str, ReportItem], dict[str, str]]:
    """Every item by id (first one wins) and the section key it sits in."""
    items: dict[str, ReportItem] = {}
    sections: dict[str, str] = {}
    for key, tier_items in (("direct", report.direct), ("indirect", report.indirect),
                            ("global", report.global_changes), ("stored_data", report.stored_data)):
        for item in tier_items:
            items.setdefault(item.id, item)
            sections.setdefault(item.id, key)
    return items, sections


def _checklist_rows(report: Report, b: _Builder, items: dict[str, ReportItem],
                    sections: dict[str, str]) -> tuple[CheckRow, ...]:
    """Checklist lines point at the footnote numbers of the items they name (numbers already assigned)."""
    rows = []
    for entry in report.checklist:
        numbers = {b.item_marker(items[i], sections[i]) for i in dict.fromkeys(entry.item_ids) if i in items}
        rows.append(CheckRow(text=clean(entry.text), why=clean(entry.why),
                             markers=tuple(sorted(n for n in numbers if n is not None))))
    return tuple(rows)


def build_view(report: Report, evidence: Mapping[str, Mapping[str, Any]] | None = None) -> View:
    b = _Builder(report, evidence or {})
    items, sections = _index_items(report)
    ranked = sorted(report.indirect, key=lambda i: (-i.confidence, clean(i.section).casefold() or "~",
                                                    clean(i.label).casefold(), i.id))
    shown, cut = ranked[:TIER2_CAP], ranked[TIER2_CAP:]
    undetermined = list(report.undetermined)       # the producer ranks these; the order is its decision
    und_shown, und_cut = undetermined[:UNDETERMINED_TOP], undetermined[UNDETERMINED_TOP:]
    requirements = sorted(report.requirements, key=lambda r: (
        STATUS_RANK[r.status], BAND_RANK[r.band], clean(r.section).casefold(), r.requirement_id))

    # footnotes are numbered in section order (3 to 8), so each table reads 1, 2, 3 and the appendix follows the
    # report; the checklist then points at the numbers of the items it names
    direct_rows = tuple(b.finding_row(i, "direct") for i in sorted(report.direct, key=_item_key))
    groups = tuple(Group(title=title, via=_via(members),
                         rows=tuple(b.finding_row(i, "indirect") for i in members))
                   for title, members in _group(shown))             # rows built group by group: reading order
    global_rows = tuple(b.finding_row(i, "global") for i in sorted(report.global_changes, key=_item_key))
    stored_rows = tuple(b.finding_row(i, "stored_data") for i in sorted(report.stored_data, key=_item_key))
    requirement_rows = tuple(b.requirement_row(r) for r in requirements)
    und_rows = tuple(b.undetermined_row(u) for u in und_shown)
    for item in cut:
        b.finding_row(item, "indirect", overflow=True)
    for und in und_cut:
        b.undetermined_row(und, overflow=True)
    checklist = _checklist_rows(report, b, items, sections)
    b.orphan_claims()

    return View(
        pr_number=report.pr_number, title=clean(report.pr_title), target=clean(report.target),
        risk=report.risk_level, summary=_headline_parts(report), confidence_line=clean(report.confidence_line),
        note=_note(report), checklist=checklist, direct=direct_rows, indirect=groups,
        indirect_total=len(ranked), indirect_shown=len(shown), global_rows=global_rows, stored_rows=stored_rows,
        requirements=requirement_rows, undetermined=und_rows, undetermined_total=len(undetermined),
        not_checked=tuple(t for t in (clean(x) for x in report.not_checked) if t),
        migration_steps=_migration_steps(report), snapshot=_snapshot_fields(report),
        units=tuple(b.units.values()))                     # numbered, so already in order


def _migration_steps(report: Report) -> tuple[str, ...]:
    """The manual migration checklist of "Stored data": the steps the producer already tied to stored-data items
    (so section 2 and section 6 say the same words), else the standard three."""
    stored = {i.id for i in report.stored_data}
    own = [clean(c.text) for c in report.checklist if c.item_ids and set(c.item_ids) <= stored]
    steps = list(dict.fromkeys(t for t in own if t))
    return tuple(steps[:5]) if steps else MIGRATION_STEPS


def _group(items: list[ReportItem]) -> list[tuple[str, list[ReportItem]]]:
    """The Tier 2 groups in reading order: the group with the strongest item first, the title breaking ties.

    `items` arrive strongest first and keep that order inside a group. Footnote numbers are assigned while the
    rows are built, so building them group by group makes the numbers read 1, 2, 3 down the page (a flat
    ranking would number a weak item of the first group after the strong item of the second).
    """
    buckets: dict[str, list[ReportItem]] = {}
    for item in items:
        buckets.setdefault(clean(item.section) or _OTHER_SCREENS, []).append(item)
    return sorted(buckets.items(), key=lambda kv: (-max(i.confidence for i in kv[1]), kv[0].casefold(), kv[0]))


def _via(members: list[ReportItem]) -> tuple[str, ...]:
    return tuple(sorted({clean(i.via) for i in members if clean(i.via)}, key=lambda v: (v.casefold(), v)))
