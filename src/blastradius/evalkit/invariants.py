"""Invariants of a produced report (plan section 9.1; D21, D22; cases 51, 54, 62).

A run that violates any of these is wrong regardless of its content: they are properties of the artifact, not
opinions about it. They are checked here against docs/REPORT_SPEC.md and the run's own evidence ledger, with
no LLM and no graph, so they can pick the good runs out of many (the stability and judge evals build on that).

  schema_valid            the JSON validates against report.model.Report (extra fields forbidden)
  citations_exist         every cited evidence id is a row of the run's evidence.jsonl
  claims_cite_evidence    every claim, finding item and requirement cites at least one id (REPORT_SPEC:
                          "every sentence in sections 3-7 cites one or more ledger ids")
  band_verb_consistent    a claim's wording matches its band (REPORT_SPEC language table)
  no_ids_or_paths_in_body no raw ids, file paths or graph jargon in sections 1-8
  ids_unique              no claim, item or requirement id is used for two rows (a footnote marker, `explain
                          <claim_id>` and the appendix key on the id)
  counts_distinct         counts in the headline are over DISTINCT screens/flows/requirements (case 51) and
                          no screen is listed twice

Independence (D21): the band table and the body patterns below are written from REPORT_SPEC, not imported from
the product's verifier, so a bug in the product's copy is caught here. When `report.vocab` and
`reason.verify.check_verbs` are importable their rules are applied as well and an offender is labelled with
the source that raised it; the product's rules can only add findings, never hide one of these.
"""

from __future__ import annotations

import importlib
import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..models import Band
from ..report.model import Report, ReportItem

OFFENDER_CAP = 50
_QUOTED = re.compile("“[^”]*”|\"[^\"]*\"")
_ID = re.compile(r"\b[a-z]{2,5}_[0-9a-f]{8,}\b")
_PATH = re.compile(
    r"(?:[\w@.~-]+/)+[\w.-]+\.(?:vue|tsx?|jsx?|mjs|cjs|json|ya?ml|scss|css|html|md|py)\b"
    r"|\b[\w-]+\.(?:vue|tsx?|scss)\b"
    r"|\b(?:packages|node_modules)/[\w@./-]+"
    r"|(?<![\w/])(?:src|components|helpers|services|composables|layouts|pages)/[\w@./-]+"
)
_JARGON = re.compile(r"\b(?:nodes?|edges?|graphs?|closures?|hops?)\b", re.IGNORECASE)

# REPORT_SPEC "Language rules": allowed phrases and forbidden words per band.
_ALLOWED: dict[Band, tuple[re.Pattern[str], ...]] = {
    Band.CONFIRMED: (re.compile(r"\bwill (?:be )?chang(?:e|ed)\b", re.IGNORECASE),
                     re.compile(r"\b(?:is|are) changed by this PR\b", re.IGNORECASE)),
    Band.LIKELY: (re.compile(r"\bprobably (?:affected|changes?)\b", re.IGNORECASE),
                  re.compile(r"\blikely (?:changes?|affected)\b", re.IGNORECASE)),
    Band.POSSIBLE: (re.compile(r"\bmight (?:also )?be affected\b", re.IGNORECASE),
                    re.compile(r"\bworth a quick look\b", re.IGNORECASE)),
    Band.UNKNOWN: (re.compile(r"\bwe cannot tell\b", re.IGNORECASE),
                   re.compile(r"\bnot determined\b", re.IGNORECASE)),
}
_FORBIDDEN: dict[Band, tuple[str, ...]] = {
    Band.CONFIRMED: ("might", "could"),
    Band.LIKELY: ("will", "is certain"),
    Band.POSSIBLE: ("will", "probably"),
    Band.UNKNOWN: ("will", "probably", "likely"),     # any claim of effect: only "we cannot tell" is left
}
_FORBIDDEN_RE = {b: tuple((w, re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE)) for w in ws)
                 for b, ws in _FORBIDDEN.items()}
_HEADLINE_COUNT = re.compile(r"\b(\d+)\s+(screens?|(?:user\s+)?flows?|requirements?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class InvariantResult:
    name: str
    passed: bool
    evaluated: bool = True
    offenders: tuple[str, ...] = ()
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "evaluated": self.evaluated,
                "offender_count": len(self.offenders), "offenders": list(self.offenders[:OFFENDER_CAP]),
                "detail": self.detail}


