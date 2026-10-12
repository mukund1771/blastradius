"""Stage 7-8 orchestration: PR -> retrieve -> grade -> judge -> assemble -> narrate -> verify -> report (D07, D17,
D18, D21, D25, D29; cases 29, 39-45, 50-52, 54, 55, 61, 76).

`run_pr(run, cfg, pr_number, store, llm)` writes `<run>/reason/<pr>/`:

  report.json        the `Report` (plain data); report.md and report.html are written by `report.render` when
                     that module exists (feature-detected: its absence never loses report.json)
  impact.json        PR facts, seeds, hits with every route, units, items before the tier-2 cap, counts
  grade.json         per-seed signals, verdict, rounds and actions, judge summary, the thresholds in force
  claims.json        every claim with its cited edges, the verification outcome and which narrator wrote the prose
  needs_review.json  the full ranked could-not-determine list (the report shows the top rows)

Nothing in these files depends on a clock, a run id or an iteration order, so the same inputs give the same
bytes in any process. Every stage runs inside a span; failures use the closed `FailureCode` set.

An empty graph (no crawl or index loaded yet), an unreadable PR or an unreachable database never raises: the
report says what could not be determined and why, with risk UNKNOWN, so a person always gets a page.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any

from ..failures import FailureCode
from ..graph.store import GraphError
from ..ids import stable_id
from ..llm import LLM
from ..models import CodeIndex
from ..report.model import Report, SnapshotInfo, Undetermined
from ..run import RunContext
from ..targets import TargetConfig
from .assemble import (
    BASE_NOT_CHECKED,
    Assembly,
    assemble,
    build_units,
    unit_titles,
)
from .change import LocaleDiff
from .facts import Retrieval
from .grade import Grading, ReviewItem, grade, grade_locale
from .judge import run_judge
from .narrate import NarrationResult, narrate, template_narration
from .pr import (
    GhCli,
    GhClient,
    GhError,
    Notice,
    PRData,
    fetch_locale_diffs,
    fetch_pr,
    mark_snapshot,
    note_head_moved,
    recheck_head,
)
from .retrieve import retrieve
from .risk import derive_risk
from .thresholds import Thresholds, load_thresholds
from .verify import Violation, verify

MAX_HITS_DUMPED = 3000


def _write_json(path: Path, obj: Any) -> str:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(path)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _load_index(run: RunContext) -> CodeIndex | None:
    path = run.run_dir / "code" / "index.json"
    try:
        return CodeIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _can_call_llm(llm: LLM | None) -> bool:
    """Offline still tries (a cached answer is free); with no client and no key there is nothing to try. Only the
    process environment is looked at: this module never reads a .env file itself."""
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return llm is not None and (llm.offline or getattr(llm, "_client", None) is not None or has_key)


# ---------------------------------------------------------------------------------------- overlay
def _overlay(run: RunContext, cfg: TargetConfig, base: CodeIndex | None, pr: PRData,
             overlay_fn: Any) -> tuple[CodeIndex | None, str | None]:
    """The PR head overlay when the code engineer's module is there and works; otherwise why not."""
    fn = overlay_fn
    if fn is None:
        try:
            fn = importlib.import_module("blastradius.code.overlay").run_overlay
        except (ImportError, AttributeError):
            return None, "the head overlay is not available in this build"
    if base is None:
        return None, "no code index exists in this run, so the PR's added files cannot be attached"
    changed = sorted({p for f in pr.files for p in (f.path, f.previous_path) if p})
    try:
        index = fn(run, cfg, base, pr.number, pr.head_repo or cfg.code.repo, pr.head_sha, changed)
    except Exception as exc:  # noqa: BLE001 - the overlay is best effort; the base graph still answers
        return None, f"the head overlay failed: {type(exc).__name__}: {str(exc)[:160]}"
    if index.stats.get("pr_overlay") != "applied":
        return None, str(index.stats.get("pr_overlay_reason") or "the head overlay was not applied")
    return index, None


# ----------------------------------------------------------------------------------------- report
def _summary(run: RunContext, base: CodeIndex | None) -> dict[str, Any]:
    crawl = _load_json(run.run_dir / "crawl" / "summary.json")
    spec = _load_json(run.run_dir / "spec" / "stats.json")
    return {
        "bundle_hash": crawl.get("bundle_hash"), "crawl_actions": crawl.get("actions_spent"),
        "crawl_screens": crawl.get("screens"), "app_version": crawl.get("app_version") or spec.get("app_version"),
        "spec_sha": spec.get("spec_sha"), "doc_version": spec.get("doc_version"),
        "deployed_vs_indexed": (base.stats.get("deployed_vs_indexed") if base else None)
        or run.manifest.get("deployed_vs_indexed"),
    }


