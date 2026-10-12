"""Gaps: absence as an evidenced, effort-carrying node (D08, D09; cases 8, 22, 27, 34, 46, 48).

A missing edge cannot say "does not exist" from "did not look" or "was not allowed to look". This stage turns
the link stage's results into Gap rows (links/gaps.jsonl, store.write_gaps), one per (kind, subject):

 requirement, in this precedence (first match wins)
   NOT_UI_TESTABLE  CLASSIFIED         ui_observable == "no" or edition desktop / self-host
   ACCESS_RESTRICTED SKIPPED_BY_POLICY requires_login; or an unrealised requirement all of whose words
                                       name a control the crawl refused to act on (a blocked transition)
   (realised by >= 1 REALIZED_BY link: no gap; a must_not_exist requirement that IS realised is a
    contradiction of the spec, reported in the summary, never as a gap: a satisfied negative is a pass)
   NO_UI_EVIDENCE   NOT_FOUND_WITHIN_BUDGET   ui_observable yes, edition cloud-web or all, nothing realises it
   NO_UI_EVIDENCE   CONFIRMED_ABSENT   the same for polarity must_not_exist (an explicit "coming soon"):
                                       the spec says it is absent and the crawl found nothing that shows it
 crawl
   ACCESS_RESTRICTED SKIPPED_BY_POLICY one per control (canonical) the crawl was refused to act on; the reason
                                       is the refusal class of its blocked transitions (auth, external_link,
                                       destructive, network_side_effect, external_widget, access)
   UNMAPPED_CODE    OBSERVED           every element the UI->code cascade left unresolved (reason from the
                                       link stage: no_text, no_candidate, llm_none, llm_budget,
                                       external_package)
   UNSPECIFIED_UI   OBSERVED           named, interactive, non-global elements no requirement realises, capped
                                       at UNSPECIFIED_PER_SCREEN per screen and UNSPECIFIED_TOTAL overall; the
                                       rest is one `grouped:N` gap per screen, so it cannot flood

Confidence is a claim about absence and is only non-zero for NO_UI_EVIDENCE gaps. With
E = (1 - exp(-actions_spent / 50)) x (1.0 if the crawl emptied its frontier else 0.8):

    NOT_FOUND_WITHIN_BUDGET  min(0.60, 0.60 x E)        never above "likely"
    CONFIRMED_ABSENT         min(0.60, 0.40 + 0.20 x E) the spec's explicit negative corroborated by the crawl;
                             never below NOT_FOUND at equal effort, same ceiling: a bounded unauthenticated
                             crawl cannot prove a negative
    judge did not run        0.0 and reason JUDGE_UNAVAILABLE (we did not look; never an absence claim)

Reason SPEC_MAY_BE_STALE is set when spec/stats.json says the docs predate the app (case 21). CONFIG_DEPENDENT
(feature flags, case 11) is not produced: the crawl records no flags. Gaps carry the crawl's bundle_hash and are
never reused across bundles (case 48); NOT_UI_TESTABLE depends only on the spec and carries none. When the crawl
ran with goals, crawl/goal_effort.json (screens and actions that overlapped the requirement) is cited in the
gap's evidence row.

Bounded re-crawl (`recrawl=True`): at most 5 NO_UI_EVIDENCE gaps (the ingest stage's requirement order),
each a goal crawl of 20
actions through `crawl.explorer.run_crawl(..., artifact_subdir=f"goal-{i}")` (the crawl module accepts one plain
path component and writes crawl/<subdir>/screens.jsonl), made only when run_crawl accepts `artifact_subdir`
(feature-detected: without it a goal crawl would overwrite the main crawl). Candidates are regenerated from
the screens that crawl added, judged, and either close the gap (a REALIZED_BY link) or raise the gap's
searched screens, actions and confidence. The new screens are not in the graph until `load` is run on them:
a link to one of them is written to links.jsonl and counted as `skipped_missing_dst` by the store.
"""