@dataclass(frozen=True)
class InvariantReport:
    subject: str
    results: tuple[InvariantResult, ...]

    @property
    def ok(self) -> bool:
        return all(r.evaluated and r.passed for r in self.results)

    @property
    def failed(self) -> list[str]:
        return [r.name for r in self.results if not (r.evaluated and r.passed)]

    def as_dict(self) -> dict[str, Any]:
        return {"subject": self.subject, "ok": self.ok, "failed": self.failed,
                "results": [r.as_dict() for r in self.results]}


def _product_vocab() -> Any | None:
    try:
        return importlib.import_module("blastradius.report.vocab")
    except ImportError:
        return None


def _plain(text: str) -> str:
    """Text without quoted names (a page or spec string is not our wording)."""
    return _QUOTED.sub(" ", text)


def _result(name: str, offenders: Iterable[str], detail: str = "") -> InvariantResult:
    found = tuple(sorted(set(offenders)))
    return InvariantResult(name, not found, True, found, detail)


# ---------------------------------------------------------------------------------------- schema
def _validate(raw: Mapping[str, Any] | Report) -> tuple[Report | None, InvariantResult]:
    if isinstance(raw, Report):
        return raw, InvariantResult("schema_valid", True)
    try:
        return Report.model_validate(raw), InvariantResult("schema_valid", True)
    except ValidationError as exc:      # locations and error types only: an input value may be page text
        offenders = [f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}" for e in exc.errors()]
        return None, InvariantResult("schema_valid", False, True, tuple(sorted(set(offenders))))


# ------------------------------------------------------------------------------------ citations
def _cited(report: Report) -> list[tuple[str, list[str]]]:
    sites: list[tuple[str, list[str]]] = []
    for name, items in (("direct", report.direct), ("indirect", report.indirect),
                        ("global_changes", report.global_changes), ("stored_data", report.stored_data)):
        sites += [(f"{name}[{i}] {it.id}", it.evidence_ids) for i, it in enumerate(items)]
    sites += [(f"claims[{i}] {c.id}", c.evidence_ids) for i, c in enumerate(report.claims)]
    sites += [(f"requirements[{i}] {r.requirement_id}", r.evidence_ids)
              for i, r in enumerate(report.requirements)]
    sites += [(f"undetermined[{i}] {u.code}", u.evidence_ids) for i, u in enumerate(report.undetermined)]
    return sites


def check_citations_exist(report: Report, evidence_ids: Collection[str]) -> InvariantResult:
    known = set(evidence_ids)
    return _result("citations_exist", (f"{where}: {eid}" for where, ids in _cited(report) for eid in ids
                                       if eid not in known),
                   detail=f"{sum(len(ids) for _, ids in _cited(report))} citations against "
                          f"{len(known)} evidence rows")


def check_claims_cite_evidence(report: Report) -> InvariantResult:
    must = [(f"claims[{i}] {c.id}", c.evidence_ids) for i, c in enumerate(report.claims)]
    for name, items in (("direct", report.direct), ("indirect", report.indirect),
                        ("global_changes", report.global_changes), ("stored_data", report.stored_data)):
        must += [(f"{name}[{i}] {it.id}", it.evidence_ids) for i, it in enumerate(items)]
    must += [(f"requirements[{i}] {r.requirement_id}", r.evidence_ids)
             for i, r in enumerate(report.requirements)]
    return _result("claims_cite_evidence", (f"{where}: cites nothing" for where, ids in must if not ids))


# ---------------------------------------------------------------------------------- band and verb
def _spec_violations(text: str, band: Band, *, require_phrase: bool) -> list[str]:
    plain = _plain(text)
    out = [f"forbidden word '{w}' for band {band.value}" for w, rx in _FORBIDDEN_RE[band] if rx.search(plain)]
    for other, patterns in _ALLOWED.items():
        if other is not band and any(p.search(plain) for p in patterns):
            out.append(f"wording of band {other.value} in a {band.value} sentence")
    if require_phrase and not any(p.search(plain) for p in _ALLOWED[band]):
        out.append(f"no {band.value} phrase (expected one of: "
                   f"{', '.join(p.pattern for p in _ALLOWED[band])})")
    return out


