"""UI -> code: the evidence cascade that links observed elements to the source files that render them.

Implements D07 (the LLM proposes, the code index disposes), D09 (confidence = method prior x ambiguity), D25
(forward render edges: R(screen) is a forward closure and is deliberately not hop-capped, because it is a
prior on where a string can come from, not an impact claim; the 2-hop cap belongs to the reasoner's reverse
closure) and cases 7 (nameless controls), 32 (ambiguous copy), 34 (external UI kit), 46 (canonical elements).

Cascade, per Link(IMPLEMENTED_BY, src=UIElementObs.id, dst=CodeFile.path, screen_ids=[...]):

 1. Route. `graph.canonical.match_route` maps a Screen's url_pattern to RouteDefs. The seed set is the page
    file(s), their parent route files (`realtime.vue` for `realtime/websocket.vue`: a nested route renders
    inside its parent) and the route layout(s) (`cfg.code.global_files` when a route names none). The
    reachability set R(screen) is the RENDERS closure of the seeds. A modal or tab state of a page uses the
    same set (a dialog is rendered somewhere under the page or the layout). Catch-all rule: a Screen whose
    url_pattern lands on the catch-all route (the crawler's soft-404 probe, "/*") gets R of the catch-all
    page and its layout only, so text that only real pages use is never linked from the 404 shell. A Screen
    that matches no route gets the global files' closure only (`how == "none"`).
 2. Text. Name, text, aria-label, placeholder and title are normalised (`text.normalise_text`) and looked up
    in the English i18n VALUE map -> keys -> files that use the key (static uses only), intersected with
    R(screen). Of an element's texts only the most specific one (fewest files) counts, so a precise
    aria-label is not diluted by a generic visible label. k files remain: confidence 0.90 for k == 1,
    max(0.30, 0.90 / k) per file for k >= 2. Keys used only outside R are not linked. Only when no i18n file
    remains, source literals (`CodeIndex.literals`) are tried the same way with prior 0.70 (same floor).
    Literals shorter than 3 characters, without a letter, or equal to a brand word (the target's name and
    display name) are ignored: they appear everywhere and prove nothing.
 2b. Context. The state a screen is in says which of the k files is the likelier owner, and two rules use it
    softly (`cascade._score`): (i) modal and tab states: when k > 1, the files that ALSO use the i18n key of
    the dialog title (else of an active tab label) keep the normal value (prior / remaining k) and the others
    are demoted to x0.5 of theirs, floor 0.15, not dropped ("Save" in the "New Collection" dialog is
    collections/Add.vue, not environments/Add.vue); (ii) the page title: a route page file that uses the
    control's text as its own document title (usePageHead) is demoted the same way when the control is
    also seen on screens where that page is not rendered. The code index records no usage position, so
    (ii) is a rule on the evidence the link stage has (text == the brand-stripped document title, file = the
    route's page, control present elsewhere): a contract request asks the index to say "head" itself.
 3. One value per (element, file, screen). A file that is one of two candidates on one screen and the only
    one on another carries 0.45 on the first and 0.90 on the second: rows are written per (element, file,
    confidence) with the screens that value was derived on, never the best value on all of them. Each link
    adds a `link.<method>` evidence row (ref = element id) naming the text that matched (as the page shows
    it), the key and the page whose closure held the file. The graph store keys IMPLEMENTED_BY by
    (element, file, method) and keeps the best row's confidence with the union of the screens: per-screen
    confidence is exact in links.jsonl and best-of in the graph until the store changes (contract request).
 4. LLM fallback for the named interactive elements nothing above resolved: one call per screen (<= 25
    controls per call, <= 25 calls per run), candidates = component/page/layout files of R(screen) with up
    to 12 of their English values, the 40 best by overlap with the controls' tokens. Output per control is
    a candidate label or NONE, a certainty (high 0.60, medium 0.45, low 0.30: never above the 0.60 cap) and
    an `external` flag (the control looks rendered by a third-party UI kit, case 34). Page text is
    untrusted-wrapped; the model answers with labels, so it cannot invent a path. Files OUTSIDE R(screen)
    (<= 10 per call, after the in-closure ones, marked "outside this page") are offered only for a
    control whose own text matched a string of that file (the closure under-reaches `<component :is>` and
    helper-produced text); a pick of one is accepted only for such a control and is written at half its
    certainty value (high 0.30, medium 0.225, low 0.15) with an evidence row that says it is unconfirmed.
 5. What remains is returned as `Unresolved` rows with a reason; link.gaps turns them into UNMAPPED_CODE.

Steps 1 to 3 are pure functions in `cascade`; this module groups observations into canonical elements, runs
the LLM fallback, and emits Links, evidence and the unresolved rows.

Known holes, stated: testid rung (the index carries no testid table; skipped and counted in the stats), `{placeholder}` templates in
values (exact match only), dynamic i18n keys (prefix uses are not linked), `<component :is>` renders (no
RENDERS edge, so the closure under-reaches), text produced by services/helpers (`.ts` files are never in a
render closure, so their strings are kept as candidates outside R and reported in the gap detail).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field

from ..graph.canonical import canonicalise
from ..llm import LLM
from ..models import METHOD_PRIOR, CodeIndex, FileClass, Link, ScreenState, Strict, UIElementObs
from ..run import RunContext
from ..safety import UNTRUSTED_PREAMBLE, untrusted
from ..targets import TargetConfig
from .budget import CallBudget, guarded
from .cascade import (
    Hit,
    Reach,
    Reachability,
    ScreenContext,
    TextMaps,
    build_text_maps,
    element_texts,
    resolve_text,
    screen_context,
    screen_order,
)
from .text import normalise_text, prompt_text, tokens

PROMPT_VERSION = "ui-to-code-pick-2"
MAX_PICK_CALLS = 25
PICK_BATCH = 25
PICK_CANDIDATES = 40
PICK_VALUES = 12
OUTSIDE_CANDIDATES = 10      # files outside R(screen) offered per batch, only those whose text matches
OUTSIDE_FACTOR = 0.5         # a pick the render closure does not confirm keeps half of its certainty value
ROLE_CHARS = 40
CERTAINTY: dict[str, float] = {"high": 0.60, "medium": 0.45, "low": 0.30}
PICK_KINDS = frozenset({FileClass.COMPONENT, FileClass.PAGE, FileClass.LAYOUT})
# files outside the render closure that may hold a control's text: UI source, plus the services and helpers
# that produce strings (spotlight searchers, notification builders); never tests, generated code or locales
OUTSIDE_KINDS = PICK_KINDS | {FileClass.SERVICE, FileClass.UTIL, FileClass.SOURCE_OTHER}
INTERACTIVE_ROLES = frozenset({
    "button", "link", "textbox", "searchbox", "checkbox", "radio", "menuitem", "menuitemcheckbox",
    "menuitemradio", "tab", "switch", "combobox", "option", "slider", "spinbutton", "treeitem",
})
_METHOD_RANK = {"i18n_key": 2, "literal_text": 1, "llm_pick": 0}


# ----------------------------------------------------------------------------------------- LLM pick
class PickItem(Strict):
    control: str = Field(description="The control label, for example e3.")
    file: str = Field(description="One candidate label, for example c7, or NONE when no candidate renders it.")
    certainty: Literal["high", "medium", "low"] = Field(description="How sure you are of the chosen file.")
    external: bool = Field(description="True when the control looks rendered by a third-party UI kit.")


class PickOut(Strict):
    picks: list[PickItem]


PICK_SYSTEM = f"""You map controls seen on a web page to the source file that renders them, for a QA \
change-impact tool.

