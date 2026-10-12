"""Run-to-run stability of the reasoning stage (plan section 9.5; D26, D27; cases 64, 72).

The product's determinism rests on a content cache (D27), so a re-run that hits the cache proves nothing about
the model. Here the reasoning stage is run N times with the cache bypassed (`BypassCacheLLM` forces
`bypass_cache=True` on every call and points the copy at a scratch cache directory, so the product's cache is
neither read nor overwritten) and the N reports are compared:

  * affected-set Jaccard: of the item ids in each pair of runs (mean and minimum, per tier too)
  * per-item agreement: the share of runs that contain the item, and the share of those that agree on its band
  * a `stable_view`: the first run's items with every item below `stability_min_agreement` demoted to the
    `possible` band and capped at 0.49. It is a separate output; no product artifact is modified.

The analysis (`analyse`) is a pure function of N reports, so it runs on a fixture of reports when the
reasoning stage is absent or no API key exists. Cost comes from the ledger delta around each repetition; the
N=100 figure is arithmetic from that measurement, labelled a lower bound.
"""

from __future__ import annotations

import copy
import importlib
import itertools
import json
import shutil
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..evidence import EvidenceLedger
from ..llm import LLM
from ..models import Band
from ..report.model import Report, ReportItem
from ..run import RunContext
from ..targets import TargetConfig
from .invariants import check_report
from .stats import jaccard, mean

TIERS = ("direct", "indirect", "global_changes", "stored_data")
BAND_RANK = {Band.UNKNOWN: 0, Band.POSSIBLE: 1, Band.LIKELY: 2, Band.CONFIRMED: 3}
_COPY_FILES = ("crawl/screens.jsonl", "crawl/transitions.jsonl", "crawl/captures.jsonl", "crawl/summary.json",
               "crawl/stats.json", "spec/requirements.json", "spec/stats.json", "code/index.json",
               "links/links.jsonl", "links/gaps.jsonl", "links/flows.jsonl", "links/link_state.json",
               "graph/load.json", "evidence.jsonl", "failures.jsonl")   # failures: report states run codes
_OUTPUTS = frozenset({"report.json", "report.md", "report.html", "claims.json", "needs_review.json",
                      "grade.json", "impact.json"})
N100 = 100


class BypassCacheLLM:
    """An LLM whose calls never read the cache and never touch the product's cache directory."""

    def __init__(self, llm: LLM, cache_dir: Path):
        self._llm = copy.copy(llm)
        self._llm.cache_dir = cache_dir

    def structured(self, **kwargs: Any) -> Any:
        kwargs["bypass_cache"] = True
        return self._llm.structured(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)


# --------------------------------------------------------------------------------------- analysis
def _items(report: Report) -> dict[str, tuple[str, ReportItem]]:
    out: dict[str, tuple[str, ReportItem]] = {}
    for tier in TIERS:
        for item in getattr(report, tier):
            out.setdefault(item.id, (tier, item))
    return out


def _pairwise(sets: Sequence[frozenset[str]]) -> dict[str, float | None]:
    pairs = [jaccard(a, b) for a, b in itertools.combinations(sets, 2)]
    if not pairs:
        return {"mean": None, "min": None, "pairs": 0}
    return {"mean": round(mean(pairs), 4), "min": round(min(pairs), 4), "pairs": len(pairs)}


def demote(item: ReportItem, cap: float) -> ReportItem:
    """The item as a stable view shows it when it is unstable: band `possible` (never raised), confidence
    capped below the 'likely' floor."""
    band = item.band if BAND_RANK[item.band] <= BAND_RANK[Band.POSSIBLE] else Band.POSSIBLE
    conf = min(item.confidence, cap)
    return item.model_copy(update={"band": band, "confidence": conf})


def analyse(reports: Sequence[Report], *, min_agreement: float, demoted_confidence: float) -> dict[str, Any]:
    """Stability of N reports of the same PR. N < 2 has no pairs: the numbers say so instead of 1.0."""
    n = len(reports)
    per_run = [_items(r) for r in reports]
    sets = [frozenset(d) for d in per_run]
    by_tier = {t: [frozenset(i for i, (tier, _) in d.items() if tier == t) for d in per_run] for t in TIERS}
    ids = sorted(set().union(*sets)) if sets else []
    rows: list[dict[str, Any]] = []
    view: list[dict[str, Any]] = []
    for item_id in ids:
        members = [d[item_id] for d in per_run if item_id in d]
        bands = Counter(it.band for _, it in members)
        modal_band, modal_n = min(bands.items(), key=lambda kv: (-kv[1], BAND_RANK[kv[0]]))
        agreement = len(members) / n
        tier, first = members[0]
        unstable = agreement < min_agreement
        rows.append({"id": item_id, "tier": tier, "label": first.label, "present_in": len(members), "n": n,
                     "agreement": round(agreement, 4), "band_agreement": round(modal_n / len(members), 4),
                     "modal_band": modal_band.value, "unstable": unstable})
        shown = demote(first, demoted_confidence) if unstable else first
        view.append({"tier": tier, "demoted": unstable, "changed": shown != first,
                     "original_band": first.band.value, "agreement": round(agreement, 4),
                     "item": shown.model_dump(mode="json")})
    risks = Counter(r.risk_level for r in reports)
    return {
        "n": n,
        "affected_set_jaccard": _pairwise(sets),
        "jaccard_by_tier": {t: _pairwise(by_tier[t]) for t in TIERS},
        "affected_set_sizes": [len(s) for s in sets],
        "risk_level": {"modal": risks.most_common(1)[0][0] if risks else None,
                       "agreement": round(risks.most_common(1)[0][1] / n, 4) if risks else None},
        "items": rows,
        "unstable_items": [r["id"] for r in rows if r["unstable"]],
        "always_present": sum(1 for r in rows if r["present_in"] == n),
        "stable_view": {"min_agreement": min_agreement, "n": n, "items": view},
    }