from __future__ import annotations

import inspect
import json
import math
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..code.index import render_closure
from ..graph.canonical import canonicalise
from ..ids import stable_id
from ..llm import LLM
from ..models import CodeIndex, CrawlSummary, Gap, GapKind, Link, Requirement, ScreenState, UIElementObs
from ..run import RunContext
from ..targets import TargetConfig
from .budget import CallBudget, ledger_delta
from .cascade import screen_order
from .inputs import LinkInputs, load_inputs, load_links, read_jsonl, write_jsonl
from .req_to_ui import MAX_JUDGE_CALLS, Retriever, build_docs, judge_requirement
from .text import tokens
from .ui_to_code import INTERACTIVE_ROLES, element_texts

NOT_FOUND = "NOT_FOUND_WITHIN_BUDGET"
NOT_FOUND_CAP = 0.60
ABSENT_BASE, ABSENT_SPAN, ABSENT_CAP = 0.40, 0.20, 0.60
EFFORT_SCALE = 50.0
INCOMPLETE_CRAWL = 0.8
UNSPECIFIED_PER_SCREEN = 8
UNSPECIFIED_TOTAL = 60
GLOBAL_PATTERNS = 3          # an element seen under this many url patterns is layout, not page content
RECRAWL_MAX_GAPS = 5
RECRAWL_ACTIONS = 20


def effort_score(actions_spent: int, stop_reason: str | None) -> float:
    """E in [0, 1): how much of the reachable surface the spent actions can have covered."""
    e = 1.0 - math.exp(-max(actions_spent, 0) / EFFORT_SCALE)
    return e * (1.0 if stop_reason == "frontier_empty" else INCOMPLETE_CRAWL)


def not_found_confidence(actions_spent: int, stop_reason: str | None) -> float:
    return round(min(NOT_FOUND_CAP, NOT_FOUND_CAP * effort_score(actions_spent, stop_reason)), 2)


def absent_confidence(actions_spent: int, stop_reason: str | None) -> float:
    return round(min(ABSENT_CAP, ABSENT_BASE + ABSENT_SPAN * effort_score(actions_spent, stop_reason)), 2)


# -------------------------------------------------------------------------------------------- context
@dataclass
class _Ctx:
    run: RunContext
    cfg: TargetConfig
    inputs: LinkInputs
    links: list[Link]
    state: dict[str, Any]
    realized: set[str] = field(default_factory=set)
    alias: dict[str, str] = field(default_factory=dict)       # observation id -> canonical element id
    members: dict[str, list[str]] = field(default_factory=dict)  # canonical id -> sorted observation ids
    obs: dict[str, UIElementObs] = field(default_factory=dict)   # observation id -> first observation
    goal_effort: dict[str, dict[str, Any]] = field(default_factory=dict)   # requirement text -> crawl row
    summary: CrawlSummary | None = None

    @property
    def bundle(self) -> str | None:
        return self.summary.bundle_hash if self.summary else None

    @property
    def actions(self) -> int:
        return self.summary.actions_spent if self.summary else 0

    @property
    def stop_reason(self) -> str | None:
        return self.summary.stop_reason if self.summary else None

    @property
    def stale(self) -> bool:
        return self.inputs.spec_stats.get("stale") is True

    @property
    def screen_ids(self) -> list[str]:
        return sorted(s.id for s in self.inputs.screens)


def _gap(ctx: _Ctx, kind: GapKind, status: str, subject: str, reason: str, summary: str, *,
         confidence: float = 0.0, screens: Iterable[str] = (), actions: int = 0, bundle: bool = True) -> Gap:
    ev = ctx.run.evidence.add(f"gap.{kind.value.lower()}", subject, summary)
    return Gap(id=stable_id("gap", ctx.cfg.name, kind.value, subject), kind=kind, status=status, subject_id=subject,
               reason=reason, confidence=confidence, screens_searched=sorted(set(screens)), actions_spent=actions,
               bundle_hash=ctx.bundle if bundle else None, evidence_id=ev)