{UNTRUSTED_PREAMBLE}

You get numbered controls (labels e1, e2, ...) and numbered candidate source files (labels c1, c2, ...), \
each with some of the text the file displays. For every control choose the one candidate whose displayed \
text or name best explains the control, or NONE when no candidate plausibly renders it. Prefer NONE to a \
weak guess. Set certainty to high only when the control's text equals text a candidate displays. Set \
external to true when the control looks rendered by a third-party UI component library rather than by \
app source (generic widgets whose text arrives from the caller). A candidate marked "outside this page" is \
not rendered under the page according to the import graph: choose it only when the text it displays equals \
the control's text. Reply only with the structured output, one pick per control."""


@dataclass(frozen=True)
class Candidate:
    label: str
    file: str
    values: tuple[str, ...]
    outside: bool = False            # not in R(screen): offered only because its text matches the control's


def pick_candidates(reach: Reach, maps: TextMaps, kinds: dict[str, FileClass],
                    control_tokens: set[str]) -> list[Candidate]:
    """The PICK_CANDIDATES files of R(screen) that best overlap the controls' tokens, nearest on ties."""
    scored: list[tuple[int, int, str, tuple[str, ...]]] = []
    for f in sorted(reach.hops):
        if kinds.get(f) not in PICK_KINDS:
            continue
        values = sorted({maps.key_values[k] for k in maps.file_keys.get(f, ()) if k in maps.key_values})
        seen = set(tokens(f.rsplit("/", 1)[-1].rsplit(".", 1)[0]))
        for v in values:
            seen.update(tokens(v))
        shown = sorted(values, key=lambda v: (-len(control_tokens & set(tokens(v))), v))[:PICK_VALUES]
        scored.append((-len(control_tokens & seen), reach.hops[f], f, tuple(shown)))
    scored.sort()
    return [Candidate(f"c{i}", f, vals) for i, (_, _, f, vals) in enumerate(scored[:PICK_CANDIDATES], 1)]


