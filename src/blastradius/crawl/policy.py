"""Exploration policy: which candidate to try next. Deterministic first, an LLM may only re-rank.

Implements D04 and D07 (LLM proposes, graph disposes): the explorer builds the candidate list, the safety
classes decide what is allowed, and a policy only *orders* the allowed candidates. `HeuristicPolicy` needs no
API key. `LLMPolicy` sends an indexed list (page text wrapped as untrusted, case 56) and accepts nothing but
ranked indices; on any error it falls back to the heuristic order. Requirement texts (`goals`) bias the order
and are logged per goal in `goal_effort.json` (D08: absence carries search effort).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..redact import redact_text
from ..safety import UNTRUSTED_PREAMBLE, untrusted
from .fingerprint import layout_key
from .perceive import Element
from .safety import ActionClass, Decision

MAX_LLM_CALLS = 40
LLM_TOP_N = 30
LLM_DISABLE_AFTER = 3  # consecutive failures before the LLM is left alone for the rest of the run

NAV_WORDS: dict[str, int] = {
    "settings": 40,
    "collections": 36,
    "environments": 36,
    "history": 34,
    "import": 32,
    "export": 30,
    "documentation": 12,
    "docs": 12,
    "shortcuts": 28,
    "keyboard": 20,
    "help": 18,
    "new": 26,
    "add": 22,
    "create": 20,
    "variables": 22,
    "headers": 20,
    "body": 20,
    "authorization": 20,
    "parameters": 18,
    "scripts": 18,
    "script": 18,
    "tests": 18,
    "theme": 14,
    "language": 14,
    "interceptor": 20,
    "proxy": 14,
    "extensions": 14,
    "graphql": 16,
    "realtime": 16,
    "websocket": 16,
    "mqtt": 14,
    "sse": 14,
    "socket": 14,
    "generate": 22,
    "code": 12,
    "mock": 18,
    "shared": 18,
    "search": 16,
    "commands": 16,
    "workspace": 14,
    "invite": 10,
    "response": 10,
    "request": 8,
    "options": 12,
    "more": 10,
    "menu": 10,
}
_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "from",
        "by",
        "at",
        "as",
        "is",
        "are",
        "be",
        "can",
        "may",
        "will",
        "should",
        "must",
        "user",
        "users",
        "able",
        "allows",
        "allow",
        "your",
        "you",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "via",
        "into",
        "any",
        "each",
        "per",
        "using",
        "use",
        "also",
        "not",
        "no",
        "have",
        "has",
        "had",
        "when",
        "where",
        "which",
        "who",
        "what",
        "how",
        "if",
        "then",
        "than",
        "so",
        "such",
    }
)


class Ranking(BaseModel):
    """What the LLM may return: indices into the presented list, best first. Nothing else is accepted."""

    ranked: list[int] = Field(default_factory=list, max_length=LLM_TOP_N)


@dataclass(frozen=True)
class Candidate:
    index: int
    element: Element
    decision: Decision


@dataclass
class PolicyContext:
    screen_id: str
    url_pattern: str
    dialog_title: str | None
    depth: int
    tried_ids: set[str] = field(default_factory=set)  # element ids already acted on
    acted_names: set[tuple[str, str, str]] = field(default_factory=set)  # layout_key acted on anywhere
    layout_counts: Mapping[tuple[str, str, str], int] = field(default_factory=dict)  # screens per layout_key
    goals: Sequence[str] = ()


def tokens(text: str) -> frozenset[str]:
    """Lower-case content words (length >= 3, light plural stripping) used for goal overlap."""
    out = set()
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if len(word) < 3 or word in _STOP:
            continue
        out.add(word[:-1] if word.endswith("s") and len(word) > 3 else word)
    return frozenset(out)


def goal_overlap(goal_tokens: frozenset[str], name: str) -> bool:
    return bool(goal_tokens & tokens(name))


class Policy(Protocol):
    name: str

    def rank(self, candidates: Sequence[Candidate], ctx: PolicyContext) -> list[int]:
        """Candidate indices, best first. Must be a permutation of the input indices."""


class HeuristicPolicy:
    """Untried and navigation-like first; layout that already appeared elsewhere last; stable tie-break."""

    name = "heuristic"

    def score(self, cand: Candidate, ctx: PolicyContext) -> float:
        el, o = cand.element, cand.element.obs
        words = tokens(o.name) | tokens(o.text)
        score = 0.0
        if o.id in ctx.tried_ids:
            score -= 1000
        key = layout_key(el)
        if key[1] and key in ctx.acted_names:
            score -= 60
        score -= 6 * min(max(ctx.layout_counts.get(key, 1) - 1, 0), 6)
        score += max((NAV_WORDS.get(w, 0) for w in words | set(o.name.lower().split())), default=0)
        score += {ActionClass.NAVIGATE: 25, ActionClass.LOCAL_WRITE: 12}.get(cand.decision.action_class, 0)
        if el.tab and not el.tab_active:
            score += 24
        if o.role in ("menuitem", "tab", "option"):
            score += 8
        elif o.role in ("textbox", "searchbox", "combobox"):
            score -= 14
        if not o.name:
            score -= 20  # an unnamed icon button: cannot tell what it does (case 7)
        if o.href and ctx.url_pattern not in o.href:
            score += 4
        for goal in ctx.goals:
            if goal_overlap(tokens(goal), o.name):
                score += 15
        return score

    def rank(self, candidates: Sequence[Candidate], ctx: PolicyContext) -> list[int]:
        order = sorted(candidates, key=lambda c: (-self.score(c, ctx), c.element.obs.id, c.index))
        return [c.index for c in order]


class LLMPolicy:
    """Re-ranks the heuristic's top candidates. Output is validated; anything wrong falls back."""

    name = "llm"

    def __init__(self, llm: Any, fallback: HeuristicPolicy | None = None, *, max_calls: int = MAX_LLM_CALLS):
        self.llm = llm
        self.fallback = fallback or HeuristicPolicy()
        self.max_calls = max_calls
        self.calls = 0
        self.failures = 0
        self.consecutive_failures = 0
        self.cache_by_screen: dict[str, list[int]] = {}

    def _prompt(self, top: Sequence[Candidate], ctx: PolicyContext) -> str:
        rows = [
            f"{i}\t{c.element.obs.role}\t{c.element.obs.name or '(unnamed icon)'}\t{c.decision.action_class}"
            for i, c in enumerate(top)
        ]
        screen = f"url pattern: {ctx.url_pattern}; dialog: {ctx.dialog_title or 'none'}; depth: {ctx.depth}"
        text = redact_text(screen + "\nindex\trole\tname\tclass\n" + "\n".join(rows), truncate=False)
        body = untrusted(text, "crawl:candidates")
        goals = ""
        if ctx.goals:
            goals = (
                "\nRequirements to look for (higher priority when an element could realize one):\n"
                + untrusted(
                    redact_text("\n".join(f"- {g[:200]}" for g in ctx.goals[:8]), truncate=False),
                    "crawl:goals",
                )
            )
        return (
            f"Candidates on the current screen:\n{body}{goals}\n\nReturn the indices, most promising first."
        )

    SYSTEM = (
        "You choose which UI element a QA crawler should try next. Prefer elements likely to reveal a new "
        "screen, dialog, tab or settings area; avoid repeating layout controls that were already tried. "
        "Return only indices from the list, each at most once. " + UNTRUSTED_PREAMBLE
    )

    def rank(self, candidates: Sequence[Candidate], ctx: PolicyContext) -> list[int]:
        base = self.fallback.rank(candidates, ctx)
        if len(base) < 2 or self.calls >= self.max_calls or self.consecutive_failures >= LLM_DISABLE_AFTER:
            return base
        if ctx.screen_id in self.cache_by_screen:
            return self._merge(self.cache_by_screen[ctx.screen_id], base)
        by_index = {c.index: c for c in candidates}
        top = [by_index[i] for i in base[:LLM_TOP_N]]
        self.calls += 1
        try:
            out = self.llm.structured(
                purpose="explore_policy",
                tier="cheap",
                system=self.SYSTEM,
                user=self._prompt(top, ctx),
                schema=Ranking,
                max_tokens=400,
            )
        except Exception:  # noqa: BLE001 - any LLM/SDK/config error means: keep the deterministic order
            self.failures += 1
            self.consecutive_failures += 1
            return base
        self.consecutive_failures = 0
        seen: set[int] = set()
        chosen = []
        for idx in out.ranked:
            if 0 <= idx < len(top) and idx not in seen:
                seen.add(idx)
                chosen.append(top[idx].index)
        self.cache_by_screen[ctx.screen_id] = chosen
        return self._merge(chosen, base)

    @staticmethod
    def _merge(chosen: list[int], base: list[int]) -> list[int]:
        """LLM order first (only candidates still pending), then the heuristic order for the rest."""
        live = set(base)
        head = [i for i in chosen if i in live]
        return [*head, *(i for i in base if i not in set(head))]


