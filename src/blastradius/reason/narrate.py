"""Stage 7c-ii: the narrator (D07, D21; cases 54, 55; REPORT_SPEC sections 1 and 2).

ONE structured call (`purpose="narrate_report"`, tier "mid", effort "low", no tools) turns structured facts
into the three things that need prose: the risk line of the headline, the confidence line, and the "what to
re-test first" checklist (<= 8 actions in the product's words, each ending in why). Everything else in the
report is already a template (`assemble`).

What the model sees: a JSON object of ids, bands, screen and control names, flow names, requirement sections
and counts, wrapped as untrusted data because the names come from the crawled page and the spec. No paths, no
ids it could be tempted to print, no diff. The PR title enters only inside its own `<untrusted_data>` wrapper,
as optional context, and the first headline line (which does contain the title) is composed by code, never by
the model. The system prompt carries the language-rules table (verbs per band) and
UNTRUSTED_PREAMBLE. The output is schema-validated and then checked by `validate_narration`; anything that does
not pass is replaced by the deterministic template, which writes a complete plain-language report with no model
at all (offline mode, no key, budget breaker, refusal, schema violation).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field

from ..llm import LLM
from ..models import Band, Strict
from ..report.model import ChecklistItem, Report, ReportItem
from ..safety import UNTRUSTED_PREAMBLE, find_secrets, untrusted
from .assemble import MIGRATION_STEPS, Assembly
from .risk import REMOVED_TEXT_VIA
from .thresholds import Thresholds
from .words import BAND_ORDER, FORBIDDEN, count_phrase, element_action, join_names, lowest_band, quoted

PROMPT_VERSION = "narrate-1"
PURPOSE = "narrate_report"
_NAMES_LIMIT = 3


class ChecklistLine(Strict):
    text: str = Field(max_length=400, description="an action a person can take, naming the screen")
    why: str = Field(max_length=300, description="why, in one plain sentence")
    item_ids: list[str] = Field(description="ids of the items this line is about, from the facts")


class Narration(Strict):
    risk_line: str = Field(max_length=500, description="starts with 'Risk: <LEVEL>.'")
    confidence_line: str = Field(max_length=400)
    checklist: list[ChecklistLine] = Field(max_length=8)


@dataclass
class NarrationResult:
    source: str                                  # "llm" | "template"
    risk_line: str
    confidence_line: str
    checklist: list[ChecklistItem]
    notes: list[str] = field(default_factory=list)


SYSTEM = (
    "You write the opening of a change-impact report for a QA lead who understands the product but not its code. "
    "You receive FACTS as JSON about one pull request: screens and controls (named as the product names them), "
    "bands of confidence, requirement sections, counts. Write only from the facts; never invent a screen, a "
    "name, a number, a file or an id.\n"
    "Produce:\n"
    "1. risk_line: one or two sentences that begin 'Risk: <LEVEL>.' using the level in the facts, name the "
    "area touched in the product's words, and give the counts of screens, flows and requirements exactly as "
    "given.\n"
    "2. confidence_line: one sentence on how many items are confirmed, probable and possible and how many could "
    "not be determined.\n"
    "3. checklist: at most 8 actions, most important first. Each says what a person should do on which screen, "
    "uses the control names given, and ends in a plain 'why'. Each cites item_ids from the facts.\n"
    "Language rules, by the band of the item a sentence is about:\n"
    "- confirmed: say 'will change' or 'is changed by this PR'; never 'might' or 'could'.\n"
    "- likely: say 'probably affected' or 'likely changes'; never 'will' or 'is certain'.\n"
    "- possible: say 'might be affected' or 'worth a quick look'; never 'will' or 'probably'.\n"
    "- unknown: say 'we cannot tell' or 'not determined'; claim no effect.\n"
    "Never write file paths, ids, or the words node, edge, graph, closure or hop. A sentence about several items "
    "uses the weakest of their bands. No markdown, no HTML.\n"
    + UNTRUSTED_PREAMBLE
)

_PATHISH = re.compile(r"(?:[\w.-]+/){2,}[\w.-]+|\b(?:ev|clm|itm|scr|el|req|flw|gap|rev)_[0-9a-f]{6,}\b")
_BAD_WORDS = re.compile(r"\b(?:node|edge|graph|closure|hop)s?\b", re.IGNORECASE)


# -------------------------------------------------------------------------------------------- facts
def build_facts(report: Report, asm: Assembly) -> dict[str, Any]:
    """The structured facts the narrator sees (module docstring): names and bands, no paths or diff."""

    def item(i: ReportItem) -> dict[str, Any]:
        return {
            "id": i.id, "tier": i.tier, "screen": i.screen or i.label, "band": i.band.value,
            "summary": i.what_changed, "shared_piece": i.via, "flows": i.flows,
            "controls": [name for _, name in asm.controls.get(i.id, [])][:5], "section": i.section,
        }

    bands = {b.value: 0 for b in (Band.CONFIRMED, Band.LIKELY, Band.POSSIBLE)}
    for i in [*report.direct, *report.indirect, *report.global_changes, *report.stored_data]:
        if i.band.value in bands:
            bands[i.band.value] += 1
    return {
        "pr_number": report.pr_number,
        "risk_level": report.risk_level,
        "counts": {k: asm.counts[k] for k in ("screens", "flows", "requirements")},
        "bands": bands,
        "could_not_determine": len(report.undetermined),
        "areas": areas(report),
        "items": [item(i) for i in [*report.direct, *report.global_changes, *report.stored_data,
                                    *report.indirect]],
        "requirements": [{"statement": r.statement, "section": r.section, "status": r.status,
                          "band": r.band.value} for r in report.requirements[:8]],
        "undetermined": [{"subject": u.subject, "why": u.why} for u in report.undetermined],
        "removed_wording": asm.removed_text[:5],
        "stored_data_steps": list(MIGRATION_STEPS) if report.stored_data else [],
    }


def areas(report: Report) -> list[str]:
    """Where the PR touches, in the spec's own section names, else in screen names."""
    sections = [r.section for r in report.requirements if r.section and r.status in ("at_risk", "loses_only_realization")]
    ordered: list[str] = []
    for s in sections:
        if s not in ordered:
            ordered.append(s)
    if not ordered:
        for i in [*report.direct, *report.indirect]:
            if i.screen and i.screen not in ordered:
                ordered.append(i.screen)
    return ordered[:3]


