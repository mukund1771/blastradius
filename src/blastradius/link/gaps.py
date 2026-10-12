"""Gaps: absence as an evidenced, effort-carrying node (D08, D09; cases 8, 22, 27, 34, 46, 48).

A missing edge cannot say "does not exist" from "did not look" or "was not allowed to look". This stage turns
the link stage's results into Gap rows (links/gaps.jsonl, store.write_gaps), one per (kind, subject):

 requirement, in this precedence (first match wins)
   NOT_UI_TESTABLE  CLASSIFIED         ui_observable == "no" or edition desktop / self-host
   ACCESS_RESTRICTED SKIPPED_BY_POLICY requires_login; or an unrealised requirement all of whose words
                                       name a control the crawl refused to act on (a blocked transition)
   (realised by >= 1 REALIZED_BY link: no gap. A must_not_exist requirement that IS realised is the
    reverse of a pass: the UI shows what the spec says is absent. It is written as an UNSPECIFIED_UI gap
    (reason contradicts_must_not_exist, subject the requirement, confidence 0.0: it is an observation, not
    an absence claim) and counted in the summary as negatives_contradicted)
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
    CONFIRMED_ABSENT         min(0.60, 0.80 x E)        the spec's explicit negative, corroborated by the
                             crawl; never below NOT_FOUND at equal effort, same ceiling: a bounded
                             unauthenticated crawl cannot prove a negative. Derived from effort alone: a
                             crawl that spent no actions has looked at nothing, whatever the spec says
    judge did not run        0.0 and reason JUDGE_UNAVAILABLE (we did not look; never an absence claim)

Reason SPEC_MAY_BE_STALE is set when spec/stats.json says the docs predate the app (case 21). CONFIG_DEPENDENT
(feature flags, case 11) is not produced: the crawl records no flags. Gaps carry the crawl's bundle_hash and are
never reused across bundles (case 48); NOT_UI_TESTABLE depends only on the spec and carries none. When the crawl
ran with goals, crawl/goal_effort.json (screens and actions that overlapped the requirement) is cited in the
gap's evidence row.

What `gaps` trusts (case 48). links/links.jsonl and links/link_state.json were derived from one crawl, one
code index and one requirement list; `gaps` reads the current ones. link_state.json is compared with the
current crawl (target, bundle_hash, a fingerprint of the screens) and code snapshot. A state that does not
belong to them is recorded as a failure and is not used: every requirement is then unjudged
(JUDGE_UNAVAILABLE, confidence 0.0, never an absence claim), UNMAPPED_CODE is skipped, and UNSPECIFIED_UI is
skipped (what no requirement realises cannot be said without the judgements). Whatever the state says, a
REALIZED_BY link whose dst is not a Screen, element or Flow of the current artifacts, and an IMPLEMENTED_BY
link whose element or file is not, are dropped and counted. When the link stage has not run at all (no
state, no links) there is no UNMAPPED_CODE gap: "link not run" is a statement about the pipeline, not about
the code.

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

from pydantic import ValidationError

from ..code.index import render_closure
from ..failures import FailureCode
from ..graph.canonical import canonicalise
from ..ids import stable_id
from ..llm import LLM
from ..models import CodeIndex, CrawlSummary, Gap, GapKind, Link, Requirement, ScreenState, UIElementObs
from ..run import RunContext
from ..targets import TargetConfig
from .budget import CallBudget, ledger_delta
from .cascade import screen_order
from .inputs import (
    LinkInputs,
    crawl_fingerprint,
    invalid_artifact,
    load_inputs,
    load_links,
    read_json,
    read_jsonl,
    read_text,
    write_jsonl,
)
from .req_to_ui import MAX_JUDGE_CALLS, Retriever, build_docs, judge_requirement
from .text import tokens
from .ui_to_code import INTERACTIVE_ROLES, element_texts

NOT_FOUND = "NOT_FOUND_WITHIN_BUDGET"
NOT_FOUND_CAP = 0.60
ABSENT_SLOPE, ABSENT_CAP = 0.80, 0.60
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
    """A corroborated explicit negative is worth more than a miss at equal effort (SLOPE > NOT_FOUND's 0.60),
    but is derived from the effort alone: with nothing looked at there is nothing to corroborate."""
    return round(min(ABSENT_CAP, ABSENT_SLOPE * effort_score(actions_spent, stop_reason)), 2)


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
    state_status: str = "ok"          # ok | unverified | missing | invalid | stale
    state_reason: str = ""
    link_ran: bool = True             # link_state.json or at least one link exists
    contradicted: list[str] = field(default_factory=list)
    screen_by_id: dict[str, ScreenState] = field(default_factory=dict)

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
    def judgements_usable(self) -> bool:
        """The link stage ran and its state belongs to the current crawl."""
        return self.link_ran and self.state_status in ("ok", "unverified", "missing")

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
    if not ctx.judgements_usable:     # a state of another crawl says nothing about this one
        return "unjudged"
    rec = ctx.state.get("requirements", {}).get(req.id)
    status = rec.get("status") if isinstance(rec, dict) else None
    return status if isinstance(status, str) else "unjudged"


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
            return _contradiction_gap(ctx, req)
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


def _label_of(ctx: _Ctx, node_id: str) -> str:
    el = ctx.obs.get(node_id)
    if el is not None:
        return f'{el.role} "{" ".join((el.name or el.text).split())[:60]}"'
    s = ctx.screen_by_id.get(node_id)
    return f'screen "{" ".join(s.title.split())[:60]}"' if s is not None else node_id


def _contradiction_gap(ctx: _Ctx, req: Requirement) -> Gap:
    """The spec says it must not exist and the UI shows it: not a pass. The link's confidence says how sure
    the judge is that the control is that thing; the gap itself makes no claim about absence."""
    rows = sorted((l for l in ctx.links if l.kind == "REALIZED_BY" and l.src == req.id),
                  key=lambda l: (-l.confidence, l.dst))
    ctx.contradicted.append(req.id)
    screens = {sid for l in rows for sid in l.screen_ids} or {
        l.dst for l in rows if l.dst in ctx.screen_by_id}
    shown = "; ".join(f"{_label_of(ctx, l.dst)} (judge {l.confidence:.2f})" for l in rows[:3])
    return _gap(ctx, GapKind.UNSPECIFIED_UI, "OBSERVED", req.id, "contradicts_must_not_exist",
                f'requirement "{req.text}" says it must not exist, but the crawl shows it: {shown}'
                + ("; the spec may be stale" if ctx.stale else ""), screens=screens)


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
    if not ctx.link_ran:
        return []          # "link not run": nothing was evaluated, so nothing is unmapped
    if ctx.state_status in ("stale", "invalid"):
        return []          # the unresolved rows describe another crawl, or cannot be read
    if "unresolved" in ctx.state and ctx.state["unresolved"] is None:
        return []          # the link stage had no code index: nothing was evaluated, so nothing is unmapped
    rows = ctx.state.get("unresolved")
    if rows is not None and not isinstance(rows, list):
        rows = None
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
        if not isinstance(r, dict) or not isinstance(r.get("element_id"), str):
            counts["unmapped_rows_malformed"] += 1
            continue
        # an observation id can belong to two canonical elements (UIElementObs.id has no dialog or ordinal
        # context); the graph resolves it to one, so a second row for the same id is the same gap
        if r["element_id"] in by_subject:
            counts["unmapped_merged_shared_obs_id"] += 1
        by_subject.setdefault(r["element_id"], r)
    out = []
    for _, r in sorted(by_subject.items()):
        reason = str(r.get("reason") or "no_candidate")
        screens = r.get("screen_ids")
        out.append(_gap(ctx, GapKind.UNMAPPED_CODE, "OBSERVED", r["element_id"], reason,
                        f'{r.get("role", "")} "{r.get("name", "")}" has no code link ({reason})'
                        + (f": {r['detail']}" if r.get("detail") else ""),
                        screens=[x for x in screens if isinstance(x, str)] if isinstance(screens, list)
                        else []))
    return out


def _unmapped_note(ctx: _Ctx) -> dict[str, str]:
    """Why UNMAPPED_CODE was or was not produced, in words a reader of the summary can act on."""
    if not ctx.link_ran:
        return {"status": "skipped", "reason": "link not run: no links/link_state.json and no "
                                               "links/links.jsonl; run `blastradius link`"}
    if ctx.state_status in ("stale", "invalid"):
        return {"status": "skipped", "reason": f"link_state.json {ctx.state_status}: {ctx.state_reason}"}
    if "unresolved" in ctx.state and ctx.state["unresolved"] is None:
        return {"status": "skipped", "reason": "the link stage had no code index"}
    return {"status": "ok", "reason": ""}


def _layout_closure(index: CodeIndex | None, cfg: TargetConfig) -> set[str]:
    if index is None:
        return set()
    seeds = sorted({r.layout for r in index.routes if r.layout} | set(cfg.code.global_files))
    return set(render_closure(index.edges, seeds)) if seeds else set()


def _unspecified_gaps(ctx: _Ctx, index: CodeIndex | None) -> tuple[list[Gap], dict[str, Any]]:
    if ctx.inputs.requirements and not ctx.judgements_usable:
        # "no requirement realises it" needs the judgements; without them it would flag every control
        return [], {"listed": 0, "grouped": 0, "screens": 0,
                    "skipped": "link not run" if not ctx.link_ran else f"link_state.json {ctx.state_status}"}
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
    ctx = _Ctx(run, cfg, inputs, links, {}, summary=inputs.summary)
    canon = canonicalise(inputs.screens, cfg.name)
    ctx.alias = canon.alias
    for obs_id, cid in sorted(canon.alias.items()):
        ctx.members.setdefault(cid, []).append(obs_id)
    for s in inputs.screens:
        ctx.screen_by_id[s.id] = s
        for el in s.elements:
            ctx.obs.setdefault(el.id, el)
    ctx.state, ctx.state_status, ctx.state_reason = _read_state(ctx)
    ctx.link_ran = ctx.state_status != "missing" or bool(links)
    dropped = _drop_stale_links(ctx, index)
    ctx.realized = {l.src for l in ctx.links if l.kind == "REALIZED_BY"}
    ctx.goal_effort = _read_goal_effort(run)

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
            [*ctx.links, *new_links], key=lambda l: (l.kind, l.src, l.dst, l.method, -l.confidence)))
    write = {}
    if store is not None:
        write = store.write_gaps(gaps, run.run_id, target=cfg.name)
        if new_links:
            write["links"] = store.write_links(new_links, run.run_id, target=cfg.name)
    return _report(gaps, counts, unspec_stats, recrawl_info, write, ctx, dropped,
                   ledger_delta(run, ledger_before) | {"enabled": llm is not None}, time.monotonic() - t0)


def _read_state(ctx: _Ctx) -> tuple[dict[str, Any], str, str]:
    """(state, status, reason). The state is returned only when it can be trusted for the current crawl;
    status is ok | unverified (an older file with nothing to compare) | missing | invalid | stale."""
    run, cfg = ctx.run, ctx.cfg
    path = run.run_dir / "links" / "link_state.json"
    raw, problem = read_json(path)
    if problem == "missing":
        return {}, "missing", "no links/link_state.json"
    shape = ""
    if not problem:
        if not isinstance(raw, dict):
            shape = f"holds a JSON {type(raw).__name__}, not an object"
        elif not isinstance(raw.get("requirements", {}), dict) or not isinstance(
                raw.get("unresolved"), (list, type(None))):
            shape = "its `requirements` or `unresolved` have the wrong shape"
    if problem or shape:
        invalid_artifact(run, path, problem or shape, rerun="blastradius link")
        return {}, "invalid", problem or shape
    reasons = []
    if "target" in raw and raw["target"] != cfg.name:
        reasons.append(f"it was derived for target {raw['target']!r}, this run is {cfg.name!r}")
    summary = ctx.summary
    if "bundle_hash" in raw and summary is not None and raw["bundle_hash"] != summary.bundle_hash:
        reasons.append(f"it was derived from crawl bundle {raw['bundle_hash']!r}, the current crawl is "
                       f"{summary.bundle_hash!r}")
    if "crawl_sha" in raw and raw["crawl_sha"] != crawl_fingerprint(ctx.inputs.screens):
        reasons.append("it was derived from a different set of screens than crawl/screens.jsonl holds now")
    if reasons:
        why = "; ".join(reasons)
        run.failures.record(FailureCode.CONFIG_INVALID,
                            f"links/link_state.json is stale: {why}; judgements ignored, run "
                            "`blastradius link` again", stage="gaps", artifact="links/link_state.json")
        return {}, "stale", why
    comparable = any(k in raw for k in ("target", "bundle_hash", "crawl_sha"))
    return raw, ("ok" if comparable else "unverified"), ""


def _drop_stale_links(ctx: _Ctx, index: CodeIndex | None) -> dict[str, int]:
    """Keep only links that point at nodes the current artifacts have (case 48)."""
    screens = set(ctx.screen_by_id)
    elements = set(ctx.obs) | set(ctx.members)
    flows: set[str] = set()
    text = read_text(ctx.run.run_dir / "links" / "flows.jsonl")
    for line in (text or "").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            flows.add(row["id"])
    files = {f.path for f in index.files} if index is not None else None
    kept: list[Link] = []
    dropped: Counter[str] = Counter()
    for l in ctx.links:
        if l.kind == "REALIZED_BY":
            ok = l.dst in screens or l.dst in elements or l.dst in flows
        else:
            ok = l.src in elements and (files is None or l.dst in files)
        if ok:
            kept.append(l)
        else:
            dropped[l.kind] += 1
    ctx.links = kept
    return dict(sorted(dropped.items()))


def _read_goal_effort(run: RunContext) -> dict[str, dict[str, Any]]:
    path = run.run_dir / "crawl" / "goal_effort.json"      # written by a crawl run with --goal
    if not path.exists():
        return {}
    raw, problem = read_json(path)
    if problem or not isinstance(raw, list):
        invalid_artifact(run, path, problem or f"holds a JSON {type(raw).__name__}, not a list",
                         rerun="blastradius crawl --goal")
        return {}
    return {r["goal"]: r for r in raw if isinstance(r, dict) and isinstance(r.get("goal"), str)}


def _load_index(run: RunContext, cfg: TargetConfig) -> CodeIndex | None:
    path: Path = run.run_dir / "code" / "index.json"
    if not path.exists():
        return None
    text = read_text(path)
    try:
        if text is None:
            raise ValueError("unreadable")
        index = CodeIndex.model_validate_json(text)
    except (ValidationError, ValueError):
        invalid_artifact(run, path, "does not validate as a code index (corrupt or truncated)",
                         rerun="blastradius index")
        return None
    return index if index.target == cfg.name else None


def _report(gaps: list[Gap], counts: Counter[str], unspec: dict[str, Any], recrawl: dict[str, Any],
            write: dict[str, Any], ctx: _Ctx, dropped: dict[str, int], llm_used: dict[str, Any],
            seconds: float) -> dict[str, Any]:
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
        "negatives_contradicted": len(ctx.contradicted),
        "contradicted_requirement_ids": sorted(ctx.contradicted),
        "link_state": {"status": ctx.state_status, "reason": ctx.state_reason, "link_ran": ctx.link_ran},
        "unmapped_code": _unmapped_note(ctx),
        "stale_links_dropped": dropped,
        "unmapped_merged_shared_obs_id": counts["unmapped_merged_shared_obs_id"],
        "unmapped_rows_malformed": counts["unmapped_rows_malformed"],
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