def _product_verifier() -> Any | None:
    try:
        return importlib.import_module("blastradius.reason.verify")
    except ImportError:
        return None


def _product_violations(text: str, band: Band, *, require_phrase: bool) -> list[str]:
    """What the product's own vocabulary and verifier say about the sentence (empty when not importable)."""
    out: list[str] = []
    fn = getattr(_product_vocab(), "verb_violations", None)
    if fn is not None:
        out += [f"report.vocab forbids '{v}' for band {band.value}" for v in fn(text, band)]
    check = getattr(_product_verifier(), "check_verbs", None)
    if check is not None:
        out += [f"reason.verify: {m}" for m in check(text, band, require_allowed=require_phrase)]
    return out


def check_band_verbs(report: Report) -> InvariantResult:
    offenders: list[str] = []
    for i, claim in enumerate(report.claims):
        where = f"claims[{i}] {claim.id}"
        offenders += [f"{where}: {v}" for v in _spec_violations(claim.text, claim.band, require_phrase=True)]
        offenders += [f"{where}: {v}"
                      for v in _product_violations(claim.text, claim.band, require_phrase=True)]
    items: list[ReportItem] = [*report.direct, *report.indirect, *report.global_changes, *report.stored_data]
    for it in items:
        where = f"item {it.id}"
        for v in (*_spec_violations(it.what_changed, it.band, require_phrase=False),
                  *_product_violations(it.what_changed, it.band, require_phrase=False)):
            offenders.append(f"{where}: {v}")
    return _result("band_verb_consistent", offenders,
                   detail=f"{len(report.claims)} claims, {len(items)} items; product rules: report.vocab "
                          f"{'applied' if _product_vocab() else 'not importable'}, reason.verify "
                          f"{'applied' if _product_verifier() else 'not importable'}")


# --------------------------------------------------------------------------------------- body text
def _body_fields(report: Report) -> list[tuple[str, str, bool]]:
    """(location, text, quoted_source) for every field a QA lead reads in sections 1-8.

    `quoted_source` marks verbatim spec text (a requirement's statement and quote): only ids are flagged
    there, because a spec may legitimately say "export collection.json" or "graph"."""
    out: list[tuple[str, str, bool]] = [("headline", report.headline, False),
                                        ("confidence_line", report.confidence_line, False)]
    out += [(f"checklist[{i}].text", c.text, False) for i, c in enumerate(report.checklist)]
    out += [(f"checklist[{i}].why", c.why, False) for i, c in enumerate(report.checklist)]
    for name, items in (("direct", report.direct), ("indirect", report.indirect),
                        ("global_changes", report.global_changes), ("stored_data", report.stored_data)):
        for i, it in enumerate(items):
            for fld in ("screen", "label", "what_changed", "section", "via"):
                value = getattr(it, fld)
                if value:
                    out.append((f"{name}[{i}].{fld}", value, False))
            out += [(f"{name}[{i}].flows", f, False) for f in it.flows]
    for i, r in enumerate(report.requirements):
        out += [(f"requirements[{i}].statement", r.statement, True),
                (f"requirements[{i}].quote", r.quote, True),
                (f"requirements[{i}].section", r.section, False)]
    for i, u in enumerate(report.undetermined):
        out += [(f"undetermined[{i}].subject", u.subject, False), (f"undetermined[{i}].why", u.why, False),
                (f"undetermined[{i}].what_to_check", u.what_to_check, False)]
    out += [(f"not_checked[{i}]", t, False) for i, t in enumerate(report.not_checked)]
    out += [(f"claims[{i}].text", c.text, False) for i, c in enumerate(report.claims)]
    return out


def _body_hits(text: str, quoted_source: bool) -> list[str]:
    plain = _plain(text)
    hits = [f"id '{m.group(0)}'" for m in _ID.finditer(plain)]
    if not quoted_source:
        hits += [f"path '{m.group(0)}'" for m in _PATH.finditer(plain)]
        hits += [f"jargon '{m.group(0).lower()}'" for m in _JARGON.finditer(plain)]
    vocab = _product_vocab()
    if vocab is not None:
        hits += [f"report.vocab id '{m.group(0)}'" for m in vocab.RAW_ID.finditer(plain)]
        if not quoted_source:
            hits += [f"report.vocab path '{m.group(0)}'" for m in vocab.FILE_PATH.finditer(plain)]
            hits += [f"report.vocab jargon '{m.group(0).lower()}'" for m in vocab.JARGON.finditer(plain)]
    return hits


