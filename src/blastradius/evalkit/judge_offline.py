"""Offline claim judge: evidence-only, binary items, seeded corruptions (plan section 9.4; D21, D22, D26;
cases 54, 72). It never runs in the product path and sees none of the generator's context.

What the judge sees for one claim: the claim text and the summary strings of the evidence rows it cites, in a
shuffled order, each wrapped as untrusted data. It does not see the generator's reasoning, the edge
confidence, the link method, the evidence kind or ref, or which band the claim was given. Four binary items:

  F1 every entity the claim names appears in the evidence
  F2 the relation the claim asserts is not stronger than the evidence supports
  F3 every number in the claim equals a number in the evidence
  F4 a quote: an exact substring of one cited evidence summary that entails the claim (string-checked here; a
     quote that is not a substring fails the item whatever the model says)

verdict per sample: `cannot_tell` when the model says it cannot tell; `supported` iff F1, F2, F3 are true and
F4 verifies; otherwise `unsupported`. k samples (the sample index is part of the prompt so a cache hit cannot
fake agreement); unanimous -> that verdict, anything else -> `needs_review`. A claim whose cited ids resolve
to no evidence is `unsupported` without a call.

Validation (D22): 20 deterministic corruptions of real claims have known labels (unsupported). The fraction
the judge flags is reported as an UPPER BOUND on its true-negative rate with a Wilson interval: corruptions
are easier to spot than natural errors, and generator and judge share a vendor family (case 72). The natural
claims' verdicts are reported as what they are: unlabelled observations, plus agreement with hand labels when
a file of them exists.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import Field

from ..ids import sha
from ..llm import LLM, LLMError, Tier
from ..models import Band, Strict
from ..safety import UNTRUSTED_PREAMBLE, untrusted
from .stats import Proportion, cohens_kappa

PURPOSE = "offline_judge"
PROMPT_VERSION = "offline-judge-1"
QUOTE_MIN_CHARS = 4
VERDICTS = ("supported", "unsupported", "cannot_tell", "needs_review", "error")
SAME_FAMILY_CAVEAT = (
    "Generator and judge are both Anthropic models (same vendor family): self-preference and shared blind "
    "spots are possible (case 72)."
)
CORRUPTION_CAVEAT = (
    "Seeded corruptions are easier to detect than natural errors, so the true-negative rate below is an "
    "upper bound on the judge's rate for natural errors (case 72)."
)
KINDS = ("swap_evidence", "flip_status", "inflate_band", "change_number", "cite_missing", "drop_qualifier")
_INFLATE = (
    (re.compile(r"\bmight (?:also )?be affected\b", re.IGNORECASE), "will change"),
    (re.compile(r"\bworth a quick look\b", re.IGNORECASE), "will change"),
    (re.compile(r"\bprobably affected\b", re.IGNORECASE), "is changed by this PR"),
    (re.compile(r"\blikely changes\b", re.IGNORECASE), "will change"),
)
_AT_RISK = re.compile(r"\bat risk\b", re.IGNORECASE)
_PROBABLY = re.compile(r"\bprobably\s+", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w-])\d+(?![\w-])")
_WS = re.compile(r"\s+")
_SCORE = re.compile(r"\s*[,;(]?\s*\b(?:confidence|conf)\b\s*[:=]?\s*\d*\.?\d+\)?", re.IGNORECASE)
_METHOD = re.compile(r"\b(?:i18n_key|literal_text|llm_pick|bm25_judge)\b")
_QUOTED_NAME = re.compile("“([^”]{2,80})”")

SYSTEM = f"""You are an offline fact checker for a change-impact report. You judge ONE claim against the \
evidence entries that the claim cites, and nothing else: use no outside knowledge and no guess about the \
software.

{UNTRUSTED_PREAMBLE}

