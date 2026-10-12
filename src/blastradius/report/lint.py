"""Deterministic report lint (REPORT_SPEC "Required lint checks"; D22, D29; cases 57, 61, 62).

It replaces any LLM "is this report useful" judge: every rule is a pattern or a count, so the same report always
gets the same verdict. Three entry points:

  lint(report)             model checks + the rendered Markdown + the rendered HTML
  lint_rendered(markdown)  everything that can be checked from the Markdown alone (CLI-free, no run needed)
  lint_html(html)          remote content, CSP, scripts, and the same body hygiene on the visible text

Severity is `error` for a rule of REPORT_SPEC and `warning` for a smell that a person should look at. Quoted
text (the PR title, spec quotes) is not ours, so it is exempt from the hygiene and verb rules.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Literal

from pydantic import ConfigDict

from ..models import Strict, band_for
from .md import render_markdown
from .model import Report
from .page import render_html
from .view import build_view
from .vocab import (
    BAND_RANK,
    BAND_WORD,
    BAND_WORDS,
    CHECKLIST_MAX,
    FILE_PATH,
    FINDING_KEYS,
    HEADLINE_MAX_LINES,
    HTML_TAG,
    JARGON,
    MAIN_BODY_WORD_BUDGET,
    NOT_CHECKED_TITLE,
    OPTIONAL_SECTIONS,
    RAW_ID,
    RAW_SHA,
    REMOTE_URL,
    SECTION_KEYS,
    SECTION_TITLES,
    TIER2_CAP,
    UNDETERMINED_TOP,
    verb_violations,
)

Severity = Literal["error", "warning"]
_KEY_BY_TITLE = {title: key for key, title in SECTION_TITLES.items()}
_BAND_FOR_WORD = {word: band for band, word in BAND_WORD.items()}
_RISK = re.compile(r"\bRisk:\s*\**\s*(LOW|MEDIUM|HIGH|UNKNOWN)\b", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_CHECKBOX = re.compile(r"^\s*[-*+]\s+\[[ xX]\]\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+\S")
_MARKER = re.compile(r"\[\^(\w+)\](?!:)")
_DEFINITION = re.compile(r"^\[\^(\w+)\]:", re.MULTILINE)
_NOTHING = re.compile(r"nothing outstanding because\s+\S", re.IGNORECASE)
_QUOTED = re.compile(r"“[^”]*”")
_CODE_SPAN = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_UNESCAPE = re.compile(r"\\(.)")
_WORD = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
_ESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_FN_REF = re.compile(r"\[\^\w+\]")


class LintIssue(Strict):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: str
    severity: Severity
    message: str
    where: str = ""

    def __str__(self) -> str:
        loc = f" [{self.where}]" if self.where else ""
        return f"{self.severity.upper()} {self.rule}{loc}: {self.message}"


def _issue(rule: str, message: str, *, where: str = "", severity: Severity = "error") -> LintIssue:
    return LintIssue(rule=rule, severity=severity, message=message, where=where)


def errors(issues: Iterable[LintIssue]) -> list[LintIssue]:
    return [i for i in issues if i.severity == "error"]


def _dedupe(issues: Iterable[LintIssue]) -> list[LintIssue]:
    seen: set[tuple[str, str]] = set()
    out = []
    for issue in issues:
        key = (issue.rule, issue.where or issue.message)
        if key not in seen:
            seen.add(key)
            out.append(issue)
    return out


# ------------------------------------------------------------------------------------- markdown parse
@dataclass
class _Block:
    level: int
    title: str
    h2: str                                  # title of the enclosing level-2 section ("" before the first)
    lines: list[str] = field(default_factory=list)


def _parse(markdown: str) -> list[_Block]:
    blocks = [_Block(0, "", "")]
    in_fence = False
    h2 = ""
    for line in markdown.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match:
            level, title = len(match.group(1)), match.group(2).strip()
            if level <= 2:
                h2 = title if level == 2 else ""
            blocks.append(_Block(level, title, h2 if level >= 2 else ""))
        else:
            blocks[-1].lines.append(line)
    return blocks


def _h2_blocks(blocks: list[_Block]) -> list[_Block]:
    return [b for b in blocks if b.level == 2]


def _section_lines(blocks: list[_Block], title: str) -> list[str]:
    """Lines of the level-2 section `title` including its sub-sections."""
    out: list[str] = []
    for b in blocks:
        if b.h2 == title and b.level >= 2:
            out.extend(b.lines)
    return out


def _body_lines(blocks: list[_Block]) -> list[str]:
    appendix = SECTION_TITLES["appendix"]
    out: list[str] = []
    for b in blocks:
        if b.h2 == appendix:
            break
        if b.level == 0 or b.level >= 2:
            out.append(("#" * b.level + " " + b.title) if b.level else "")
            out.extend(b.lines)
    return out


def _appendix_lines(blocks: list[_Block]) -> list[str]:
    return _section_lines(blocks, SECTION_TITLES["appendix"])


def _unescape(text: str) -> str:
    return _UNESCAPE.sub(r"\1", text)


def _strip_code(text: str) -> str:
    return _CODE_SPAN.sub(" ", text)


def _cells(row: str) -> list[str]:
    inner = row.strip()
    inner = inner.removeprefix("|")
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [c.strip() for c in _ESCAPED_PIPE.split(inner)]


def _table_rows(lines: list[str]) -> Iterator[list[str]]:
    """Data rows of every table in `lines` (header and delimiter rows skipped)."""
    state = 0
    for line in lines:
        if not line.lstrip().startswith("|"):
            state = 0
            continue
        if state == 0:
            state = 1                    # header row
        elif state == 1:
            state = 2                    # delimiter row
        else:
            yield _cells(line)


def _excerpt(text: str, n: int = 60) -> str:
    text = _unescape(_FN_REF.sub("", text)).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# ------------------------------------------------------------------------------------- markdown rules
def _risk_first_lines(markdown: str) -> list[LintIssue]:
    first = [ln for ln in markdown.splitlines() if ln.strip()][:3]
    if any(_RISK.search(ln) for ln in first):
        return []
    return [_issue("risk-first-lines", "the risk level (LOW, MEDIUM, HIGH or UNKNOWN) is not in the first 3 "
                   "lines", where="headline")]


def _sections(blocks: list[_Block]) -> list[LintIssue]:
    out: list[LintIssue] = []
    present: list[str] = []
    for b in _h2_blocks(blocks):
        key = _KEY_BY_TITLE.get(b.title)
        if key is None:
            out.append(_issue("section-unexpected", f'unexpected section "{b.title}"; the titles and their '
                              "order are fixed by REPORT_SPEC", where=b.title))
        else:
            present.append(key)
    for key in SECTION_KEYS:
        if key not in present and key not in OPTIONAL_SECTIONS and key != "undetermined":
            out.append(_issue("section-missing", f'section "{SECTION_TITLES[key]}" is missing', where=key))
    order = [SECTION_KEYS.index(k) for k in present]
    if order != sorted(order):
        out.append(_issue("section-order", "sections are out of the fixed order of REPORT_SPEC", where="order"))
    if len(present) != len(set(present)):
        out.append(_issue("section-order", "a section appears more than once", where="duplicate"))
    return out


def _checklist(blocks: list[_Block]) -> list[LintIssue]:
    lines = _section_lines(blocks, SECTION_TITLES["checklist"])
    items = [m.group(1) for m in map(_CHECKBOX.match, lines) if m]
    out: list[LintIssue] = []
    if len(items) > CHECKLIST_MAX:
        out.append(_issue("checklist-max", f"the checklist has {len(items)} items; the limit is "
                          f"{CHECKLIST_MAX}", where="checklist"))
    for text in items:
        if "Why:" not in text:
            out.append(_issue("checklist-why", f'checklist item has no "Why:" clause: {_excerpt(text)}',
                              where=_excerpt(text), severity="warning"))
    return out


def _tables(blocks: list[_Block]) -> list[LintIssue]:
    out: list[LintIssue] = []
    for key in FINDING_KEYS:
        title = SECTION_TITLES[key]
        for cells in _table_rows(_section_lines(blocks, title)):
            where = _excerpt(cells[0])
            band_cell = _unescape(cells[-1]).replace("*", "").strip().lower() if len(cells) >= 3 else ""
            if band_cell not in BAND_WORDS:
                out.append(_issue("band-word", f'a row in "{title}" has no band word (confirmed, likely, '
                                  f"possible, unknown): {where}", where=f"{key}: {where}"))
                continue
            if not _MARKER.search(" ".join(cells)):
                out.append(_issue("uncited-sentence", f'a row in "{title}" cites no evidence: {where}',
                                  where=f"{key}: {where}"))
            sentence = cells[0] if key == "requirements" else cells[1]
            band = _BAND_FOR_WORD[band_cell]
            for phrase in verb_violations(_unescape(_FN_REF.sub("", sentence)), band):
                out.append(_issue("band-verb", f'"{phrase}" is not allowed for a {band_cell} item: {where}',
                                  where=f"{key}: {where}: {phrase}"))
    return out


def _subtree(blocks: list[_Block], index: int) -> list[_Block]:
    """The heading block at `index` and every deeper block that follows it."""
    level = blocks[index].level
    out = [blocks[index]]
    for b in blocks[index + 1:]:
        if b.level and b.level <= level:
            break
        out.append(b)
    return out


def _undetermined(blocks: list[_Block]) -> list[LintIssue]:
    title = SECTION_TITLES["undetermined"]
    at = [i for i, b in enumerate(blocks) if b.title == title and b.level >= 1]
    if not at:
        return [_issue("undetermined-section", f'the section "{title}" is missing', where="undetermined")]
    out: list[LintIssue] = []
    # the "did not look at" bullets are not findings: only rows, items or the explicit sentence count
    lines = [ln for b in _subtree(blocks, at[0]) if b.title != NOT_CHECKED_TITLE for ln in b.lines]
    has_rows = any(True for _ in _table_rows(lines)) or any(_BULLET.match(ln) for ln in lines)
    if not (has_rows or _NOTHING.search(_unescape(" ".join(lines)))):
        out.append(_issue("undetermined-section", "the section is empty: list what could not be determined, or "
                          'say "Nothing outstanding because ..."', where="undetermined"))
    finding_levels = [b.level for b in blocks if _KEY_BY_TITLE.get(b.title) in FINDING_KEYS and b.level >= 1]
    if finding_levels and blocks[at[0]].level > min(finding_levels):
        out.append(_issue("undetermined-weight", "could not determine has a deeper heading than the findings",
                          where="undetermined"))
    return out


def _not_checked(blocks: list[_Block]) -> list[LintIssue]:
    for b in blocks:
        if b.title == NOT_CHECKED_TITLE:
            if any(_BULLET.match(ln) for ln in b.lines):
                return []
            break
    return [_issue("not-checked", f'the list "{NOT_CHECKED_TITLE}" is missing or empty', where="not_checked")]


def _hygiene(text: str, *, where: str) -> list[LintIssue]:
    """Ids, hashes, paths and jargon in text that a QA lead reads (quoted material excluded)."""
    plain = _QUOTED.sub(" ", _unescape(_strip_code(_FN_REF.sub("", text))))
    out: list[LintIssue] = []
    for rule, rx, label in (("raw-id", RAW_ID, "an internal id"), ("raw-sha", RAW_SHA, "a commit hash"),
                            ("file-path", FILE_PATH, "a file path or package name"),
                            ("jargon", JARGON, "graph jargon")):
        m = rx.search(plain)
        if m:
            out.append(_issue(rule, f'{label} outside the appendix: "{m.group(0)}"', where=f"{where}: "
                              f"{m.group(0)}"))
    return out


def _body_hygiene(blocks: list[_Block]) -> list[LintIssue]:
    out: list[LintIssue] = []
    for line in _body_lines(blocks):
        if line.startswith("# "):            # the title quotes the PR title verbatim
            continue
        out.extend(_hygiene(line, where="body"))
    return out


def _markdown_hygiene(markdown: str) -> list[LintIssue]:
    plain = _strip_code(markdown)
    out: list[LintIssue] = []
    for m in HTML_TAG.finditer(re.sub(r"\\<", "", plain)):
        out.append(_issue("html-in-text", f"HTML in the Markdown: {m.group(0)[:40]}", where=m.group(0)[:40]))
        break
    m = REMOTE_URL.search(plain)
    if m:
        out.append(_issue("remote-url", f"a remote URL or image in the Markdown: {m.group(0)}",
                          where=m.group(0)))
    return out


def _word_budget(blocks: list[_Block]) -> list[LintIssue]:
    words = 0
    for b in blocks:
        if b.h2 == SECTION_TITLES["appendix"]:
            break
        for line in b.lines:
            if line.lstrip().startswith("|"):
                continue
            text = _FN_REF.sub(" ", _unescape(line))
            text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s*)?", "", text)
            words += len(_WORD.findall(text))
    if words > MAIN_BODY_WORD_BUDGET:
        return [_issue("word-budget", f"the main body has {words} words outside tables; the budget is "
                       f"{MAIN_BODY_WORD_BUDGET}", where="main body")]
    return []


def _footnotes(blocks: list[_Block]) -> list[LintIssue]:
    body = "\n".join(_body_lines(blocks))
    defined = set(_DEFINITION.findall("\n".join(_appendix_lines(blocks))))
    used = {m.group(1) for m in _MARKER.finditer(_strip_code(body))}
    return [_issue("footnote-unresolved", f"footnote [^{n}] has no entry in the appendix", where=f"[^{n}]")
            for n in sorted(used - defined, key=lambda s: (len(s), s))]


def lint_rendered(markdown: str) -> list[LintIssue]:
    """Every rule that can be checked from the Markdown text alone."""
    blocks = _parse(markdown)
    issues = [*_risk_first_lines(markdown), *_sections(blocks), *_checklist(blocks), *_tables(blocks),
              *_undetermined(blocks), *_not_checked(blocks), *_body_hygiene(blocks),
              *_markdown_hygiene(markdown), *_word_budget(blocks), *_footnotes(blocks)]
    return _dedupe(issues)


# --------------------------------------------------------------------------------------------- html
_REMOTE_ATTR = re.compile(
    r"(?i)\b(?:src|href|action|poster|data|formaction|srcset|ping|background)\s*=\s*[\"']?\s*"
    r"(?:https?:|ftp:|//|javascript:)")
_CSS_URL = re.compile(r"(?i)url\(\s*[\"']?\s*(?!data:)")
_ACTIVE_TAG = re.compile(r"(?i)<\s*(script|iframe|object|embed|link|form|base|frame|applet)\b")
_HANDLER = re.compile(r"(?i)<[^>]+\s(on[a-z]+)\s*=")
_REFRESH = re.compile(r"(?i)http-equiv\s*=\s*[\"']?refresh")
_IMG_NOT_DATA = re.compile(r"(?i)<img\b(?![^>]*\bsrc\s*=\s*[\"']?data:)")
_META = re.compile(r"(?is)<meta\b[^>]*>")
_CSP_HTTP_EQUIV = re.compile(r"(?i)http-equiv\s*=\s*[\"']?content-security-policy")
_CONTENT_ATTR = re.compile(r"(?is)\bcontent\s*=\s*\"([^\"]*)\"")
_APPENDIX_HTML = re.compile(r"(?is)<details class=\"appendix\".*?</details>")
_HEADING_HTML = re.compile(r"(?is)<h([1-6])\b[^>]*>(.*?)</h\1>")
_STYLE_HTML = re.compile(r"(?is)<style\b.*?</style>")
_H1_HTML = re.compile(r"(?is)<h1\b.*?</h1>")
_TAG = re.compile(r"<[^>]+>")


def lint_html(page: str) -> list[LintIssue]:
    """Active content, remote content and the CSP in the HTML file, plus hygiene on its visible text."""
    out: list[LintIssue] = []
    policies = [m.group(1) for tag in _META.findall(page) if _CSP_HTTP_EQUIV.search(tag)
                for m in [_CONTENT_ATTR.search(tag)] if m]
    if not any("default-src 'none'" in p for p in policies):
        out.append(_issue("csp-missing", "no Content-Security-Policy meta tag with default-src 'none'",
                          where="head"))
    for rule, rx, message in (
            ("remote-url", _REMOTE_ATTR, "a remote or script URL in an attribute"),
            ("remote-url", _CSS_URL, "a CSS url() that is not a data: URI"),
            ("remote-url", _IMG_NOT_DATA, "an image that is not a data: URI"),
            ("active-content", _ACTIVE_TAG, "a script, frame, link, form or base element"),
            ("active-content", _HANDLER, "an inline event handler"),
            ("active-content", _REFRESH, "a meta refresh")):
        m = rx.search(page)
        if m:
            out.append(_issue(rule, f"{message}: {m.group(0)[:50]}", where=m.group(0)[:50]))
    if "@import" in page:
        out.append(_issue("remote-url", "a CSS @import", where="@import"))
    levels = {html_lib.unescape(_TAG.sub("", m.group(2))).strip(): int(m.group(1))
              for m in _HEADING_HTML.finditer(page)}
    finding_levels = [levels[t] for k in FINDING_KEYS if (t := SECTION_TITLES[k]) in levels]
    gap_level = levels.get(SECTION_TITLES["undetermined"])
    if gap_level is not None and finding_levels and gap_level > min(finding_levels):
        out.append(_issue("undetermined-weight", "could not determine has a deeper heading than the findings",
                          where="undetermined"))
    visible = _APPENDIX_HTML.sub(" ", _STYLE_HTML.sub(" ", _H1_HTML.sub(" ", page)))
    text = html_lib.unescape(_TAG.sub("\n", visible))
    for line in text.splitlines():
        out.extend(_hygiene(line, where="html"))
    return _dedupe(out)


# --------------------------------------------------------------------------------------------- model
def _text_fields(report: Report) -> Iterator[tuple[str, str]]:
    yield "pr_title", report.pr_title
    yield "headline", report.headline
    yield "confidence_line", report.confidence_line
    for i, c in enumerate(report.checklist):
        yield f"checklist[{i}].text", c.text
        yield f"checklist[{i}].why", c.why
    for tier, items in (("direct", report.direct), ("indirect", report.indirect),
                        ("global_changes", report.global_changes), ("stored_data", report.stored_data)):
        for i, item in enumerate(items):
            for name in ("screen", "label", "what_changed", "section", "via"):
                yield f"{tier}[{i}].{name}", getattr(item, name) or ""
            for j, flow in enumerate(item.flows):
                yield f"{tier}[{i}].flows[{j}]", flow
    for i, r in enumerate(report.requirements):
        for name in ("statement", "quote", "section"):
            yield f"requirements[{i}].{name}", getattr(r, name)
    for i, u in enumerate(report.undetermined):
        for name in ("subject", "why", "what_to_check"):
            yield f"undetermined[{i}].{name}", getattr(u, name)
    for i, text in enumerate(report.not_checked):
        yield f"not_checked[{i}]", text
    for i, claim in enumerate(report.claims):
        yield f"claims[{i}].text", claim.text


def _cited_ids(report: Report) -> Iterator[tuple[str, str]]:
    for tier, items in (("direct", report.direct), ("indirect", report.indirect),
                        ("global_changes", report.global_changes), ("stored_data", report.stored_data)):
        for item in items:
            for e in item.evidence_ids:
                yield f"{tier}:{item.label}", e
    for r in report.requirements:
        for e in r.evidence_ids:
            yield f"requirement:{r.requirement_id}", e
    for u in report.undetermined:
        for e in u.evidence_ids:
            yield f"undetermined:{u.subject}", e
    for c in report.claims:
        for e in c.evidence_ids:
            yield f"claim:{c.id}", e


def _unshown_sentences(report: Report) -> list[LintIssue]:
    """Verbs in sentences that are not rows of a body table: items cut by a cap (they are in the appendix) and
    claims whose sentence differs from every sentence the report shows."""
    out: list[LintIssue] = []
    for unit in build_view(report).units:
        if unit.overflow and unit.band is not None:
            for phrase in verb_violations(unit.text, unit.band):
                out.append(_issue("band-verb", f'"{phrase}" is not allowed for a {unit.band.value} item: '
                                  f"{unit.text[:50]}", where=f"entry {unit.number}: {phrase}"))
    items = [*report.direct, *report.indirect, *report.global_changes, *report.stored_data]
    shown = {" ".join(i.what_changed.split()) for i in items} | {" ".join(r.statement.split())
                                                                  for r in report.requirements}
    prefixes = sorted({p for i in items for p in (i.label, i.screen or "") if p}, key=len, reverse=True)
    for c in sorted(report.claims, key=lambda c: c.id):
        text = " ".join(c.text.split())
        for p in prefixes:
            if text.startswith(p + ": "):
                text = text[len(p) + 2:]
                break
        if text in shown:
            continue
        for phrase in verb_violations(text, c.band):
            out.append(_issue("band-verb", f'"{phrase}" is not allowed for a {c.band.value} claim: {text[:50]}',
                              where=f"claim {c.id}: {phrase}"))
    return out


def lint_model(report: Report, *, known_evidence: Collection[str] | None = None) -> list[LintIssue]:
    """The rules that need the data model: raw HTML in a field (the renderer would escape it), band and
    confidence agreeing, the headline agreeing with the risk level, caps, and claims."""
    out: list[LintIssue] = []
    for name, text in _text_fields(report):
        if HTML_TAG.search(text):
            out.append(_issue("html-in-field", f"HTML tags in a text field: {text[:40]!r}", where=name))
    items = [*report.direct, *report.indirect, *report.global_changes, *report.stored_data]
    for item in items:
        if band_for(item.confidence) is not item.band:
            out.append(_issue("band-confidence", f"band {item.band.value} does not match confidence "
                              f"{item.confidence:.2f}", where=f"item {item.label}"))
    stated = {m.group(1).upper() for m in _RISK.finditer(report.headline)}
    if stated and report.risk_level not in stated:
        out.append(_issue("risk-consistency", f"the headline says {sorted(stated)} but risk_level is "
                          f"{report.risk_level}", where="headline"))
    lines = [ln for ln in report.headline.splitlines() if ln.strip()]
    if len(lines) > HEADLINE_MAX_LINES:
        out.append(_issue("headline-lines", f"the headline has {len(lines)} lines; the limit is "
                          f"{HEADLINE_MAX_LINES}", where="headline", severity="warning"))
    if not report.confidence_line.strip():
        out.append(_issue("confidence-line", "there is no sentence on confidence", where="confidence_line",
                          severity="warning"))
    if len(report.checklist) > CHECKLIST_MAX:
        out.append(_issue("checklist-max", f"the checklist has {len(report.checklist)} items; the limit is "
                          f"{CHECKLIST_MAX}", where="checklist"))
    known_items = {i.id for i in items}
    for i, entry in enumerate(report.checklist):
        for item_id in entry.item_ids:
            if item_id not in known_items:
                out.append(_issue("checklist-ref", f"checklist item {i + 1} refers to an unknown item",
                                  where=f"checklist[{i}]:{item_id}"))
    by_id = {i.id: i for i in items}
    for i, entry in enumerate(report.checklist):
        known = [by_id[x] for x in entry.item_ids if x in by_id]
        if known:
            weakest = max(known, key=lambda it: BAND_RANK[it.band]).band
            for phrase in verb_violations(f"{entry.text} {entry.why}", weakest):
                out.append(_issue("band-verb", f'"{phrase}" is not allowed in a checklist line about a '
                                  f"{weakest.value} item", where=f"checklist[{i}]: {phrase}"))
    ids = [i.id for i in items]
    for dup in sorted({x for x in ids if ids.count(x) > 1}):
        out.append(_issue("duplicate-id", f"two report items share the id {dup}", where=dup, severity="warning"))
    if len(report.indirect) > TIER2_CAP:
        out.append(_issue("tier2-cap", f"{len(report.indirect)} indirect items; only {TIER2_CAP} are shown in "
                          "the body, the rest go to the appendix", where="indirect", severity="warning"))
    if len(report.undetermined) > UNDETERMINED_TOP:
        out.append(_issue("undetermined-top", f"{len(report.undetermined)} could-not-determine items; only "
                          f"{UNDETERMINED_TOP} are shown in the body", where="undetermined", severity="warning"))
    if not [t for t in report.not_checked if t.strip()]:
        out.append(_issue("not-checked", f'the list "{NOT_CHECKED_TITLE}" is missing or empty',
                          where="not_checked"))
    for claim in report.claims:
        if not claim.evidence_ids:
            out.append(_issue("claim-no-evidence", "a claim cites no evidence", where=f"claim {claim.id}"))
    if known_evidence is not None:
        known = set(known_evidence)
        for owner, evidence_id in sorted(set(_cited_ids(report))):
            if evidence_id not in known:
                out.append(_issue("evidence-unknown", f"cited evidence {evidence_id} is not in the ledger",
                                  where=f"{owner}:{evidence_id}"))
    out.extend(_unshown_sentences(report))
    return _dedupe(out)


def lint(report: Report, *, known_evidence: Collection[str] | None = None) -> list[LintIssue]:
    """All checks on one report: the model, its Markdown rendering and its HTML rendering."""
    issues = [*lint_model(report, known_evidence=known_evidence), *lint_rendered(render_markdown(report)),
              *lint_html(render_html(report))]
    return _dedupe(issues)
