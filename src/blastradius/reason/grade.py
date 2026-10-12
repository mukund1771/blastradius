"""Stage 7b: the CRAG-inspired retrieval grader (D17, D21, D25; cases 50, 51, 76; plan section 7).

Honest naming: published CRAG is a fine-tuned scalar evaluator with a web-search fallback. This keeps its
trigger pattern, Correct / Ambiguous / Incorrect -> corrective action, over deterministic signals, and is
called CRAG-inspired. It is a *runtime retrieval-quality* checker, independent of the linker's judge, the
verifier and the offline eval judge (D21).

Signals, per seed (a changed source file): mapped?; elements reached and the strongest route; the 2-hop
closure's UI files and their share of the app (the breadth gate: a share >= `grade.breadth_fraction_h2` is
too wide to accept); hub touched (hub package, global file, or a direct-importer share >= `hub_fraction_h1`);
minimum edge confidence; change kind from the diff; named-import hit rate (importers whose import names a
changed symbol).

Verdict:  CORRECT    seed mapped, >= 1 route at >= `min_element_confidence`, breadth under the gate, no hub
          INCORRECT  no usable evidence (nothing reaches a screen): "could not determine" + an UNMAPPED_CHANGE gap
          AMBIGUOUS  evidence present but wide, hub-dependent or weak
Actions (at most `max_rounds` corrective rounds; ESCALATE only records and is free):
          ACCEPT     CORRECT
          NARROW     named-import / change-kind filter. It only DOWNGRADES: an importer that names none of the
                     changed symbols, a reader of an additive-only change (case 76: additive setters write
                     shared state that existing readers consume) and every route of a type-only change keep
                     their edge, capped at "possible". Nothing is deleted.
          EXPAND     admit the held-in-reserve forward hop (D25) and the locale route (elements that show text
                     an i18n key this file uses resolves to), both capped
          PROBE      a deterministic census of who imports the file; yields an evidence row
          ESCALATE   an entry in needs_review.json
The verdict only ever CAPS a route's confidence (`Route.cap`); no code path here raises one. Thresholds and
caps come from `eval/thresholds.json`.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field

from ..ids import stable_id
from ..link.text import normalise_text
from ..models import MAX_HOPS, CodeIndex, FileClass, Gap, GapKind, Strict
from ..run import RunContext
from .change import ChangeKind
from .facts import Hit, Retrieval, Route, SeedInfo, path_confidence
from .thresholds import Thresholds, load_thresholds
from .words import humanize_path, quoted

Verdict = Literal["CORRECT", "AMBIGUOUS", "INCORRECT"]
Action = Literal["ACCEPT", "NARROW", "EXPAND", "PROBE", "ESCALATE"]
_UI_KINDS = frozenset({"page", "layout", "component"})
_EXPLAINED_ELSEWHERE = frozenset({"stored_data", "global_layout", "global_style", "hub"})


class Signals(Strict):
    mapped: bool
    evidence_routes: int
    strong: bool
    best_confidence: float
    min_edge_confidence: float | None
    direct_elements: int
    closure_ui_files_h2: int
    closure_fraction_h2: float
    effective_ui_files_h2: int
    effective_fraction_h2: float
    wide: bool
    hub: bool
    hub_reasons: list[str] = Field(default_factory=list)
    change_kind: str
    symbols_known: bool
    symbol_hit_rate: float | None = None


class Round(Strict):
    number: int
    verdict_before: Verdict
    action: Action
    effect: dict[str, Any] = Field(default_factory=dict)


class ReviewItem(Strict):
    id: str
    code: str                           # a FailureCode value or a Gap kind
    reason_kind: str                    # the aggregation key (case 61)
    subject: str                        # product words where possible
    detail: str
    what_to_check: str
    seeds: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    rank: int = 50                      # lower sorts first


class SeedGrade(Strict):
    seed: str
    display_path: str
    file_class: str
    verdict: Verdict
    action: Action
    rounds: list[Round] = Field(default_factory=list)
    signals: Signals
    reasons: list[str] = Field(default_factory=list)
    gap: Gap | None = None
    reviews: list[ReviewItem] = Field(default_factory=list)
    probe_evidence_id: str | None = None


class Grading(Strict):
    seeds: list[SeedGrade] = Field(default_factory=list)
    verdict: Verdict = "CORRECT"                       # PR level: the weakest seed that matters
    counts: dict[str, int] = Field(default_factory=dict)
    rounds_max: int = 2


# --------------------------------------------------------------------------------- relevance filter
def edge_relevant(kind: str, symbols: Sequence[str], touched: set[str]) -> bool | None:
    """Does this import edge name something the change touched? None = cannot tell (no named imports)."""
    if kind == "RENDERS":
        return True
    syms = set(symbols)
    if not syms:
        return None
    if "*" in syms:
        return True
    return bool(syms & touched)


def route_relevant(route: Route, touched: set[str]) -> bool | None:
    """True when every edge on the path names a touched symbol (or cannot tell), False when one provably does not."""
    verdict: bool | None = True
    for kind, symbols in zip(route.kinds, route.symbols, strict=False):
        one = edge_relevant(kind, symbols, touched)
        if one is False:
            return False
        if one is None:
            verdict = None
    return verdict


def _effective_ui_files(index: CodeIndex | None, seed: SeedInfo, touched: set[str], *,
                        by_kind: bool) -> int | None:
    """UI files that reach `seed` within MAX_HOPS through named-import-relevant edges only (None: no index).
    A change kind that downgrades every reader (additive-only, type-only) leaves none, index or not."""
    if by_kind:
        return 0
    if index is None:
        return None
    kinds = {f.path: f.kind.value for f in index.files}
    importers: dict[str, list[Any]] = {}
    for e in index.edges:
        importers.setdefault(e.dst, []).append(e)
    seen = {seed.path: 0}
    queue = deque([seed.path])
    ui = 0
    while queue:
        node = queue.popleft()
        if seen[node] >= MAX_HOPS:
            continue
        for e in sorted(importers.get(node, ()), key=lambda e: (e.src, e.kind)):
            if e.src in seen or e.src == seed.path or edge_relevant(e.kind, e.symbols, touched) is False:
                continue
            seen[e.src] = seen[node] + 1
            ui += kinds.get(e.src) in _UI_KINDS
            queue.append(e.src)
    return ui


# ---------------------------------------------------------------------------------------- signals
class _State:
    """Mutable per-seed grading state: which actions ran and what they did to breadth."""

    def __init__(self) -> None:
        self.narrowed = False
        self.effective_ui_files: int | None = None
        self.used: list[Action] = []


def _seed_routes(retr: Retrieval, seed: SeedInfo) -> list[tuple[Hit, Route]]:
    return [(h, r) for h in retr.hits for r in h.routes if r.seed == seed.path]


def _hub(seed: SeedInfo, th: Thresholds, state: _State, fraction: float) -> tuple[bool, list[str]]:
    """Hub touched: a hub package, a global layout or style, or direct importers >= `hub_fraction_h1` of UI files.
    The named-import filter answers a hub package or a direct-importer share: once NARROW has run and the
    relevant closure is under the breadth gate, the hub no longer decides the verdict. A global layout or style
    reaches every screen by construction and stays a hub."""
    structural = []
    if "global_layout" in seed.categories:
        structural.append("global layout")
    if "global_style" in seed.categories:
        structural.append("global style")
    soft = []
    if "hub" in seed.categories:
        soft.append("hub package")
    if seed.fraction_h1 >= th.grade.hub_fraction_h1:
        soft.append(f"{seed.fraction_h1:.0%} of UI files import it directly")
    if state.narrowed and fraction < th.grade.breadth_fraction_h2:
        soft = []
    reasons = structural + soft
    return bool(reasons), reasons


def signals_for(retr: Retrieval, seed: SeedInfo, th: Thresholds, state: _State) -> Signals:
    routes = [(h, r) for h, r in _seed_routes(retr, seed) if r.admitted and r.uncapped > 0.0]
    best = max((r.uncapped for _, r in routes), default=0.0)
    edge_confs = [r.impl_confidence for _, r in routes]
    direct = {h.key for h, r in routes if r.direction in ("self", "copy")}
    one_hop = [(h, r) for h, r in routes if r.direction in ("reverse", "overlay") and r.hops == 1]
    judged = [(h, r) for h, r in one_hop if route_relevant(r, set(seed.touched)) is not None and r.symbols]
    hit_rate = None
    if judged and seed.symbols_known:
        hit_rate = round(sum(route_relevant(r, set(seed.touched)) is True for _, r in judged) / len(judged), 4)
    ui_files = state.effective_ui_files if state.effective_ui_files is not None else seed.ui_importers_h2
    fraction = round(ui_files / seed.ui_total, 4) if seed.ui_total else seed.fraction_h2
    hub, hub_reasons = _hub(seed, th, state, fraction)
    mapped = seed.in_snapshot or (seed.added and seed.overlay_attached)
    return Signals(
        mapped=mapped, evidence_routes=len(routes), strong=best >= th.grade.min_element_confidence,
        best_confidence=round(best, 4), min_edge_confidence=min(edge_confs) if edge_confs else None,
        direct_elements=len(direct), closure_ui_files_h2=seed.ui_importers_h2,
        closure_fraction_h2=seed.fraction_h2, effective_ui_files_h2=ui_files, effective_fraction_h2=fraction,
        wide=fraction >= th.grade.breadth_fraction_h2, hub=hub, hub_reasons=hub_reasons,
        change_kind=seed.kind.value, symbols_known=seed.symbols_known, symbol_hit_rate=hit_rate)


def verdict_for(sig: Signals) -> Verdict:
    if not sig.mapped or sig.evidence_routes == 0:
        return "INCORRECT"
    if sig.strong and not sig.wide and not sig.hub:
        return "CORRECT"
    return "AMBIGUOUS"


def _can_narrow(seed: SeedInfo) -> bool:
    return seed.kind in (ChangeKind.ADDITIVE_API, ChangeKind.TYPE_ONLY) or (
        seed.kind in (ChangeKind.BODY, ChangeKind.SIGNATURE, ChangeKind.DELETE) and seed.symbols_known)


def choose(verdict: Verdict, sig: Signals, seed: SeedInfo, used: Sequence[Action]) -> Action:
    """The next corrective action (module docstring); ESCALATE when nothing further can improve the evidence."""
    if verdict == "CORRECT":
        return "ACCEPT"
    if verdict == "AMBIGUOUS" and (sig.wide or sig.hub) and "NARROW" not in used and _can_narrow(seed):
        return "NARROW"
    if verdict == "AMBIGUOUS" and (sig.wide or sig.hub):
        return "ESCALATE"
    for action in ("EXPAND", "PROBE"):
        if action not in used:
            return action             # type: ignore[return-value]
    return "ESCALATE"


# ----------------------------------------------------------------------------------------- actions
def _apply_kind_caps(seed: SeedInfo, routes: Sequence[tuple[Hit, Route]], th: Thresholds,
                     min_conf: float) -> dict[str, int]:
    """The change-kind cap and the named-import filter. Downgrades only; returns what it did."""
    touched = set(seed.touched)
    downgraded = readers = kept_readers = 0
    for _, r in routes:
        if not r.admitted:
            continue
        before = r.confidence
        reader = r.direction in ("reverse", "overlay") and r.hops >= 1
        if seed.kind is ChangeKind.TYPE_ONLY:
            r.cap("type_only", th.caps.type_only)
        elif reader:
            if seed.kind is ChangeKind.ADDITIVE_API:
                r.cap("additive_reader", th.caps.additive_reader)
            if seed.symbols_known and route_relevant(r, touched) is False:
                r.cap("symbol_miss", th.caps.symbol_miss)
        downgraded += r.confidence < before
        if reader:
            readers += 1
            kept_readers += r.confidence >= min_conf
    return {"downgraded": downgraded, "readers": readers, "readers_kept": kept_readers}


def _narrow(seed: SeedInfo, retr: Retrieval, th: Thresholds, state: _State, index: CodeIndex | None) -> dict[str, Any]:
    effect: dict[str, Any] = _apply_kind_caps(seed, _seed_routes(retr, seed), th, th.grade.min_element_confidence)
    by_kind = seed.kind in (ChangeKind.ADDITIVE_API, ChangeKind.TYPE_ONLY)
    effective = _effective_ui_files(index, seed, set(seed.touched), by_kind=by_kind)
    if effective is None:        # no code index at hand: scale the closure by the share of readers that stayed
        total = effect["readers"]
        effective = round(seed.ui_importers_h2 * (effect["readers_kept"] / total)) if total else \
            seed.ui_importers_h2
        effect["breadth_estimated"] = True
    state.narrowed, state.effective_ui_files = True, effective
    effect.update(effective_ui_files_h2=effective, kind=seed.kind.value)
    return effect


def _expand(seed: SeedInfo, retr: Retrieval, th: Thresholds, store: Any, index: CodeIndex | None) -> dict[str, Any]:
    admitted = 0
    for _, r in _seed_routes(retr, seed):
        if r.direction == "forward" and not r.admitted:
            r.admitted = True
            r.restrict("forward", th.caps.forward)
            admitted += 1
    located = _locale_route(seed, retr, th, store, index) if seed.keys else 0
    return {"forward_admitted": admitted, "locale_route_elements": located, "keys": len(seed.keys)}


def _locale_route(seed: SeedInfo, retr: Retrieval, th: Thresholds, store: Any, index: CodeIndex | None) -> int:
    """Elements that show the English text of a key this seed uses, implemented by *other* files."""
    rows = store.read("elements_for_keys", keys=list(seed.keys))
    if index is not None:
        value_of: dict[str, str | None] = {k: index.i18n.get(k) for k in seed.keys}
    else:
        value_of = {r["key"]: r.get("en") for r in store.read("keys_used_by_files", paths=[seed.path])}
    by_key = {h.key: h for h in retr.hits}
    count = 0
    for row in rows:
        if row["match"] != "exact" or row["file"] == seed.path:
            continue
        want = normalise_text(value_of.get(row["key"]) or "")
        if not want or normalise_text(row["name"]) != want:
            continue
        impl = float(row["confidence"] or 0.0)
        for screen_id in row["screen_ids"]:
            route = Route(
                seed=seed.path, direction="locale", hops=0, file=row["file"], via=[row["file"], seed.path],
                impl_confidence=impl, impl_method=row.get("method"), impl_evidence_id=row.get("evidence_id"),
                confidence=path_confidence(impl, 0), uncapped=path_confidence(impl, 0),
                note=f"shows the same text as {quoted(want)} used by the changed file",
                evidence_ids=[row["evidence_id"]] if row.get("evidence_id") else [])
            route.restrict("locale_route", th.caps.locale_route)
            hit = by_key.get((screen_id, row["element_id"]))
            if hit is None:
                hit = Hit(screen_id=screen_id, element_id=row["element_id"], element_role=row["role"],
                          element_name=row["name"], owner_file=row.get("owner_file"))
                retr.hits.append(hit)
                by_key[hit.key] = hit
            hit.routes.append(route)
            count += 1
    retr.hits.sort(key=lambda h: h.key)
    return count


def _probe(seed: SeedInfo, store: Any, run: RunContext) -> tuple[dict[str, Any], str]:
    """Who imports this file, by class: a census that explains an empty or weak result (yields evidence)."""
    census: dict[str, int] = {}
    importers = 0
    if seed.added and not seed.in_snapshot:
        importers = len(seed.overlay_importers)
        census = {"in the PR": importers} if importers else {}
    elif seed.in_snapshot:
        for row in store.read("importer_files", paths=[seed.path]):
            if row["importer"] is None:
                continue
            importers += 1
            census[row["kind"] or "unknown"] = census.get(row["kind"] or "unknown", 0) + 1
    census = dict(sorted(census.items()))
    ui = sum(n for k, n in census.items() if k in _UI_KINDS)
    parts = ", ".join(f"{n} {k}" for k, n in census.items()) or "none"
    ev = run.evidence.add("reason.probe", seed.path,
                          f"{seed.path} is imported by {importers} file(s) ({parts}); {ui} of them draw a screen")
    return {"importers": importers, "ui_importers": ui, "by_class": census}, ev


# -------------------------------------------------------------------------------------------- stage
def _unmapped_gap(seed: SeedInfo, retr: Retrieval, reason: str, evidence_id: str | None) -> Gap | None:
    if not seed.is_ui_dir:
        return None
    return Gap(id=stable_id("gap", "UNMAPPED_CHANGE", seed.display_path), kind=GapKind.UNMAPPED_CHANGE,
               status="OBSERVED", subject_id=seed.display_path, reason=reason, confidence=0.0,
               evidence_id=evidence_id)


_WHY = {
    "OVERLAY_UNAVAILABLE": "this PR adds the file, and the PR's own code could not be read to see where it is used",
    "ADDED_NOT_USED": ("this PR adds the file, but no other file in the PR uses it (or it is used through a "
                       "dynamic import)"),
    "ADDED_NOT_REACHING_SCREEN": ("this PR adds the file and other code in the PR uses it, but none of that code "
                                  "reaches a screen we observed"),
    "PR_PATH_MISSING_AT_HEAD": ("the file is not in the code snapshot we indexed (the PR may be based on an older "
                                "commit, or renamed it)"),
    "TEXT_NOT_SHOWN": "the changed text is not shown on any screen we observed",
    "NOT_MAPPED_TO_SCREEN": ("no control we observed is built from this file or from code that uses it within two "
                             "steps (it may be backend-only, internal, or on a screen the crawl did not reach)"),
}


def _reason_for_incorrect(seed: SeedInfo, retr: Retrieval) -> tuple[str, str]:
    """(reason code, reason in plain words) for a seed that reached nothing."""
    if seed.added and not seed.in_snapshot:
        if retr.overlay.get("status") != "applied":
            code = "OVERLAY_UNAVAILABLE"
        else:
            code = "ADDED_NOT_REACHING_SCREEN" if seed.overlay_attached else "ADDED_NOT_USED"
    elif not seed.in_snapshot:
        code = "PR_PATH_MISSING_AT_HEAD"
    elif seed.kind is ChangeKind.COPY_ONLY:
        code = "TEXT_NOT_SHOWN"
    else:
        code = "NOT_MAPPED_TO_SCREEN"
    return code, _WHY[code]


def _grade_locale_seed(res: Any, th: Thresholds) -> SeedGrade:
    """COPY_ONLY seed: correct when the changed text reaches controls that show it, or when nothing existing
    changed (only added strings); ambiguous when controls share a file with the text but show other words."""
    changed = res.changed + res.removed
    if res.problem:
        verdict: Verdict = "INCORRECT"
    elif changed == 0 or res.exact_matches:
        verdict = "CORRECT"
    elif res.elements_matched:
        verdict = "AMBIGUOUS"
    else:
        verdict = "INCORRECT"
    sig = Signals(
        mapped=res.problem is None, evidence_routes=res.elements_matched, strong=res.exact_matches > 0 or changed == 0,
        best_confidence=0.0, min_edge_confidence=None, direct_elements=res.exact_matches, closure_ui_files_h2=0,
        closure_fraction_h2=0.0, effective_ui_files_h2=0, effective_fraction_h2=0.0, wide=False, hub=False,
        change_kind=ChangeKind.COPY_ONLY.value, symbols_known=False)
    return SeedGrade(seed=f"locale:{res.path}", display_path=res.path, file_class=FileClass.LOCALE.value,
                     verdict=verdict, action="ACCEPT" if verdict == "CORRECT" else "ESCALATE", signals=sig,
                     reasons=[] if verdict == "CORRECT" else [res.problem or "changed text reaches no control"])


def grade(run: RunContext, store: Any, retr: Retrieval, *, base_index: CodeIndex | None = None,
          th: Thresholds | None = None) -> Grading:
    """Grade every seed and apply the corrective actions in place (routes are capped, reserve routes admitted)."""
    th = th or load_thresholds()
    out = Grading(rounds_max=th.grade.max_rounds)
    with run.stage("reason.grade", seeds=len(retr.seeds)) as span:
        for seed in retr.seeds:
            out.seeds.append(_grade_seed(run, store, retr, seed, th, base_index))
        for res in retr.locales:
            out.seeds.append(_grade_locale_seed(res, th))
        out.seeds.sort(key=lambda g: g.seed)
        counts = {v: sum(g.verdict == v for g in out.seeds) for v in ("CORRECT", "AMBIGUOUS", "INCORRECT")}
        out.counts = counts
        out.verdict = "INCORRECT" if counts["INCORRECT"] and not (counts["CORRECT"] or counts["AMBIGUOUS"]) else \
            "AMBIGUOUS" if counts["AMBIGUOUS"] or counts["INCORRECT"] else "CORRECT"
        span.set(**counts)
    return out


def _grade_seed(run: RunContext, store: Any, retr: Retrieval, seed: SeedInfo, th: Thresholds,
                base_index: CodeIndex | None) -> SeedGrade:
    state = _State()
    rounds: list[Round] = []
    probe_ev: str | None = None
    sig = signals_for(retr, seed, th, state)
    if seed.is_global_only:
        # reaches every screen by construction: no per-screen evidence to improve, the global item says so
        return SeedGrade(seed=seed.path, display_path=seed.display_path, file_class=seed.file_class.value,
                         verdict="AMBIGUOUS", action="ESCALATE", signals=sig, reasons=_reasons(sig, "AMBIGUOUS"))
    verdict = verdict_for(sig)
    for number in range(1, th.grade.max_rounds + 1):
        action = choose(verdict, sig, seed, state.used)
        if action in ("ACCEPT", "ESCALATE"):
            break
        if action == "NARROW":
            effect = _narrow(seed, retr, th, state, base_index)
        elif action == "EXPAND":
            effect = _expand(seed, retr, th, store, base_index)
        else:
            effect, probe_ev = _probe(seed, store, run)
        state.used.append(action)
        rounds.append(Round(number=number, verdict_before=verdict, action=action, effect=effect))
        sig = signals_for(retr, seed, th, state)
        verdict = verdict_for(sig)
    if not state.narrowed:
        # ACCEPT and ESCALATE still apply the change-kind cap: an additive-only or type-only change cannot make
        # a reader more than "possible" however small its closure is
        _apply_kind_caps(seed, _seed_routes(retr, seed), th, th.grade.min_element_confidence)
    if verdict == "AMBIGUOUS":
        for _, r in _seed_routes(retr, seed):
            if r.direction not in ("self", "copy"):
                r.cap("ambiguous_indirect", th.caps.ambiguous_indirect)
    final: Action = "ACCEPT" if verdict == "CORRECT" else "ESCALATE"
    reasons = _reasons(sig, verdict)
    grade_ = SeedGrade(seed=seed.path, display_path=seed.display_path, file_class=seed.file_class.value,
                       verdict=verdict, action=final, rounds=rounds, signals=sig, reasons=reasons,
                       probe_evidence_id=probe_ev)
    if verdict == "INCORRECT":
        _finish_incorrect(run, store, retr, seed, grade_)
    elif verdict == "AMBIGUOUS":
        grade_.reviews.append(_review_ambiguous(seed, sig, grade_))
    return grade_


def _reasons(sig: Signals, verdict: Verdict) -> list[str]:
    out = []
    if not sig.mapped:
        out.append("seed not mapped")
    if sig.evidence_routes == 0:
        out.append("no route reaches a screen")
    if sig.wide:
        out.append(f"2-hop closure holds {sig.effective_fraction_h2:.1%} of UI files "
                   f"(gate {'not met' if verdict != 'CORRECT' else 'met'})")
    if sig.hub:
        out.append("hub: " + ", ".join(sig.hub_reasons))
    if sig.evidence_routes and not sig.strong:
        out.append(f"best route {sig.best_confidence:.2f} is below the element floor")
    return out


def _finish_incorrect(run: RunContext, store: Any, retr: Retrieval, seed: SeedInfo, grade_: SeedGrade) -> None:
    code, why = _reason_for_incorrect(seed, retr)
    if grade_.probe_evidence_id is None and seed.in_snapshot:
        _, grade_.probe_evidence_id = _probe(seed, store, run)
    evidence = [e for e in (seed.evidence_id, grade_.probe_evidence_id) if e]
    grade_.reasons.append(code)
    if set(seed.categories) & _EXPLAINED_ELSEWHERE:
        return          # a data-schema, layout, style or hub file is reported as a stored-data or global item
    grade_.gap = _unmapped_gap(seed, retr, code, grade_.probe_evidence_id or seed.evidence_id)
    name = humanize_path(seed.display_path)
    grade_.reviews.append(ReviewItem(
        id=stable_id("rev", seed.display_path, code), code=("UNMAPPED_CHANGE" if grade_.gap else "NEEDS_HUMAN"),
        reason_kind=code, subject=f"the {name} {'component' if seed.is_ui_dir else 'code'}",
        detail=why, seeds=[seed.display_path], evidence_ids=evidence,
        what_to_check=(f"Find where {name} appears in the product and try it by hand; this change is not tied to "
                       "any screen we observed."), rank=20 if seed.is_ui_dir else 40))


def _review_ambiguous(seed: SeedInfo, sig: Signals, grade_: SeedGrade) -> ReviewItem:
    name = humanize_path(seed.display_path)
    if sig.hub:
        why = "shared by a large part of the app, so the list of affected screens cannot be narrowed with confidence"
        kind = "HUB_TOUCHED"
    elif sig.wide:
        why = "used by a large part of the app even after keeping only code that uses the changed parts"
        kind = "TOO_BROAD"
    else:
        why = "the only links we found to screens are weak"
        kind = "WEAK_EVIDENCE"
    return ReviewItem(
        id=stable_id("rev", seed.display_path, kind), code="NEEDS_HUMAN", reason_kind=kind,
        subject=f"the shared {name} code", detail=why, seeds=[seed.display_path],
        evidence_ids=[e for e in (seed.evidence_id, grade_.probe_evidence_id) if e],
        what_to_check=f"Decide which screens that rely on {name} need a re-test; the list shown is not exhaustive.",
        rank=10 if sig.hub else 30)


def grade_locale(retr: Retrieval, th: Thresholds) -> list[ReviewItem]:
    """A locale diff has no seed of its own in the code graph: its 'grade' is whether changed text reached a screen."""
    out = []
    for res in retr.locales:
        if res.problem:
            out.append(ReviewItem(
                id=stable_id("rev", res.path, res.problem), code="NEEDS_HUMAN", reason_kind=res.problem.upper(),
                subject="the interface texts", detail=f"the texts file could not be compared ({res.problem})",
                what_to_check="Compare the changed wording by hand.", seeds=[res.path],
                evidence_ids=[res.evidence_id] if res.evidence_id else [], rank=25))
        elif (res.changed or res.removed) and res.elements_matched == 0:
            out.append(ReviewItem(
                id=stable_id("rev", res.path, "TEXT_NOT_SHOWN"), code="NEEDS_HUMAN", reason_kind="TEXT_NOT_SHOWN",
                subject="changed wording", detail="no control we observed shows the changed or removed text",
                what_to_check="Check where the changed wording appears (dialogs and messages the crawl did not "
                              "reach).", seeds=[res.path],
                evidence_ids=[res.evidence_id] if res.evidence_id else [], rank=45))
    return out
