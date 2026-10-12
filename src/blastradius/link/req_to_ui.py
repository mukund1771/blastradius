"""Requirement -> UI: BM25 candidates, then an LLM judge that may answer NONE (D07, D20; cases 22, 27, 53).

Documents. One per canonical element that has a name (graph.canonical: layout controls that recur on every
Screen are one document, not one per page) and one per Screen. An element document is its name, text,
aria-label, placeholder and title (counted OWN_TEXT_WEIGHT = 3 times) plus the context of the Screen it was
first seen on (title, dialog title, active tabs), because that context is shared by every element of the
screen and cannot tell them apart: on 10 author-picked (requirement, control) pairs of the real crawl the
control ranked in the top 15 for 7 with weight 1 and 9 with weight 3 (in-sample, not a recall estimate).
A Screen document is its title, dialog title, active tabs, url words and up to 40 element names.
BM25+ (rank_bm25; see `Retriever` for why not Okapi) ranks them for each requirement's text; the top 15 that
share at least one token with it are the candidates. A requirement with none has no candidates and costs no
LLM call.

Judge. Two independent samples per requirement (tier mid, effort low, purpose judge_req_ui): the candidate
list is shuffled with a seed derived from (requirement id, sample index) and labelled c1..cN, the schema is
`{id, supports: full|partial|none}` per candidate plus `none_of_above`. Per candidate:

    confidence = 0.80 (METHOD_PRIOR[bm25_judge]) x agreement x level
    agreement  = 1.0 both samples support, 0.5 one does (also when the other call failed), dropped if none
    level      = 1.0 full, 0.7 partial (the weaker of the supporting samples)

At most 3 links per requirement. A sample that says `none_of_above` supports nothing, whatever it lists.
Everything the judge sees (requirement text and quote, element names, screen titles) is untrusted-wrapped.

Which requirements are judged. Skipped by policy (they become gaps in link.gaps): ui_observable == "no",
requires_login, edition desktop or self-host. A requirement with polarity must_not_exist is judged like any
other: a supported candidate means the UI shows the thing the spec calls absent, and link.gaps reports that
as a contradiction instead of a gap. Per requirement the outcome is recorded as `judged` (both samples),
`partial` (one sample), `unjudged` (no verdict: no API key, offline miss, breaker, call cap) or
`no_candidates`, so link.gaps never turns "the judge did not run" into "the UI is absent".
"""

from __future__ import annotations

import random
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field
from rank_bm25 import BM25Plus

from ..graph.canonical import canonicalise
from ..ids import sha
from ..llm import LLM
from ..models import METHOD_PRIOR, Link, Requirement, ScreenState, Strict
from ..run import RunContext
from ..safety import UNTRUSTED_PREAMBLE, untrusted
from .budget import CallBudget, guarded
from .cascade import screen_order
from .text import prompt_text, tokens

PROMPT_VERSION = "judge-req-ui-1"
TOP_K = 15
SAMPLES = 2
MAX_LINKS = 3
MAX_JUDGE_CALLS = 400
SCREEN_NAMES = 40
OWN_TEXT_WEIGHT = 3        # an element's own words count this many times against its screen context
LEVEL: dict[str, float] = {"full": 1.0, "partial": 0.7}
SKIP_EDITIONS = frozenset({"desktop", "self-host"})


# ------------------------------------------------------------------------------------------ documents
@dataclass(frozen=True)
class UiDoc:
    key: str                   # canonical element id | screen id (unique)
    kind: Literal["element", "screen"]
    dst: str                   # Link.dst: representative observation id | screen id
    screen_id: str             # the Screen the document was built from
    screens: tuple[str, ...]   # element: screens of the same route that show it (Link.screen_ids); screen: ()
    text: str                  # what BM25 indexes
    label: str                 # what the judge reads


def _context(s: ScreenState) -> list[str]:
    return [t for t in (s.title, s.dialog_title or "", *s.active_tabs) if t]