def _degraded(run: RunContext, cfg: TargetConfig, pr: PRData | None, number: int, code: str, subject: str,
              why: str, check: str, indexed: str, summary: dict[str, Any]) -> tuple[Report, Assembly]:
    """A complete, honest report for the case where nothing could be mapped (module docstring)."""
    ev = run.evidence.add("reason.degraded", f"{cfg.code.repo}#{number}:{code}", why)
    row = Undetermined(code=code, subject=subject, why=why, what_to_check=check, evidence_ids=[ev])
    snap = SnapshotInfo(
        target=cfg.name, repo=cfg.code.repo, indexed_commit=indexed or "unknown",
        pr_base_sha=pr.base_sha if pr else None, pr_head_sha=pr.head_sha if pr else None,
        crawl_bundle_hash=summary.get("bundle_hash"), spec_sha=summary.get("spec_sha"),
        doc_version=summary.get("doc_version"), app_version=summary.get("app_version"))
    report = Report(target=cfg.name, pr_number=number, pr_title=pr.title if pr else "", risk_level="UNKNOWN",
                    headline="", confidence_line="", undetermined=[row], not_checked=list(BASE_NOT_CHECKED),
                    snapshot=snap)
    report.risk_level = derive_risk(report)
    asm = Assembly(report=report, basis={}, review=[], expected_direct=[], overflow=[], impact={},
                   counts={"screens": 0, "flows": 0, "requirements": 0, "direct": 0, "indirect": 0,
                           "indirect_shown": 0, "global": 0, "stored_data": 0, "screen_nodes": 0},
                   controls={})
    return report, asm


def _headline(report: Report, risk_line: str) -> str:
    return f"Blast radius: PR #{report.pr_number} “{report.pr_title}”\n{risk_line}"


def _apply(report: Report, n: NarrationResult) -> None:
    report.headline = _headline(report, n.risk_line)
    report.confidence_line = n.confidence_line
    report.checklist = n.checklist


def _finish(run: RunContext, th: Thresholds, llm: LLM | None, report: Report, asm: Assembly, pr_title: str,
            *, pinned: bool = True) -> tuple[NarrationResult, list[Violation], list[dict[str, Any]]]:
    """Narrate, verify, regenerate once (template), then ship with a visible notice if it still fails."""
    attempts: list[dict[str, Any]] = []
    with run.stage("reason.narrate") as span:
        narration = narrate(llm, report, asm, th, pr_title=pr_title, use_llm=_can_call_llm(llm))
        span.set(source=narration.source)
    _apply(report, narration)
    with run.stage("reason.verify") as span:
        violations = _verify(run, report, asm, th, pinned)
        attempts.append({"narrator": narration.source, "violations": [v.as_dict() for v in violations]})
        if violations and narration.source != "template":
            note = "narration: template (model wording failed the verifier)"
            narration = template_narration(report, asm, th, [note])
            _apply(report, narration)
            violations = _verify(run, report, asm, th, pinned)
            attempts.append({"narrator": "template", "violations": [v.as_dict() for v in violations]})
        span.set(violations=len(violations))
    if violations:
        run.failures.record(FailureCode.VERIFIER_REJECTED,
                            f"{len(violations)} verifier violation(s): "
                            + "; ".join(f"{v.code}@{v.where}" for v in violations[:5]),
                            pr=report.pr_number, violations=[v.as_dict() for v in violations[:20]])
        report.confidence_line += (f" Notice: this report did not pass its own automatic checks "
                                   f"({len(violations)} problem(s)); treat the wording as unchecked.")
    return narration, violations, attempts


def _verify(run: RunContext, report: Report, asm: Assembly, th: Thresholds, pinned: bool) -> list[Violation]:
    return verify(report, run, basis=asm.basis, expected_direct=asm.expected_direct,
                  tier2_cap=th.retrieval.tier2_cap, checklist_max=th.retrieval.checklist_max, pinned=pinned)


def _head_notice(report: Report, text: str) -> None:
    report.undetermined.insert(0, Undetermined(
        code="NEEDS_HUMAN", subject="the whole PR", why=text,
        what_to_check="Run the report again for the new commit before acting on this one.",
        evidence_ids=[]))
    report.confidence_line += " Notice: the PR changed after this analysis began."