def outside_candidates(groups: Sequence[_Group], reach: Reach, maps: TextMaps, kinds: dict[str, FileClass],
                       control_tokens: set[str], first_label: int) -> list[Candidate]:
    """Files outside R(screen) that use a string equal to a control's text (`_Group.outside`), at most
    OUTSIDE_CANDIDATES, the ones matching most controls first. A file whose text does not match any control
    of the batch is never offered: R is a prior, not a wall, but only text evidence may lift it."""
    votes: Counter[str] = Counter()
    for g in groups:
        for f in g.outside:
            if f not in reach.hops and kinds.get(f) in OUTSIDE_KINDS:
                votes[f] += 1
    ranked = sorted(votes, key=lambda f: (-votes[f], f))[:OUTSIDE_CANDIDATES]
    out = []
    for i, f in enumerate(ranked, first_label):
        values = sorted({maps.key_values[k] for k in maps.file_keys.get(f, ()) if k in maps.key_values})
        shown = sorted(values, key=lambda v: (-len(control_tokens & set(tokens(v))), v))[:PICK_VALUES]
        out.append(Candidate(f"c{i}", f, tuple(shown), outside=True))
    return out


def _control_line(label: str, el: UIElementObs) -> str:
    parts = [f"role={prompt_text(el.role, ROLE_CHARS)}",
             f"name={json.dumps(prompt_text(el.name), ensure_ascii=False)}"]
    for attr in ("text", "aria_label", "placeholder", "title_attr"):
        val = getattr(el, attr)
        if val and val != el.name:
            parts.append(f"{attr}={json.dumps(prompt_text(val), ensure_ascii=False)}")
    return f"{label}: " + " ".join(parts)


def pick_prompt(screen: ScreenState, reach: Reach, controls: Sequence[tuple[str, UIElementObs]],
                candidates: Sequence[Candidate]) -> str:
    ctrl = untrusted("\n".join(_control_line(label, el) for label, el in controls), "crawl:controls")
    cand = untrusted("\n".join(
        f"{c.label}: {c.file.rsplit('/', 1)[-1]} ({c.file}) displays: "
        + (" | ".join(json.dumps(prompt_text(v), ensure_ascii=False) for v in c.values) or "(no static text)")
        + (" [outside this page]" if c.outside else "")
        for c in candidates), "code:candidates")
    route = prompt_text(reach.pattern or "unknown", ROLE_CHARS * 2)
    return (f"Page: route {route}, screen kind {screen.kind}.\n\n"
            f"Controls seen on this page:\n{ctrl}\n\nCandidate source files:\n{cand}\n\n"
            "Choose a candidate label or NONE for every control.")