# ------------------------------------------------------------------------------------------ requirements
def _blocked_controls(ctx: _Ctx) -> dict[str, dict[str, Any]]:
    """canonical element id -> {"label", "classes", "reasons"} for every control the crawl was refused."""
    out: dict[str, dict[str, Any]] = {}
    for t in sorted(ctx.inputs.transitions, key=lambda t: t.id):
        if not t.blocked or not t.element_id:
            continue
        cid = ctx.alias.get(t.element_id, t.element_id)
        rec = out.setdefault(cid, {"label": t.label, "classes": set(), "reasons": set(), "from": set()})
        rec["classes"].add(t.reason.split(":", 1)[0].strip() or "blocked")
        rec["reasons"].add(t.reason)
        rec["from"].add(t.from_screen)
    return out


def _blocked_match(req: Requirement, blocked: dict[str, dict[str, Any]]) -> list[str]:
    """Blocked controls whose every label word appears in the requirement (icon-only labels never match)."""
    words = set(tokens(req.text)) | set(tokens(req.quote))
    hit = []
    for cid, rec in sorted(blocked.items()):
        label = rec["label"]
        toks = tokens(label) if label and not label.startswith("[icon") else []
        if toks and set(toks) <= words:
            hit.append(cid)
    return hit


def _judge_state(ctx: _Ctx, req: Requirement) -> str:
    rec = ctx.state.get("requirements", {}).get(req.id)
    return rec["status"] if rec else "unjudged"


def _requirement_gap(ctx: _Ctx, req: Requirement, blocked: dict[str, dict[str, Any]],
                     summary: Counter[str]) -> Gap | None:
    if req.ui_observable == "no" or req.edition in ("desktop", "self-host"):
        why = f"edition:{req.edition}" if req.edition in ("desktop", "self-host") else "not_ui_observable"
        return _gap(ctx, GapKind.NOT_UI_TESTABLE, "CLASSIFIED", req.id, why,
                    f'requirement "{req.text}" is not testable through the cloud web UI ({why})', bundle=False)
    if req.requires_login:
        return _gap(ctx, GapKind.ACCESS_RESTRICTED, "SKIPPED_BY_POLICY", req.id, "requires_login",
                    f'requirement "{req.text}" needs an account; the crawl is unauthenticated by policy',
                    screens=ctx.screen_ids, actions=ctx.actions)
    if req.id in ctx.realized:
        if req.polarity == "must_not_exist":
            summary["negatives_contradicted"] += 1
        return None
    hit = _blocked_match(req, blocked)
    if hit:
        classes = sorted({c for cid in hit for c in blocked[cid]["classes"]})
        names = ", ".join(repr(blocked[c]["label"]) for c in hit[:3])
        return _gap(ctx, GapKind.ACCESS_RESTRICTED, "SKIPPED_BY_POLICY", req.id, "+".join(classes),
                    f'requirement "{req.text}" names control(s) {names} that the crawl was not allowed to use',
                    screens=ctx.screen_ids, actions=ctx.actions)
    if req.ui_observable != "yes" or req.edition not in ("cloud-web", "all"):
        summary["not_gapped_unclassified"] += 1
        return None
    return _absence_gap(ctx, req)


_HOW = {"judged": "judged by two samples", "partial": "judged by one sample",
        "no_candidates": "no UI text overlapped it", "unjudged": "the judge did not run"}