Answer four binary items.
f1_entities_in_evidence: true only if every screen, control, requirement or file the claim names appears in \
the evidence entries.
f2_relation_not_stronger: true only if the relation the claim asserts is no stronger than the evidence \
supports. A claim that something will change needs evidence that its own code changed; evidence that one \
file merely imports or renders another supports at most a weaker, hedged statement.
f3_numbers_match: true only if every number in the claim equals a number in the evidence (or the count of \
items the evidence lists). True when the claim has no numbers.
can_tell: false when the evidence is too thin or ambiguous to decide the items above.
quote: the shortest exact substring of ONE evidence entry that entails the claim, copied character for \
character; an empty string when no entry entails it.
Reply only with the structured output."""


class JudgeAnswer(Strict):
    f1_entities_in_evidence: bool = Field(description="Every entity the claim names appears in the evidence.")
    f2_relation_not_stronger: bool = Field(description="The claimed relation is not stronger than the "
                                                       "evidence.")
    f3_numbers_match: bool = Field(description="Every number in the claim appears in the evidence.")
    can_tell: bool = Field(description="False when the evidence is too thin or ambiguous to decide.")
    quote: str = Field(description="Exact substring of one evidence entry that entails the claim, or empty.")


# -------------------------------------------------------------------------------------------- items
@dataclass(frozen=True)
class ClaimRef:
    id: str
    text: str
    evidence_ids: tuple[str, ...]
    band: Band | None = None
    pr: str = ""


@dataclass(frozen=True)
class JudgeItem:
    item_id: str
    origin: str                                  # "natural" or a corruption kind
    text: str
    evidence: tuple[tuple[str, str], ...]        # (evidence id, summary) for the ids that resolved
    cited: int                                   # ids the claim cites, resolved or not
    source_claim_id: str | None = None
    note: str = ""


def scrub_scores(summary: str) -> str:
    """The evidence text without the generator's own score or link method, which some summaries embed
    ("direct impact on Home page: 6 control(s), confidence 1.00"): the judge must not lean on them."""
    return _WS.sub(" ", _METHOD.sub("", _SCORE.sub("", summary))).strip()


def resolve(claim_text: str, evidence_ids: Sequence[str], evidence: Mapping[str, Mapping[str, Any]], *,
            item_id: str, origin: str = "natural", source: str | None = None, note: str = "") -> JudgeItem:
    scrubbed = {eid: scrub_scores(str(evidence[eid].get("summary") or ""))
                for eid in dict.fromkeys(evidence_ids) if eid in evidence}
    got = tuple((eid, text) for eid, text in scrubbed.items() if text)
    return JudgeItem(item_id, origin, claim_text, got, len(set(evidence_ids)), source, note)


def entity_coverage(items: Sequence[JudgeItem]) -> dict[str, Any]:
    """Of the product names a claim quotes (set in “ ”), how many occur in the evidence it cites. Low coverage
    means the evidence is written in code words (paths, ids) and the judge's F1 cannot be met whatever the
    claim's truth; it is a property of the evidence rows, reported so the verdicts are read correctly."""
    names = [(i, n) for i in items for n in _QUOTED_NAME.findall(i.text)]
    found = sum(1 for i, n in names if any(_squash(n) in _squash(t) for _, t in i.evidence))
    return {"quoted_names": len(names), "found_in_cited_evidence": Proportion.of(found, len(names)).as_dict(),
            "claims_with_a_quoted_name": len({i.item_id for i, _ in names})}


def _squash(text: str) -> str:
    return _norm(text).casefold()


def visible(text: str) -> str:
    """What the judge actually reads of `text`: the body of its untrusted wrapper (comments, control and
    delimiter characters already neutralised), which is what a quote has to be a substring of."""
    wrapped = untrusted(text, "e")
    return wrapped.split("\n", 1)[1].rsplit("\n</untrusted_data>", 1)[0]


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def quote_verifies(quote: str, evidence_texts: Sequence[str]) -> bool:
    q = _norm(quote)
    return len(q) >= QUOTE_MIN_CHARS and any(q in _norm(visible(t)) for t in evidence_texts)


