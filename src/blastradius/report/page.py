"""HTML writer: one self-contained file (REPORT_SPEC; cases 57, 61).

No script, no remote font, image or style sheet: a Content-Security-Policy meta tag forbids everything but inline
CSS and `data:` images, and the lint (`report.lint.lint_html`) checks the output for the same. The page works
without JavaScript: the checklist is native checkboxes, the appendix is a `<details>` element, footnote markers
are in-page anchors. Light and dark follow `prefers-color-scheme`; print always uses the light palette. Risk and
band are a word first and a colour or a shape second, never colour alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..config import PACKAGE_VERSION
from ..models import Band
from .escape import html_attr, html_text, sentence
from .md import band_label, legend
from .model import Report
from .view import (
    CheckRow,
    EvidenceView,
    FindingRow,
    Group,
    RequirementRow,
    UndeterminedRow,
    Unit,
    View,
    build_view,
)
from .vocab import (
    BAND_GLYPH,
    BAND_RANGE,
    BAND_WORD,
    GLOBAL_SENTENCE,
    NOT_CHECKED_TITLE,
    RISK_GLYPH,
    SECTION_TITLES,
    TIER2_CAP,
    UNDETERMINED_TOP,
)

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"

LIGHT = {
    "bg": "#ffffff", "fg": "#1f2328", "muted": "#4f5963", "line": "#d1d9e0", "soft": "#f6f8fa",
    "link": "#0550ae", "focus": "#0969da", "accent": "#0550ae", "gap": "#6e3fc0",
    "low-bg": "#dafbe1", "low-fg": "#0f5323", "low-bd": "#2e7d3e",
    "med-bg": "#fff2c2", "med-fg": "#5c3600", "med-bd": "#b07a00",
    "high-bg": "#ffdcd7", "high-fg": "#7d0a1c", "high-bd": "#c62828",
    "unk-bg": "#e8ecf0", "unk-fg": "#363e46", "unk-bd": "#6e7781",
    "conf-bg": "#0550ae", "conf-fg": "#ffffff", "like-bg": "#ddf4ff", "like-fg": "#0a3069", "like-bd": "#0969da",
    "poss-fg": "#0a3069", "poss-bd": "#0969da", "none-bg": "#e8ecf0", "none-fg": "#363e46", "none-bd": "#6e7781",
}
DARK = {
    "bg": "#0d1117", "fg": "#e6edf3", "muted": "#a1abb5", "line": "#3d444d", "soft": "#151b23",
    "link": "#79c0ff", "focus": "#58a6ff", "accent": "#58a6ff", "gap": "#b392f0",
    "low-bg": "#0f2d1c", "low-fg": "#7ee787", "low-bd": "#3fb950",
    "med-bg": "#3a2a00", "med-fg": "#f2cc60", "med-bd": "#d29922",
    "high-bg": "#3d0f14", "high-fg": "#ffa198", "high-bd": "#f85149",
    "unk-bg": "#21262d", "unk-fg": "#c9d1d9", "unk-bd": "#8b949e",
    "conf-bg": "#1f6feb", "conf-fg": "#ffffff", "like-bg": "#0c2d6b", "like-fg": "#cae8ff", "like-bd": "#58a6ff",
    "poss-fg": "#cae8ff", "poss-bd": "#58a6ff", "none-bg": "#21262d", "none-fg": "#c9d1d9", "none-bd": "#8b949e",
}
# (foreground token, background token) pairs the tests check for WCAG AA contrast in both palettes
CONTRAST_PAIRS = (
    ("fg", "bg"), ("muted", "bg"), ("muted", "soft"), ("link", "bg"), ("accent", "bg"), ("gap", "bg"),
    ("low-fg", "low-bg"), ("med-fg", "med-bg"), ("high-fg", "high-bg"), ("unk-fg", "unk-bg"),
    ("conf-fg", "conf-bg"), ("like-fg", "like-bg"), ("poss-fg", "bg"), ("none-fg", "none-bg"),
)


def _tokens(palette: Mapping[str, str]) -> str:
    return "".join(f"--{k}:{v};" for k, v in palette.items())


# A flex item and a table cell keep the width of their longest word (`min-width:auto`), and `overflow-wrap:break-word`
# does not lower that minimum: an unbroken URL or identifier then pushes the page wider than a phone. `anywhere`
# does lower it, so every box that holds a person's own words (headline, checklist, table cells) says so.
_CSS = """
:root{color-scheme:light dark;@@LIGHT@@}
@media (prefers-color-scheme:dark){:root{@@DARK@@}}
@media print{:root{@@LIGHT@@}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);
 font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
 overflow-wrap:break-word}