# --------------------------------------------------------------------------------------------- stage
def run_pr(run: RunContext, cfg: TargetConfig, pr_number: int, store: Any, llm: LLM | None, *,
           gh: GhClient | None = None, overlay_fn: Any = None, render: bool = True) -> dict[str, str]:
    """Blast-radius report for one pull request (module docstring). Returns the paths of what was written."""
    th = load_thresholds()
    out_dir = run.path("reason", str(pr_number), ".keep").parent
    gh = gh or GhCli()
    with run.stage("reason", pr=pr_number) as span:
        base = _load_index(run)
        summary = _summary(run, base)
        indexed = base.snapshot_id if base else ""
        extra: list[ReviewItem] = []
        pr: PRData | None = None
        retr: Retrieval | None = None
        grading: Grading | None = None
        judge: dict[str, Any] = {"skipped": "not run"}
        locale_diffs: list[LocaleDiff] = []
        asm: Assembly | None = None
        report: Report | None = None
        try:
            pr = fetch_pr(run, cfg, gh, pr_number, compare_to=indexed or cfg.code.ref)
        except GhError as exc:
            report, asm = _degraded(
                run, cfg, None, pr_number, FailureCode.NEEDS_HUMAN.value, "this pull request",
                f"the pull request could not be read from GitHub ({str(exc)[:160]})",
                "Check that the PR number is right and that the GitHub CLI is signed in, then run the report again.",
                indexed, summary)
        if pr is not None:
            report, asm, retr, grading, judge, locale_diffs = _analyse(
                run, cfg, store, llm, gh, pr, th, base, summary, indexed, overlay_fn, extra)
        assert report is not None and asm is not None
        title = pr.title if pr else ""
        narration, violations, attempts = _finish(run, th, llm, report, asm, title, pinned=pr is not None)
        if pr is not None:
            check = recheck_head(gh, pr)
            if check.moved:
                note_head_moved(run, pr, check)
                _head_notice(report, next(n.message for n in pr.notices if n.code == "PR_HEAD_MOVED"))
        span.set(risk=report.risk_level, violations=len(violations))
        paths = _write(run, out_dir, report, asm, pr, retr, grading, judge, th, narration, attempts,
                       locale_diffs, render)
    return paths


def _analyse(run: RunContext, cfg: TargetConfig, store: Any, llm: LLM | None, gh: GhClient, pr: PRData,
             th: Thresholds, base: CodeIndex | None, summary: dict[str, Any], indexed: str, overlay_fn: Any,
             extra: list[ReviewItem]) -> tuple[Report, Assembly, Retrieval | None, Grading | None,
                                                dict[str, Any], list[LocaleDiff]]:
    try:
        counts = store.counts()
    except GraphError as exc:
        report, asm = _degraded(run, cfg, pr, pr.number, FailureCode.GRAPH_CONN_FAILED.value,
                                "the code and screen database",
                                f"the database could not be read ({str(exc)[:120]})",
                                "Start the database (docker compose up) and run the report again.", indexed, summary)
        return report, asm, None, None, {"skipped": "database unavailable"}, []
    if not counts.get("CodeFile"):
        report, asm = _degraded(run, cfg, pr, pr.number, FailureCode.NEEDS_HUMAN.value, "the whole PR",
                                "no code has been indexed and loaded yet, so no change can be tied to a screen",
                                "Run index, load, link and crawl for this target, then run the report again.",
                                indexed, summary)
        return report, asm, None, None, {"skipped": "empty graph"}, []

    try:
        mark_snapshot(run, pr, store)
        overlay, why = _overlay(run, cfg, base, pr, overlay_fn)
        if why:
            pr.notices.append(Notice(code="OVERLAY_UNAVAILABLE", message=why))
        locale_diffs = fetch_locale_diffs(run, cfg, gh, pr)
        retr = retrieve(run, cfg, store, pr, locale_diffs, base_index=base, overlay=overlay, th=th)
        grading = grade(run, store, retr, base_index=base, th=th)
        extra.extend(grade_locale(retr, th))
        if not counts.get("Screen"):
            extra.append(ReviewItem(
                id=stable_id("rev", "pr", "NO_CRAWL"), code="NEEDS_HUMAN", reason_kind="NO_CRAWL",
                subject="the screens of the app", detail="no screens have been crawled yet, so changed code cannot "
                "be tied to what users see", what_to_check="Run the crawl and link stages, then run the report "
                "again.", rank=2))
        units, owner = build_units(store, cfg, retr)
        judge = {"skipped": "no model available"}
        if _can_call_llm(llm):
            judge = run_judge(run, llm, pr, retr, grading, unit_titles(units, owner), th)
        index = overlay if overlay is not None else base
        i18n = dict(index.i18n) if index is not None else {}
        added = [v for d in locale_diffs for v in (*d.added.values(), *[new for _, new in d.changed.values()])]
        with run.stage("reason.assemble") as span:
            asm = assemble(run, cfg, store, pr, retr, grading, th, units=units, owner=owner, i18n=i18n,
                           indexed_commit=indexed or "unknown", extra_review=extra, locale_added=added,
                           summary=summary)
            span.set(**asm.counts)
        asm.impact["judge"] = judge
        return asm.report, asm, retr, grading, judge, locale_diffs
    except GraphError as exc:
        report, asm = _degraded(run, cfg, pr, pr.number, FailureCode.GRAPH_CONN_FAILED.value,
                                "the code and screen database", f"a database read failed ({str(exc)[:120]})",
                                "Check the database and run the report again.", indexed, summary)
        return report, asm, None, None, {"skipped": "database error"}, []


