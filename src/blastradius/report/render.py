"""Stage 8 output: `report.md` and `report.html` from a `Report` (D29; REPORT_SPEC; cases 57, 61, 62).

`render` is the only function that touches the run: it reads the evidence records the appendix cites, writes
the two files, and records the lint verdict as a trace event (the pipeline decides what to do with errors).
The two documents come from one `view.build_view`, so they list the same items in the same order with the same
footnote numbers. The output depends only on the report and the evidence records, never on a clock or a hash
seed, so it is byte-identical across processes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..run import RunContext
from .lint import LintIssue, lint_html, lint_model, lint_rendered
from .md import markdown_from_view, render_markdown
from .model import Report
from .page import page_from_view, render_html
from .view import build_view

__all__ = ["cited_evidence_ids", "render", "render_html", "render_markdown"]


def cited_evidence_ids(report: Report) -> list[str]:
    """Every evidence id the report cites, sorted and unique."""
    def walk() -> Iterator[str]:
        for items in (report.direct, report.indirect, report.global_changes, report.stored_data):
            for item in items:
                yield from item.evidence_ids
        for req in report.requirements:
            yield from req.evidence_ids
        for und in report.undetermined:
            yield from und.evidence_ids
        for claim in report.claims:
            yield from claim.evidence_ids

    return sorted(set(walk()))


def _records(report: Report, run: RunContext) -> dict[str, dict[str, Any]]:
    found = ((e, run.evidence.get(e)) for e in cited_evidence_ids(report))
    return {e: rec for e, rec in found if rec is not None}


def render(report: Report, run: RunContext, *, out_dir: Path | None = None) -> dict[str, str]:
    """Write `report.md` and `report.html`; returns `{"md": path, "html": path}`.

    The default directory is `<run>/reason/<pr number>/`. The files are written even when the lint finds
    errors; the verdict goes to the trace so the caller can regenerate, or ship with the issues stated.
    """
    target = out_dir if out_dir is not None else run.path("reason", str(report.pr_number))
    target.mkdir(parents=True, exist_ok=True)
    with run.tracer.span("stage", "report.render", pr=report.pr_number) as span:
        evidence = _records(report, run)
        view = build_view(report, evidence)
        markdown = markdown_from_view(view)
        page = page_from_view(view)
        issues: list[LintIssue] = [*lint_model(report, known_evidence=run.evidence.ids()),
                                   *lint_rendered(markdown), *lint_html(page)]
        errors = sorted({i.rule for i in issues if i.severity == "error"})
        span.set(units=len(view.units), cited=len(cited_evidence_ids(report)), found=len(evidence),
                 lint_errors=len(errors), lint_warnings=sum(1 for i in issues if i.severity == "warning"),
                 lint_rules=errors)
        md_path, html_path = target / "report.md", target / "report.html"
        md_path.write_text(markdown, encoding="utf-8", newline="\n")
        html_path.write_text(page, encoding="utf-8", newline="\n")
    return {"md": str(md_path), "html": str(html_path)}