def build_prompt(item: JudgeItem, sample: int, k: int) -> str:
    """The user message: claim and shuffled evidence only. Labels E1.. are per-sample (position bias)."""
    order = sorted(range(len(item.evidence)), key=lambda i: sha(item.item_id, sample, item.evidence[i][0]))
    lines = [f"Sample {sample} of {k}.", "", "Claim:", untrusted(item.text, "claim"), "",
             "Cited evidence (the only facts you may use):"]
    for pos, i in enumerate(order, 1):
        lines += [f"E{pos}:", untrusted(item.evidence[i][1], f"evidence:E{pos}")]
    lines += ["", "Answer the items for this claim only."]
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------- judging
@dataclass
class SampleOutcome:
    verdict: str
    f1: bool | None = None
    f2: bool | None = None
    f3: bool | None = None
    quote_verified: bool | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "f1": self.f1, "f2": self.f2, "f3": self.f3,
                "quote_verified": self.quote_verified, "error": self.error}


@dataclass
class ItemOutcome:
    item: JudgeItem
    verdict: str
    via: str                                     # "judge" | "no_resolved_evidence"
    samples: list[SampleOutcome] = field(default_factory=list)
    degraded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"item_id": self.item.item_id, "origin": self.item.origin, "verdict": self.verdict,
                "via": self.via, "cited": self.item.cited, "resolved": len(self.item.evidence),
                "degraded": self.degraded, "source_claim_id": self.item.source_claim_id,
                "samples": [s.as_dict() for s in self.samples]}


def aggregate(samples: Sequence[SampleOutcome], k: int) -> tuple[str, bool]:
    """(verdict, degraded). Unanimous over the successful samples; fewer than two successes is an error."""
    ok = [s.verdict for s in samples if s.error is None]
    if len(ok) < min(2, k):
        return "error", False
    degraded = len(ok) < k
    return (ok[0] if len(set(ok)) == 1 else "needs_review"), degraded


class OfflineJudge:
    def __init__(self, llm: LLM, *, k: int = 3, tier: Tier = "strong"):
        if k < 1:
            raise ValueError("k must be at least 1")
        self.llm = llm
        self.k = k
        self.tier = tier
        self.tier_requested = tier
        self.fallback_reason: str | None = None

    def _ask(self, user: str) -> JudgeAnswer:
        try:
            return self.llm.structured(purpose=PURPOSE, tier=self.tier, system=SYSTEM, user=user,
                                       schema=JudgeAnswer, max_tokens=1024, prompt_version=PROMPT_VERSION)
        except LLMError as exc:
            if self.tier == "strong" and "not found or not available" in str(exc):
                self.tier, self.fallback_reason = "mid", str(exc)[:160]
                return self.llm.structured(purpose=PURPOSE, tier=self.tier, system=SYSTEM, user=user,
                                           schema=JudgeAnswer, max_tokens=1024, prompt_version=PROMPT_VERSION)
            raise

    def sample(self, item: JudgeItem, index: int) -> SampleOutcome:
        try:
            ans = self._ask(build_prompt(item, index, self.k))
        except (LLMError, TypeError) as exc:      # TypeError: the SDK's "could not resolve authentication"
            return SampleOutcome("error", error=f"{type(exc).__name__}")
        if not ans.can_tell:
            return SampleOutcome("cannot_tell", ans.f1_entities_in_evidence, ans.f2_relation_not_stronger,
                                 ans.f3_numbers_match, None)
        verified = quote_verifies(ans.quote, [t for _, t in item.evidence])
        ok = (ans.f1_entities_in_evidence and ans.f2_relation_not_stronger and ans.f3_numbers_match
              and verified)
        return SampleOutcome("supported" if ok else "unsupported", ans.f1_entities_in_evidence,
                             ans.f2_relation_not_stronger, ans.f3_numbers_match, verified)

    def judge(self, item: JudgeItem) -> ItemOutcome:
        if not item.evidence:
            return ItemOutcome(item, "unsupported", "no_resolved_evidence")
        samples = [self.sample(item, i) for i in range(1, self.k + 1)]
        verdict, degraded = aggregate(samples, self.k)
        return ItemOutcome(item, verdict, "judge", samples, degraded)


