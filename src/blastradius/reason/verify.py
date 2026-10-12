"""Stage 8: the deterministic verifier (D07, D21; cases 54, 55; REPORT_SPEC "Language rules").

Independent of the grader, the judge and the narrator: it reads only the finished `Report`, the run's evidence
ledger and the claim basis, and checks what a reader would be misled by:

  schema          the Report validates (round trip)
  citations       every evidence id an item, claim, requirement or could-not-determine row cites is in the
                  run's evidence ledger (the citation set)
  verbs           a claim's band is not stronger than the lowest band among the edges it cites, and its sentence
                  uses an allowed phrase for that band and none of the forbidden words; items likewise
                  (names taken from the page, set in “ ”, are stripped first)
  checklist       at most the maximum, cites existing items, and uses no word forbidden for the weakest band cited
  completeness    every direct (tier 1) item the retrieval found is present; tier 2 is capped
  body hygiene    no file path, evidence id or code term (node, edge, graph, closure, hop) and no HTML outside
                  the appendix fields; the risk level is in the first three headline lines
  risk            the level is the one `risk.derive_risk` gives for the items
  snapshot        the PR head sha is pinned (unless the PR itself could not be read)

Case 54: the verifier checks that a citation exists and that the claim does not outrun its citations, not that
the citation is *true*; rendering the witness path per claim and the offline evidence-only judge cover that.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from ..models import Band, band_for
from ..report.model import Report
from ..run import RunContext
from .risk import derive_risk
from .words import ALLOWED, BAND_ORDER, FORBIDDEN, lowest_band, strip_quoted

try:        # one vocabulary with the renderer and the lint (report.vocab says reason.verify should share it)
    from ..report.vocab import FILE_PATH as _PATH
    from ..report.vocab import HTML_TAG as _HTML
    from ..report.vocab import JARGON as _CODE_WORDS
    from ..report.vocab import RAW_ID as _ID
    from ..report.vocab import RAW_SHA as _SHA
    from ..report.vocab import verb_violations as _lint_verbs
except ImportError:         # pragma: no cover - the report package is another module's; keep a local copy
    _PATH = re.compile(r"(?:[\w.@~-]+/)+[\w.-]*\.(?:vue|tsx?|jsx?|mjs|cjs|json|scss|css|md|ya?ml|html)\b"
                       r"|\bpackages/|\bsrc/[\w-]+")
    _ID = re.compile(r"\b(?:ev|clm|itm|scr|el|req|flw|flow|gap|rev)_[0-9a-f]{6,}\b")
    _CODE_WORDS = re.compile(r"\b(?:node|edge|graph|closure|hop)s?\b", re.IGNORECASE)
    _HTML = re.compile(r"<\s*[A-Za-z/!][^>]*>")
    _SHA = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
    _lint_verbs = None


@dataclass(frozen=True)
class Violation:
    code: str
    where: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "where": self.where, "message": self.message}


def _rank(band: Band) -> int:
    return BAND_ORDER.index(band)


def _words(text: str) -> str:
    return strip_quoted(text).lower()


def check_verbs(text: str, band: Band, *, require_allowed: bool) -> list[str]:
    low = _words(text)
    out = []
    if require_allowed and not any(p in low for p in ALLOWED[band]):
        out.append(f"no allowed phrase for a {band.value} claim ({' / '.join(ALLOWED[band])})")
    for w in FORBIDDEN[band]:
        if re.search(rf"\b{re.escape(w)}\b", low):
            out.append(f"'{w}' is not allowed in a {band.value} claim")
    if _lint_verbs is not None:     # the renderer's lint also rejects close synonyms (may, possibly, ...)
        out.extend(f"'{p}' is not allowed in a {band.value} claim" for p in _lint_verbs(text, band)
                   if f"'{p}' is not allowed in a {band.value} claim" not in out)
    return out


def _body_fields(report: Report) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = [("headline", report.headline), ("confidence_line", report.confidence_line)]
    for i, c in enumerate(report.checklist):
        out += [(f"checklist[{i}].text", c.text), (f"checklist[{i}].why", c.why)]
    for tier in ("direct", "indirect", "global_changes", "stored_data"):
        for item in getattr(report, tier):
            out += [(f"{tier}:{item.id}.label", item.label), (f"{tier}:{item.id}.what_changed", item.what_changed)]
            if item.screen:
                out.append((f"{tier}:{item.id}.screen", item.screen))
            if item.via:
                out.append((f"{tier}:{item.id}.via", item.via))
            if item.section:
                out.append((f"{tier}:{item.id}.section", item.section))
            out += [(f"{tier}:{item.id}.flow", f) for f in item.flows]
    for i, u in enumerate(report.undetermined):
        out += [(f"undetermined[{i}].subject", u.subject), (f"undetermined[{i}].why", u.why),
                (f"undetermined[{i}].what_to_check", u.what_to_check)]
    out += [(f"not_checked[{i}]", t) for i, t in enumerate(report.not_checked)]
    return out


def verify(report: Report, run: RunContext, *, basis: Mapping[str, Sequence[Mapping[str, object]]],
           expected_direct: Iterable[str], tier2_cap: int, checklist_max: int,
           pinned: bool = True) -> list[Violation]:
    """Every violation of the rules in the module docstring (empty = the report may ship as is)."""
    v: list[Violation] = []
    try:
        Report.model_validate(report.model_dump(mode="json"))
    except ValidationError as exc:
        return [Violation("SCHEMA", "report", str(exc)[:300])]

    # citations
    def cites(where: str, ids: Iterable[str]) -> None:
        for ev in ids:
            if not run.evidence.exists(ev):
                v.append(Violation("CITATION_MISSING", where, f"evidence id {ev} is not in the ledger"))

    for tier in ("direct", "indirect", "global_changes", "stored_data"):
        for item in getattr(report, tier):
            cites(f"{tier}:{item.id}", item.evidence_ids)
            if band_for(item.confidence) is not item.band:
                v.append(Violation("BAND_MISMATCH", f"{tier}:{item.id}", "band does not match confidence"))
            for msg in check_verbs(item.what_changed, item.band, require_allowed=True):
                v.append(Violation("VERB", f"{tier}:{item.id}", msg))
    for r in report.requirements:
        cites(f"requirement:{r.requirement_id}", r.evidence_ids)
        if not r.quote.strip():
            v.append(Violation("QUOTE_MISSING", f"requirement:{r.requirement_id}", "a requirement has no quote"))
    for i, u in enumerate(report.undetermined):
        cites(f"undetermined[{i}]", u.evidence_ids)

    # claims: citations, basis, verbs
    for c in report.claims:
        cites(f"claim:{c.id}", c.evidence_ids)
        edges = list(basis.get(c.id, ()))
        if not c.evidence_ids or not edges:
            v.append(Violation("CLAIM_UNCITED", f"claim:{c.id}", "a claim cites no edge"))
        else:
            confs = [float(e["confidence"]) for e in edges]       # type: ignore[arg-type]
            floor = band_for(min(confs))
            if _rank(c.band) > _rank(floor):
                v.append(Violation("CLAIM_OVERSTATED", f"claim:{c.id}",
                                   f"claim is {c.band.value} but its weakest cited edge is {floor.value}"))
        for msg in check_verbs(c.text, c.band, require_allowed=True):
            v.append(Violation("VERB", f"claim:{c.id}", msg))

    # checklist
    if len(report.checklist) > checklist_max:
        v.append(Violation("CHECKLIST_LENGTH", "checklist", f"{len(report.checklist)} > {checklist_max}"))
    items = {i.id: i for t in ("direct", "indirect", "global_changes", "stored_data") for i in getattr(report, t)}
    for n, line in enumerate(report.checklist):
        unknown = [i for i in line.item_ids if i not in items]
        if unknown:
            v.append(Violation("CHECKLIST_REF", f"checklist[{n}]", f"cites unknown item(s) {unknown}"))
        band = lowest_band(items[i].band for i in line.item_ids if i in items)
        for msg in check_verbs(f"{line.text} {line.why}", band, require_allowed=False):
            v.append(Violation("VERB", f"checklist[{n}]", msg))

    # completeness
    have = {i.id for i in report.direct}
    for missing in sorted(set(expected_direct) - have):
        v.append(Violation("MISSING_TIER1", f"direct:{missing}", "a directly changed screen is not in the report"))
    if len(report.indirect) > tier2_cap:
        v.append(Violation("TIER2_UNCAPPED", "indirect", f"{len(report.indirect)} > {tier2_cap}"))

    # body hygiene
    for where, text in _body_fields(report):
        clean = strip_quoted(text)
        if _PATH.search(clean) or _ID.search(clean) or _SHA.search(clean):
            v.append(Violation("RAW_ID_OR_PATH", where, "a file path, id or hash appears outside the appendix"))
        if _CODE_WORDS.search(clean):
            v.append(Violation("CODE_TERM", where, "a code or graph term appears in the body"))
        if _HTML.search(text):
            v.append(Violation("HTML", where, "markup appears in a text field"))
    first = report.headline.splitlines()[:3]
    if not any(f"Risk: {report.risk_level}" in ln for ln in first):
        v.append(Violation("RISK_NOT_IN_HEADLINE", "headline", "the risk level is not in the first three lines"))
    if report.risk_level == "UNKNOWN" and not report.undetermined:
        v.append(Violation("UNKNOWN_WITHOUT_REASON", "undetermined", "risk is UNKNOWN but nothing says why"))

    # risk
    expected = derive_risk(report)
    if expected != report.risk_level:
        v.append(Violation("RISK_INCONSISTENT", "risk_level",
                           f"report says {report.risk_level}, its items give {expected}"))
    if pinned and not report.snapshot.pr_head_sha:       # an unreadable PR has no head to pin (said in the report)
        v.append(Violation("SNAPSHOT_UNPINNED", "snapshot", "the PR head sha is not recorded"))
    return v