.wrap{max-width:62rem;margin:0 auto;padding:1.25rem 16px 3rem}
a{color:var(--link)}
:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
code{font:.85em ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-wrap:anywhere}
.sr{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0 0 0 0);border:0}
.eyebrow{margin:0;color:var(--muted);font-size:.85rem;letter-spacing:.04em;text-transform:uppercase}
h1{font-size:1.55rem;line-height:1.25;margin:.2rem 0 1rem;overflow-wrap:anywhere}
h2{font-size:1.25rem;line-height:1.3;margin:0 0 .75rem}
h3{font-size:1.02rem;margin:1.25rem 0 .4rem}
p{margin:.5rem 0}
.head .risk{display:flex;flex-wrap:wrap;gap:.5rem .9rem;align-items:center;margin:.25rem 0 .75rem}
.head .summary{margin:0;flex:1 1 18rem}
.head .summary,ul.checks li,ul.checks label>span,th,td{min-width:0;overflow-wrap:anywhere}
.pill{display:inline-flex;align-items:center;gap:.45rem;padding:.25rem .75rem;border-radius:6px;
 border:2px solid;font-weight:700;white-space:nowrap}
.risk-low .pill{background:var(--low-bg);color:var(--low-fg);border-color:var(--low-bd)}
.risk-medium .pill{background:var(--med-bg);color:var(--med-fg);border-color:var(--med-bd)}
.risk-high .pill{background:var(--high-bg);color:var(--high-fg);border-color:var(--high-bd)}
.risk-unknown .pill{background:var(--unk-bg);color:var(--unk-fg);border-color:var(--unk-bd)}
.note{color:var(--muted);font-size:.92rem}
section.block{border:1px solid var(--line);border-left:6px solid var(--accent);border-radius:8px;
 padding:1rem 1rem 1.1rem;margin:1.25rem 0;background:var(--bg)}
section.block.gaps{border-left-color:var(--gap)}
.legend,.lead{color:var(--muted);font-size:.92rem}
ul.checks{list-style:none;margin:.25rem 0;padding:0}
ul.checks li{margin:.5rem 0}
ul.checks label{display:flex;gap:.7rem;align-items:flex-start;cursor:pointer}
ul.checks input{width:1.2rem;height:1.2rem;margin:.2rem 0 0;flex:none}
.why{color:var(--muted)}
.via{margin:.25rem 0 .5rem}
.tbl{overflow-x:auto}
table{width:100%;border-collapse:collapse;margin:.5rem 0}
th,td{text-align:left;vertical-align:top;padding:.6rem .65rem;border-bottom:1px solid var(--line)}
thead th{font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
 border-bottom:2px solid var(--line)}
tbody th{font-weight:600}
.where{display:block;color:var(--muted);font-weight:400;font-size:.9rem}
.flows{display:block;color:var(--muted);font-size:.9rem}
.quote{display:block;color:var(--muted);font-size:.92rem;margin-top:.25rem}
.band{display:inline-flex;align-items:center;gap:.35rem;padding:.1rem .6rem;border-radius:999px;
 font-size:.85rem;font-weight:600;white-space:nowrap;border:1.5px solid transparent}