# ------------------------------------------------------------------------------------ corruptions
@dataclass(frozen=True)
class Corruption:
    kind: str
    source: ClaimRef
    text: str
    evidence_ids: tuple[str, ...]
    note: str

    @property
    def item_id(self) -> str:
        return f"cor_{sha(self.kind, self.source.id, self.text, *self.evidence_ids)}"


def _swap_evidence(c: ClaimRef, others: Sequence[str]) -> Corruption | None:
    pool = sorted((o for o in others if o not in c.evidence_ids), key=lambda o: sha("swap", c.id, o))
    if not c.evidence_ids or not pool:
        return None
    swapped = (pool[0], *c.evidence_ids[1:])
    return Corruption("swap_evidence", c, c.text, swapped, f"{c.evidence_ids[0]} replaced by {pool[0]}")


def _flip_status(c: ClaimRef, _o: Sequence[str]) -> Corruption | None:
    if not _AT_RISK.search(c.text):
        return None
    return Corruption("flip_status", c, _AT_RISK.sub("loses its only realization", c.text, count=1),
                      c.evidence_ids, "at_risk -> loses_only_realization")


def _inflate_band(c: ClaimRef, _o: Sequence[str]) -> Corruption | None:
    for rx, stronger in _INFLATE:
        if rx.search(c.text):
            return Corruption("inflate_band", c, rx.sub(stronger, c.text, count=1), c.evidence_ids,
                              f"'{rx.pattern}' -> '{stronger}'")
    return None


def _change_number(c: ClaimRef, _o: Sequence[str]) -> Corruption | None:
    m = _NUMBER.search(c.text)
    if not m:
        return None
    new = str(int(m.group(0)) + 1)
    return Corruption("change_number", c, c.text[:m.start()] + new + c.text[m.end():], c.evidence_ids,
                      f"{m.group(0)} -> {new}")


def _cite_missing(c: ClaimRef, _o: Sequence[str]) -> Corruption | None:
    if not c.evidence_ids:
        return None
    ghost = f"ev_{sha('missing', c.id, c.evidence_ids[0])}"
    return Corruption("cite_missing", c, c.text, (ghost, *c.evidence_ids[1:]),
                      f"{c.evidence_ids[0]} replaced by nonexistent {ghost}")


def _drop_qualifier(c: ClaimRef, _o: Sequence[str]) -> Corruption | None:
    if not _PROBABLY.search(c.text):
        return None
    return Corruption("drop_qualifier", c, _PROBABLY.sub("", c.text, count=1), c.evidence_ids,
                      "'probably' removed")


_MAKERS: dict[str, Callable[[ClaimRef, Sequence[str]], Corruption | None]] = {
    "swap_evidence": _swap_evidence, "flip_status": _flip_status, "inflate_band": _inflate_band,
    "change_number": _change_number, "cite_missing": _cite_missing, "drop_qualifier": _drop_qualifier,
}