def _where(s: ScreenState) -> str:
    parts = [f'screen "{prompt_text(s.title)}"' if s.title else f"route {s.url_pattern}"]
    if s.dialog_title:
        parts.append(f'dialog "{prompt_text(s.dialog_title)}"')
    if s.active_tabs:
        parts.append("tabs " + ", ".join(f'"{prompt_text(t)}"' for t in s.active_tabs))
    return ", ".join(parts)


def build_docs(screens: Sequence[ScreenState], target: str) -> list[UiDoc]:
    ordered = sorted(screens, key=screen_order)
    by_screen = {s.id: s for s in ordered}
    obs = {(s.id, el.id): el for s in ordered for el in s.elements}
    rank = {s.id: i for i, s in enumerate(ordered)}
    first: dict[str, tuple[str, str]] = {}
    shown_on: dict[str, set[str]] = {}
    for ap in canonicalise(ordered, target).appearances:
        cand = (ap["screen"], ap["obs_id"])
        shown_on.setdefault(ap["element"], set()).add(ap["screen"])
        old = first.get(ap["element"])
        if old is None or (rank[cand[0]], cand[1]) < (rank[old[0]], old[1]):
            first[ap["element"]] = cand
    docs: list[UiDoc] = []
    for cid, (sid, oid) in sorted(first.items()):
        el, s = obs[(sid, oid)], by_screen[sid]
        names = list(dict.fromkeys(
            " ".join(v.split()) for v in (el.name, el.text, el.aria_label, el.placeholder, el.title_attr) if v))
        if not names:
            continue
        # judged in the context of one screen; the claim covers that route in any of its states (tabs, dialogs)
        same_route = tuple(sorted(i for i in shown_on[cid] if by_screen[i].url_pattern == s.url_pattern))
        docs.append(UiDoc(cid, "element", oid, sid, same_route, " ".join([*names * OWN_TEXT_WEIGHT, *_context(s)]),
                          f'{el.role} {" / ".join(repr(prompt_text(n)) for n in names)} on {_where(s)}'))
    for s in ordered:
        names = list(dict.fromkeys(
            " ".join(el.name.split()) for el in sorted(s.elements, key=lambda e: (e.ordinal, e.id)) if el.name))
        words = " ".join(w for w in s.url_pattern.replace("-", " ").replace("_", " ").split("/") if w)
        docs.append(UiDoc(s.id, "screen", s.id, s.id, (), " ".join([*_context(s), words, *names[:SCREEN_NAMES]]),
                          f"{s.kind} {_where(s)} with controls: " + ", ".join(repr(prompt_text(n)) for n in names[:8])))
    return docs


class Retriever:
    """BM25+ (rank_bm25) over a fixed document set. Deterministic: ties break on document key.

    BM25Okapi's IDF turns negative for a term present in more than half of the documents and rank_bm25 then
    floors it at a fraction of the average IDF, which can itself be negative on a small crawl: the commonest UI
    words would silently score zero. BM25+ keeps IDF positive. rank_bm25 adds the per-term constant for
    absent terms too, so a candidate must also share at least one token with the query."""

    def __init__(self, docs: Sequence[UiDoc]):
        self.docs = [d for d in docs if tokens(d.text)]
        self._tokens = [frozenset(tokens(d.text)) for d in self.docs]
        self._bm25 = BM25Plus([tokens(d.text) for d in self.docs]) if self.docs else None

    def top(self, query: str, k: int = TOP_K) -> list[tuple[UiDoc, float]]:
        q = tokens(query)
        if self._bm25 is None or not q:
            return []
        scores = self._bm25.get_scores(q)
        want = set(q)
        ranked = sorted(((d, float(s)) for d, s, toks in zip(self.docs, scores, self._tokens, strict=True)
                         if want & toks), key=lambda p: (-p[1], p[0].key))
        return ranked[:k]


# ---------------------------------------------------------------------------------------------- judge
class CandidateVerdict(Strict):
    id: str = Field(description="The candidate label, for example c3.")
    supports: Literal["full", "partial", "none"] = Field(
        description="full: the candidate clearly provides the capability; partial: related or only part of "
                    "it; none: unrelated.")


