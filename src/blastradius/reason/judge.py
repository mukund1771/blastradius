"""Stage 7b-ii: the runtime residue judge (D18, D21; cases 50, 54, 55, 76).

Runs only on subgraphs the deterministic grader left AMBIGUOUS, and only to *demote*:

 * at most `judge.max_candidates` (12) evidenced candidates per subgraph (a candidate is a distinct control
   with an admitted route that carries evidence ids), presented in a deterministic shuffle so position carries
   no signal; at most `judge.max_calls` (6) calls per PR, two samples (two different shuffles) per subgraph;
 * the model answers per candidate RELEVANT | IRRELEVANT | UNSURE with a reason of at most 25 words and the
   evidence ids it relied on. A verdict that cites no evidence shown for that candidate counts as UNSURE;
 * IRRELEVANT in both samples caps the candidate's routes from that seed at `caps.judge_demote`. RELEVANT and
   UNSURE change nothing: the judge never raises a confidence and never adds an item (D18);
 * the only way it can *name something missing* is from the reserve list shown to it (forward routes the
   grader held back): a nomination both samples agree on is admitted at `caps.unmapped_probe` together with an
   evidence row recording the probe (the route and its witness path exist in the graph). Anything else it says
   is ignored;
 * two samples that disagree on a candidate leave it unchanged and go to needs_review.json.

Page text (control names, screen titles) and the diff are untrusted: wrapped with `safety.untrusted`, and the
system prompt carries UNTRUSTED_PREAMBLE. The model has no tools. With no key or offline the stage records why
and the deterministic caps stand.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, Literal

from pydantic import Field

from ..ids import stable_id
from ..llm import LLM
from ..models import Strict
from ..run import RunContext
from ..safety import UNTRUSTED_PREAMBLE, untrusted
from .facts import Hit, Retrieval, Route, SeedInfo
from .grade import Grading, ReviewItem, SeedGrade
from .pr import PRData
from .thresholds import Thresholds, load_thresholds
from .words import humanize_path

PROMPT_VERSION = "residue-judge-1"
PURPOSE = "grade_residue"
Label = Literal["RELEVANT", "IRRELEVANT", "UNSURE"]


class CandidateVerdict(Strict):
    id: str = Field(description="the candidate label exactly as given, such as c3")
    verdict: Label
    reason: str = Field(max_length=400, description="at most 25 words")
    evidence_ids: list[str] = Field(default_factory=list, description="evidence ids shown for this candidate")


class Nomination(Strict):
    id: str = Field(description="a label from the reserve list, such as r2")
    reason: str = Field(max_length=400, description="at most 25 words")


class JudgeOut(Strict):
    verdicts: list[CandidateVerdict]
    nominations: list[Nomination] = Field(default_factory=list)


SYSTEM = (
    "You check whether connections found by a code-impact tool are real. A pull request changed one source file "
    "(the CHANGE). The tool says some on-screen controls may be affected because they are drawn by code that "
    "uses the changed file (the CANDIDATES), each with the chain of files that links it. Judge each candidate "
    "only from the facts shown.\n"
    "- RELEVANT: the described change plausibly alters what this control shows or does.\n"
    "- IRRELEVANT: the link is only structural (the control's code imports something unrelated to the change, "
    "or the change cannot reach it).\n"
    "- UNSURE: the facts do not decide it.\n"
    "Rules: answer every candidate; give a reason of at most 25 words; list in evidence_ids only ids that are "
    "shown for that candidate; you may nominate a few labels from the RESERVE list that you think are "
    "clearly affected, and nothing else; never invent labels or evidence ids. You have no tools.\n"
    + UNTRUSTED_PREAMBLE
)


def _witness(route: Route) -> str:
    """The chain of files that links a control to the change, in words (file names are fork-authored: wrapped by
    the caller)."""
    chain = " -> ".join(humanize_path(p) for p in route.via) or humanize_path(route.file)
    names = [", ".join(s) for s in route.symbols if s]
    imported = f"; imports named: {' | '.join(names)}" if names else ""
    return f"{route.direction}, {route.hops} step(s): {chain}{imported}"


def _screen_words(titles: dict[str, str], screen_id: str) -> str:
    return titles.get(screen_id, "a screen")


def _candidates_for(retr: Retrieval, seed: SeedInfo, limit: int) -> list[tuple[Hit, Route]]:
    """Evidenced, admitted routes of this seed, one per control, strongest first (ties by key)."""
    best: dict[str, tuple[Hit, Route]] = {}
    for hit in retr.hits:
        for r in hit.routes:
            if r.seed != seed.path or not r.admitted or r.confidence <= 0 or not r.evidence_ids:
                continue
            key = hit.element_id
            if key not in best or (r.confidence, hit.screen_id) > (best[key][1].confidence, best[key][0].screen_id):
                best[key] = (hit, r)
    ranked = sorted(best.values(), key=lambda hr: (-hr[1].confidence, hr[0].element_id, hr[0].screen_id))
    return ranked[:limit]


def _reserve_for(retr: Retrieval, seed: SeedInfo, limit: int) -> list[tuple[Hit, Route]]:
    out = [(h, r) for h in retr.hits for r in h.routes
           if r.seed == seed.path and not r.admitted and r.evidence_ids]
    out.sort(key=lambda hr: (-hr[1].confidence, hr[0].element_id, hr[0].screen_id))
    return out[:limit]


def _shuffle(items: list[Any], seed_parts: tuple[str, ...]) -> list[Any]:
    digest = hashlib.sha256("\x1f".join(seed_parts).encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    out = list(items)
    rng.shuffle(out)
    return out


def _diff_excerpt(pr: PRData, seed: SeedInfo, lines: int = 40) -> str:
    f = pr.by_path().get(seed.display_path)
    if f is None or not f.patch:
        return "(no diff text available)"
    kept = [ln for ln in f.patch.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    return "\n".join(kept[:lines])


def build_prompt(pr: PRData, seed: SeedInfo, grade: SeedGrade, cands: list[tuple[Hit, Route]],
                 reserve: list[tuple[Hit, Route]], titles: dict[str, str], order_key: tuple[str, ...],
                 sample: tuple[int, int] = (0, 1)) -> tuple[
        str, dict[str, tuple[Hit, Route]], dict[str, tuple[Hit, Route]]]:
    """The user message of one review pass. The pass number is part of the text, so two passes are two distinct
    requests (two different cache entries, two real calls) even when the shuffles happen to coincide."""
    labelled = {f"c{i}": hr for i, hr in enumerate(_shuffle(cands, order_key), 1)}
    reserve_l = {f"r{i}": hr for i, hr in enumerate(reserve, 1)}
    sig = grade.signals
    change = "\n".join([
        f"file: {humanize_path(seed.display_path)} ({seed.file_class.value}); kind of change: {seed.kind.value}",
        "changed exported names: " + (", ".join(seed.touched) or "unknown")
        + "; added: " + (", ".join(seed.added_symbols) or "none")
        + "; removed: " + (", ".join(seed.removed_symbols) or "none"),
        f"breadth: {sig.effective_ui_files_h2} UI files within two steps, {sig.effective_fraction_h2:.0%} of the app",
        "diff (changed lines):", _diff_excerpt(pr, seed)])
    parts = [f"REVIEW PASS {sample[0] + 1} OF {sample[1]}", "CHANGE",
             untrusted(change, f"pr#{pr.number}:change"), "", "CANDIDATES"]
    for label, (hit, route) in labelled.items():
        facts = (f"control: {hit.element_role} {hit.element_name}\nscreen: {_screen_words(titles, hit.screen_id)}\n"
                 f"link: {_witness(route)}")
        parts.append(f"{label}: {untrusted(facts, 'crawl:candidate', max_chars=600)} method {route.impl_method}; "
                     f"confidence {route.confidence:.2f}; evidence: {', '.join(route.evidence_ids)}")
    if reserve_l:
        parts += ["", "RESERVE (held back by the tool; nominate only clearly affected ones)"]
        for label, (hit, route) in reserve_l.items():
            facts = f"control: {hit.element_role} {hit.element_name}\nlink: {_witness(route)}"
            parts.append(f"{label}: {untrusted(facts, 'crawl:reserve', max_chars=600)}")
    return "\n".join(parts), labelled, reserve_l


def _valid(verdict: CandidateVerdict, cand: tuple[Hit, Route]) -> Label:
    """A RELEVANT or IRRELEVANT verdict must cite evidence that was shown for this candidate."""
    shown = set(cand[1].evidence_ids)
    cited = [e for e in verdict.evidence_ids if e in shown]
    return verdict.verdict if cited or verdict.verdict == "UNSURE" else "UNSURE"


def run_judge(run: RunContext, llm: LLM, pr: PRData, retr: Retrieval, grading: Grading, titles: dict[str, str],
              th: Thresholds | None = None) -> dict[str, Any]:
    """Judge the AMBIGUOUS subgraphs (module docstring). Returns a summary; routes are demoted in place and
    `grading` gains review items for disagreements."""
    th = th or load_thresholds()
    jt = th.judge
    summary: dict[str, Any] = {"calls": 0, "subgraphs": 0, "demoted": 0, "disagreements": 0, "nominated": 0,
                               "skipped": None}
    ambiguous = [g for g in grading.seeds if g.verdict == "AMBIGUOUS" and not g.seed.startswith("locale:")]
    if not ambiguous:
        summary["skipped"] = "no ambiguous subgraph"
        return summary
    seeds = {s.path: s for s in retr.seeds}
    ambiguous.sort(key=lambda g: (-g.signals.closure_ui_files_h2, g.seed))
    with run.stage("reason.judge", subgraphs=len(ambiguous)) as span:
        for g in ambiguous:
            if summary["calls"] + jt.samples > jt.max_calls:
                summary["skipped"] = "call budget"
                break
            seed = seeds[g.seed]
            cands = _candidates_for(retr, seed, jt.max_candidates)
            if not cands:
                continue
            ok = _judge_subgraph(run, llm, pr, retr, g, seed, cands, titles, th, summary)
            if not ok:
                summary["skipped"] = summary["skipped"] or "model unavailable"
                break
        span.set(**{k: v for k, v in summary.items() if isinstance(v, int)})
    return summary


def _judge_subgraph(run: RunContext, llm: LLM, pr: PRData, retr: Retrieval, g: SeedGrade, seed: SeedInfo,
                    cands: list[tuple[Hit, Route]], titles: dict[str, str], th: Thresholds,
                    summary: dict[str, Any]) -> bool:
    jt = th.judge
    reserve = _reserve_for(retr, seed, jt.reserve)
    samples: list[dict[str, Label]] = []
    nominated: list[set[str]] = []
    for k in range(jt.samples):
        user, labelled, reserve_l = build_prompt(pr, seed, g, cands, reserve, titles,
                                                 (jt.shuffle_seed, str(pr.number), seed.path, str(k)),
                                                 (k, jt.samples))
        try:
            out = llm.structured(purpose=PURPOSE, tier="cheap", system=SYSTEM, user=user, schema=JudgeOut,
                                 max_tokens=1800, prompt_version=PROMPT_VERSION)
        except Exception as exc:  # noqa: BLE001 - every LLM or SDK failure means "no judge", never a failed run
            run.tracer.event("judge_unavailable", seed=seed.path, error=type(exc).__name__)
            summary["error"] = type(exc).__name__
            return False
        summary["calls"] += 1
        by_label = {v.id: v for v in out.verdicts if v.id in labelled}
        mapped: dict[str, Label] = {}
        for label, cand in labelled.items():
            v = by_label.get(label)
            key = cand[0].element_id
            mapped[key] = _valid(v, cand) if v else "UNSURE"
        samples.append(mapped)
        nominated.append({reserve_l[n.id][0].element_id + "|" + reserve_l[n.id][0].screen_id
                          for n in out.nominations[:jt.max_nominations] if n.id in reserve_l})
    summary["subgraphs"] += 1
    _apply(run, retr, g, seed, cands, samples, th, summary)
    _nominate(run, retr, seed, reserve, nominated, th, summary)
    return True


def _apply(run: RunContext, retr: Retrieval, g: SeedGrade, seed: SeedInfo, cands: list[tuple[Hit, Route]],
           samples: list[dict[str, Label]], th: Thresholds, summary: dict[str, Any]) -> None:
    disagreeing: list[str] = []
    for hit, _ in cands:
        votes = {s.get(hit.element_id, "UNSURE") for s in samples}
        if len(votes) > 1:
            disagreeing.append(hit.element_name or hit.element_id)
            summary["disagreements"] += 1
            continue
        if votes == {"IRRELEVANT"}:
            for h in retr.hits:
                if h.element_id != hit.element_id:
                    continue
                for r in h.routes:
                    if r.seed == seed.path and r.admitted:
                        r.cap("judge_demote", th.caps.judge_demote)
            summary["demoted"] += 1
    if disagreeing:
        g.reviews.append(ReviewItem(
            id=stable_id("rev", seed.display_path, "JUDGE_DISAGREEMENT"), code="NEEDS_HUMAN",
            reason_kind="JUDGE_DISAGREEMENT", subject=f"controls linked to the {humanize_path(seed.display_path)} code",
            detail=f"two independent checks disagreed about {len(disagreeing)} control(s); they were left as found",
            what_to_check="Look at these controls by hand: " + ", ".join(sorted(disagreeing)[:5]) + ".",
            seeds=[seed.display_path], rank=35))


def _nominate(run: RunContext, retr: Retrieval, seed: SeedInfo, reserve: list[tuple[Hit, Route]],
              nominated: list[set[str]], th: Thresholds, summary: dict[str, Any]) -> None:
    """A reserve route both samples nominated is admitted, capped, with a probe evidence row."""
    if len(nominated) < 2:
        return
    agreed = set.intersection(*nominated) if nominated else set()
    for hit, route in reserve:
        if f"{hit.element_id}|{hit.screen_id}" not in agreed or route.admitted:
            continue
        ev = run.evidence.add(
            "reason.probe", f"judge-nomination:{seed.path}->{hit.element_id}",
            f"a forward link from {humanize_path(seed.display_path)} to {humanize_path(route.file)} exists in the "
            f"code graph ({route.hops} step); admitted at 'possible' after two independent nominations")
        route.admitted = True
        route.restrict("unmapped_probe", th.caps.unmapped_probe)
        route.evidence_ids = sorted({*route.evidence_ids, ev})
        summary["nominated"] += 1