def _absence_gap(ctx: _Ctx, req: Requirement) -> Gap:
    state = _judge_state(ctx, req)
    negative = req.polarity == "must_not_exist"
    if state == "unjudged":          # nothing was looked at with a judge: no absence claim, no confidence
        reason, conf, status = "JUDGE_UNAVAILABLE", 0.0, NOT_FOUND
    else:
        reason = "SPEC_MAY_BE_STALE" if ctx.stale else ""
        conf = (absent_confidence if negative else not_found_confidence)(ctx.actions, ctx.stop_reason)
        status = "CONFIRMED_ABSENT" if negative else NOT_FOUND
    goal = ctx.goal_effort.get(req.text)
    aimed = (f"; the crawl aimed at it: {goal['screens_visited']} matching screens, {goal['actions_spent']} "
             f"actions on matching controls" if goal else "")
    s = ctx.summary
    refused = (f"; the crawl also had {s.blocked} blocked, {s.disallowed} disallowed and {s.suspect} suspect "
               f"captures that never became screens" if s and (s.blocked or s.disallowed or s.suspect) else "")
    summary = (f'requirement "{req.text}": searched {len(ctx.inputs.screens)} screens in {ctx.actions} actions '
               f"(crawl stop: {ctx.stop_reason or 'unknown'}); candidates {_HOW.get(state, state)}{aimed}{refused}; "
               + ("spec may be stale" if ctx.stale else "spec not known to be stale"))
    return _gap(ctx, GapKind.NO_UI_EVIDENCE, status, req.id, reason, summary, confidence=conf,
                screens=ctx.screen_ids, actions=ctx.actions)


# --------------------------------------------------------------------------------------------- crawl
def _access_gaps(ctx: _Ctx, blocked: dict[str, dict[str, Any]]) -> list[Gap]:
    out = []
    for cid, rec in sorted(blocked.items()):
        subject = (ctx.members.get(cid) or [cid])[0]
        reason = "+".join(sorted(rec["classes"]))
        out.append(_gap(ctx, GapKind.ACCESS_RESTRICTED, "SKIPPED_BY_POLICY", subject, reason,
                        f'control "{rec["label"]}" was not acted on: ' + "; ".join(sorted(rec["reasons"]))[:300],
                        screens=rec["from"], actions=ctx.actions))
    return out


def _code_gaps(ctx: _Ctx, counts: Counter[str]) -> list[Gap]:
    if "unresolved" in ctx.state and ctx.state["unresolved"] is None:
        return []          # the link stage had no code index: nothing was evaluated, so nothing is unmapped
    rows = ctx.state.get("unresolved")
    if rows is None:       # no link_state.json: derive the deterministic part from links + screens
        linked = {ctx.alias.get(l.src, l.src) for l in ctx.links if l.kind == "IMPLEMENTED_BY"}
        rows = []
        for cid, obs in sorted(ctx.members.items()):
            if cid in linked:
                continue
            el = ctx.obs.get(obs[0])
            named = el is not None and bool(element_texts(el))
            rows.append({"element_id": obs[0], "reason": "no_candidate" if named else "no_text",
                         "detail": "", "name": el.name if el else "", "role": el.role if el else "",
                         "screen_ids": []})
    by_subject: dict[str, dict[str, Any]] = {}
    for r in rows:
        # an observation id can belong to two canonical elements (UIElementObs.id has no dialog or ordinal
        # context); the graph resolves it to one, so a second row for the same id is the same gap
        if r["element_id"] in by_subject:
            counts["unmapped_merged_shared_obs_id"] += 1
        by_subject.setdefault(r["element_id"], r)
    return [_gap(ctx, GapKind.UNMAPPED_CODE, "OBSERVED", r["element_id"], r["reason"],
                 f'{r["role"]} "{r["name"]}" has no code link ({r["reason"]})'
                 + (f": {r['detail']}" if r.get("detail") else ""), screens=r.get("screen_ids", []))
            for _, r in sorted(by_subject.items())]


def _layout_closure(index: CodeIndex | None, cfg: TargetConfig) -> set[str]:
    if index is None:
        return set()
    seeds = sorted({r.layout for r in index.routes if r.layout} | set(cfg.code.global_files))
    return set(render_closure(index.edges, seeds)) if seeds else set()