# -------------------------------------------------------------------------------------- collection
Runner = Callable[[RunContext, TargetConfig, Any, Any, int, int], dict[str, Any]]


def pipeline_runner(scratch: RunContext, cfg: TargetConfig, store: Any, llm: Any, pr: int,
                    rep: int) -> dict[str, Any]:
    """One reasoning pass in a scratch run directory; returns the report it wrote."""
    pipeline = importlib.import_module("blastradius.reason.pipeline")
    with scratch.stage("pr", pr=pr, repetition=rep):
        pipeline.run_pr(scratch, cfg, pr, store, llm)
    return json.loads((scratch.run_dir / "reason" / str(pr) / "report.json").read_text(encoding="utf-8"))


def reasoning_available() -> bool:
    try:
        importlib.import_module("blastradius.reason.pipeline")
    except ImportError:
        return False
    return True


def _seed_scratch(src: RunContext, dst: RunContext, pr: int) -> None:
    """Upstream inputs only; every reasoning output is left out so a repetition recomputes it."""
    for rel in _COPY_FILES:
        s = src.run_dir / rel
        if s.exists():
            d = dst.run_dir / rel
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
    pr_dir = src.run_dir / "reason" / str(pr)
    if pr_dir.is_dir():
        for f in pr_dir.iterdir():
            if f.is_file() and f.name not in _OUTPUTS:
                (dst.run_dir / "reason" / str(pr)).mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst.run_dir / "reason" / str(pr) / f.name)


@dataclass
class Rep:
    index: int
    report: Report | None = None
    error: str | None = None
    cost_usd: float = 0.0
    llm_calls: int = 0
    invariants_ok: bool | None = None


@dataclass
class Collected:
    pr: str
    reps: list[Rep] = field(default_factory=list)


def collect_live(run: RunContext, cfg: TargetConfig, store: Any, llm: LLM, pr: int, n: int,
                 runner: Runner = pipeline_runner) -> Collected:
    """N repetitions with the cache bypassed, each in its own scratch run (the product run is not written).

    Each report is kept under eval/stability/reports/<pr>/ so the analysis can be replayed offline."""
    scratch_root = run.path("eval", "stability", "scratch", ".keep").parent
    bypass = BypassCacheLLM(llm, run.path("eval", "stability", "cache", ".keep").parent)
    out = Collected(str(pr))
    for i in range(n):
        scratch = RunContext.create(cfg.name, run_id=f"{run.run_id}-stab-{pr}-{i}", settings=run.settings,
                                    runs_dir=scratch_root)
        _seed_scratch(run, scratch, pr)
        scratch.evidence = EvidenceLedger(scratch.run_dir, scratch.run_id)    # now sees the seeded rows
        before = llm.run.ledger.summary()
        rep = Rep(i)
        try:
            raw = runner(scratch, cfg, store, bypass, pr, i)
            rep.report = Report.model_validate(raw)
            rep.invariants_ok = check_report(rep.report, scratch.evidence.ids(),
                                             subject=f"pr {pr} rep {i}").ok
            kept = run.path("eval", "stability", "reports", str(pr), f"rep_{i}.json")
            kept.write_text(json.dumps(raw, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        except ValidationError:
            rep.error = "report did not validate against report.model.Report"
        except Exception as exc:  # noqa: BLE001 - reported per repetition, never hidden
            rep.error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160] if str(exc) else ''}"
        after = llm.run.ledger.summary()
        rep.cost_usd = round(after["spent_usd"] - before["spent_usd"], 6)
        rep.llm_calls = after["calls"] - before["calls"]
        out.reps.append(rep)
        shutil.rmtree(scratch.run_dir, ignore_errors=True)
    return out