# ------------------------------------------------------------------------------------- orchestration
@dataclass
class Unresolved:
    element_id: str                  # representative observation id (the canonical element resolves it)
    obs_ids: list[str]
    screen_ids: list[str]
    name: str
    role: str
    reason: str                      # no_text | no_candidate | llm_none | llm_budget | external_package
    detail: str = ""


@dataclass
class UiLinkResult:
    links: list[Link] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Group:
    """One canonical element: its observations, where they were seen, and what resolved it."""
    cid: str
    seen: list[tuple[str, UIElementObs]]                 # (screen id, observation) by screen priority
    best: dict[str, Hit] = field(default_factory=dict)   # file -> best hit over every screen
    # (obs id, file) -> screen id -> hit
    hits: dict[tuple[str, str], dict[str, Hit]] = field(default_factory=dict)
    matched_text: bool = False
    outside: set[str] = field(default_factory=set)

    @property
    def rep(self) -> UIElementObs:
        return self.seen[0][1]

    @property
    def obs_ids(self) -> list[str]:
        return sorted({el.id for _, el in self.seen})


def _better(new: Hit, old: Hit | None) -> bool:
    if old is None:
        return True
    a, b = (new.confidence, _METHOD_RANK[new.method]), (old.confidence, _METHOD_RANK[old.method])
    return a > b or (a == b and (new.text, new.keys) < (old.text, old.keys))


def _build_groups(screens: Sequence[ScreenState], target: str) -> list[_Group]:
    ordered = sorted(screens, key=screen_order)
    rank = {s.id: i for i, s in enumerate(ordered)}
    obs = {(s.id, el.id): el for s in ordered for el in s.elements}
    canon = canonicalise(ordered, target)
    pairs: dict[str, list[tuple[str, str]]] = {}
    for ap in canon.appearances:
        pairs.setdefault(ap["element"], []).append((ap["screen"], ap["obs_id"]))
    return [
        _Group(el["id"], [(sid, obs[(sid, oid)]) for sid, oid in sorted(
            pairs[el["id"]], key=lambda p: (rank[p[0]], p[1]))])
        for el in canon.elements
    ]


def _derive(groups: list[_Group], reach: Reachability, by_screen: dict[str, ScreenState], maps: TextMaps,
            contexts: dict[str, ScreenContext]) -> None:
    for g in groups:
        shown_on = [reach.of(by_screen[sid].url_pattern) for sid, _ in g.seen]
        for sid, el in g.seen:
            texts = element_texts(el)
            if not texts:
                continue
            r = reach.of(by_screen[sid].url_pattern)
            # route pages of this screen that are not rendered on some other screen showing the same element
            elsewhere = {f for f in r.pages if any(f not in other.hops for other in shown_on)}
            res = resolve_text([t for t, _ in texts], r, maps, contexts[sid], elsewhere)
            g.matched_text |= res.matched_text
            g.outside |= res.outside
            for hit in res.hits:
                if _better(hit, g.best.get(hit.file)):
                    g.best[hit.file] = hit
                g.hits.setdefault((el.id, hit.file), {})[sid] = hit


def _call_pick(run: RunContext, llm: LLM, screen: ScreenState, reach: Reach, controls: list[tuple[str, _Group]],
               cands: list[Candidate], budget: CallBudget) -> PickOut | None:
    user = pick_prompt(screen, reach, [(label, g.rep) for label, g in controls], cands)
    return guarded(run, llm, budget, context=f"ui_to_code_pick {screen.id}", purpose="ui_to_code_pick",
                   tier="mid", effort="low", system=PICK_SYSTEM, user=user, schema=PickOut,
                   prompt_version=PROMPT_VERSION)