# ------------------------------------------------------------------------------------- the template
def _why(band: Band, subject: str) -> str:
    return {
        Band.CONFIRMED: f"{subject} is changed by this PR.",
        Band.LIKELY: f"{subject} is probably affected by this PR.",
        Band.POSSIBLE: f"{subject} might be affected by this PR; worth a quick look.",
    }.get(band, f"we cannot tell what this PR does to {subject}; not determined.")


def template_risk_line(report: Report, asm: Assembly) -> str:
    level, c = report.risk_level, asm.counts
    counts = (f"{count_phrase(c['screens'], 'screen')}, {count_phrase(c['flows'], 'user flow')}, "
              f"{count_phrase(c['requirements'], 'requirement')}.")
    if report.risk_level == "UNKNOWN":
        return (f"Risk: {level}. No screen we observed could be tied to this change, so nothing can be said about "
                "its effect.")
    if report.risk_level == "LOW" and not (report.direct or report.indirect or report.global_changes
                                           or report.stored_data):
        return f"Risk: {level}. Only files that do not change what users see were edited (tests, generated files, docs)."
    where = areas(report)
    touches = f"Touches {join_names(where, _NAMES_LIMIT)}. " if where else ""
    extras = []
    if report.global_changes:
        extras.append("It also changes code shared across the app.")
    if report.stored_data:
        extras.append("It also changes how data is saved.")
    return f"Risk: {level}. {touches}{counts}" + (" " + " ".join(extras) if extras else "")


def template_confidence_line(report: Report, asm: Assembly) -> str:
    n = {b: sum(1 for i in [*report.direct, *report.indirect, *report.global_changes, *report.stored_data]
                if i.band is b) for b in (Band.CONFIRMED, Band.LIKELY, Band.POSSIBLE)}
    undetermined = len(report.undetermined)
    tail = (f"{count_phrase(undetermined, 'thing')} could not be determined and "
            f"{'are' if undetermined != 1 else 'is'} listed below with the same weight as the findings.") \
        if undetermined else "Nothing is left undetermined."
    return (f"Confidence: {count_phrase(n[Band.CONFIRMED], 'item')} confirmed, {n[Band.LIKELY]} probable and "
            f"{n[Band.POSSIBLE]} possible. {tail}")


def template_checklist(report: Report, asm: Assembly, th: Thresholds) -> list[ChecklistItem]:
    """At most `checklist_max` actions, strongest band first; within a band removed wording, then direct items,
    then saved data (its three steps stay together), then shared code, then indirect screens."""
    ranked: list[tuple[tuple[int, int, float, str], list[ChecklistItem]]] = []

    def controls(i: ReportItem) -> str:
        return " and ".join(element_action(role, name) for role, name in asm.controls.get(i.id, [])[:2])

    def key(band: Band, group: int, conf: float, ident: str) -> tuple[int, int, float, str]:
        return (BAND_ORDER[::-1].index(band), group, -conf, ident)

    for wording in asm.removed_text[:2]:
        removed = [i for i in report.direct if i.via == REMOVED_TEXT_VIA]
        line = ChecklistItem(
            text=f"Look for the removed wording {quoted(wording)} across the screens listed below and check no "
                 "screen shows a raw text id instead.",
            why=_why(Band.CONFIRMED, "the screen that showed this wording"), item_ids=[i.id for i in removed])
        ranked.append((key(Band.CONFIRMED, 0, 1.0, wording), [line]))
    for i in report.direct:
        acts = controls(i)
        text = (f"On the {i.label}, {acts} and check it still behaves as before." if acts
                else f"Open the {i.label} and check it still behaves as before.")
        ranked.append((key(i.band, 1, i.confidence, i.id),
                       [ChecklistItem(text=text, why=_why(i.band, f"the {i.label}"), item_ids=[i.id])]))
    for s in report.stored_data:
        steps = [ChecklistItem(text=step, why=_why(s.band, s.label), item_ids=[s.id]) for step in MIGRATION_STEPS]
        ranked.append((key(s.band, 2, s.confidence, s.id), steps))
    for g in report.global_changes:
        pages = [i.label for i in [*report.direct, *report.indirect] if i.screen][:3]
        text = ("Click through the main screens of the app as a quick smoke test"
                + (f", starting with {join_names([quoted(p) for p in pages], 3)}." if pages else "."))
        ranked.append((key(g.band, 3, g.confidence, g.id),
                       [ChecklistItem(text=text, why=_why(g.band, "the shared code"), item_ids=[g.id])]))
    for i in report.indirect[:2]:
        ranked.append((key(i.band, 4, i.confidence, i.id), [ChecklistItem(
            text=f"Take a quick look at the {i.label}; it shares code this PR edits.",
            why=_why(i.band, f"the {i.label}"), item_ids=[i.id])]))
    out: list[ChecklistItem] = []
    for _, block in sorted(ranked, key=lambda r: r[0]):
        if len(out) + len(block) <= th.retrieval.checklist_max:
            out.extend(block)
    return out