.band-confirmed{background:var(--conf-bg);color:var(--conf-fg);border-color:var(--conf-bg)}
.band-likely{background:var(--like-bg);color:var(--like-fg);border-color:var(--like-bd)}
.band-possible{background:transparent;color:var(--poss-fg);border:1.5px dashed var(--poss-bd)}
.band-unknown{background:var(--none-bg);color:var(--none-fg);border:1.5px dotted var(--none-bd)}
sup.fn{font-size:.75em;line-height:0}
sup.fn a{text-decoration:none;font-weight:700;padding:0 .12em}
.unsourced{color:var(--muted);font-size:.9em}
details.appendix{border:1px solid var(--line);border-radius:8px;margin:1.25rem 0;padding:.4rem 1rem 1rem}
details.appendix>summary{cursor:pointer;padding:.5rem 0}
details.appendix>summary h2{display:inline;margin:0}
dl.snap{display:grid;grid-template-columns:max-content 1fr;gap:.25rem 1rem;margin:.5rem 0}
dl.snap dt{color:var(--muted)}
dl.snap dd{margin:0;overflow-wrap:anywhere}
ol.ev{padding-left:1.4rem}
ol.ev>li{margin:1rem 0;padding-left:.25rem}
ol.ev ul{margin:.35rem 0;padding-left:1.1rem}
.sec{color:var(--muted);font-size:.85rem;text-transform:uppercase;letter-spacing:.04em}
.foot{color:var(--muted);font-size:.88rem}
@media (max-width:40rem){
 .wrap{padding-top:.75rem}
 section.block{padding:.85rem .8rem}
 table,thead,tbody,tr,th,td{display:block}
 thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
 tr{border:1px solid var(--line);border-radius:6px;margin:.6rem 0;padding:.3rem .7rem}
 th,td{border:0;padding:.3rem 0}
 td::before,tbody th::before{content:attr(data-label);display:block;font-size:.74rem;font-weight:600;
  text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
 dl.snap{grid-template-columns:1fr}
}
@page{margin:14mm}
@media print{
 body{font-size:11pt}
 .wrap{max-width:none;padding:0}
 section.block,details.appendix{border-color:#888}
 tr,ul.checks li,ol.ev>li{break-inside:avoid}
 details::details-content{content-visibility:visible;display:block}
 a{color:inherit}
}
""".replace("@@LIGHT@@", _tokens(LIGHT)).replace("@@DARK@@", _tokens(DARK))


def _band(band: Band) -> str:
    word = BAND_WORD[band]
    return (f'<span class="band band-{word}"><span aria-hidden="true">{BAND_GLYPH[band]}</span>'
            f"{band_label(band)}</span>")


def _marker(number: int | None, *, plain: bool = False) -> str:
    if number is None:
        return "" if plain else ' <span class="unsourced">(unsourced)</span>'
    return f' <sup class="fn"><a href="#ev-{number}" aria-label="Evidence {number}">{number}</a></sup>'


def _marks(numbers: tuple[int, ...]) -> str:
    return "".join(_marker(n, plain=True) for n in numbers)


def _table(caption: str, header: list[str], rows: list[list[str]], *, first_is_row_header: bool = True) -> str:
    head = "".join(f'<th scope="col">{html_text(h)}</th>' for h in header)
    body = []
    for cells in rows:
        tds = []
        for i, (label, content) in enumerate(zip(header, cells, strict=True)):
            tag = "th" if i == 0 and first_is_row_header else "td"
            scope = ' scope="row"' if tag == "th" else ""
            tds.append(f'<{tag}{scope} data-label="{html_attr(label)}">{content}</{tag}>')
        body.append("<tr>" + "".join(tds) + "</tr>")
    return (f'<div class="tbl"><table><caption class="sr">{html_text(caption)}</caption>'
            f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>")


def _finding_cells(row: FindingRow) -> list[str]:
    first = html_text(row.title)
    if row.where:
        first += f'<span class="where">on {html_text(row.where)}</span>'
    text = html_text(sentence(row.text)) + _marker(row.marker)
    if row.flows:
        text += f'<span class="flows">Flows: {html_text("; ".join(row.flows))}.</span>'
    return [first, text, _band(row.band)]


def _findings(caption: str, header_text: str, rows: tuple[FindingRow, ...], first: str = "Screen") -> str:
    return _table(caption, [first, header_text, "How sure"], [_finding_cells(r) for r in rows])


def _requirement_cells(row: RequirementRow) -> list[str]:
    statement = html_text(sentence(row.statement)) + _marker(row.marker)
    if row.quote:
        statement += f'<span class="quote">Spec says: “{html_text(row.quote)}”</span>'
    return [statement, html_text(row.section), html_text(row.status), _band(row.band)]


def _undetermined_cells(row: UndeterminedRow) -> list[str]:
    return [html_text(row.subject), html_text(sentence(row.why)) + _marker(row.marker, plain=True),
            html_text(sentence(row.check))]


def _section(key: str, body: str, *, gaps: bool = False) -> str:
    cls = "block gaps" if gaps else "block"
    return (f'<section class="{cls}" id="s-{key}" aria-labelledby="h-{key}">'
            f'<h2 id="h-{key}">{html_text(SECTION_TITLES[key])}</h2>{body}</section>')


# ------------------------------------------------------------------------------------------ sections
def _head(v: View) -> str:
    glyph = RISK_GLYPH.get(v.risk, "")
    quoted_title = f' "{html_text(v.title)}"' if v.title else ""
    parts = [f'<header class="head"><p class="eyebrow">{html_text(v.target)}</p>',
             f"<h1>Blast radius: PR #{v.pr_number}{quoted_title}</h1>",
             (f'<div class="risk risk-{v.risk.lower()}"><span class="pill"><span aria-hidden="true">{glyph}</span>'
             f"<span>Risk: {html_text(v.risk)}</span></span>")]
    if v.summary:
        parts.append(f'<p class="summary">{html_text(v.summary)}</p>')
    parts.append("</div>")
    if v.confidence_line:
        parts.append(f'<p class="confidence">{html_text(v.confidence_line)}</p>')
    if v.note:
        parts.append(f'<p class="note">{html_text(v.note)}</p>')
    return "".join(parts) + "</header>"


def _checklist_item(row: CheckRow) -> str:
    why = f' <span class="why"><strong>Why:</strong> {html_text(sentence(row.why))}</span>' if row.why else ""
    return (f'<li><label><input type="checkbox"><span>{html_text(sentence(row.text))}{why}'
            f"{_marks(row.markers)}</span></label></li>")


def _checklist(v: View) -> str:
    if not v.checklist:
        body = "<p>No re-test steps could be worked out; see what we could not determine, below.</p>"
    else:
        body = '<ul class="checks">' + "".join(_checklist_item(r) for r in v.checklist) + "</ul>"
    return _section("checklist", body)


def _legend() -> str:
    return f'<p class="legend">{html_text(legend())}</p>'


def _direct(v: View) -> str:
    if not v.direct:
        return _section("direct", "<p>Nothing in this PR's own code maps to a screen we could identify.</p>")
    body = ('<p class="lead">These screens and elements have their own code in this PR.</p>' + _legend()
            + _findings("Directly changed screens and elements", "What changed", v.direct))
    return _section("direct", body)


def _group(g: Group) -> str:
    via = (f'<p class="via">Connected through: {html_text("; ".join(g.via))}.</p>' if g.via else "")
    return (f"<h3>{html_text(g.title)}</h3>{via}"
            + _findings(f"Screens reached through shared code: {g.title}", "What may be affected", g.rows))


def _indirect(v: View) -> str:
    if not v.indirect:
        return _section("indirect", "<p>No other screen was reached through shared code.</p>")
    body = '<p class="lead">Screens that reuse code this PR changes, grouped by the part of the spec they belong to.</p>'
    if v.indirect_total > v.indirect_shown:
        body += (f'<p class="lead">Showing the {v.indirect_shown} strongest of {v.indirect_total} '
                 f"(at most {TIER2_CAP}); the rest are listed in the appendix.</p>")
    return _section("indirect", body + "".join(_group(g) for g in v.indirect))


def _global(v: View) -> str:
    if not v.global_rows:
        return ""
    return _section("global", f"<p>{html_text(GLOBAL_SENTENCE)}</p>"
                    + _findings("Shared pieces that changed", "What changed", v.global_rows, "Shared piece"))


def _stored(v: View) -> str:
    if not v.stored_rows:
        return ""
    steps = "".join(f'<li><label><input type="checkbox"><span>{html_text(s)}</span></label></li>'
                    for s in v.migration_steps)
    body = ("<p>This PR changes how something is saved. Check it by hand.</p>"
            + _findings("Saved data that changed", "What changed", v.stored_rows, "Saved data")
            + f'<ul class="checks">{steps}</ul>')
    return _section("stored_data", body)


def _requirements(v: View) -> str:
    if not v.requirements:
        return _section("requirements", "<p>No requirement was linked to an affected screen.</p>")
    header = ["What the spec requires", "Where in the spec", "Status", "How sure"]
    return _section("requirements", _table("Requirements at risk", header,
                                           [_requirement_cells(r) for r in v.requirements],
                                           first_is_row_header=False))


def _undetermined(v: View) -> str:
    if v.undetermined:
        header = ["Item", "Why we could not tell", "What a person should check"]
        body = _table("Things we could not determine", header, [_undetermined_cells(r) for r in v.undetermined])
        hidden = v.undetermined_total - len(v.undetermined)
        if hidden > 0:
            body += f'<p class="lead">{hidden} more (top {UNDETERMINED_TOP} shown) are listed in the appendix.</p>'
    else:
        body = ("<p>Nothing outstanding because no failures or items needing review were recorded for this PR. "
                "That is not a claim that nothing was missed; see what this report did not look at.</p>")
    body += f"<h3>{html_text(NOT_CHECKED_TITLE)}</h3>"
    if v.not_checked:
        body += "<ul>" + "".join(f"<li>{html_text(sentence(t))}</li>" for t in v.not_checked) + "</ul>"
    else:
        body += "<p>This run recorded nothing about what it did not look at.</p>"
    return _section("undetermined", body, gaps=True)


# ----------------------------------------------------------------------------------------- appendix
def _evidence_item(e: EvidenceView) -> str:
    if not e.found:
        return f"<li>Evidence <code>{html_text(e.id)}</code>: not found in this run's evidence ledger.</li>"
    head = f"Evidence <code>{html_text(e.id)}</code> ({html_text(e.kind)})"
    bits = [f"{head}: {html_text(sentence(e.summary))}" if e.summary else f"{head}."]
    if e.ref:
        bits.append(f"Reference <code>{html_text(e.ref)}</code>.")
    bits.extend(f"{html_text(k)} <code>{html_text(val)}</code>." for k, val in e.extras)
    if e.span:
        bits.append(f"Trace span <code>{html_text(e.span)}</code>.")
    return "<li>" + " ".join(bits) + "</li>"


def _codes(values: tuple[str, ...]) -> str:
    return ", ".join(f"<code>{html_text(v)}</code>" for v in values)


def _unit(u: Unit) -> str:
    section = "Claim not shown as a sentence above" if u.section == "claim" else SECTION_TITLES[u.section]
    band = ""
    if u.band is not None:
        conf = f" ({u.confidence:.2f})" if u.confidence is not None else ""
        band = f" {_band(u.band)}{html_text(conf)}"
    cut = " <em>Cut from the main report by its display cap.</em>" if u.overflow else ""
    lines = []
    if u.claim_id:
        said = f": {html_text(sentence(u.claim_text))}" if u.claim_text and u.claim_text != u.text else "."
        lines.append(f"<li>Claim <code>{html_text(u.claim_id)}</code>{said}</li>")
    if u.code:
        lines.append(f"<li>Reason code <code>{html_text(u.code)}</code>.</li>")
    lines.extend(_evidence_item(e) for e in u.evidence)
    if not u.evidence:
        lines.append("<li>No evidence was cited for this statement.</li>")
    if u.files:
        lines.append(f"<li>Files: {_codes(u.files)}.</li>")
    if u.requirement_ids:
        lines.append(f"<li>Requirements: {_codes(u.requirement_ids)}.</li>")
    if u.flows:
        lines.append(f"<li>Flows: {_codes(u.flows)}.</li>")
    return (f'<li id="ev-{u.number}" value="{u.number}"><span class="sec">{html_text(section)}</span><br>'
            f"<strong>{html_text(u.title)}</strong>: {html_text(sentence(u.text))}{band}{cut}"
            f"<ul>{''.join(lines)}</ul></li>")


def _appendix(v: View) -> str:
    snapshot = "".join(
        f"<dt>{html_text(k)}</dt><dd>{f'<code>{html_text(val)}</code>' if code else html_text(val)}</dd>"
        for k, val, code in v.snapshot)
    units = ('<ol class="ev">' + "".join(_unit(u) for u in v.units) + "</ol>" if v.units
             else "<p>No statement in this report cites evidence.</p>")
    bands = "".join(f"<li>{band_label(b)}: confidence {html_text(BAND_RANGE[b])}.</li>" for b in
                    (Band.CONFIRMED, Band.LIKELY, Band.POSSIBLE, Band.UNKNOWN))
    return (
        '<details class="appendix" id="appendix"><summary>'
        f'<h2 id="h-appendix">{html_text(SECTION_TITLES["appendix"])}</h2></summary>'
        "<p>Everything above cites the numbered entries below. File paths, ids and commit hashes appear only "
        "here.</p><h3>Snapshot</h3>"
        f'<dl class="snap">{snapshot}</dl><h3>Evidence behind each statement</h3>{units}'
        f"<h3>How the bands are set</h3><ul>{bands}</ul>"
        "<p>The bands are rules of thumb applied to how each link was found. They have not been validated "
        "against outcomes, so read them as an ordering, not as probabilities.</p>"
        f'<p class="foot">Generated by blastradius {html_text(PACKAGE_VERSION)}.</p></details>')


def page_from_view(v: View) -> str:
    title = f"Blast radius: PR #{v.pr_number} ({v.target})"
    body = "".join((_head(v), _checklist(v), _direct(v), _indirect(v), _global(v), _stored(v),
                    _requirements(v), _undetermined(v), _appendix(v)))
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f'<meta name="generator" content="blastradius {html_text(PACKAGE_VERSION)}">'
        f"<title>{html_text(title)}</title><style>{_CSS}</style></head>\n"
        f'<body><main class="wrap">{body}</main></body></html>\n')


def render_html(report: Report, evidence: Mapping[str, Mapping[str, Any]] | None = None) -> str:
    return page_from_view(build_view(report, evidence))