def seed_corruptions(claims: Sequence[ClaimRef], n: int,
                     evidence_universe: Sequence[str]) -> list[Corruption]:
    """Up to n corrupted claims, deterministic and balanced over the kinds that apply.

    Round-robin over KINDS; inside a kind the claim with the smallest hash(kind, claim id) goes first, and a
    claim is corrupted at most once per kind. Fewer than n are returned when the claims do not allow more."""
    pool = sorted({c.id: c for c in claims}.values(), key=lambda c: c.id)
    by_kind: dict[str, list[Corruption]] = {}
    for kind in KINDS:
        made = [m for c in pool if (m := _MAKERS[kind](c, evidence_universe)) is not None]
        by_kind[kind] = sorted(made, key=lambda m: sha("seed", kind, m.source.id))
    out: list[Corruption] = []
    cursor = dict.fromkeys(KINDS, 0)
    while len(out) < n and any(cursor[k] < len(by_kind[k]) for k in KINDS):
        for kind in KINDS:
            if len(out) < n and cursor[kind] < len(by_kind[kind]):
                out.append(by_kind[kind][cursor[kind]])
                cursor[kind] += 1
    return out


# --------------------------------------------------------------------------------------- the eval
def load_hand_labels(path: Path) -> dict[str, str]:
    """claim id -> "supported" | "unsupported" from a hand-labelled file ({} when absent or unreadable)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in data.items() if v in ("supported", "unsupported")} \
        if isinstance(data, dict) else {}


def _counts(outcomes: Sequence[ItemOutcome]) -> dict[str, Any]:
    c = Counter(o.verdict for o in outcomes)
    n = len(outcomes)
    return {"n": n, **{v: Proportion.of(c[v], n).as_dict() for v in VERDICTS}}


def run_judge(llm: LLM, claims: Sequence[ClaimRef], evidence: Mapping[str, Mapping[str, Any]], *,
              k: int = 3, n_corruptions: int = 20, tier: Tier = "strong",
              hand_labels: Mapping[str, str] | None = None,
              generator_models: Sequence[str] = ()) -> dict[str, Any]:
    """Judge the natural claims and the seeded corruptions; returns the judge section of results.json."""
    judge = OfflineJudge(llm, k=k, tier=tier)
    unique = sorted({c.id: c for c in claims}.values(), key=lambda c: c.id)
    universe = sorted(evidence)
    natural_items = [resolve(c.text, c.evidence_ids, evidence, item_id=c.id) for c in unique]
    cors = seed_corruptions(unique, n_corruptions, universe)
    cor_items = [resolve(c.text, c.evidence_ids, evidence, item_id=c.item_id, origin=c.kind,
                         source=c.source.id, note=c.note) for c in cors]
    before = llm.run.ledger.summary()
    natural, seeded, aborted = judge_all(judge, natural_items, cor_items)
    after = llm.run.ledger.summary()
    out = _report(judge, natural, seeded, cors, before, after, hand_labels or {}, generator_models, llm, k,
                  n_corruptions)
    out["aborted"] = aborted
    out["natural"]["entity_coverage"] = entity_coverage(natural_items)    # over every claim, judged or not
    return out


def judge_all(judge: OfflineJudge, natural_items: Sequence[JudgeItem], cor_items: Sequence[JudgeItem],
              *, stop_after: int = 2) -> tuple[list[ItemOutcome], list[ItemOutcome], dict[str, Any] | None]:
    """Judge every item, stopping when `stop_after` judged items in a row end in `error`: a missing key, an
    exhausted budget or a cold offline cache fails every call alike, and retrying 100 items would only fill
    failures.jsonl. What was judged before the stop is kept, and the stop is reported."""
    natural: list[ItemOutcome] = []
    seeded: list[ItemOutcome] = []
    streak = 0
    for group, items in ((natural, natural_items), (seeded, cor_items)):
        for item in items:
            outcome = judge.judge(item)
            group.append(outcome)
            if outcome.via == "judge":
                streak = streak + 1 if outcome.verdict == "error" else 0
            if streak >= stop_after:
                reasons = sorted({s.error for o in (*natural, *seeded) for s in o.samples if s.error})
                why = f"{streak} judged items in a row failed: {', '.join(reasons)}"
                return natural, seeded, {"after_items": len(natural) + len(seeded), "reason": why}
    return natural, seeded, None


def _report(judge: OfflineJudge, natural: list[ItemOutcome], seeded: list[ItemOutcome],
            cors: list[Corruption], before: Mapping[str, Any], after: Mapping[str, Any],
            hand: Mapping[str, str], generator_models: Sequence[str], llm: LLM, k: int,
            requested: int) -> dict[str, Any]:
    model = llm.model_for(judge.tier)
    caveats = [SAME_FAMILY_CAVEAT, CORRUPTION_CAVEAT,
               "Judge cost is a lower bound: thinking is always on and billed as output."]
    if judge.tier != judge.tier_requested:
        caveats.append(f"Requested tier {judge.tier_requested} was unavailable ({judge.fallback_reason}); "
                       f"the judge ran on {judge.tier}, so judge and generator may be the same model tier.")
    if model in generator_models:
        caveats.append(f"The judge model {model} is also a model that generated pipeline output.")
    flagged = sum(1 for o in seeded if o.verdict == "unsupported")
    llm_judged = [o for o in seeded if o.via == "judge"]
    originals = [n for n in natural if n.item.item_id in {c.source.id for c in cors}]
    truth = ["supported"] * len(originals) + ["unsupported"] * len(seeded)
    got = [o.verdict for o in originals] + [o.verdict for o in seeded]
    always = ["supported"] * len(truth)
    hand_pairs = [(hand[o.item.item_id], o.verdict) for o in natural if o.item.item_id in hand]
    spent = round(after["spent_usd"] - before["spent_usd"], 4)
    return {
        "k": k, "tier_requested": judge.tier_requested, "tier_used": judge.tier, "judge_model": model,
        "generator_models": sorted(set(generator_models)), "purpose": PURPOSE,
        "caveats": caveats, "item_definitions": {
            "F1": "entities appear in evidence", "F2": "relation not stronger than the cited evidence",
            "F3": "numbers equal evidence",
            "F4": "quote is an exact substring of cited evidence (string-checked)"},
        "natural": {**_counts(natural), "labelled": False,
                    "note": "natural claims have no ground truth here; rates are observations, not accuracy"},
        "seeded": {
            "requested": requested, "produced": len(cors), "judged": len(seeded),
            "by_kind": dict(sorted(Counter(c.kind for c in cors).items())),
            "tnr_upper_bound": Proportion.of(flagged, len(seeded)).as_dict(),
            "tnr_llm_judged_only": Proportion.of(
                sum(1 for o in llm_judged if o.verdict == "unsupported"), len(llm_judged)).as_dict(),
            "caught_without_a_call": sum(1 for o in seeded if o.via == "no_resolved_evidence"),
            "outcomes": {v: sum(1 for o in seeded if o.verdict == v) for v in VERDICTS},
            "label_caveat": "originals are ASSUMED supported (unverified) in the kappa below",
            "kappa_originals_assumed_supported_vs_corruptions": cohens_kappa(truth, got),
            "baseline_always_supported": {
                "tnr": 0.0 if seeded else None, "kappa": cohens_kappa(truth, always),
                "accuracy": Proportion.of(len(originals), len(truth)).as_dict()},
            "items": [o.as_dict() | {"note": o.item.note} for o in seeded],
        },
        "hand_check": ({"n": len(hand_pairs),
                        "agreement": Proportion.of(sum(1 for t, g in hand_pairs if t == g),
                                                   len(hand_pairs)).as_dict(),
                        "kappa": cohens_kappa([t for t, _ in hand_pairs], [g for _, g in hand_pairs])}
                       if hand_pairs else {"n": 0, "status": "no hand labels matched"}),
        "samples_per_item": k, "calls_made": after["calls"] - before["calls"],
        "model_verdicts": sum(1 for o in (*natural, *seeded) if o.via == "judge" and o.verdict != "error"),
        "cost_usd_lower_bound": spent, "natural_items": [o.as_dict() for o in natural],
    }