def check_body_clean(report: Report) -> InvariantResult:
    offenders = [f"{where}: {hit}" for where, text, quoted in _body_fields(report)
                 for hit in _body_hits(text, quoted)]
    return _result("no_ids_or_paths_in_body", offenders)


# ------------------------------------------------------------------------------------- counts
def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _distinct_screens(items: Iterable[ReportItem]) -> int:
    return len({_norm(i.screen) for i in items if i.screen})


def _distinct_flows(items: Iterable[ReportItem]) -> int:
    return len({_norm(f) for i in items for f in i.flows if f})


def _headline_counts(headline: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for n, noun in _HEADLINE_COUNT.findall(headline):
        kind = "screens" if noun.lower().startswith("screen") else (
            "flows" if "flow" in noun.lower() else "requirements")
        out.setdefault(kind, int(n))
    return out


def _flat_items(report: Report) -> list[ReportItem]:
    return [*report.direct, *report.indirect, *report.global_changes, *report.stored_data]


def check_ids_unique(report: Report) -> InvariantResult:
    """Ids are content hashes (rule 4): two rows with one id are a collision, whatever their text says."""
    offenders: list[str] = []
    for label, ids in (("item", [it.id for it in _flat_items(report)]),
                       ("claim", [c.id for c in report.claims]),
                       ("requirement", [r.requirement_id for r in report.requirements])):
        offenders += [f"{label} id used for {n} rows: {i}" for i, n in Counter(ids).items() if n > 1]
    return _result("ids_unique", offenders)


def check_counts_distinct(report: Report) -> InvariantResult:
    offenders: list[str] = []
    items = [("direct", report.direct), ("indirect", report.indirect),
             ("global_changes", report.global_changes), ("stored_data", report.stored_data)]
    flat = [it for _, group in items for it in group]
    offenders += [f"(tier, screen, label) repeated {n} times: {k}" for k, n in
                  Counter((it.tier, _norm(it.screen or ""), _norm(it.label)) for it in flat).items() if n > 1]
    for it in flat:
        offenders += [f"item {it.id} cites {eid} {n} times"
                      for eid, n in Counter(it.evidence_ids).items() if n > 1]
    item_ids = {it.id for it in flat}
    for i, c in enumerate(report.checklist):
        offenders += [f"checklist[{i}] points at unknown item {iid}"
                      for iid in c.item_ids if iid not in item_ids]
    headline = _headline_counts(report.headline)
    req_ids = {r.requirement_id for r in report.requirements}
    candidates = {
        "screens": {_distinct_screens([*report.direct, *report.indirect]), _distinct_screens(flat)},
        "flows": {_distinct_flows([*report.direct, *report.indirect]), _distinct_flows(flat)},
        "requirements": {len(req_ids), len(req_ids | {r for it in flat for r in it.requirement_ids})},
    }
    for kind, stated in sorted(headline.items()):
        if stated not in candidates[kind]:
            offenders.append(f"headline says {stated} {kind}; distinct count is "
                             f"{' or '.join(str(c) for c in sorted(candidates[kind]))}")
    return _result("counts_distinct", offenders,
                   detail=f"headline counts checked: {sorted(headline) or 'none'}")


# ------------------------------------------------------------------------------------------ entry
def check_report(raw: Mapping[str, Any] | Report, evidence_ids: Collection[str], *,
                 subject: str = "") -> InvariantReport:
    """Run every invariant. When the schema is invalid the content invariants are `evaluated=False` (and so
    the report is not ok): there is no trustworthy structure to read them from."""
    report, schema = _validate(raw)
    names = ("citations_exist", "claims_cite_evidence", "band_verb_consistent", "no_ids_or_paths_in_body",
             "ids_unique", "counts_distinct")
    if report is None:
        skipped = tuple(InvariantResult(n, False, False, detail="not evaluated: schema invalid")
                        for n in names)
        return InvariantReport(subject, (schema, *skipped))
    return InvariantReport(subject, (
        schema,
        check_citations_exist(report, evidence_ids),
        check_claims_cite_evidence(report),
        check_band_verbs(report),
        check_body_clean(report),
        check_ids_unique(report),
        check_counts_distinct(report),
    ))
