"""Stage 7c-i: graded facts -> the Report's items, requirements and could-not-determine list (D29; cases 50-52, 61).

Deterministic and LLM-free: every sentence an item carries is a template over product words (screen and dialog
titles and control names from the crawl, requirement sections from the spec), so the narrator only has to
write the headline, the confidence line and the checklist, and a template fallback can always do that too.

Unit of reporting. A QA lead thinks in pages and dialogs, not in tab states of one page, so an item is one
(page or dialog, tier). `Report` counts are over distinct such units, flows and requirements (case 51);
`impact.json` keeps the Screen-node counts too. Confidence of an item = noisy-OR of its controls' best routes,
clamped to the band of the strongest (`facts.combine`), so combining evidence never crosses a band and the
band word of every sentence can be checked against the routes it cites.

Tiers: DIRECT = a control built from a changed file or whose text changed; INDIRECT = reached only through
importers or renderers, capped at `retrieval.tier2_cap` and grouped by requirement section; GLOBAL = a changed
layout, global style or hub package (no per-screen list: every screen may be affected); STORED DATA = a changed
data-schema file. Routes below the "possible" band are not items; they are counted as one could-not-determine
row, so weak evidence is never presented as an effect.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..code.fileclass import workspace_package
from ..ids import stable_id
from ..link.flows import GENERIC_DIALOG_TITLES
from ..link.text import tokens
from ..models import Band, band_for
from ..report.model import (
    Claim,
    Report,
    ReportItem,
    RequirementRisk,
    SnapshotInfo,
    Tier,
    Undetermined,
)
from ..run import RunContext
from ..targets import TargetConfig
from .facts import Hit, Retrieval, Route, SeedInfo, combine
from .grade import Grading, ReviewItem
from .pr import PRData
from .risk import REMOVED_TEXT_VIA, derive_risk
from .thresholds import Thresholds
from .words import humanize_path, join_names, quoted, screen_label, sentence

_DISTINCTIVE = 7       # a single shared word counts as "related" only when it is this long ("environment")
_BAND_FLOOR = {Band.CONFIRMED: 0.8, Band.LIKELY: 0.5, Band.POSSIBLE: 0.25, Band.UNKNOWN: 0.0}
_VERB = {
    Band.CONFIRMED: "is changed by this PR",
    Band.LIKELY: "is probably affected by this PR",
    Band.POSSIBLE: "might be affected by this PR; worth a quick look",
}
_KIND_PHRASE = {
    "ADDITIVE_API": "it gained new functions, so existing users only see a difference through state the new "
                    "functions store",
    "TYPE_ONLY": "only type definitions or comments changed",
    "SIGNATURE": "the way it is called changed",
    "DELETE": "it is removed",
    "BODY": "its behaviour changed",
}
MIGRATION_STEPS = (
    "Export your saved data, import the export again, and check nothing is lost.",
    "Open data saved by the previous version in the new build and check it still loads.",
    "If you use sync or teams, sync once and check the same data appears on a second device.",
)
BASE_NOT_CHECKED = (
    "Only the signed-out surface of the web app was observed.",
    "One viewport size was used.",
    "Only English text (`en`) was compared.",
    "Frontend files only: backend, scripts and build tooling are outside this analysis.",
    "Cloud web edition only: desktop and self-hosted behaviour was not assessed.",
)


@dataclass
class Unit:
    key: tuple[str, str]
    label: str
    screen_ids: list[str] = field(default_factory=list)


@dataclass
class Assembly:
    report: Report
    basis: dict[str, list[dict[str, Any]]]
    review: list[ReviewItem]
    expected_direct: list[str]
    overflow: list[ReportItem]
    impact: dict[str, Any]
    counts: dict[str, int]
    controls: dict[str, list[tuple[str, str]]]      # item id -> (role, name) of the controls involved
    removed_text: list[str] = field(default_factory=list)   # old wording of removed text screens still show


# ------------------------------------------------------------------------------------------- units
def build_units(store: Any, cfg: TargetConfig, retr: Retrieval) -> tuple[dict[tuple[str, str], Unit],
                                                                          dict[str, tuple[str, str]]]:
    """Screens that carry a route -> reporting units (a page, or a dialog on a page)."""
    ids = sorted({h.screen_id for h in retr.hits})
    rows = list(store.read("screens_by_id", screen_ids=ids)) if ids else []
    brands = [cfg.display_name, cfg.name]
    by_pattern: dict[str, str] = {}
    for r in sorted(rows, key=lambda r: (r["kind"] != "page", r["screen_id"])):
        pattern = r["url_pattern"] or "/"
        by_pattern.setdefault(pattern, screen_label(title=r["title"] or "", dialog_title=None, kind="page",
                                                    active_tabs=[], url_pattern=pattern, brand_words=brands))
    units: dict[tuple[str, str], Unit] = {}
    owner: dict[str, tuple[str, str]] = {}
    for r in sorted(rows, key=lambda r: r["screen_id"]):
        pattern = r["url_pattern"] or "/"
        dialog = (r["dialog_title"] or "").strip() if r["kind"] == "modal" else ""
        key = (pattern, dialog)
        page = by_pattern[pattern]
        if key not in units:
            if dialog:
                noun = "popup" if dialog.casefold() in GENERIC_DIALOG_TITLES else "dialog"
                label = f"{sentence(dialog)} {noun} on the {page}"
            else:
                label = page
            units[key] = Unit(key=key, label=label)
        units[key].screen_ids.append(r["screen_id"])
        owner[r["screen_id"]] = key
    return units, owner


def unit_titles(units: dict[tuple[str, str], Unit], owner: dict[str, tuple[str, str]]) -> dict[str, str]:
    return {sid: units[key].label for sid, key in owner.items()}


# --------------------------------------------------------------------------------------- selection
def _live(hit: Hit, seeds: dict[str, SeedInfo]) -> list[Route]:
    """Routes that may support an item: admitted, at least "possible", and not from a global-only seed."""
    out = []
    for r in hit.routes:
        seed = seeds.get(r.seed)
        if seed is not None and seed.is_global_only:
            continue
        if r.admitted and band_for(r.confidence) is not Band.UNKNOWN:
            out.append(r)
    return out


def _strong_direct(hit: Hit, route: Route) -> bool:
    """Hop 0 onto a changed file or a changed string. A shared label ("one of k files", k > 3) below 0.5
    is not direct: the file merely contains the same words."""
    if route.hops != 0 or route.direction not in ("self", "copy") or not route.admitted:
        return False
    if len(hit.impl_files) > 3 and route.impl_confidence < 0.5:
        return False
    return route.impl_confidence >= 0.5 or len(hit.impl_files) <= 1


def _is_direct(route: Route) -> bool:
    return route.hops == 0 and route.direction in ("self", "copy")


def _best(routes: list[Route]) -> Route:
    return max(routes, key=lambda r: (r.confidence, r.direction in ("self", "copy"), -r.hops, r.seed))


def _kind_of(seed: SeedInfo | None) -> str:
    return seed.kind.value if seed else "BODY"


def _verb(band: Band) -> str:
    return _VERB.get(band, "we cannot tell whether it is affected; not determined")


def _listed_control(hit: Hit) -> bool:
    """Named in the body only when the link is unique or at least 0.5. Shared ties stay in the "N more" count."""
    best = max((r.impl_confidence for r in hit.routes if r.admitted), default=0.0)
    if len(hit.impl_files) > 3 and best < 0.5:
        return False
    return best >= 0.5 or len(hit.impl_files) <= 1


def _controls(hits: list[Hit]) -> list[str]:
    seen: dict[str, None] = {}
    for h in sorted(hits, key=lambda h: (h.element_name, h.element_id)):
        if not _listed_control(h):
            continue
        name = h.element_name or h.element_role or "control"
        seen.setdefault(quoted(name), None)
    return list(seen)


def _direct_text(band: Band, hits: list[Hit], routes: list[Route], seeds: dict[str, SeedInfo]) -> tuple[str, str | None]:
    notes = sorted({r.note for r in routes if r.direction == "copy" and r.note})
    severe = any(r.high_severity for r in routes)
    controls = _controls(hits)
    text = f"This screen {_verb(band)}."
    if notes:
        text += " " + sentence(join_names(notes, 2, more="more change")) + "."
    if severe:
        text += " Screens that still use the removed text can show a raw text id instead of words."
    if any(seeds.get(r.seed) is not None and seeds[r.seed].kind.value == "DELETE" for r in routes):
        text += " The code behind these controls is removed by this PR."
    if controls:
        text += f" Controls involved: {join_names(controls, 3)}."
    return text, (REMOVED_TEXT_VIA if severe else None)


def _global_text(band: Band, noun: str, names: str) -> str:
    edit = f"This PR edits the {noun} ({names})."
    tail = "The lists in this report are therefore not exhaustive."
    if band is Band.CONFIRMED:
        return f"{edit} Every screen is changed by this PR to some degree. {tail}"
    if band is Band.LIKELY:
        return f"{edit} Many screens are probably affected by this PR. {tail}"
    return f"{edit} Many screens might be affected by this PR; worth a quick look. {tail}"


def _via_phrase(routes: list[Route], seeds: dict[str, SeedInfo]) -> str:
    names = sorted({humanize_path(seeds[r.seed].display_path) if r.seed in seeds else humanize_path(r.seed)
                    for r in routes})
    if len(names) == 1:
        return f"the shared {names[0]} code"
    return f"{len(names)} pieces of shared code ({', '.join(names[:3])}{' and more' if len(names) > 3 else ''})"


def _indirect_text(band: Band, hits: list[Hit], routes: list[Route], seeds: dict[str, SeedInfo]) -> str:
    kinds = sorted({_kind_of(seeds.get(r.seed)) for r in routes})
    why = "; ".join(_KIND_PHRASE.get(k, _KIND_PHRASE["BODY"]) for k in kinds[:2])
    text = (f"This screen {_verb(band)}. It uses {_via_phrase(routes, seeds)}, which this PR edits ({why}).")
    named = _controls(hits)
    if named:
        text += f" Controls built from code that uses it: {join_names(named, 3)}."
    return text


# ------------------------------------------------------------------------------------------ builder
class _Builder:
    def __init__(self, run: RunContext, cfg: TargetConfig, pr: PRData, retr: Retrieval, th: Thresholds,
                 units: dict[tuple[str, str], Unit], owner: dict[str, tuple[str, str]], labels: dict[str, str]):
        self.run, self.cfg, self.pr, self.retr = run, cfg, pr, retr
        self.th, self.units, self.owner, self.labels = th, units, owner, labels
        self.seeds = {s.path: s for s in retr.seeds}
        self.basis: dict[str, list[dict[str, Any]]] = {}
        self.controls: dict[str, list[tuple[str, str]]] = {}
        self.removed_text: set[str] = set()
        self.foreign: set[str] = set()      # cited by the graph, absent from this run's ledger

    # ---- evidence and claims ------------------------------------------------------------------
    def _impact_evidence(self, tier: str, key: str, label: str, n: int, conf: float, via: str) -> str:
        return self.run.evidence.add(
            "reason.impact", f"{self.pr.repo}#{self.pr.number}:{tier}:{key}",
            f"{tier} impact on {label}: {n} control(s), confidence {conf:.2f}{', via ' + via if via else ''}")

    def _known(self, ids: Iterable[str]) -> list[str]:
        """The ids the run's ledger holds; the rest are remembered (the pipeline says so in the report)."""
        out = []
        for ev in ids:
            if self.run.evidence.exists(ev):
                out.append(ev)
            else:
                self.foreign.add(ev)
        return out

    def _claim(self, subject: str, text: str, band: Band, edges: list[tuple[str, float]], own: str,
               conf: float) -> Claim:
        """One cited sentence. Its edges are those in the ledger; when the graph's own evidence lives in another
        run, the claim rests on this run's `reason.impact` row at the item's confidence (never on a made-up id)."""
        known = set(self._known(e for e, _ in edges))
        kept = sorted({(e, c) for e, c in edges if e in known}) or [(own, conf)]
        claim = Claim(id=stable_id("clm", self.pr.number, subject, text), text=f"{subject}: {text}",
                      evidence_ids=sorted({own, *[e for e, _ in kept]}), band=band)
        self.basis[claim.id] = [{"evidence_id": e, "confidence": c} for e, c in kept]
        return claim

    def _grouped_controls(self, hits: list[Hit]) -> list[str]:
        """One evidence row per (role, name): "in N files", capped. Duplicate link rows are not cited."""
        groups: dict[tuple[str, str], set[str]] = {}
        for h in hits:
            if not _listed_control(h):
                continue
            name = h.element_name or h.element_role or "control"
            role = h.element_role or "control"
            groups.setdefault((role, name), set()).update(r.file for r in h.routes if r.file)
        ids: list[str] = []
        for role, name in sorted(groups)[:8]:
            n = len(groups[(role, name)])
            ids.append(self.run.evidence.add(
                "reason.control", f"{self.pr.repo}#{self.pr.number}:{role}:{name}:{n}",
                f'{role} "{name}" in {n} file(s)'))
        return ids

    def _edges_at_band(self, routes: list[Route], band: Band) -> list[tuple[str, float]]:
        """The routes that carry the item's band (the claim cites these, so its lowest cited edge is its band)."""
        out = []
        for r in routes:
            if band_for(r.confidence) is band:
                for ev in r.evidence_ids or ([r.impl_evidence_id] if r.impl_evidence_id else []):
                    out.append((ev, r.confidence))
        return out

    # ---- tier 1 and 2 ---------------------------------------------------------------------------
    def unit_items(self) -> tuple[list[ReportItem], list[ReportItem], list[Claim]]:
        by_unit: dict[tuple[str, str], list[tuple[Hit, list[Route]]]] = {}
        for hit in self.retr.hits:
            live = _live(hit, self.seeds)
            if live and hit.screen_id in self.owner:
                by_unit.setdefault(self.owner[hit.screen_id], []).append((hit, live))
        direct: list[ReportItem] = []
        indirect: list[ReportItem] = []
        claims: list[Claim] = []
        for key in sorted(by_unit):
            unit = self.units[key]
            entries = by_unit[key]
            direct_entries = [(h, [r for r in rs if _strong_direct(h, r)]) for h, rs in entries]
            direct_entries = [(h, rs) for h, rs in direct_entries if rs]
            use = direct_entries or entries
            tier: Tier = "direct" if direct_entries else "indirect"
            hits = [h for h, _ in use]
            per_hit_routes = [rs for _, rs in use]
            routes = [r for rs in per_hit_routes for r in rs]
            conf = combine(_best(rs).confidence for rs in per_hit_routes)
            band = band_for(conf)
            if band is Band.UNKNOWN:
                continue
            item, claim = self._unit_item(tier, unit, hits, routes, conf, band)
            (direct if tier == "direct" else indirect).append(item)
            claims.append(claim)
        return direct, indirect, claims

    def _unit_item(self, tier: Tier, unit: Unit, hits: list[Hit], routes: list[Route], conf: float,
                   band: Band) -> tuple[ReportItem, Claim]:
        seeds_used = sorted({r.seed for r in routes})
        if tier == "direct":
            what, via = _direct_text(band, hits, routes, self.seeds)
            if via is None and any(r.direction == "copy" for r in routes):
                via = "changed wording"
        else:
            what, via = _indirect_text(band, hits, routes, self.seeds), _via_phrase(routes, self.seeds)
        flows = sorted({f["name"] for h in hits for f in h.flows if f.get("name")})
        reqs = sorted({str(q["id"]) for h in hits for q in h.requirements})
        key = f"{unit.key[0]}|{unit.key[1]}"
        own = self._impact_evidence(tier, key, unit.label, len(hits), conf, via or "")
        seed_ev = [self.seeds[s].evidence_id for s in seeds_used if s in self.seeds and self.seeds[s].evidence_id][:6]
        self._known(ev for r in routes for ev in r.evidence_ids)  # other-run link rows stay flagged, not cited
        grouped = self._grouped_controls(hits)
        files = sorted({self.seeds[s].display_path if s in self.seeds else s for s in seeds_used} |
                       {r.file for r in routes if r.file})
        item = ReportItem(
            id=stable_id("itm", self.pr.number, tier, key), tier=tier, screen=unit.label, label=unit.label,
            what_changed=what, band=band, confidence=conf, section=None, via=via, flows=flows,
            requirement_ids=reqs, evidence_ids=sorted({own, *seed_ev, *grouped}), files=files)
        claim = self._claim(unit.label, what, band, [(e, conf) for e in grouped], own, conf)
        self.controls[item.id] = sorted({(h.element_role, h.element_name) for h in hits if h.element_name})
        self.removed_text |= {r.old_text for r in routes if r.high_severity and r.old_text}
        return item, claim

    # ---- global and stored data -------------------------------------------------------------------
    def global_items(self) -> tuple[list[ReportItem], list[Claim]]:
        groups: dict[str, list[SeedInfo]] = {}
        for s in self.retr.seeds:
            for cat in s.categories:
                if cat in ("global_layout", "global_style", "hub"):
                    groups.setdefault(cat, []).append(s)
        items, claims = [], []
        for cat in sorted(groups):
            seeds = groups[cat]
            label, noun = {
                "global_layout": ("The shared page frame (layout) around every screen", "page layout"),
                "global_style": ("Global styles used by every screen", "global styles"),
                "hub": ("Code shared across much of the app", "shared package"),
            }[cat]
            routes = [r for s in seeds for h in self.retr.hits for r in h.routes if r.seed == s.path and r.admitted]
            if cat == "hub":
                live = [r for r in routes if band_for(r.confidence) is not Band.UNKNOWN]
                conf = max((r.confidence for r in live), default=self.th.caps.ambiguous_indirect)
                conf = min(conf, self.th.confidence.global_change)
            else:
                conf = self.th.confidence.global_change
            band = band_for(conf)
            names = ", ".join(sorted({self._global_name(s, cat) for s in seeds})[:3])
            what = _global_text(band, noun, names)
            own = self._impact_evidence("global", cat, label, len(seeds), conf, noun)
            seed_ev = [s.evidence_id for s in seeds if s.evidence_id]
            item = ReportItem(
                id=stable_id("itm", self.pr.number, "global", cat), tier="global", screen=None, label=label,
                what_changed=what, band=band, confidence=conf, via=noun,
                evidence_ids=sorted({own, *seed_ev}), files=sorted(s.display_path for s in seeds))
            items.append(item)
            claims.append(self._claim(label, what, band, [(e, conf) for e in seed_ev], own, conf))
        return items, claims

    def stored_items(self) -> tuple[list[ReportItem], list[Claim]]:
        groups: dict[str, list[SeedInfo]] = {}
        for s in self.retr.seeds:
            if "stored_data" in s.categories:
                groups.setdefault(self._data_name(s), []).append(s)
        items, claims = [], []
        for name in sorted(groups):
            seeds = groups[name]
            conf = self.th.confidence.stored_data
            if all(s.kind.value == "TYPE_ONLY" for s in seeds):
                conf = min(conf, self.th.caps.type_only)
            band = band_for(conf)
            label = f"Saved {name} data"
            what = f"The saved {name} data format {_verb(band)}."
            if band is Band.CONFIRMED:
                what += " Data saved by older versions has to keep opening in the new build."
            own = self._impact_evidence("stored_data", name, label, len(seeds), conf, "")
            seed_ev = [s.evidence_id for s in seeds if s.evidence_id]
            item = ReportItem(
                id=stable_id("itm", self.pr.number, "stored_data", name), tier="stored_data", screen=None,
                label=label, what_changed=what, band=band, confidence=conf, via="saved data format",
                evidence_ids=sorted({own, *seed_ev}), files=sorted(s.display_path for s in seeds))
            items.append(item)
            claims.append(self._claim(label, what, band, [(e, conf) for e in seed_ev], own, conf))
        return items, claims

    def _global_name(self, seed: SeedInfo, category: str) -> str:
        """A hub package by its package name in words; a layout or style by its file name in words."""
        if category == "hub":
            for root in self.cfg.code.roots:
                if seed.display_path.startswith(root.strip("/") + "/"):
                    pkg = workspace_package(root) or root
                    return quoted(pkg.lstrip("@").replace("/", " ").replace("-", " "))
        return humanize_path(seed.display_path)

    @staticmethod
    def _data_name(seed: SeedInfo) -> str:
        parts = seed.display_path.split("/src/", 1)
        rel = parts[1] if len(parts) == 2 else seed.display_path
        if "/" not in rel:                      # a file directly under src: the package names it
            return humanize_path(seed.display_path) or "application"
        return humanize_path(rel.split("/")[0]) or "application"