def _run_picks(run: RunContext, llm: LLM, groups: list[_Group], by_screen: dict[str, ScreenState],
               reach: Reachability, maps: TextMaps, kinds: dict[str, FileClass], budget: CallBudget,
               ) -> tuple[dict[str, str], dict[str, tuple[str, str, bool]]]:
    """(reason per group the LLM did not link, pick per group: (file, certainty, outside R(screen)))."""
    pending: dict[str, list[_Group]] = {}
    for g in groups:
        if not g.best and element_texts(g.rep) and g.rep.role in INTERACTIVE_ROLES:
            pending.setdefault(g.seen[0][0], []).append(g)
    reasons: dict[str, str] = {}
    picks: dict[str, tuple[str, str, bool]] = {}
    for sid in sorted(pending, key=lambda s: screen_order(by_screen[s])):
        screen, r = by_screen[sid], reach.of(by_screen[sid].url_pattern)
        todo = sorted(pending[sid], key=lambda g: (g.rep.ordinal, g.rep.id))
        for start in range(0, len(todo), PICK_BATCH):
            batch = todo[start : start + PICK_BATCH]
            ctoks = {t for g in batch for text, _ in element_texts(g.rep) for t in tokens(text)}
            cands = pick_candidates(r, maps, kinds, ctoks)
            cands += outside_candidates(batch, r, maps, kinds, ctoks, len(cands) + 1)
            if not cands:
                reasons.update({g.cid: "no_candidate" for g in batch})
                continue
            controls = [(f"e{i}", g) for i, g in enumerate(batch, 1)]
            out = _call_pick(run, llm, screen, r, controls, cands, budget) if budget.open else None
            if out is None:
                reasons.update({g.cid: "llm_budget" for g in batch})
                continue
            by_label = {c.label: c for c in cands}
            answered = {p.control: p for p in out.picks}
            for label, g in controls:
                p = answered.get(label)
                c = by_label.get(p.file) if p is not None else None
                # a file outside R is accepted only for a control whose own text matched a string of that file
                if p is not None and c is not None and (not c.outside or c.file in g.outside):
                    picks[g.cid] = (c.file, p.certainty, c.outside)
                else:
                    reasons[g.cid] = "external_package" if (p and p.external) else "llm_none"
    return reasons, picks


def _apply_pick(g: _Group, pick: tuple[str, str, bool], reach: Reachability,
                by_screen: dict[str, ScreenState]) -> None:
    file, certainty, outside = pick
    conf = min(CERTAINTY[certainty], METHOD_PRIOR["llm_pick"])
    if outside:                      # the render closure does not confirm it: lower, and say so
        conf = round(conf * OUTSIDE_FACTOR, 4)
    hit = Hit(file, "llm_pick", conf, normalise_text(g.rep.name or g.rep.text),
              note="outside the render closure of the page" if outside else "")
    g.best[file] = hit
    for sid, el in g.seen:
        if outside or file in reach.of(by_screen[sid].url_pattern).hops:
            g.hits.setdefault((el.id, file), {})[sid] = hit


def _explain(hit: Hit, el: UIElementObs, shown: str, file: str, page: str, pattern: str | None) -> str:
    where = f"rendered under {page} (route {pattern or 'none'})"
    shared = f"; one of {hit.of} files with this text on the page" if hit.of > 1 else ""
    why = f"; {'demoted: ' if hit.demoted else ''}{hit.note}" if hit.note and hit.method != "llm_pick" else ""
    if hit.method == "llm_pick":
        if hit.note:
            return (f'{el.role} "{shown}": the model chose {file}, which uses a string equal to this '
                    f"text but is {hit.note} (route {pattern or 'none'}); lower confidence")
        return f'{el.role} "{shown}": the model chose {file} among candidates {where}'
    if hit.keys:
        return (f'{el.role} "{shown}" equals the English value of i18n key {", ".join(hit.keys[:3])}; '
                f"{file} uses it and is {where}{shared}{why}")
    return f'{el.role} "{shown}" equals a text literal of {file}, {where}{shared}{why}'