def _unspecified_gaps(ctx: _Ctx, index: CodeIndex | None) -> tuple[list[Gap], dict[str, int]]:
    layout = _layout_closure(index, ctx.cfg)
    files_of: dict[str, set[str]] = {}
    for l in ctx.links:
        if l.kind == "IMPLEMENTED_BY":
            files_of.setdefault(ctx.alias.get(l.src, l.src), set()).add(l.dst)
    realized_dst = {ctx.alias.get(l.dst, l.dst) for l in ctx.links if l.kind == "REALIZED_BY"}
    patterns: dict[str, set[str]] = {}
    first: dict[str, tuple[ScreenState, UIElementObs]] = {}
    for s in sorted(ctx.inputs.screens, key=screen_order):
        for el in sorted(s.elements, key=lambda e: (e.ordinal, e.id)):
            cid = ctx.alias.get(el.id, el.id)
            patterns.setdefault(cid, set()).add(s.url_pattern)
            first.setdefault(cid, (s, el))
    per_screen: dict[str, list[tuple[str, UIElementObs]]] = {}
    for cid, (s, el) in sorted(first.items()):
        if el.role not in INTERACTIVE_ROLES or not element_texts(el) or cid in realized_dst:
            continue
        if len(patterns[cid]) >= GLOBAL_PATTERNS or (files_of.get(cid) and files_of[cid] <= layout):
            continue
        per_screen.setdefault(s.id, []).append((cid, el))
    gaps, listed, grouped = [], 0, 0
    by_id = {s.id: s for s in ctx.inputs.screens}
    for sid in sorted(per_screen, key=lambda i: screen_order(by_id[i])):
        rows = sorted(per_screen[sid], key=lambda r: (0 if files_of.get(r[0]) else 1, r[1].name.casefold(), r[0]))
        room = max(0, min(UNSPECIFIED_PER_SCREEN, UNSPECIFIED_TOTAL - listed))
        for cid, el in rows[:room]:
            subject = (ctx.members.get(cid) or [el.id])[0]
            gaps.append(_gap(ctx, GapKind.UNSPECIFIED_UI, "OBSERVED", subject, "no_requirement",
                             f'{el.role} "{el.name or el.text}" on "{by_id[sid].title}" matches no requirement',
                             screens=[sid]))
        listed += len(rows[:room])
        rest = len(rows) - len(rows[:room])
        if rest:
            grouped += rest
            gaps.append(_gap(ctx, GapKind.UNSPECIFIED_UI, "OBSERVED", sid, f"grouped:{rest}",
                             f'{rest} more named controls on "{by_id[sid].title}" match no requirement '
                             "(not listed one by one)", screens=[sid]))
    return gaps, {"listed": listed, "grouped": grouped, "screens": len(per_screen)}


# ---------------------------------------------------------------------------------------- re-crawl
def _goal_screens(run: RunContext, subdir: str) -> list[ScreenState]:
    """The screens a goal crawl wrote: run_crawl(artifact_subdir=s) puts them in crawl/<s>/screens.jsonl."""
    return read_jsonl(run.run_dir / "crawl" / subdir / "screens.jsonl", ScreenState)[0]


def _goal_pass(ctx: _Ctx, llm: LLM, gap: Gap, req: Requirement, subdir: str, known: set[str],
               budget: CallBudget) -> tuple[Gap | None, list[Link], dict[str, Any]]:
    """One goal crawl for one gap. Returns (the gap, raised; or None when closed), new links, a summary row."""
    from ..crawl import explorer

    crawl = explorer.run_crawl(ctx.run, ctx.cfg, llm, goals=[req.text], max_actions=RECRAWL_ACTIONS,
                               artifact_subdir=subdir)
    searched = _goal_screens(ctx.run, subdir)
    fresh = [s for s in searched if s.id not in known]
    links = judge_requirement(ctx.run, llm, req, Retriever(build_docs(fresh, ctx.cfg.name)), budget).links \
        if fresh else []
    if links:
        return None, links, {"requirement": req.id, "closed": True, "screens": sorted(s.id for s in fresh)}
    spent = gap.actions_spent + crawl.actions_spent
    negative = req.polarity == "must_not_exist"
    text = (f'{gap.reason or "no reason"}: re-crawled for {RECRAWL_ACTIONS} actions aiming at "{req.text}": '
            f"{len(fresh)} new screens, none supports it")
    raised = gap.model_copy(update={
        "screens_searched": sorted(set(gap.screens_searched) | {s.id for s in searched}),
        "actions_spent": spent,
        "confidence": (absent_confidence if negative else not_found_confidence)(spent, ctx.stop_reason),
        "evidence_id": ctx.run.evidence.add("gap.no_ui_evidence", req.id, text)})
    return raised, [], {"requirement": req.id, "closed": False, "new_screens": len(fresh),
                        "actions": crawl.actions_spent}