# -------------------------------------------------------------------------------------- requirements
def _requirement_confidence(hit_conf: float, link_conf: float) -> float:
    return round(hit_conf * link_conf, 4)


def spec_requirement_ids(run: RunContext) -> set[str]:
    """Ids this run actually extracted. An earlier run's graph rows are not this run's spec."""
    path = run.run_dir / "spec" / "requirements.json"
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    rows = data if isinstance(data, list) else data.get("requirements", []) if isinstance(data, dict) else []
    return {str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")}


def _related_to_change(text: str, section: str | None, context: set[str]) -> bool:
    """A requirement shares a distinctive word, or two words, with what the PR changes."""
    shared = (set(tokens(text)) | set(tokens(section or ""))) & context
    return len(shared) >= 2 or any(len(t) >= _DISTINCTIVE for t in shared)


def build_requirements(run: RunContext, store: Any, pr: PRData, retr: Retrieval, seeds: dict[str, SeedInfo],
                       bundle_hash: str | None, locale_texts: list[str], diff_texts: list[str],
                       context: set[str]) -> tuple[list[RequirementRisk], dict[str, float], int, int]:
    """Requirements at risk, losing their only realization, unverifiable, or possibly closed by this PR, the
    confidence each statement rests on (absences have none, so they carry the floor of their band), how many
    absences were left out as unrelated, and how many requirements reached only
    through shared code or a whole screen, and unrelated to the change, were left out.

    A requirement realised by a control that a changed file implements itself (any hop-0 route whose direction is
    self or copy, even when a shared-code route on the same control is more confident) is at risk. One reached only
    through shared code, or tied to a whole screen or a flow, says only that the feature lives near the change: a
    flow step or the Sidebar can sit next to the environments editor, so every requirement of that flow or page
    would otherwise be "at risk". Those count only when the requirement shares a word with what the PR changes
    (`context`), or when this PR removes every control that realises them. Otherwise they are left out and counted.

    An absence is reported only when it shares a word with what the PR changes (`context`: control names of the
    directly changed screens, the changed files' names, the strings the PR adds, the sections of at-risk
    requirements). Otherwise every unmet requirement of a page would be "cannot verify" whenever any file that
    page renders changes (case 61)."""
    risks: dict[str, RequirementRisk] = {}
    confs: dict[str, float] = {}
    unrelated = 0
    weak_unrelated = 0
    allowed = spec_requirement_ids(run)
    if not allowed:
        return [], {}, 0, 0

    # at risk: requirements realised by an affected control, its screen or its flow
    Entry = tuple[float, str | None, list[str]]
    per_req: dict[str, list[Entry]] = {}            # realised by a control a changed file implements (hop 0)
    indirect_req: dict[str, list[Entry]] = {}       # realised by a control reached only through shared code
    coarse: dict[str, list[Entry]] = {}             # realised by a whole screen or a flow, not by one control
    for hit in retr.hits:
        live = [r for r in hit.routes if r.admitted and band_for(r.confidence) is not Band.UNKNOWN
                and not (seeds.get(r.seed) and seeds[r.seed].is_global_only)]
        if not live:
            continue
        best = max(r.confidence for r in live)
        # Any hop-0 self/copy route counts, not only the strongest. A shared-code route at 0.45 must not hide
        # a changed file that implements the same control at 0.30.
        direct_here = any(r.hops == 0 and r.direction in ("self", "copy") for r in live)
        ev = sorted({e for r in live for e in r.evidence_ids})
        for q in hit.requirements:
            rid = str(q["id"])
            if rid not in allowed:
                continue
            link = float(q.get("confidence") or 0.0)
            if q.get("via") in ("screen", "flow"):
                bucket = coarse
            else:
                bucket = per_req if direct_here else indirect_req
            bucket.setdefault(rid, []).append((round(best * link, 4), q.get("evidence_id"), ev))
    ids = sorted(set(per_req) | set(indirect_req) | set(coarse))
    info = {r["requirement_id"]: r for r in store.read("requirements_by_id", requirement_ids=ids)} if ids else {}
    removed = {s.path for s in seeds.values() if s.status == "removed"}
    lost = _lost_requirements(store, retr, removed, ids) if removed else set()
    for rid in ids:
        row = info.get(rid)
        if row is None or not row.get("quote"):
            continue
        direct_entries = per_req.get(rid, [])
        entries = [*direct_entries, *indirect_req.get(rid, []), *coarse.get(rid, [])]
        conf = combine(c for c, _, _ in entries)
        band = band_for(conf)
        if band is Band.UNKNOWN:
            continue
        related = _related_to_change(row["text"], row.get("section"), context)
        # A removal stays even when the only route is shared code and the wording does not overlap: that is
        # "the only control is gone", not "this requirement is unrelated".
        if not direct_entries and rid not in lost and not related:
            weak_unrelated += 1
            continue
        evidence = sorted({e for _, link_ev, route_ev in entries for e in [link_ev, *route_ev]
                           if e and run.evidence.exists(e)})[:4]
        status = "loses_only_realization" if rid in lost else "at_risk"
        risks[rid] = RequirementRisk(requirement_id=rid, statement=row["text"], quote=row["quote"],
                                     section=row["section"] or "", status=status, band=band,   # type: ignore[arg-type]
                                     evidence_ids=evidence)
        confs[rid] = conf

    # cannot verify / may close a gap: requirements no control realises whose searched screens sit under the change
    paths = sorted({s.path for s in seeds.values() if s.in_snapshot and not s.is_global_only})
    if paths:
        rows = store.read("absence_near", changed=paths, hops=2, bundle_hash=bundle_hash)
        strings = [set(tokens(text)) for text in [*locale_texts, *diff_texts]]
        context = context | {t for r in risks.values() for t in tokens(r.section)}
        for row in rows:
            rid = row["requirement_id"]
            if rid not in allowed or rid in risks:
                continue
            q = store.read("requirements_by_id", requirement_ids=[rid])
            if not q or not q[0].get("quote"):
                continue
            req_tokens = set(tokens(row["text"]))
            closes = any(len(req_tokens & words) >= 2 for words in strings)
            related = _related_to_change(row["text"], row["section"], context)
            if not closes and not related:         # "add", "user", "request": every page has them
                unrelated += 1
                continue
            ev = run.evidence.add("reason.absence", f"{pr.repo}#{pr.number}:{rid}",
                                  f"requirement has no realising control; its searched screens sit under "
                                  f"{len(row['seeds'])} changed file(s)")
            band = Band.POSSIBLE if closes else Band.UNKNOWN
            risks[rid] = RequirementRisk(
                requirement_id=rid, statement=row["text"], quote=q[0]["quote"], section=row["section"] or "",
                status="may_close_gap" if closes else "cannot_verify", band=band, evidence_ids=[ev])
            confs[rid] = _BAND_FLOOR[band]
    order = {"loses_only_realization": 0, "at_risk": 1, "may_close_gap": 2, "cannot_verify": 3}
    ordered = sorted(risks.values(), key=lambda r: (order[r.status], -_band_rank(r.band), r.section, r.requirement_id))
    return ordered, confs, unrelated, weak_unrelated


def _change_words(retr: Retrieval, direct: list[ReportItem], controls: dict[str, list[tuple[str, str]]],
                  added: list[str]) -> set[str]:
    """Words that describe what this PR changes: directly affected control names, changed file names, new text."""
    words: set[str] = set()
    for item in direct:
        for _, name in controls.get(item.id, []):
            words |= set(tokens(name))
    for s in retr.seeds:
        words |= set(tokens(humanize_path(s.display_path)))
    for text in added:
        words |= set(tokens(text))
    return words


def _band_rank(band: Band) -> int:
    return {Band.CONFIRMED: 3, Band.LIKELY: 2, Band.POSSIBLE: 1, Band.UNKNOWN: 0}[band]


def _lost_requirements(store: Any, retr: Retrieval, removed: set[str], requirement_ids: list[str]) -> set[str]:
    """Requirements all of whose realising controls are implemented only by files this PR removes."""
    lost_elements = {h.element_id for h in retr.hits if h.impl_files and set(h.impl_files) <= removed}
    if not lost_elements or not requirement_ids:
        return set()
    out: set[str] = set()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in store.read("requirement_realizations", requirement_ids=requirement_ids):
        grouped.setdefault(row["requirement_id"], []).append(row)
    for rid, rows in grouped.items():
        targets = [r for r in rows if r["target_id"]]
        if targets and all(r["target_kind"] == "UIElement" and r["target_id"] in lost_elements for r in targets):
            out.add(rid)
    return out


# ---------------------------------------------------------------------------- could not determine
_AGGREGATE_CHECK = {
    "NOT_MAPPED_TO_SCREEN": "Find where each of these appears in the product and try it by hand; none of them is "
                            "tied to a screen we observed.",
    "ADDED_NOT_USED": "Check whether each new file is used anywhere; nothing in this PR renders it.",
    "OVERLAY_UNAVAILABLE": "Look at where each new file is used; the PR's own code could not be read.",
    "PR_PATH_MISSING_AT_HEAD": "Rebase the PR on the current main and run the analysis again, or look at these "
                               "files by hand.",
}
_LOW_RANK = 60


def _closed(text: str) -> str:
    """One sentence, capitalised, with exactly one full stop."""
    return sentence(text.strip().rstrip(".")) + "."


def _plural_subject(items: list[ReviewItem]) -> str:
    names = [i.subject for i in items]
    return join_names(names, 3, more="more")


def aggregate_review(items: list[ReviewItem], top: int) -> list[Undetermined]:
    """One could-not-determine row per reason, ranked (case 61); the full list stays in needs_review.json."""
    groups: dict[str, list[ReviewItem]] = {}
    for it in items:
        groups.setdefault(it.reason_kind, []).append(it)
    rows: list[tuple[int, int, str, Undetermined]] = []
    for kind, members in groups.items():
        members.sort(key=lambda i: (i.rank, i.subject, i.id))
        first = members[0]
        evidence = sorted({e for m in members for e in m.evidence_ids})[:8]
        if len(members) == 1:
            subject, check = first.subject, first.what_to_check
            why = _closed(first.detail)
        else:
            subject = f"{len(members)} items: {_plural_subject(members)}"
            check = _AGGREGATE_CHECK.get(kind, first.what_to_check)
            why = _closed(first.detail)
        rows.append((min(m.rank for m in members), -len(members), kind,
                     Undetermined(code=first.code, subject=subject, why=why, what_to_check=check,
                                  evidence_ids=evidence)))
    rows.sort(key=lambda r: r[:3])
    return [r[3] for r in rows[:top]]


def pr_level_reviews(pr: PRData, retr: Retrieval, run_codes: dict[str, int]) -> list[ReviewItem]:
    """Things about the PR or the run (not about one seed) that a human should know."""
    out: list[ReviewItem] = []
    notes = {n.code: n.message for n in pr.notices}
    if "STALE_BASE" in notes:
        overlap = pr.compare.overlap if pr.compare else []
        out.append(ReviewItem(
            id=stable_id("rev", "pr", "STALE_BASE"), code="STALE_BASE", reason_kind="STALE_BASE",
            subject="the whole PR", detail=notes["STALE_BASE"],
            what_to_check="Rebase the PR on main and run the report again before relying on the file lists; "
                          f"{len(overlap)} file(s) were edited on both sides.", rank=5,
            evidence_ids=[e for e in [pr.evidence_id] if e]))
    if "PR_HEAD_MOVED" in notes:
        out.append(ReviewItem(
            id=stable_id("rev", "pr", "PR_HEAD_MOVED"), code="NEEDS_HUMAN", reason_kind="PR_HEAD_MOVED",
            subject="the whole PR", detail=notes["PR_HEAD_MOVED"],
            what_to_check="Run the report again for the new commit.", rank=1))
    if retr.outside_index:
        n = len(retr.outside_index)
        out.append(ReviewItem(
            id=stable_id("rev", "pr", "OUTSIDE_INDEX"), code="NEEDS_HUMAN", reason_kind="OUTSIDE_INDEX",
            subject=f"{n} changed file(s) outside the frontend we index",
            detail="they belong to backend, scripts or other packages, which this analysis does not cover",
            what_to_check="Ask the owners of those packages what user-visible behaviour they change.",
            rank=45, seeds=retr.outside_index[:20]))
    if retr.not_assessed:
        out.append(ReviewItem(
            id=stable_id("rev", "pr", "NOT_ASSESSED"), code="NEEDS_HUMAN", reason_kind="NOT_ASSESSED",
            subject=f"{retr.not_assessed} changed file(s) beyond the size limit",
            detail="the PR changes more files than are analysed one by one",
            what_to_check="Split the PR or review the remaining files by hand.", rank=15))
    if pr.files_truncated:
        out.append(ReviewItem(
            id=stable_id("rev", "pr", "FILES_TRUNCATED"), code="NEEDS_HUMAN", reason_kind="FILES_TRUNCATED",
            subject="the list of changed files", detail=notes.get(
                "NEEDS_HUMAN", "GitHub did not list every changed file"),
            what_to_check="Review the files GitHub does not list by hand.", rank=15))
    blocked = sum(run_codes.get(c, 0) for c in ("ACCESS_BLOCKED", "ACCESS_SUSPECT", "ACCESS_DISALLOWED"))
    if blocked:
        out.append(ReviewItem(
            id=stable_id("rev", "pr", "ACCESS"), code="ACCESS_BLOCKED", reason_kind="ACCESS_RESTRICTED",
            subject=f"{blocked} page(s) the crawler could not open",
            detail="the site's protection or its robots rules kept the crawler out, so screens behind them are "
                   "not in this report",
            what_to_check="Re-test those pages by hand; ask the site owner to allow the crawler if you want them "
                          "covered.", rank=12))
    missing_patch = notes.get("PATCH_MISSING")
    if missing_patch:
        out.append(ReviewItem(
            id=stable_id("rev", "pr", "PATCH_MISSING"), code="NEEDS_HUMAN", reason_kind="PATCH_MISSING",
            subject="files without a readable diff", detail=missing_patch,
            what_to_check="Open those files in the PR and look at what changed.", rank=42))
    if "COMPARE_UNAVAILABLE" in notes:
        out.append(ReviewItem(
            id=stable_id("rev", "pr", "COMPARE_UNAVAILABLE"), code="NEEDS_HUMAN", reason_kind="COMPARE_UNAVAILABLE",
            subject="how far the PR is behind main", detail=notes["COMPARE_UNAVAILABLE"],
            what_to_check="Check by hand whether the PR is based on a recent main.", rank=44))
    return out


def not_checked_lines(pr: PRData, retr: Retrieval, run_codes: dict[str, int], overflow: int,
                      more_undetermined: int, *, stored_data: bool = False, unrelated_absences: int = 0,
                      screen_level_requirements: int = 0) -> list[str]:
    out = list(BASE_NOT_CHECKED)
    if stored_data:
        out.append("Other programs that read this saved data (a command-line tool, the backend, the desktop app) "
                   "were not assessed.")
    packages = sorted({p.split("/")[1] for p in retr.outside_index if p.startswith("packages/")
                       and p.count("/") > 1})
    if packages:
        out.append(f"Packages with changes outside the analysed frontend: {', '.join(packages[:5])}.")
    aside = retr.set_aside
    parts = [f"{n} {k.replace('_', ' ')} file(s)" for k, n in aside.items() if n]
    if parts:
        out.append("Set aside as not user-facing: " + ", ".join(parts) + ".")
    if retr.other_locale_files:
        out.append(f"{len(retr.other_locale_files)} translation file(s) in other languages were not compared.")
    if overflow:
        out.append(f"{overflow} further screen(s) that share code with the change are not listed: only the most "
                   "likely are shown (the full list is in the appendix data).")
    if more_undetermined:
        out.append(f"{more_undetermined} more lower-priority item(s) could not be determined (see the appendix "
                   "data).")
    if unrelated_absences:
        out.append(f"{unrelated_absences} requirement(s) with no matching screen sit on pages this PR touches but "
                   "do not mention anything it changes; they are not listed.")
    if screen_level_requirements:
        out.append(f"{screen_level_requirements} requirement(s) reach this PR only through a whole screen, a flow, "
                   "or code it shares, and do not mention anything it changes; they are not listed.")
    if run_codes.get("BUDGET_EXHAUSTED"):
        out.append("The crawl stopped at its action budget, so some screens were not observed.")
    if run_codes.get("SPEC_STALE"):
        out.append("The product documentation may be older than the app, so some requirements may be out of date.")
    if run_codes.get("CODE_PARSE_PARTIAL"):
        out.append("Some source files could not be read completely.")
    return out


def run_failure_counts(run: RunContext) -> dict[str, int]:
    """Codes recorded by earlier stages of this run (failures.jsonl) plus this session's."""
    counts: Counter[str] = Counter(str(f["code"]) for f in run.failures.items)
    path = run.run_dir / "failures.jsonl"
    seen = len(run.failures.items)
    if path.exists():
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        for ln in lines[: max(0, len(lines) - seen)]:       # this session's rows were appended last
            try:
                counts[str(json.loads(ln)["code"])] += 1
            except (ValueError, KeyError):
                continue
    return dict(counts)


# ----------------------------------------------------------------------------------------- stage
def added_labels(retr: Retrieval, i18n: dict[str, str]) -> dict[str, str]:
    """Product words for an ADDED file: its `title` string, or its one string when it has exactly one of two or
    more words; otherwise a name made from the file name (a guess from several unrelated strings would mislead)."""
    out: dict[str, str] = {}
    for s in retr.seeds:
        keys = [k for k in sorted(s.keys) if i18n.get(k)]
        titles = [k for k in keys if k.rsplit(".", 1)[-1] == "title"]
        sole = keys if len(keys) == 1 and len(i18n[keys[0]].split()) >= 2 else []
        pick = (titles or sole or [None])[0]
        out[s.path] = i18n[pick] if pick else humanize_path(s.display_path)
    return out


_QUOTED = re.compile(r"\"([^\"\n]{4,100})\"|'([^'\n]{4,100})'|`([^`\n$]{4,100})`")
_TEXT_NODE = re.compile(r">\s*([A-Za-z][^<>{}\n]{3,100}?)\s*<")


def _patch_texts(pr: PRData) -> list[str]:
    """Words this PR newly puts in front of a user: quoted strings and template text nodes on added lines of
    source files (never tests or generated files, never bare code). Requirements are matched string by string."""
    out: list[str] = []
    for f in pr.files:
        if f.set_aside or not f.patch:
            continue
        for line in f.patch.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            body = line[1:]
            for m in _QUOTED.finditer(body):
                out.append(next(g for g in m.groups() if g))
            out.extend(m.group(1) for m in _TEXT_NODE.finditer(body))
            if len(out) >= 4000:
                return out
    return out


def assemble(run: RunContext, cfg: TargetConfig, store: Any, pr: PRData, retr: Retrieval, grading: Grading,
             th: Thresholds, *, units: dict[tuple[str, str], Unit], owner: dict[str, tuple[str, str]],
             i18n: dict[str, str], indexed_commit: str, extra_review: list[ReviewItem],
             locale_added: list[str], summary: dict[str, Any]) -> Assembly:
    """Items, requirements, could-not-determine and the Report skeleton (headline and checklist are filled in
    by `narrate`; the risk level is derived here by the rule in `risk`)."""
    labels = added_labels(retr, i18n)
    b = _Builder(run, cfg, pr, retr, th, units, owner, labels)
    direct, indirect, claims = b.unit_items()
    g_items, g_claims = b.global_items()
    s_items, s_claims = b.stored_items()
    claims += g_claims + s_claims

    seeds = b.seeds
    run_codes = run_failure_counts(run)
    bundle = summary.get("bundle_hash")
    context = _change_words(retr, direct, b.controls, locale_added)
    reqs, req_conf, unrelated, screen_level = build_requirements(run, store, pr, retr, seeds, bundle, locale_added,
                                                                 _patch_texts(pr), context)
    known = {r.requirement_id: r for r in reqs}
    for it in (*direct, *indirect):
        it.requirement_ids = sorted(r for r in it.requirement_ids if r in known)
        if it.tier == "indirect" and it.requirement_ids:
            top = Counter(known[r].section for r in it.requirement_ids if known[r].section).most_common(1)
            it.section = top[0][0] if top else None

    direct.sort(key=lambda i: (i.via != REMOVED_TEXT_VIA, -i.confidence, -len(i.requirement_ids), i.label, i.id))
    indirect.sort(key=lambda i: (-i.confidence, -len(i.requirement_ids), -len(i.flows), i.label, i.id))
    cap = th.retrieval.tier2_cap
    shown, overflow = indirect[:cap], indirect[cap:]

    # weak evidence is a could-not-determine row, not a finding
    weak = _weak_links(retr, seeds, units, owner)
    review = [*extra_review, *pr_level_reviews(pr, retr, run_codes)]
    if b.foreign:
        review.append(ReviewItem(
            id=stable_id("rev", "pr", "EVIDENCE_FROM_OTHER_RUN"), code="NEEDS_HUMAN",
            reason_kind="EVIDENCE_FROM_OTHER_RUN", subject=f"the proof behind {len(b.foreign)} link(s)",
            detail="those links were written by another run, so their evidence is not in this run's ledger and "
                   "could not be cited or checked here",
            what_to_check="Run the report in the run that built the links (use the same run for index, link and "
                          "pr), or run link again in this run.", rank=3))
    for g in grading.seeds:
        review.extend(g.reviews)
    if weak:
        review.append(weak)
    fresh = sorted({labels[s.path] for s in retr.seeds if s.added and labels.get(s.path)})
    if fresh:
        fresh_words = join_names([quoted(n) for n in fresh], 6)
        review.append(ReviewItem(
            id=stable_id("rev", "pr", "NEW_IN_PR"), code="UNMAPPED_CHANGE", reason_kind="NEW_IN_PR",
            subject="New in this PR; not observable in the live app yet",
            detail=f"This PR adds {fresh_words}. The live app does not show that yet",
            what_to_check=f"After this PR is deployed, open the screen that should contain {fresh_words}.",
            rank=4))
    review = sorted({r.id: r for r in review}.values(), key=lambda r: (r.rank, r.reason_kind, r.id))
    top = th.retrieval.undetermined_top
    undetermined = aggregate_review(review, top)
    more = max(0, len({r.reason_kind for r in review}) - len(undetermined))

    for r in reqs:
        claims.append(_requirement_claim(b, r, req_conf[r.requirement_id]))

    report = Report(
        target=cfg.name, pr_number=pr.number, pr_title=pr.title, risk_level="UNKNOWN", headline="",
        confidence_line="", direct=direct, indirect=shown, global_changes=g_items, stored_data=s_items,
        requirements=reqs, undetermined=undetermined,
        not_checked=not_checked_lines(pr, retr, run_codes, len(overflow), more, stored_data=bool(s_items),
                                      unrelated_absences=unrelated, screen_level_requirements=screen_level),
        snapshot=_snapshot(run, cfg, pr, indexed_commit, summary), claims=sorted(claims, key=lambda c: c.id))
    report.risk_level = derive_risk(report)
    counts = _counts(report, retr, overflow, direct, shown)
    impact = {"units": {f"{k[0]}|{k[1]}": {"label": u.label, "screens": sorted(u.screen_ids)}
                        for k, u in sorted(units.items())}}
    removed_text = sorted(b.removed_text)
    return Assembly(report=report, basis=b.basis, review=review, expected_direct=[i.id for i in direct],
                    overflow=overflow, impact=impact, counts=counts, controls=b.controls,
                    removed_text=removed_text)


def _requirement_claim(b: _Builder, r: RequirementRisk, conf: float) -> Claim:
    quoted_statement = quoted(r.statement)
    text = {
        "at_risk": f"This requirement rests on a screen that {_verb(r.band)}.",
        "loses_only_realization": f"The only control that shows this requirement {_verb(r.band)} (it is removed).",
        "may_close_gap": "This PR adds wording that might close this gap; worth a quick look.",
        "cannot_verify": "We cannot tell whether the screens that should show this requirement still do; not "
                         "determined.",
    }[r.status]
    own = b.run.evidence.add("reason.requirement", f"{b.pr.repo}#{b.pr.number}:{r.requirement_id}:{r.status}",
                             f"requirement {r.requirement_id} is {r.status} at {r.band.value}")
    claim = Claim(id=stable_id("clm", b.pr.number, r.requirement_id, quoted_statement, text),
                  text=f"{quoted_statement}: {text}",
                  evidence_ids=sorted({own, *r.evidence_ids}), band=r.band)
    b.basis[claim.id] = [{"evidence_id": e, "confidence": conf} for e in sorted({own, *r.evidence_ids})]
    return claim


def _weak_links(retr: Retrieval, seeds: dict[str, SeedInfo], units: dict[tuple[str, str], Unit],
                owner: dict[str, tuple[str, str]]) -> ReviewItem | None:
    """Units whose only evidence is below "possible": not findings, one could-not-determine row."""
    weak: set[tuple[str, str]] = set()
    strong: set[tuple[str, str]] = set()
    for h in retr.hits:
        key = owner.get(h.screen_id)
        if key is None:
            continue
        for r in h.routes:
            if not r.admitted or (seeds.get(r.seed) and seeds[r.seed].is_global_only):
                continue
            (weak if band_for(r.confidence) is Band.UNKNOWN else strong).add(key)
    only_weak = sorted(weak - strong)
    if not only_weak:
        return None
    names = [units[k].label for k in only_weak if k in units]
    return ReviewItem(
        id=stable_id("rev", "pr", "WEAK_LINKS"), code="NEEDS_HUMAN", reason_kind="WEAK_LINKS",
        subject=f"{len(names)} screen(s) linked only by very weak evidence: {join_names([quoted(n) for n in names], 3)}",
        detail="the connection from the change to these screens is too weak to call an effect",
        what_to_check="Look at these screens if time allows; nothing says they are affected.", rank=_LOW_RANK)


def _snapshot(run: RunContext, cfg: TargetConfig, pr: PRData, indexed_commit: str,
              summary: dict[str, Any]) -> SnapshotInfo:
    return SnapshotInfo(
        target=cfg.name, repo=cfg.code.repo, indexed_commit=indexed_commit, pr_base_sha=pr.base_sha,
        pr_head_sha=pr.head_sha, base_behind_main_commits=pr.compare.behind_by if pr.compare else None,
        deployed_vs_indexed=summary.get("deployed_vs_indexed"), crawl_bundle_hash=summary.get("bundle_hash"),
        crawl_actions=summary.get("crawl_actions"), crawl_screens=summary.get("crawl_screens"),
        spec_sha=summary.get("spec_sha"), doc_version=summary.get("doc_version"),
        app_version=summary.get("app_version"))


def _counts(report: Report, retr: Retrieval, overflow: list[ReportItem], direct: list[ReportItem],
            shown: list[ReportItem]) -> dict[str, int]:
    """Distinct screens (units), flows and requirements over every item before the tier-2 cap (case 51)."""
    items = [*direct, *shown, *overflow]
    return {
        "screens": len({i.screen for i in items if i.screen}),
        "flows": len({f for i in items for f in i.flows}),
        "requirements": len({r.requirement_id for r in report.requirements}),
        "direct": len(direct), "indirect": len(shown) + len(overflow), "indirect_shown": len(shown),
        "global": len(report.global_changes), "stored_data": len(report.stored_data),
        "screen_nodes": len({h.screen_id for h in retr.hits if h.live_routes()}),
    }