def template_narration(report: Report, asm: Assembly, th: Thresholds, notes: list[str] | None = None) -> \
        NarrationResult:
    return NarrationResult("template", template_risk_line(report, asm), template_confidence_line(report, asm),
                           template_checklist(report, asm, th), notes or [])


# --------------------------------------------------------------------------------------- validation
def validate_narration(n: Narration, report: Report, th: Thresholds) -> list[str]:
    """Why a model-written narration cannot be used (empty list = usable)."""
    problems: list[str] = []
    known = {i.id for i in [*report.direct, *report.indirect, *report.global_changes, *report.stored_data]}
    bands = {i.id: i.band for i in [*report.direct, *report.indirect, *report.global_changes, *report.stored_data]}
    if f"Risk: {report.risk_level}" not in n.risk_line:
        problems.append("risk line does not state the registered risk level")
    if len(n.checklist) > th.retrieval.checklist_max:
        problems.append("checklist is longer than the maximum")
    texts = [n.risk_line, n.confidence_line, *[c.text + " " + c.why for c in n.checklist]]
    for t in texts:
        if _PATHISH.search(t):
            problems.append("a path or an id appears in the prose")
        if _BAD_WORDS.search(t):
            problems.append("a code or graph term appears in the prose")
        if "<" in t or "```" in t:
            problems.append("markup appears in the prose")
    for c in n.checklist:
        if not c.text.strip() or not c.why.strip():
            problems.append("a checklist line is empty")
        if any(i not in known for i in c.item_ids):
            problems.append("a checklist line cites an item that does not exist")
        band = lowest_band(bands[i] for i in c.item_ids if i in bands)
        low = re.sub("“[^”]*”", " ", (c.text + " " + c.why)).lower()
        for word in FORBIDDEN[band]:
            if re.search(rf"\b{re.escape(word)}\b", low):
                problems.append(f"'{word}' is not allowed for a {band.value} item")
    return sorted(set(problems))


# --------------------------------------------------------------------------------------------- stage
def narrate(llm: LLM | None, report: Report, asm: Assembly, th: Thresholds, *, pr_title: str = "",
            use_llm: bool = True) -> NarrationResult:
    """The model's narration when it passes validation, else the template (module docstring)."""
    if not use_llm or llm is None:
        return template_narration(report, asm, th, ["narration: template (no model requested)"])
    facts = build_facts(report, asm)
    # control, screen and flow names come from the crawled page and requirement text from the spec: all of it is
    # untrusted material, so the whole facts object is wrapped (the instructions live in the system prompt)
    user = "FACTS\n" + untrusted(json.dumps(facts, sort_keys=True, ensure_ascii=False, indent=1), "report-facts",
                                 max_chars=40000)
    if pr_title and not find_secrets(pr_title):
        user += "\n\nOptional context, the pull request's own title:\n" + untrusted(pr_title, "pr:title", max_chars=300)
    try:
        out = llm.structured(purpose=PURPOSE, tier="mid", system=SYSTEM, user=user, schema=Narration,
                             max_tokens=2000, effort="low", prompt_version=PROMPT_VERSION)
    except Exception as exc:  # noqa: BLE001 - any model failure falls back to the template
        return template_narration(report, asm, th, [f"narration: template ({type(exc).__name__})"])
    problems = validate_narration(out, report, th)
    if problems:
        return template_narration(report, asm, th, ["narration: template (model wording rejected: "
                                                    + "; ".join(problems) + ")"])
    checklist = [ChecklistItem(text=c.text.strip(), why=c.why.strip(), item_ids=list(c.item_ids))
                 for c in out.checklist]
    return NarrationResult("llm", out.risk_line.strip(), out.confidence_line.strip(), checklist)