def _recrawl(ctx: _Ctx, llm: LLM, gaps: list[Gap], reqs: dict[str, Requirement]) -> tuple[
        list[Gap], list[Link], dict[str, Any]]:
    # imported late: playwright is only needed when a re-crawl is asked for
    from ..crawl import explorer

    info: dict[str, Any] = {"requested": True, "supported": False, "attempted": 0, "closed": [], "still_open": []}
    if "artifact_subdir" not in inspect.signature(explorer.run_crawl).parameters:
        info["skipped"] = ("crawl.explorer.run_crawl has no artifact_subdir parameter: a goal crawl would "
                           "overwrite the main crawl, so no re-crawl was made")
        return gaps, [], info
    info["supported"] = True
    known = {s.id for s in ctx.inputs.screens}
    order = {rid: i for i, rid in enumerate(reqs)}          # the ingest stage's ranking, then gap id
    todo = sorted((g for g in gaps if g.kind == GapKind.NO_UI_EVIDENCE and g.status == NOT_FOUND
                   and g.reason != "JUDGE_UNAVAILABLE"), key=lambda g: (order[g.subject_id], g.id))
    budget = CallBudget(MAX_JUDGE_CALLS)
    new_links: list[Link] = []
    by_id = {g.id: g for g in gaps}
    for i, gap in enumerate(todo[:RECRAWL_MAX_GAPS]):
        info["attempted"] += 1
        raised, links, row = _goal_pass(ctx, llm, gap, reqs[gap.subject_id], f"goal-{i}", known, budget)
        new_links.extend(links)
        if raised is None:
            by_id.pop(gap.id)
        else:
            by_id[gap.id] = raised
        info["closed" if raised is None else "still_open"].append(row)
    return sorted(by_id.values(), key=lambda g: (g.kind.value, g.subject_id, g.id)), new_links, info