class JudgeOut(Strict):
    verdicts: list[CandidateVerdict]
    none_of_above: bool = Field(description="True when no candidate supports the requirement.")


JUDGE_SYSTEM = f"""You check whether parts of a web application's user interface show the capability that a \
product requirement describes, for a QA team.

{UNTRUSTED_PREAMBLE}

You get one requirement and numbered candidates (c1, c2, ...): controls and screens that were observed in \
the running application. For every candidate answer full (the candidate clearly is, or contains, the \
control or screen that provides the capability), partial (related to it or only one part of it) or none \
(unrelated). If no candidate supports the requirement, set none_of_above to true. Judge only from the \
candidate's own words. Never assume a feature exists because the requirement says so. Reply only with the \
structured output, one verdict per candidate."""


def shuffled(items: Sequence[Any], req_id: str, sample: int) -> list[Any]:
    """Seeded by (requirement id, sample index); sample 1 never repeats sample 0's order when n >= 2."""
    out = list(items)
    random.Random(int(sha(req_id, sample, n=16), 16)).shuffle(out)
    if sample > 0 and len(out) > 1 and out == shuffled(items, req_id, 0):
        out.reverse()
    return out


def judge_prompt(req: Requirement, order: Sequence[UiDoc], sample: int) -> str:
    cand = untrusted("\n".join(f"c{i}: [{d.kind}] {d.label}" for i, d in enumerate(order, 1)), "crawl:candidates")
    body = untrusted(f"requirement: {req.text}\nquote: {req.quote}\nsection: {req.section}", f"spec:{req.id}")
    return (f"Requirement:\n{body}\n\nCandidates:\n{cand}\n\nReview pass {sample + 1} of {SAMPLES}. "
            "Give a verdict for every candidate.")


@dataclass
class ReqResult:
    req_id: str
    status: str                     # judged | partial | unjudged | no_candidates | skipped
    reason: str = ""
    candidates: int = 0
    links: list[Link] = field(default_factory=list)


def is_judgeable(req: Requirement) -> str:
    """"" when the requirement goes to the judge, else the policy reason it is skipped."""
    if req.requires_login:
        return "requires_login"
    if req.edition in SKIP_EDITIONS:
        return f"edition:{req.edition}"
    if req.ui_observable == "no":
        return "not_ui_observable"
    return ""


def _supports(out: JudgeOut | None, labels: dict[str, UiDoc]) -> dict[str, str]:
    """doc key -> full | partial for the candidates a sample supports."""
    if out is None or out.none_of_above:
        return {}
    got: dict[str, str] = {}
    for v in out.verdicts:
        doc = labels.get(v.id)
        if doc is not None and v.supports in LEVEL:
            got.setdefault(doc.key, v.supports)
    return got


def _confidence(samples: list[dict[str, str]], key: str) -> float | None:
    levels = [s[key] for s in samples if key in s]
    if not levels:
        return None
    agreement = 1.0 if len(levels) == SAMPLES else 0.5
    return round(METHOD_PRIOR["bm25_judge"] * agreement * min(LEVEL[v] for v in levels), 4)


def _judge_links(run: RunContext, req: Requirement, ranked: list[tuple[UiDoc, float]],
                 samples: list[dict[str, str]], model: str | None) -> list[Link]:
    scored = []
    for rank, (doc, _) in enumerate(ranked, 1):
        conf = _confidence(samples, doc.key)
        if conf is not None:
            scored.append((-conf, rank, doc.key, doc))
    links = []
    for neg, rank, _, doc in sorted(scored)[:MAX_LINKS]:
        verdicts = "/".join(s.get(doc.key, "none") for s in samples)
        summary = (f'requirement "{req.text}" ({req.section}) -> {doc.kind} {doc.label}; judged {verdicts} '
                   f"over {len(samples)} samples, bm25 rank {rank}")
        links.append(Link(kind="REALIZED_BY", src=req.id, dst=doc.dst, confidence=-neg, method="bm25_judge",
                          evidence_id=run.evidence.add("link.bm25_judge", req.id, summary), judge=model,
                          screen_ids=list(doc.screens)))
    return links


