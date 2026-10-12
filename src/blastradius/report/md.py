"""Markdown writer: reads well in a terminal and on GitHub (REPORT_SPEC; cases 57, 61, 62).

Pure Markdown, no HTML tags. Every dynamic string goes through `escape.md_text` or `escape.md_code`. Section
titles are exactly those of REPORT_SPEC at heading level 2, so "could not determine" has the same weight as a
finding. Footnote markers `[^n]` resolve to the numbered entries of the appendix, the only place that carries
file paths, ids, commit hashes and edge methods.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..config import PACKAGE_VERSION
from ..models import Band
from .escape import md_code, md_text, sentence
from .model import Report
from .view import CheckRow, EvidenceView, FindingRow, RequirementRow, UndeterminedRow, Unit, View, build_view
from .vocab import (
    BAND_MEANING,
    BAND_RANGE,
    BAND_WORD,
    GLOBAL_SENTENCE,
    NOT_CHECKED_TITLE,
    SECTION_TITLES,
    TIER2_CAP,
    UNDETERMINED_TOP,
)

SECTION_NAME = {**SECTION_TITLES, "claim": "Claim not shown as a sentence above"}


def band_label(band: Band) -> str:
    return BAND_WORD[band].capitalize()


def legend() -> str:
    parts = [f"{band_label(b)} ({BAND_MEANING[b]})" for b in (Band.CONFIRMED, Band.LIKELY, Band.POSSIBLE,
                                                               Band.UNKNOWN)]
    return "How sure: " + ", ".join(parts) + "."


def _marks(numbers: tuple[int, ...] | list[int]) -> str:
    return "".join(f"[^{n}]" for n in numbers)


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out.extend("| " + " | ".join(cells) + " |" for cells in rows)
    return out


def _cited(text: str, marker: int | None) -> str:
    return f"{text} [^{marker}]" if marker is not None else f"{text} (unsourced)"


def _finding_cells(row: FindingRow) -> list[str]:
    screen = f"**{md_text(row.title)}**" + (f" (on {md_text(row.where)})" if row.where else "")
    text = _cited(md_text(sentence(row.text)), row.marker)
    if row.flows:
        text += f" Flows: {md_text('; '.join(row.flows))}."
    return [screen, text, band_label(row.band)]


def _requirement_cells(row: RequirementRow) -> list[str]:
    quote = f" Spec says: “{md_text(row.quote)}”" if row.quote else ""
    return [_cited(md_text(sentence(row.statement)), row.marker) + quote, md_text(row.section),
            md_text(row.status), band_label(row.band)]


def _undetermined_cells(row: UndeterminedRow) -> list[str]:
    why = md_text(sentence(row.why))
    return [f"**{md_text(row.subject)}**", f"{why} [^{row.marker}]" if row.marker is not None else why,
            md_text(sentence(row.check))]


def _checklist_line(row: CheckRow) -> str:
    why = f" Why: {md_text(sentence(row.why))}" if row.why else ""
    marks = f" {_marks(row.markers)}" if row.markers else ""
    return f"- [ ] {md_text(sentence(row.text))}{why}{marks}"


def _findings(header: str, rows: tuple[FindingRow, ...], first: str = "Screen") -> list[str]:
    return _table([first, header, "How sure"], [_finding_cells(r) for r in rows])


# ------------------------------------------------------------------------------------------ sections
def _head(v: View) -> list[str]:
    quoted_title = f' "{md_text(v.title)}"' if v.title else ""
    out = [f"# Blast radius: PR #{v.pr_number}{quoted_title}", ""]
    out += [f"**Risk: {v.risk}.** {md_text(v.summary)}".rstrip(), ""]
    if v.confidence_line:
        out += [md_text(v.confidence_line), ""]
    if v.note:
        out += [md_text(v.note), ""]
    return out


def _checklist(v: View) -> list[str]:
    out = [f"## {SECTION_TITLES['checklist']}", ""]
    if v.checklist:
        out += [_checklist_line(r) for r in v.checklist]
    else:
        out.append("No re-test steps could be worked out; see what we could not determine, below.")
    return out + [""]


def _direct(v: View) -> list[str]:
    out = [f"## {SECTION_TITLES['direct']}", ""]
    if not v.direct:
        return out + ["Nothing in this PR's own code maps to a screen we could identify.", ""]
    out += ["These screens and elements have their own code in this PR.", "", legend(), ""]
    return out + _findings("What changed", v.direct) + [""]


def _indirect(v: View) -> list[str]:
    out = [f"## {SECTION_TITLES['indirect']}", ""]
    if not v.indirect:
        return out + ["No other screen was reached through shared code.", ""]
    out += ["Screens that reuse code this PR changes, grouped by the part of the spec they belong to.", ""]
    if v.indirect_total > v.indirect_shown:
        out += [(f"Showing the {v.indirect_shown} strongest of {v.indirect_total} (at most {TIER2_CAP}); "
                "the rest are listed in the appendix."), ""]
    for group in v.indirect:
        out += [f"### {md_text(group.title)}", ""]
        if group.via:
            out += [f"Connected through: {md_text('; '.join(group.via))}.", ""]
        out += _findings("What may be affected", group.rows) + [""]
    return out


def _global(v: View) -> list[str]:
    if not v.global_rows:
        return []
    return [f"## {SECTION_TITLES['global']}", "", GLOBAL_SENTENCE, ""] + _findings(
        "What changed", v.global_rows, "Shared piece") + [""]


def _stored(v: View) -> list[str]:
    if not v.stored_rows:
        return []
    out = [f"## {SECTION_TITLES['stored_data']}", "", "This PR changes how something is saved. Check it by hand.",
           ""]
    out += _findings("What changed", v.stored_rows, "Saved data") + [""]
    return out + [f"- [ ] {md_text(step)}" for step in v.migration_steps] + [""]


def _requirements(v: View) -> list[str]:
    out = [f"## {SECTION_TITLES['requirements']}", ""]
    if not v.requirements:
        return out + ["No requirement was linked to an affected screen.", ""]
    rows = [_requirement_cells(r) for r in v.requirements]
    return out + _table(["What the spec requires", "Where in the spec", "Status", "How sure"], rows) + [""]


def _undetermined(v: View) -> list[str]:
    out = [f"## {SECTION_TITLES['undetermined']}", ""]
    if v.undetermined:
        rows = [_undetermined_cells(r) for r in v.undetermined]
        out += _table(["Item", "Why we could not tell", "What a person should check"], rows) + [""]
        hidden = v.undetermined_total - len(v.undetermined)
        if hidden > 0:
            out += [f"{hidden} more (top {UNDETERMINED_TOP} shown) are listed in the appendix.", ""]
    else:
        out += [("Nothing outstanding because no failures or items needing review were recorded for this PR. "
                "That is not a claim that nothing was missed; see what this report did not look at."), ""]
    out += [f"### {NOT_CHECKED_TITLE}", ""]
    if v.not_checked:
        out += [f"- {md_text(sentence(t))}" for t in v.not_checked]
    else:
        out.append("This run recorded nothing about what it did not look at.")
    return out + [""]


# ----------------------------------------------------------------------------------------- appendix
def _evidence_line(e: EvidenceView) -> str:
    if not e.found:
        return f"- Evidence {md_code(e.id)}: not found in this run's evidence ledger."
    bits = [f"- Evidence {md_code(e.id)} ({md_text(e.kind)}): {md_text(sentence(e.summary))}"]
    if e.ref:
        bits.append(f"Reference {md_code(e.ref)}.")
    bits.extend(f"{md_text(k)} {md_code(val)}." for k, val in e.extras)
    if e.span:
        bits.append(f"Trace span {md_code(e.span)}.")
    return " ".join(bits)


def _band_note(u: Unit) -> str:
    if u.band is None:
        return ""
    confidence = f" ({u.confidence:.2f})" if u.confidence is not None else ""
    return f" {band_label(u.band)}{confidence}."


def _unit_lines(u: Unit) -> list[str]:
    cut = " Cut from the main report by its display cap." if u.overflow else ""
    out = [(f"[^{u.number}]: **{md_text(SECTION_NAME[u.section])}.** {md_text(u.title)}: "
            f"{md_text(sentence(u.text))}{_band_note(u)}{cut}"), ""]
    if u.claim_id:
        said = f": {md_text(sentence(u.claim_text))}" if u.claim_text and u.claim_text != u.text else "."
        out.append(f"    - Claim {md_code(u.claim_id)}{said}")
    if u.code:
        out.append(f"    - Reason code {md_code(u.code)}.")
    out.extend(f"    {_evidence_line(e)}" for e in u.evidence)
    if not u.evidence:
        out.append("    - No evidence was cited for this statement.")
    if u.files:
        out.append("    - Files: " + ", ".join(md_code(f) for f in u.files) + ".")
    if u.requirement_ids:
        out.append("    - Requirements: " + ", ".join(md_code(r) for r in u.requirement_ids) + ".")
    if u.flows:
        out.append("    - Flows: " + ", ".join(md_code(f) for f in u.flows) + ".")
    return out + [""]


def _appendix(v: View) -> list[str]:
    out = [f"## {SECTION_TITLES['appendix']}", "",
           ("Everything above cites the numbered entries below. File paths, ids and commit hashes appear only "
           "here."), "", "### Snapshot", ""]
    out += [f"- {md_text(k)}: {md_code(val) if code else md_text(val)}" for k, val, code in v.snapshot] + [""]
    out += ["### Evidence behind each statement", ""]
    if v.units:
        for u in v.units:
            out += _unit_lines(u)
    else:
        out += ["No statement in this report cites evidence.", ""]
    out += ["### How the bands are set", ""]
    out += [f"- {band_label(b)}: confidence {BAND_RANGE[b]}." for b in
            (Band.CONFIRMED, Band.LIKELY, Band.POSSIBLE, Band.UNKNOWN)]
    out += ["", ("The bands are rules of thumb applied to how each link was found. They have not been validated "
            "against outcomes, so read them as an ordering, not as probabilities."), "",
            f"Generated by blastradius {PACKAGE_VERSION}."]
    return out


def markdown_from_view(v: View) -> str:
    lines: list[str] = []
    for part in (_head(v), _checklist(v), _direct(v), _indirect(v), _global(v), _stored(v), _requirements(v),
                 _undetermined(v), _appendix(v)):
        lines.extend(part)
    return "\n".join(lines).rstrip("\n") + "\n"


def render_markdown(report: Report, evidence: Mapping[str, Mapping[str, Any]] | None = None) -> str:
    return markdown_from_view(build_view(report, evidence))
