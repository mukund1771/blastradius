"""Shared contracts. Every stage reads and writes these shapes; nothing else is exchanged between stages.

Artifacts on disk (relative to the run dir):
  crawl/captures.jsonl      Capture            one per fetch/state, with its AccessResult
  crawl/screens.jsonl       ScreenState        one per distinct UI state (elements embedded)
  crawl/transitions.jsonl   Transition
  crawl/summary.json        CrawlSummary
  spec/requirements.json    list[Requirement]
  code/index.json           CodeIndex
  graph/load.json           GraphLoadSummary
  links/links.jsonl         Link
  links/gaps.jsonl          Gap
  links/flows.jsonl         Flow
  reason/<pr>/...           see the reason package

Rules: ids are content hashes (ids.stable_id); confidences are floats in [0,1]; anything that came from a
web page, spec, or PR is stored as data and wrapped by safety.untrusted before it reaches an LLM.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------------- access
class AccessVerdict(StrEnum):
    OK = "OK"
    SUSPECT = "SUSPECT"          # soft signals: needs review, never enters the graph
    BLOCKED = "BLOCKED"          # hard block/challenge: creates an ACCESS_RESTRICTED gap
    DISALLOWED = "DISALLOWED"    # robots.txt disallows or is unreadable: skipped by policy


class AccessResult(Strict):
    verdict: AccessVerdict
    vendor: str | None = None
    signals: list[str] = Field(default_factory=list)
    rung: Literal["L0", "L1", "RECORDED"] = "L0"   # L0 live unauthenticated; L1 owner-granted; RECORDED offline
    reason: str = ""


class Capture(Strict):
    id: str
    target: str
    url: str
    final_url: str
    status: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)   # allowlisted names only (see access.capture)
    title: str | None = None
    html_path: str | None = None
    screenshot_path: str | None = None
    captured_at: str
    source_kind: Literal["live", "recorded"] = "live"
    parent_id: str | None = None
    action: str | None = None
    interactive_count: int | None = None
    access: AccessResult


# ----------------------------------------------------------------------------------------------- UI
class UIElementObs(Strict):
    id: str                                  # stable_id("el", target, owner_hint|url_pattern, role, name, ordinal)
    role: str
    name: str                                # accessible name with kbd glyphs stripped
    text: str = ""
    tag: str = ""
    testid: str | None = None
    dom_id: str | None = None
    href: str | None = None
    placeholder: str | None = None
    title_attr: str | None = None
    aria_label: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    selector: str = ""
    visible: bool = True
    ordinal: int = 0                         # nth element with the same (role, name) on the screen
    owner_hint: str | None = None            # owning component/scope id when the page exposes one (drift detector, canonical key)


class ScreenState(Strict):
    id: str                                  # stable_id("scr", target, fingerprint)
    target: str
    url: str
    url_pattern: str
    title: str = ""
    kind: Literal["page", "modal", "tab", "embed"] = "page"
    fingerprint: str
    dialog_title: str | None = None
    active_tabs: list[str] = Field(default_factory=list)
    elements: list[UIElementObs] = Field(default_factory=list)
    screenshot: str | None = None
    dom_path: str | None = None
    capture_id: str
    first_seen_action: str | None = None
    depth: int = 0


class Transition(Strict):
    id: str
    from_screen: str
    to_screen: str
    element_id: str | None = None
    action: Literal["click", "type", "press", "goto", "hover", "select"] = "click"
    label: str = ""
    observed: bool = True
    blocked: bool = False                    # attempted but refused (policy/WAF)
    reason: str = ""


class CrawlSummary(Strict):
    target: str
    started_at: str
    finished_at: str
    screens: int
    elements: int
    transitions: int
    actions_spent: int
    actions_budget: int
    wall_clock_s: float
    blocked: int = 0
    suspect: int = 0
    disallowed: int = 0
    egress_blocked: int = 0                  # requests aborted by the allowlist (hosts are in crawl/stats.json)
    policy_blocked: int = 0                  # actions refused by the safety classes
    soft_404: int = 0                        # links that rendered the catch-all
    restores: int = 0                        # state re-establishments (budget burn)
    stop_reason: Literal["frontier_empty", "action_budget", "time_budget", "circuit_breaker", "error"] = "frontier_empty"
    app_version: str | None = None
    bundle_hash: str | None = None           # hash of the main JS bundle name(s) at start; drift detector
    bundle_hash_end: str | None = None
    url_patterns: list[str] = Field(default_factory=list)
    effort_by_url_pattern: dict[str, int] = Field(default_factory=dict)   # actions spent per url_pattern


# --------------------------------------------------------------------------------------------- spec
class Requirement(Strict):
    id: str                                  # stable_id("req", target, normalised quote)
    target: str
    text: str                                # atomic, normalised: "User can ..."
    quote: str                               # verbatim substring of the normalised source
    source_url: str
    section: str                             # spec page title or heading path (free vocabulary for QA leads)
    doc_version: str | None = None
    edition: Literal["cloud-web", "desktop", "self-host", "all", "unknown"] = "unknown"
    polarity: Literal["must_exist", "must_not_exist"] = "must_exist"
    ui_observable: Literal["yes", "no", "unknown"] = "unknown"
    requires_login: bool = False
    source_kind: Literal["docs", "gherkin"] = "docs"
    tags: list[str] = Field(default_factory=list)
    spec_sha: str


# --------------------------------------------------------------------------------------------- code
class FileClass(StrEnum):
    PAGE = "page"
    LAYOUT = "layout"
    COMPONENT = "component"
    SERVICE = "service"
    UTIL = "util"
    LOCALE = "locale"
    DATA_SCHEMA = "data_schema"      # persisted-data shape: PERSISTED_DATA impact
    STYLE_GLOBAL = "style_global"
    TEST = "test"
    GENERATED = "generated"
    LOCKFILE = "lockfile"
    DOCS = "docs"
    CI = "ci"
    BUILD_CONFIG = "build_config"
    EXTERNAL = "external"            # external package (e.g. @hoppscotch/ui): a leaf with no source here
    SOURCE_OTHER = "source_other"


class CodeFile(Strict):
    path: str                        # repo-relative, posix
    kind: FileClass
    loc: int = 0
    sha: str | None = None
    package: str | None = None       # workspace package name when known


class CodeEdge(Strict):
    src: str                         # importer / renderer
    dst: str                         # imported / rendered
    kind: Literal["IMPORTS", "RENDERS"]
    symbols: list[str] = Field(default_factory=list)   # named imports (symbol-level narrowing)
    via: str | None = None           # tag name or import specifier that produced the edge


class RouteDef(Strict):
    pattern: str                     # "/", "/graphql", "/realtime/websocket", "/:catchAll"
    file: str
    layout: str | None = None


class KeyUse(Strict):
    file: str
    key: str                         # i18n key, or a prefix when dynamic
    dynamic: bool = False


class TextLiteral(Strict):
    file: str
    text: str                        # normalised literal visible text (non-i18n targets)


class CodeIndex(Strict):
    target: str
    repo: str
    ref: str                         # branch/tag/sha asked for
    snapshot_id: str                 # resolved commit sha
    adapter: str
    files: list[CodeFile] = Field(default_factory=list)
    edges: list[CodeEdge] = Field(default_factory=list)
    routes: list[RouteDef] = Field(default_factory=list)
    i18n: dict[str, str] = Field(default_factory=dict)          # key -> English value
    key_uses: list[KeyUse] = Field(default_factory=list)
    literals: list[TextLiteral] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)         # resolved_tag_rate, unresolved, counts


# ---------------------------------------------------------------------------------------------- link
class Link(Strict):
    kind: Literal["REALIZED_BY", "IMPLEMENTED_BY"]
    src: str                          # Requirement.id | UIElement.id
    dst: str                          # UIElement/Screen/Flow id | CodeFile.path
    confidence: float
    method: str                       # route | i18n_key | literal_text | testid | llm_pick | bm25_judge | human
    evidence_id: str
    judge: str | None = None
    screen_ids: list[str] = Field(default_factory=list)   # observations this link was derived on ([] = all)


class GapKind(StrEnum):
    NO_UI_EVIDENCE = "NO_UI_EVIDENCE"
    NOT_UI_TESTABLE = "NOT_UI_TESTABLE"
    ACCESS_RESTRICTED = "ACCESS_RESTRICTED"
    UNSPECIFIED_UI = "UNSPECIFIED_UI"
    UNMAPPED_CODE = "UNMAPPED_CODE"
    UNMAPPED_CHANGE = "UNMAPPED_CHANGE"      # a PR file in a UI directory that maps to nothing


class Gap(Strict):
    id: str
    kind: GapKind
    status: str                       # NOT_FOUND_WITHIN_BUDGET | CONFIRMED_ABSENT | CLASSIFIED | SKIPPED_BY_POLICY | OBSERVED
    subject_id: str                   # Requirement.id | UIElement.id | Screen.id | CodeFile.path
    reason: str = ""                  # SPEC_MAY_BE_STALE | CONFIG_DEPENDENT | external-package | dynamic-render | ...
    confidence: float = 0.0
    screens_searched: list[str] = Field(default_factory=list)
    actions_spent: int = 0
    bundle_hash: str | None = None
    evidence_id: str | None = None


class Flow(Strict):
    id: str
    name: str
    steps: list[str]                  # UIElement ids in order
    screens: list[str]                # Screen ids visited, in order
    goal: str = ""


# --------------------------------------------------------------------------------------- confidence
class Band(StrEnum):
    CONFIRMED = "confirmed"           # >= 0.8
    LIKELY = "likely"                 # 0.5 - 0.8
    POSSIBLE = "possible"             # 0.25 - 0.5
    UNKNOWN = "unknown"               # < 0.25


def band_for(confidence: float) -> Band:
    if confidence >= 0.8:
        return Band.CONFIRMED
    if confidence >= 0.5:
        return Band.LIKELY
    if confidence >= 0.25:
        return Band.POSSIBLE
    return Band.UNKNOWN


# Method priors (D09). Final confidence = prior x ambiguity penalty after the reachability filter.
METHOD_PRIOR: dict[str, float] = {
    "route": 0.95,
    "i18n_key": 0.90,
    "testid": 0.90,
    "literal_text": 0.70,
    "llm_pick": 0.60,        # hard cap for any LLM-chosen UI->code link
    "bm25_judge": 0.80,      # cap for requirement->UI links (further scaled by judge agreement)
    "human": 1.00,
}
HOP_DECAY = 0.8
MAX_HOPS = 2