def _link_rows(run: RunContext, g: _Group, reach: Reachability, by_screen: dict[str, ScreenState],
               judge: str | None) -> list[Link]:
    """One row per (observation, file, confidence): a file that is one of two candidates on one screen and the
    only one on another carries each value on the screens it was derived on, never the best on all of them."""
    obs = {el.id: el for _, el in g.seen}
    rows = []
    for (oid, file), per_screen in sorted(g.hits.items()):
        el = obs[oid]
        original = dict(element_texts(el))
        by_conf: dict[float, list[str]] = {}
        for sid, hit in per_screen.items():
            by_conf.setdefault(hit.confidence, []).append(sid)
        for conf in sorted(by_conf, reverse=True):
            sids = sorted(by_conf[conf])
            hit = per_screen[sids[0]]
            r = reach.of(by_screen[sids[0]].url_pattern)
            shown = original.get(hit.text, hit.text)        # the text that matched, as the page shows it
            summary = _explain(hit, el, shown, file, r.owner.get(file, "?"), r.pattern)
            rows.append(Link(kind="IMPLEMENTED_BY", src=oid, dst=file, confidence=conf, method=hit.method,
                             evidence_id=run.evidence.add(f"link.{hit.method}", oid, summary),
                             judge=judge if hit.method == "llm_pick" else None, screen_ids=sids))
    return rows


def _dedupe(links: list[Link]) -> list[Link]:
    """One link per (src, dst, confidence): an observation id can belong to two canonical elements, so two
    groups can derive the same row; their screens are unioned. Rows of one (src, dst) that differ in
    confidence stay apart, each with its own screens (a store keyed by (element, file, method) keeps the
    best of them: see the contract request in the report)."""
    best: dict[tuple[str, str, float], Link] = {}
    screens: dict[tuple[str, str, float], set[str]] = {}
    for l in links:
        key = (l.src, l.dst, l.confidence)
        screens.setdefault(key, set()).update(l.screen_ids)
        old = best.get(key)
        rank = (_METHOD_RANK[l.method], l.evidence_id)
        if old is None or rank > (_METHOD_RANK[old.method], old.evidence_id):
            best[key] = l
    return [best[k].model_copy(update={"screen_ids": sorted(screens[k])})
            for k in sorted(best, key=lambda k: (k[0], k[1], -k[2]))]


def _unresolved(g: _Group, reasons: dict[str, str]) -> Unresolved:
    rep = g.rep
    if not element_texts(rep):
        reason, detail = "no_text", "no name, text, label, placeholder or title"
    else:
        reason, detail = reasons.get(g.cid, "no_candidate"), ""
    if reason == "no_candidate":
        if g.outside:
            detail = "text matches strings used only outside this page's render closure: " + ", ".join(
                sorted(g.outside)[:3])
        elif g.matched_text:
            detail = "text matches an app string but no file that renders on this page uses it"
        else:
            detail = "text matches no i18n value or source literal"
    return Unresolved(rep.id, g.obs_ids, sorted({s for s, _ in g.seen}), rep.name or rep.text, rep.role,
                      reason, detail)


