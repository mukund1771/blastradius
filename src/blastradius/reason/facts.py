"""The shapes the reasoning stages pass to each other (D07, D09, D25; cases 50, 51).

Nothing here is HTML, Markdown or a sentence a QA lead reads: that is `report.model.Report`, built last by
`assemble`. These are the retrieval facts the grader, the judge and the assembler share, and they are what
`impact.json` and `grade.json` are made of, so every field is plain, sorted data.

A **Route** is one way a (screen, element) was reached from one changed file: the witness path, the
IMPLEMENTED_BY edge it ends on, and a path confidence = edge confidence x HOP_DECAY per hop. A **Hit** is one
distinct (screen, element) with all its routes: counts are over Hits (case 51), confidence is combined from the
routes by `combine` (noisy-OR, never above the band of the strongest route, so the "lowest cited edge decides
the verb" rule of REPORT_SPEC stays true). Nothing in this module raises a confidence: caps only lower.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import Field

from ..models import HOP_DECAY, Band, FileClass, Strict, band_for
from .change import ChangeKind

Direction = Literal["self", "reverse", "forward", "overlay", "copy", "locale"]
SeedCategory = Literal["normal", "global_layout", "global_style", "hub", "stored_data", "locale"]

# the highest value that is still inside a band: noisy-OR may lift a score within its band, never across it
BAND_CEILING: dict[Band, float] = {Band.CONFIRMED: 0.99, Band.LIKELY: 0.799, Band.POSSIBLE: 0.499, Band.UNKNOWN: 0.249}


def noisy_or(values: Iterable[float]) -> float:
    miss = 1.0
    for v in values:
        miss *= 1.0 - max(0.0, min(1.0, v))
    return 1.0 - miss


def combine(values: Iterable[float]) -> float:
    """Confidence of alternative routes to one thing: noisy-OR, clamped to the band of the strongest route (and
    below 1.0: a static analysis is never certain)."""
    vals = [max(0.0, min(1.0, v)) for v in values]
    if not vals:
        return 0.0
    return round(min(noisy_or(vals), BAND_CEILING[band_for(max(vals))]), 4)


def path_confidence(impl_confidence: float | None, hops: int) -> float:
    """Product of the edge confidences along a route (code edges are structural, 1.0) x HOP_DECAY per hop."""
    return round(max(0.0, min(1.0, impl_confidence or 0.0)) * HOP_DECAY**hops, 4)


class Route(Strict):
    seed: str                                    # the changed path (or locale file) the route starts from
    direction: Direction
    hops: int = 0
    file: str = ""                               # the file implementing the element
    via: list[str] = Field(default_factory=list)             # implementing file first, seed last
    kinds: list[str] = Field(default_factory=list)
    symbols: list[list[str]] = Field(default_factory=list)   # named imports along `via`, edge by edge
    specifiers: list[str | None] = Field(default_factory=list)
    impl_confidence: float = 0.0
    impl_method: str | None = None
    impl_evidence_id: str | None = None
    confidence: float = 0.0                      # path confidence after every cap
    uncapped: float = 0.0                        # evidence strength: before the claim caps of grading
    caps: list[str] = Field(default_factory=list)            # "<reason>=<value>", sorted
    admitted: bool = True                        # False: reserve evidence, only EXPAND admits it
    note: str = ""                               # copy changes: what the text did
    old_text: str = ""                           # copy changes: the English text before the PR
    high_severity: bool = False                  # a removed string that is still shown
    evidence_ids: list[str] = Field(default_factory=list)

    def cap(self, reason: str, value: float) -> None:
        """Lower the confidence to `value` when it is higher; never raises it."""
        if self.confidence > value:
            self.confidence = round(value, 4)
        tag = f"{reason}={value:g}"
        if tag not in self.caps:
            self.caps = sorted([*self.caps, tag])

    def restrict(self, reason: str, value: float) -> None:
        """A cap that says the *evidence itself* is weak (a held-back forward hop, a shared string, a probe):
        it lowers `uncapped` too, so the grader does not count the route as strong evidence."""
        self.cap(reason, value)
        if self.uncapped > value:
            self.uncapped = round(value, 4)

    @property
    def band(self) -> Band:
        return band_for(self.confidence)


class Hit(Strict):
    screen_id: str
    element_id: str
    element_role: str = ""
    element_name: str = ""
    owner_file: str | None = None
    hidden_on_screen: bool = False
    impl_files: list[str] = Field(default_factory=list)       # every file the element is IMPLEMENTED_BY here
    routes: list[Route] = Field(default_factory=list)
    flows: list[dict[str, str]] = Field(default_factory=list)
    requirements: list[dict[str, object]] = Field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.screen_id, self.element_id)

    def live_routes(self) -> list[Route]:
        return [r for r in self.routes if r.admitted and r.confidence > 0.0]


class SeedInfo(Strict):
    path: str                                    # the path the baseline knows (a rename's old name)
    display_path: str                            # the PR's path
    file_class: FileClass
    status: str
    kind: ChangeKind
    categories: list[SeedCategory] = Field(default_factory=lambda: ["normal"])
    in_snapshot: bool = False
    added: bool = False
    touched: list[str] = Field(default_factory=list)
    added_symbols: list[str] = Field(default_factory=list)
    removed_symbols: list[str] = Field(default_factory=list)
    symbols_known: bool = False
    importers_h1: int = 0
    ui_importers_h1: int = 0
    importers_h2: int = 0
    ui_importers_h2: int = 0
    fraction_h1: float = 0.0
    fraction_h2: float = 0.0
    ui_total: int = 0
    overlay_importers: list[str] = Field(default_factory=list)   # who renders/imports an ADDED file at the head
    overlay_attached: bool = False
    stale_overlap: bool = False
    patch_missing: bool = False
    keys: list[str] = Field(default_factory=list)                # static i18n keys the file uses
    evidence_id: str | None = None

    @property
    def is_ui_dir(self) -> bool:
        return self.file_class in (FileClass.PAGE, FileClass.LAYOUT, FileClass.COMPONENT)

    @property
    def is_global_only(self) -> bool:
        return "global_layout" in self.categories or "global_style" in self.categories


class LocaleResult(Strict):
    path: str
    added: int = 0
    changed: int = 0
    removed: int = 0
    eol_only: bool = False
    problem: str | None = None
    elements_matched: int = 0
    exact_matches: int = 0
    high_severity_keys: list[str] = Field(default_factory=list)
    changed_keys: list[str] = Field(default_factory=list)
    evidence_id: str | None = None


class Retrieval(Strict):
    seeds: list[SeedInfo] = Field(default_factory=list)
    hits: list[Hit] = Field(default_factory=list)
    locales: list[LocaleResult] = Field(default_factory=list)
    set_aside: dict[str, int] = Field(default_factory=dict)       # file class -> count of changed files
    outside_index: list[str] = Field(default_factory=list)        # source files no indexed root contains
    other_locale_files: list[str] = Field(default_factory=list)
    not_assessed: int = 0                                         # seeds beyond max_seeds
    overlay: dict[str, object] = Field(default_factory=dict)      # status, added/modified/deleted, reason
    ui_total: int = 0

    def seed(self, path: str) -> SeedInfo | None:
        return next((s for s in self.seeds if s.path == path or s.display_path == path), None)