# ------------------------------------------------------------------------------------------------ run
def run_gaps(run: RunContext, cfg: TargetConfig, store: Any, llm: LLM | None, *,
             recrawl: bool = False) -> dict[str, Any]:
    t0 = time.monotonic()
    ledger_before = run.ledger.summary()
    inputs = load_inputs(run, cfg, code=False)
    index = _load_index(run, cfg)
    links = load_links(run)
    state_path = run.run_dir / "links" / "link_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    ctx = _Ctx(run, cfg, inputs, links, state, summary=inputs.summary)
    ctx.realized = {l.src for l in links if l.kind == "REALIZED_BY"}
    canon = canonicalise(inputs.screens, cfg.name)
    ctx.alias = canon.alias
    for obs_id, cid in sorted(canon.alias.items()):
        ctx.members.setdefault(cid, []).append(obs_id)
    for s in inputs.screens:
        for el in s.elements:
            ctx.obs.setdefault(el.id, el)
    effort_path = run.run_dir / "crawl" / "goal_effort.json"      # written by a crawl run with --goal
    if effort_path.exists():
        ctx.goal_effort = {r["goal"]: r for r in json.loads(effort_path.read_text(encoding="utf-8"))}

    counts: Counter[str] = Counter()
    with run.stage("gaps.build") as span:
        blocked = _blocked_controls(ctx)
        gaps: list[Gap] = []
        for req in inputs.requirements:
            g = _requirement_gap(ctx, req, blocked, counts)
            if g is not None:
                gaps.append(g)
        gaps.extend(_access_gaps(ctx, blocked))
        gaps.extend(_code_gaps(ctx, counts))
        unspecified, unspec_stats = _unspecified_gaps(ctx, index)
        gaps.extend(unspecified)
        span.set(gaps=len(gaps))

    new_links: list[Link] = []
    recrawl_info: dict[str, Any] = {"requested": False}
    if recrawl and llm is not None:
        with run.stage("gaps.recrawl"):
            gaps, new_links, recrawl_info = _recrawl(ctx, llm, gaps, {r.id: r for r in inputs.requirements})
    elif recrawl:
        recrawl_info = {"requested": True, "supported": False, "skipped": "no LLM client supplied"}

    gaps = sorted({(g.id, g.bundle_hash): g for g in gaps}.values(), key=lambda g: (g.kind.value, g.subject_id, g.id))
    write_jsonl(run.path("links", "gaps.jsonl"), list(gaps))
    if new_links:
        write_jsonl(run.path("links", "links.jsonl"), sorted(
            [*links, *new_links], key=lambda l: (l.kind, l.src, l.dst, l.method)))
    write = {}
    if store is not None:
        write = store.write_gaps(gaps, run.run_id, target=cfg.name)
        if new_links:
            write["links"] = store.write_links(new_links, run.run_id, target=cfg.name)
    return _report(gaps, counts, unspec_stats, recrawl_info, write, ctx,
                   ledger_delta(run, ledger_before) | {"enabled": llm is not None}, time.monotonic() - t0)


def _load_index(run: RunContext, cfg: TargetConfig) -> CodeIndex | None:
    path: Path = run.run_dir / "code" / "index.json"
    if not path.exists():
        return None
    index = CodeIndex.model_validate_json(path.read_text(encoding="utf-8"))
    return index if index.target == cfg.name else None


def _report(gaps: list[Gap], counts: Counter[str], unspec: dict[str, int], recrawl: dict[str, Any],
            write: dict[str, Any], ctx: _Ctx, llm_used: dict[str, Any], seconds: float) -> dict[str, Any]:
    by_kind = Counter(g.kind.value for g in gaps)
    by_status = Counter(f"{g.kind.value}/{g.status}" for g in gaps)
    reasons = Counter(f"{g.kind.value}/{g.reason}" for g in gaps if g.reason)
    hist: dict[str, Counter[str]] = {}
    for g in gaps:
        if g.kind == GapKind.NO_UI_EVIDENCE:
            hist.setdefault(g.status, Counter())[f"{g.confidence:.2f}"] += 1
    return {
        "gaps": len(gaps),
        "by_kind": dict(sorted(by_kind.items())),
        "by_status": dict(sorted(by_status.items())),
        "by_reason": dict(sorted(reasons.items())),
        "requirements": len(ctx.inputs.requirements),
        "requirements_realized": len(ctx.realized & {r.id for r in ctx.inputs.requirements}),
        "negatives_contradicted": counts["negatives_contradicted"],
        "unmapped_merged_shared_obs_id": counts["unmapped_merged_shared_obs_id"],
        "requirements_not_gapped_unclassified": counts["not_gapped_unclassified"],
        "unspecified_ui": unspec,
        "confidence_histogram": {k: dict(sorted(v.items())) for k, v in sorted(hist.items())},
        "effort": {"actions_spent": ctx.actions, "stop_reason": ctx.stop_reason,
                   "E": round(effort_score(ctx.actions, ctx.stop_reason), 3), "spec_stale": ctx.stale},
        "bundle_hash": ctx.bundle,
        "recrawl": recrawl,
        "llm": llm_used,
        "graph": write,
        "wall_clock_s": round(seconds, 3),
    }