def judge_requirement(run: RunContext, llm: LLM, req: Requirement, retriever: Retriever,
                      budget: CallBudget) -> ReqResult:
    """Candidates for one requirement and, when there are any, up to SAMPLES judge calls."""
    ranked = retriever.top(req.text)
    if not ranked:
        return ReqResult(req.id, "no_candidates")
    docs = [d for d, _ in ranked]
    samples: list[dict[str, str]] = []
    for i in range(SAMPLES):
        if not budget.open:
            break
        order = shuffled(docs, req.id, i)
        out = guarded(run, llm, budget, context=f"judge_req_ui {req.id}", purpose="judge_req_ui", tier="mid",
                      effort="low", system=JUDGE_SYSTEM, user=judge_prompt(req, order, i), schema=JudgeOut,
                      prompt_version=PROMPT_VERSION)
        if out is not None:
            samples.append(_supports(out, {f"c{n}": d for n, d in enumerate(order, 1)}))
    if not samples:
        return ReqResult(req.id, "unjudged", "judge_unavailable", len(docs))
    model = llm.model_for("mid")
    status = "judged" if len(samples) == SAMPLES else "partial"
    return ReqResult(req.id, status, "", len(docs), _judge_links(run, req, ranked, samples, model))


@dataclass
class ReqLinkResult:
    links: list[Link] = field(default_factory=list)
    results: dict[str, ReqResult] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)


def link_req_to_ui(run: RunContext, llm: LLM | None, requirements: Iterable[Requirement],
                   screens: Sequence[ScreenState], target: str, *, max_calls: int = MAX_JUDGE_CALLS) -> ReqLinkResult:
    t0 = time.monotonic()
    out = ReqLinkResult()
    retriever = Retriever(build_docs(screens, target))
    budget = CallBudget(max_calls)
    for req in sorted(requirements, key=lambda r: r.id):
        skip = is_judgeable(req)
        if skip:
            out.results[req.id] = ReqResult(req.id, "skipped", skip)
        elif llm is None:
            out.results[req.id] = ReqResult(req.id, "unjudged", "judge_unavailable",
                                            len(retriever.top(req.text)))
        else:
            res = judge_requirement(run, llm, req, retriever, budget)
            if res.status == "unjudged" and not budget.open and budget.calls >= budget.max_calls:
                res.reason = "judge_budget"
            out.results[req.id] = res
            out.links.extend(res.links)
    out.links.sort(key=lambda l: (l.src, -l.confidence, l.dst))
    out.stats = _summarise(out, budget, len(retriever.docs), llm is not None, time.monotonic() - t0)
    return out


def _summarise(out: ReqLinkResult, budget: CallBudget, docs: int, llm_on: bool, seconds: float) -> dict[str, Any]:
    status = Counter(r.status for r in out.results.values())
    hist = Counter(f"{l.confidence:.2f}" for l in out.links)
    per_req = Counter(l.src for l in out.links)
    return {
        "requirements": len(out.results),
        "documents": docs,
        "status": dict(sorted(status.items())),
        "skipped_by_policy": dict(sorted(Counter(r.reason for r in out.results.values()
                                                 if r.status == "skipped").items())),
        "requirements_linked": len(per_req),
        "links": len(out.links),
        "confidence_histogram": {"bm25_judge": dict(sorted(hist.items()))},
        "judge_calls": budget.calls,
        "judge_failed_calls": budget.failed,
        "llm_enabled": llm_on,
        "wall_clock_s": round(seconds, 3),
    }


__all__ = [
    "JUDGE_SYSTEM", "JudgeOut", "ReqLinkResult", "ReqResult", "Retriever", "UiDoc", "build_docs",
    "is_judgeable", "judge_prompt", "judge_requirement", "link_req_to_ui", "shuffled",
]