def _write(run: RunContext, out_dir: Path, report: Report, asm: Assembly, pr: PRData | None,
           retr: Retrieval | None, grading: Grading | None, judge: dict[str, Any], th: Thresholds,
           narration: NarrationResult, attempts: list[dict[str, Any]], locale_diffs: list[LocaleDiff],
           render: bool) -> dict[str, str]:
    paths: dict[str, str] = {"dir": str(out_dir)}
    hits = sorted((h for h in (retr.hits if retr else []) if h.live_routes()), key=lambda h: h.key)
    impact: dict[str, Any] = {
        "pr": pr.model_dump(mode="json", exclude={"files": {"__all__": {"patch"}}}) if pr else None,
        "counts": asm.counts, "units": asm.impact.get("units", {}),
        "seeds": [s.model_dump(mode="json") for s in (retr.seeds if retr else [])],
        "hits": [h.model_dump(mode="json") for h in hits[:MAX_HITS_DUMPED]],
        "hits_total": len(hits), "hits_truncated": len(hits) > MAX_HITS_DUMPED,
        "items": {
            "direct": [i.model_dump(mode="json") for i in report.direct],
            "indirect_shown": [i.model_dump(mode="json") for i in report.indirect],
            "indirect_overflow": [i.model_dump(mode="json") for i in asm.overflow],
            "global": [i.model_dump(mode="json") for i in report.global_changes],
            "stored_data": [i.model_dump(mode="json") for i in report.stored_data],
        },
        "locales": [r.model_dump(mode="json") for r in (retr.locales if retr else [])],
        "locale_files": [{"path": d.path, "added": d.added, "changed": {k: list(v) for k, v in d.changed.items()},
                          "removed": d.removed, "eol_only": d.eol_only, "problem": d.problem}
                         for d in locale_diffs],
        "set_aside": retr.set_aside if retr else {}, "outside_index": retr.outside_index if retr else [],
        "other_locale_files": retr.other_locale_files if retr else [],
        "not_assessed": retr.not_assessed if retr else 0, "overlay": retr.overlay if retr else {},
        "notices": [n.model_dump(mode="json") for n in (pr.notices if pr else [])],
        "gaps": [g.gap.model_dump(mode="json") for g in (grading.seeds if grading else []) if g.gap],
        "judge": judge,
    }
    paths["impact"] = _write_json(out_dir / "impact.json", impact)
    grade_doc = {
        "grading": grading.model_dump(mode="json") if grading else None, "judge": judge,
        "thresholds": th.model_dump(mode="json", by_alias=True, exclude={"doc"}),
        "naming": "CRAG-inspired: Correct/Ambiguous/Incorrect -> accept/narrow/expand/probe/escalate over "
                  "deterministic signals; not the published CRAG evaluator",
    }
    paths["grade"] = _write_json(out_dir / "grade.json", grade_doc)
    shown = {u.subject for u in report.undetermined}
    paths["needs_review"] = _write_json(out_dir / "needs_review.json", {
        "items": [r.model_dump(mode="json") for r in asm.review],
        "shown_in_report": len(report.undetermined), "shown_subjects": sorted(shown)})
    paths["claims"] = _write_json(out_dir / "claims.json", {
        "claims": [c.model_dump(mode="json") for c in report.claims], "basis": asm.basis,
        "verification": {"attempts": attempts, "narrator": narration.source, "notes": narration.notes,
                         "ok": not attempts[-1]["violations"] if attempts else True}})
    paths["report_json"] = _write_json(out_dir / "report.json", report.model_dump(mode="json"))
    if render:
        paths.update(_render(run, report))
    return paths


def _render(run: RunContext, report: Report) -> dict[str, str]:
    """`report.render.render(report, run)` when that module exists; its failure never loses report.json."""
    try:
        fn = importlib.import_module("blastradius.report.render").render
    except (ImportError, AttributeError):
        return {}
    try:
        written = fn(report, run)
    except Exception as exc:  # noqa: BLE001 - rendering is another module's job; report.json is already safe
        run.tracer.event("render_failed", error=f"{type(exc).__name__}: {str(exc)[:200]}")
        return {}
    out: dict[str, str] = {}
    for key, value in dict(written).items():
        name = str(key)
        out[name if name.startswith("report") else f"report_{name}"] = str(value)
    return out