def _summarise(groups: list[_Group], links: list[Link], unresolved: list[Unresolved], budget: CallBudget,
               reach: Reachability, patterns: Iterable[str], llm_on: bool) -> dict[str, Any]:
    screen_hits = [h for g in groups for per_screen in g.hits.values() for h in per_screen.values()]
    hist: dict[str, Counter[str]] = {}
    for l in links:
        hist.setdefault(l.method, Counter())[f"{l.confidence:.2f}"] += 1
    named = [g for g in groups if element_texts(g.rep)]
    linked = len(groups) - len(unresolved)
    named_linked = len(named) - sum(1 for u in unresolved if u.reason != "no_text")
    return {
        "observations": len({o for g in groups for o in g.obs_ids}),
        "canonical_elements": len(groups),
        "shared_observation_ids": sum(1 for n in Counter(o for g in groups for o in g.obs_ids).values() if n > 1),
        "named_canonical_elements": len(named),
        "linked_canonical_elements": linked,
        "linked_pct_all": round(100 * linked / len(groups), 1) if groups else 0.0,
        "linked_pct_named": round(100 * named_linked / len(named), 1) if named else 0.0,
        "links": len(links),
        "links_by_method": dict(sorted(Counter(l.method for l in links).items())),
        "confidence_histogram": {m: dict(sorted(h.items())) for m, h in sorted(hist.items())},
        "unresolved_reasons": dict(sorted(Counter(u.reason for u in unresolved).items())),
        "screen_hits": {
            "total": len(screen_hits),
            "demoted": sum(1 for h in screen_hits if h.demoted),
            "preferred_by_context": sum(1 for h in screen_hits if h.note and not h.demoted
                                        and h.method != "llm_pick"),
            "llm_pick_outside_closure": sum(1 for h in screen_hits if h.method == "llm_pick" and h.note),
        },
        "route_resolution": dict(sorted(Counter(reach.of(p).how for p in set(patterns)).items())),
        "testid_rung": {"elements_with_testid": sum(1 for g in groups if any(
            el.testid for _, el in g.seen)), "status": "skipped: CodeIndex carries no testid table"},
        "llm_enabled": llm_on,
        "llm_pick_calls": budget.calls,
        "llm_pick_failed_calls": budget.failed,
        "elements_resolved_without_llm": sum(
            1 for g in groups if g.best and all(h.method != "llm_pick" for h in g.best.values())),
    }


def link_ui_to_code(run: RunContext, cfg: TargetConfig, index: CodeIndex, screens: Sequence[ScreenState],
                    llm: LLM | None, *, max_pick_calls: int = MAX_PICK_CALLS) -> UiLinkResult:
    """Run the cascade. `llm=None` runs the deterministic rungs only (unresolved stay `no_candidate`)."""
    t0 = time.monotonic()
    maps = build_text_maps(index, (cfg.display_name, cfg.name))
    reach = Reachability(index, cfg.code.global_files)
    kinds = {f.path: f.kind for f in index.files}
    by_screen = {s.id: s for s in screens}
    groups = _build_groups(screens, cfg.name)
    brands = (cfg.display_name, cfg.name)
    contexts = {s.id: screen_context(s, brands) for s in screens}
    with run.stage("link.ui_to_code.cascade") as span:
        _derive(groups, reach, by_screen, maps, contexts)
        span.set(canonical_elements=len(groups))

    budget = CallBudget(max_pick_calls)
    reasons: dict[str, str] = {}
    if llm is not None:
        with run.stage("link.ui_to_code.pick") as span:
            reasons, picks = _run_picks(run, llm, groups, by_screen, reach, maps, kinds, budget)
            for g in groups:
                if g.cid in picks:
                    _apply_pick(g, picks[g.cid], reach, by_screen)
            span.set(calls=budget.calls, failed=budget.failed, picked=len(picks))
    judge = llm.model_for("mid") if llm is not None and budget.calls else None

    links: list[Link] = []
    unresolved: list[Unresolved] = []
    for g in groups:
        if g.best:
            links.extend(_link_rows(run, g, reach, by_screen, judge))
        else:
            unresolved.append(_unresolved(g, reasons))
    links = _dedupe(links)
    unresolved.sort(key=lambda u: (u.reason, u.element_id))
    stats = _summarise(groups, links, unresolved, budget, reach, (s.url_pattern for s in screens), llm is not None)
    stats["wall_clock_s"] = round(time.monotonic() - t0, 3)
    return UiLinkResult(links, unresolved, stats)
