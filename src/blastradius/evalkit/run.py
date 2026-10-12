"""`blastradius eval`: run the selected evaluations, write eval/results.json and eval/eval.md (plan 9).

  invariants  schema, citations, band/verb, body hygiene, distinct counts of each reason/<pr>/report.json
  recall      synthetic-PR recall over the graph (CIRCULAR, labelled as such)
  oracle      source-map oracle vs the linker (independent signal; test-only; needs network and a browser)
  judge       offline evidence-only claim judge with seeded corruptions (needs an LLM)
  stability   N cache-bypassed reasoning runs (needs reasoning and an LLM)

`which=None` runs every evaluation whose inputs exist; the others are reported as `skipped` with the reason,
and a failing one as `error`: nothing is dropped silently, and no metric is printed without its n.
results.json holds no timestamps, so it is byte-identical for identical inputs. Thresholds are the
pre-registered ones (`prereg`), recorded with the hash of the registered file.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..config import Settings
from ..failures import FailureCode
from ..llm import LLM
from ..report.model import Report
from ..run import RunContext
from ..targets import TargetConfig
from . import prereg
from .artifacts import load_evidence, load_links, load_pr_reports
from .invariants import check_report
from .judge_offline import ClaimRef, load_hand_labels, run_judge
from .oracle_run import run_oracle
from .stability import Runner, pipeline_runner, run_stability
from .stats import fmt_rate
from .synthetic import run_synthetic_recall

ALL = ("invariants", "recall", "oracle", "judge", "stability")
SCHEMA_VERSION = 1
INVARIANT_CAVEAT = ("Invariants check the form of a report, not its truth: a report can pass all of them and "
                    "still be wrong (a valid-but-irrelevant citation passes, case 54).")


@dataclass(frozen=True)
class EvalOptions:
    """Knobs that are not part of the CLI contract; defaults are the pre-registered values."""

    live_oracle: bool = True
    oracle_paths: tuple[str, ...] | None = None
    oracle_cache_dir: Path | None = None
    harvest_fn: Callable[..., Any] | None = None
    stability_n: int | None = None
    pr_numbers: tuple[int, ...] = ()
    stability_fixture: Path | None = None
    runner: Runner = pipeline_runner
    judge_k: int | None = None
    n_corruptions: int | None = None
    max_recall_files: int = 200


# -------------------------------------------------------------------------------------- the evals
def _invariants(run: RunContext) -> dict[str, Any]:
    reports = load_pr_reports(run)
    if not reports:
        return {"status": "skipped", "reason": "no reason/<pr>/report.json (run `blastradius pr <n>`)"}
    evidence = set(load_evidence(run))
    rows = []
    for r in reports:
        if r.problems:
            rows.append({"subject": f"pr {r.name}", "ok": False, "failed": ["unreadable"],
                         "problems": r.problems})
        else:
            rows.append(check_report(r.data, evidence, subject=f"pr {r.name}").as_dict())
    by_inv: dict[str, int] = {}
    for row in rows:
        for name in row["failed"]:
            by_inv[name] = by_inv.get(name, 0) + 1
    return {"status": "ok", "n_reports": len(rows), "n_ok": sum(1 for r in rows if r["ok"]),
            "all_ok": all(r["ok"] for r in rows), "violations_by_invariant": dict(sorted(by_inv.items())),
            "evidence_rows": len(evidence), "reports": rows,
            "rule": "a report violating any invariant is wrong regardless of its content (plan section 9.1)",
            "caveats": [INVARIANT_CAVEAT]}


def _recall(run: RunContext, cfg: TargetConfig, store: Any, th: dict[str, Any],
            opts: EvalOptions) -> dict[str, Any]:
    if store is None:
        return {"status": "skipped", "reason": "no graph store"}
    links = load_links(run)
    if not links:
        return {"status": "skipped", "reason": "no links/links.jsonl (run `blastradius link`)"}
    out = run_synthetic_recall(store, cfg.name, links, min_confidence=th["eval_only"]["high_confidence"],
                               breadth_max=th["registered"]["breadth_fraction_max"],
                               max_files=opts.max_recall_files)
    return {"status": "ok", **out}


def _claims(run: RunContext) -> list[ClaimRef]:
    out: list[ClaimRef] = []
    for r in load_pr_reports(run):
        try:
            report = Report.model_validate(r.data)
        except ValidationError:
            continue
        out += [ClaimRef(c.id, c.text, tuple(c.evidence_ids), c.band, r.name) for c in report.claims]
    return out


def _generator_models(run: RunContext) -> list[str]:
    path = run.run_dir / "ledger.jsonl"
    models: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("purpose") != "offline_judge" and rec.get("model"):
                models.add(str(rec["model"]))
    return sorted(models)


def _cannot_call(llm: LLM | None) -> str | None:
    """Why no model call can be made at all (None when one can, or when replay from cache is wanted)."""
    if llm is None:
        return "no LLM client"
    if llm.offline or getattr(llm, "_client", None) is not None or Settings.has_api_key():
        return None
    return "no ANTHROPIC_API_KEY and no client: nothing to call (the judge is never run from a guess)"


def _judge(run: RunContext, llm: LLM | None, th: dict[str, Any], opts: EvalOptions) -> dict[str, Any]:
    claims = _claims(run)
    if not claims:
        return {"status": "skipped", "reason": "no claims in any reason/<pr>/report.json"}
    if (why := _cannot_call(llm)) is not None or llm is None:
        return {"status": "skipped", "reason": why}
    out = run_judge(llm, claims, load_evidence(run), k=opts.judge_k or th["eval_only"]["judge_k"],
                    n_corruptions=opts.n_corruptions or th["eval_only"]["seeded_corruptions"],
                    hand_labels=load_hand_labels(run.run_dir / "eval" / "judge_hand_labels.json"),
                    generator_models=_generator_models(run))
    if out["model_verdicts"] == 0:       # a key that is rejected or a cold offline cache: nothing was judged
        why = (out["aborted"] or {}).get("reason", "every judged item errored")
        return {"status": "unavailable", "reason": why, "n_claims": len(claims), **out}
    return {"status": "ok", "n_claims": len(claims), **out}


def _stability(run: RunContext, cfg: TargetConfig, store: Any, llm: LLM | None, th: dict[str, Any],
               opts: EvalOptions) -> dict[str, Any]:
    if opts.stability_fixture is None:
        if llm is not None and llm.offline:
            return {"status": "skipped",
                    "reason": "offline: a stability run needs live calls (cache bypassed)"}
        if (why := _cannot_call(llm)) is not None:
            return {"status": "skipped", "reason": why}
    reason_dir = run.run_dir / "reason"
    seen = ([int(p.name) for p in sorted(reason_dir.iterdir()) if p.name.isdigit()]
            if reason_dir.is_dir() else [])
    prs = sorted(set(opts.pr_numbers) | set(seen))
    return run_stability(run, cfg, store, llm, n=opts.stability_n or th["eval_only"]["stability_n"],
                         min_agreement=th["eval_only"]["stability_min_agreement"],
                         demoted_confidence=th["eval_only"]["demoted_confidence"], pr_numbers=prs,
                         fixture_dir=opts.stability_fixture, runner=opts.runner)


# ------------------------------------------------------------------------------------------- entry
def run_eval(run: RunContext, cfg: TargetConfig, store: Any, llm: LLM | None, which: list[str] | None = None,
             *, options: EvalOptions | None = None) -> dict[str, Any]:
    """Run the selected evaluations; write <run>/eval/results.json and eval.md; return the results dict."""
    opts = options or EvalOptions()
    wanted = list(ALL) if not which else list(dict.fromkeys(which))
    unknown = [w for w in wanted if w not in ALL]
    if unknown:
        raise ValueError(f"unknown evaluation(s) {unknown}; choose from {list(ALL)}")
    th = prereg.resolve()
    if th["sha256"] is None:
        run.failures.record(FailureCode.CONFIG_INVALID,
                            f"pre-registered thresholds not usable: {th['source']}", stage="eval")
    before = run.ledger.summary()
    runners: dict[str, Callable[[], dict[str, Any]]] = {
        "invariants": lambda: _invariants(run),
        "recall": lambda: _recall(run, cfg, store, th, opts),
        "oracle": lambda: _oracle(run, cfg, opts),
        "judge": lambda: _judge(run, llm, th, opts),
        "stability": lambda: _stability(run, cfg, store, llm, th, opts),
    }
    evals: dict[str, Any] = {}
    with run.tracer.span("stage", "eval", which=",".join(wanted)):
        for name in ALL:
            if name not in wanted:
                continue
            with run.tracer.span("eval", name) as sp:
                try:
                    evals[name] = runners[name]()
                except Exception as exc:  # noqa: BLE001 - an eval that raises is reported, not hidden
                    detail = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}"
                    run.failures.record(FailureCode.NEEDS_HUMAN, f"eval {name} raised {detail}", stage="eval")
                    evals[name] = {"status": "error", "error": detail}
                sp.set(status=evals[name].get("status"))
    after = run.ledger.summary()
    results = {
        "schema_version": SCHEMA_VERSION, "target": cfg.name, "run_id": run.run_id,
        "requested": wanted, "thresholds": th, "evals": evals,
        "summary": {n: evals[n].get("status") for n in evals},
        "llm": {"calls": after["calls"] - before["calls"],
                "cache_hits": after["cache_hits"] - before["cache_hits"],
                "spent_usd": round(after["spent_usd"] - before["spent_usd"], 4),
                "avoided_usd": round(after["avoided_usd"] - before["avoided_usd"], 4)},
    }
    out_dir = run.path("eval", "results.json").parent
    (out_dir / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True, ensure_ascii=False,
                                                     default=str) + "\n", encoding="utf-8")
    (out_dir / "eval.md").write_text(render_markdown(results), encoding="utf-8")
    return results


def _oracle(run: RunContext, cfg: TargetConfig, opts: EvalOptions) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"live": opts.live_oracle, "cache_dir": opts.oracle_cache_dir,
                              "paths": opts.oracle_paths}
    if opts.harvest_fn is not None:
        kwargs["harvest_fn"] = opts.harvest_fn
    return run_oracle(run, cfg, **kwargs)


# -------------------------------------------------------------------------------------- rendering
def _l(*parts: str) -> str:
    return "".join(parts)


def _bullets(items: list[str]) -> list[str]:
    return [f"- {i}" for i in items]


def _not_run(r: dict[str, Any]) -> list[str]:
    return [f"Not run: {r.get('reason', r.get('error'))}"]


def _md_invariants(r: dict[str, Any]) -> list[str]:
    if r.get("status") != "ok":
        return _not_run(r)
    lines = [_l(f"{r['n_ok']} of {r['n_reports']} reports pass every invariant (evidence rows available: ",
                f"{r['evidence_rows']}). A report that fails any is wrong regardless of its content.")]
    for rep in r["reports"]:
        lines.append(f"- {rep['subject']}: " + ("ok" if rep["ok"] else "FAILED " + ", ".join(rep["failed"])))
        for res in rep.get("results", []):
            if not res["passed"]:
                shown = res["offenders"][:5]
                lines.append(f"  - {res['name']} ({res['offender_count']} offenders): " + "; ".join(shown))
    return [*lines, "", "Caveats:", *_bullets(r.get("caveats", []))]


def _md_recall(r: dict[str, Any]) -> list[str]:
    if r.get("status") != "ok":
        return _not_run(r)
    return [
        f"**{r['label']}**", "",
        _l(f"- elements checked: {r['n_elements']} over {r['n_files']} files ",
           f"(of {r['candidate_files_before_cap']} candidates)"),
        f"- (element, screen) recall: {fmt_rate(r['recall'])}",
        _l(f"- recall excluding trivially broad files: {fmt_rate(r['recall_nontrivial'])} ",
           f"({len(r['trivial_files'])} files reach >= {r['breadth_max']:.0%} of ",
           f"{r['total_screens']} screens)"),
        f"- flow recall: {fmt_rate(r['flow_recall'])}",
        f"- misses: {r['miss_count']} (retrieval at hop cap {r['hop_cap']})",
    ]


def _md_oracle_scoring(s: dict[str, Any]) -> list[str]:
    lines = [
        _l(f"- scored: {s['scored']} distinct elements ({s['labels']} labels; excluded: ",
           f"{s['excluded']['not_in_crawl']} not in the crawl, ",
           f"{s['excluded']['file_not_on_main']} file not on main)"),
        f"- linker coverage: {fmt_rate(s['linker_coverage'])}",
        f"- top-1 precision (of covered): {fmt_rate(s['top1_precision_of_covered'])}",
        f"- top-1 accuracy (of all scored): {fmt_rate(s['top1_accuracy_of_scored'])}",
        f"- hit@3 (of covered): {fmt_rate(s['hit_at_3_of_covered'])}",
        f"- hit@3 (of all scored): {fmt_rate(s['hit_at_3_of_scored'])}",
        "- by evidence method of the top-1 link:",
    ]
    for m, v in s["by_top1_method"].items():
        lines.append(f"  - {m}: top-1 {fmt_rate(v['top1_correct'])}; hit@3 {fmt_rate(v['hit_at_3'])}")
    return lines


def _md_oracle(r: dict[str, Any]) -> list[str]:
    lines = [f"**{r['statement']}**", ""] if r.get("statement") else []
    if r["status"] not in ("ok", "fallback_hand_labels"):
        return [*lines, f"Status {r['status']}: {r.get('reason', r.get('error', ''))}"]
    lines.append(f"- label source: {r.get('source')}")
    o = r.get("oracle")
    if o:
        att = o["attribution"]
        lines += [
            _l(f"- oracle coverage: {fmt_rate(o['oracle_coverage'])} of perceived elements got a repo file"),
            _l(f"- component attribution: {fmt_rate(att['basename_equals_type_name'])} basenames equal the ",
               f"component's own `__name`; vote share unanimous {fmt_rate(att['vote_share_unanimous'])}"),
            f"- deployed vs indexed main: {o['drift'].get('summary', o['drift'].get('status'))}",
            _l("- label plausibility (not a score): ",
               f"{fmt_rate(o['owner_text_check']['name_found_in_owner_file'])} of named elements have their ",
               "name in the file the oracle names"),
        ]
    if r.get("scoring"):
        lines += _md_oracle_scoring(r["scoring"])
    return [*lines, "", "Caveats:", *_bullets(r.get("caveats", []))]


def _md_judge(r: dict[str, Any]) -> list[str]:
    if r.get("status") == "unavailable":
        cov = r["natural"]["entity_coverage"]
        return [
            _l(f"Unavailable: no item received a model verdict ({r['reason']}). ",
               "No judge number below is a measurement."),
            _l(f"- evidence readability (not a judge result): {cov['claims_with_a_quoted_name']} claims ",
               f"quote product names; {fmt_rate(cov['found_in_cited_evidence'])} of those names occur in ",
               "the evidence the claims cite"),
            *_bullets(r["caveats"]),
        ]
    if r.get("status") != "ok":
        return _not_run(r)
    s, nat = r["seeded"], r["natural"]
    return [
        _l(f"- judge: {r['judge_model']} (tier requested {r['tier_requested']}, used {r['tier_used']}), ",
           f"k={r['k']} samples per item, purpose `{r['purpose']}`"),
        _l(f"- seeded corruptions: {s['produced']} of {s['requested']} requested, {s['judged']} judged ",
           f"({s['by_kind']})"),
        f"- true-negative rate, UPPER BOUND: {fmt_rate(s['tnr_upper_bound'])}",
        f"- true-negative rate on corruptions that needed a judge call: {fmt_rate(s['tnr_llm_judged_only'])}",
        f"- outcomes on corruptions: {s['outcomes']}",
        _l(f"- natural claims judged: {nat['n']} (unlabelled): supported {fmt_rate(nat['supported'])}; ",
           f"unsupported {fmt_rate(nat['unsupported'])}; cannot_tell {fmt_rate(nat['cannot_tell'])}; ",
           f"needs_review {fmt_rate(nat['needs_review'])}; error {fmt_rate(nat['error'])}"),
        _l("- kappa (originals assumed supported vs corruptions): ",
           f"{s['kappa_originals_assumed_supported_vs_corruptions']}; always-supported baseline kappa ",
           f"{s['baseline_always_supported']['kappa']}"),
        _l(f"- evidence readability: {fmt_rate(nat['entity_coverage']['found_in_cited_evidence'])} of the ",
           f"product names the {nat['entity_coverage']['claims_with_a_quoted_name']} claims quote occur in ",
           "the evidence they cite (low means F1 cannot be met)"),
        f"- hand check: {r['hand_check']}",
        f"- cost (lower bound): ${r['cost_usd_lower_bound']} over {r['calls_made']} calls",
        *([f"- ABORTED after {r['aborted']['after_items']} items: {r['aborted']['reason']}"]
          if r.get("aborted") else []),
        "", "Caveats:", *_bullets(r["caveats"]),
    ]


def _md_stability(r: dict[str, Any]) -> list[str]:
    if r.get("status") not in ("ok", "error", "degenerate"):
        return _not_run(r)
    lines = [f"- source: {r['source']}"]
    for pr, v in r["per_pr"].items():
        j = v["affected_set_jaccard"]
        lines.append(_l(
            f"- PR {pr}: N={v['n_completed']} of {v['n_requested']} repetitions; affected-set Jaccard ",
            f"mean {j['mean']} min {j['min']} over {j['pairs']} pairs; ",
            f"{len(v['unstable_items'])} of {len(v['items'])} items below agreement {r['min_agreement']}; ",
            f"{v['always_present']} present in every run; invariants clean in {v['invariants_clean']} of ",
            f"{v['invariants_evaluated']} evaluated"))
        lines += [f"  - error in repetition {e['repetition']}: {e['error']}" for e in v["errors"]]
    return [*lines,
            f"- cost measured: ${r['cost_usd']}; {r['n100'].get('arithmetic', r['n100'].get('status'))}",
            f"- stable view (separate output, product artifacts untouched): {r['stable_view']}",
            "", "Caveats:", *_bullets(r["caveats"])]


_RENDER: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "invariants": _md_invariants, "recall": _md_recall, "oracle": _md_oracle, "judge": _md_judge,
    "stability": _md_stability,
}
_TITLES = {"invariants": "1. Invariants", "recall": "2. Synthetic-PR recall (circular)",
           "oracle": "3. Independent link signal: source-map oracle", "judge": "4. Offline claim judge",
           "stability": "5. Stability under repeated runs"}


def render_markdown(results: dict[str, Any]) -> str:
    th = results["thresholds"]
    lines = [
        f"# Evaluation: {results['target']}, run {results['run_id']}", "",
        "Every rate below is k/n with its Wilson 95% interval; a number without its n is not printed.",
        _l(f"Thresholds were pre-registered ({th['source']}, registered {th.get('registered_on')}, ",
           f"sha256 {str(th.get('sha256'))[:12]}): {th['registered']}; eval-only: {th['eval_only']}."),
        "", "| evaluation | status |", "|---|---|",
    ]
    lines += [f"| {n} | {s} |" for n, s in results["summary"].items()]
    for name in ALL:
        if name in results["evals"]:
            lines += ["", f"## {_TITLES[name]}", "", *_RENDER[name](results["evals"][name])]
    bad = {n: e for n, e in results["evals"].items() if e.get("status") == "error"}
    if bad:
        lines += ["", "## Failures", *_bullets([f"{n}: {e.get('error')}" for n, e in bad.items()])]
    return "\n".join([*lines, "", f"LLM in this stage: {results['llm']}", ""])