def collect_fixture(directory: Path) -> list[Collected]:
    """Reports saved as <dir>/<pr>/*.json (or <dir>/*.json for one PR), one file per repetition."""
    out: list[Collected] = []
    groups = [(d.name, sorted(d.glob("*.json"))) for d in sorted(directory.iterdir()) if d.is_dir()]
    groups = [(n, fs) for n, fs in groups if fs] or [("fixture", sorted(directory.glob("*.json")))]
    for name, files in groups:
        col = Collected(name)
        for i, f in enumerate(files):
            rep = Rep(i)
            try:
                rep.report = Report.model_validate_json(f.read_text(encoding="utf-8"))
            except (ValidationError, OSError, ValueError) as exc:
                rep.error = f"{f.name}: {type(exc).__name__}"
            col.reps.append(rep)
        out.append(col)
    return out


# ------------------------------------------------------------------------------------------ entry
def summarise(col: Collected, *, min_agreement: float,
              demoted_confidence: float) -> tuple[dict[str, Any], dict]:
    ok = [r for r in col.reps if r.report is not None]
    result = analyse([r.report for r in ok if r.report is not None], min_agreement=min_agreement,
                     demoted_confidence=demoted_confidence)
    view = result.pop("stable_view")
    spent = sum(r.cost_usd for r in col.reps)
    result.update(
        pr=col.pr, n_requested=len(col.reps), n_completed=len(ok),
        errors=[{"repetition": r.index, "error": r.error} for r in col.reps if r.error],
        invariants_clean=sum(1 for r in ok if r.invariants_ok),
        invariants_evaluated=sum(1 for r in ok if r.invariants_ok is not None),
        llm_calls=sum(r.llm_calls for r in col.reps), cost_usd=round(spent, 4))
    return result, view


def n100_arithmetic(total_usd: float, reps: int) -> dict[str, Any]:
    if reps == 0:
        return {"status": "no completed repetition: nothing to extrapolate"}
    per = total_usd / reps
    return {"measured_usd": round(total_usd, 4), "repetitions": reps, "usd_per_repetition": round(per, 4),
            "n100_usd_lower_bound": round(per * N100, 2),
            "arithmetic": f"{per:.4f} USD/repetition x {N100} = {per * N100:.2f} USD",
            "note": "lower bound: thinking is billed as output and rate-limit retries are not modelled"}


def run_stability(run: RunContext, cfg: TargetConfig, store: Any, llm: LLM | None, *, n: int,
                  min_agreement: float, demoted_confidence: float, pr_numbers: Sequence[int] = (),
                  fixture_dir: Path | None = None, runner: Runner = pipeline_runner) -> dict[str, Any]:
    """The stability section of results.json; writes eval/stable_view.json next to it."""
    caveats = [
        "Agreement across N runs measures how repeatable the pipeline is, not whether a run is right.",
        "N is small; an item's agreement is k/N, so one run moves it by 1/N.",
        "Calls are not seeded (D26: 5.5 models take no sampling parameters); variance is the model's own.",
    ]
    if fixture_dir is not None:
        cols = collect_fixture(fixture_dir)
        source = f"fixture reports in {fixture_dir.name} (no live run)"
    elif pr_numbers and llm is not None and (runner is not pipeline_runner or reasoning_available()):
        cols = [collect_live(run, cfg, store, llm, pr, n, runner) for pr in sorted(pr_numbers)]
        source = f"live: reason.pipeline.run_pr x {n} per PR, cache bypassed"
    else:
        why = ("no PR numbers (no reason/<pr>/ output and none given)" if not pr_numbers
               else "no LLM client" if llm is None else "reason.pipeline is not importable")
        return {"status": "skipped", "reason": why, "caveats": caveats}
    per_pr: dict[str, Any] = {}
    views: dict[str, Any] = {}
    for col in cols:
        per_pr[col.pr], views[col.pr] = summarise(col, min_agreement=min_agreement,
                                                  demoted_confidence=demoted_confidence)
    spent = sum(r.cost_usd for c in cols for r in c.reps)
    done = sum(1 for c in cols for r in c.reps if r.report is not None)
    calls = sum(r.llm_calls for c in cols for r in c.reps)
    live = fixture_dir is None
    if live and done and calls == 0:
        caveats.append("No model call completed in any repetition: the pipeline fell back to its "
                       "deterministic templates, so the agreement below shows only that the deterministic "
                       "path is repeatable. It says nothing about model variance.")
    path = run.path("eval", "stable_view.json")
    doc = {"min_agreement": min_agreement, "demoted_confidence": demoted_confidence, "per_pr": views}
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    status = "error" if not done else "degenerate" if live and calls == 0 else "ok"
    return {"status": status, "source": source, "n_per_pr": n, "per_pr": per_pr, "llm_calls": calls,
            "model_exercised": calls > 0, "stable_view": "eval/stable_view.json", "cost_usd": round(spent, 4),
            "n100": n100_arithmetic(spent, sum(len(c.reps) for c in cols)), "caveats": caveats,
            "min_agreement": min_agreement}