# ------------------------------------------------------------------------------------ goal effort
class GoalEffort:
    """Per requirement text: screens whose elements overlap it, actions spent on overlapping elements, and
    the candidate element names that overlap. Consumed by link.gaps for search-effort gaps (D08)."""

    def __init__(self, goals: Sequence[str]):
        self.goals = list(goals)
        self._tokens = [tokens(g) for g in self.goals]
        self._screens: list[set[str]] = [set() for _ in self.goals]
        self._actions: Counter[int] = Counter()
        self._names: list[dict[str, int]] = [{} for _ in self.goals]

    def observe_screen(self, screen_id: str, names: Sequence[str]) -> None:
        for gi, gt in enumerate(self._tokens):
            for name in names:
                if name and goal_overlap(gt, name):
                    self._screens[gi].add(screen_id)
                    self._names[gi][name] = self._names[gi].get(name, 0) + 1

    def record_action(self, name: str) -> None:
        for gi, gt in enumerate(self._tokens):
            if name and goal_overlap(gt, name):
                self._actions[gi] += 1

    def to_json(self) -> list[dict[str, Any]]:
        return [
            {
                "goal": g,
                "tokens": sorted(self._tokens[i]),
                "screens_visited": len(self._screens[i]),
                "screen_ids": sorted(self._screens[i]),
                "actions_spent": self._actions[i],
                "candidate_names": sorted(self._names[i], key=lambda n: (-self._names[i][n], n))[:25],
            }
            for i, g in enumerate(self.goals)
        ]
