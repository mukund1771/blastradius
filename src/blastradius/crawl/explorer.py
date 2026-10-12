"""The explorer: a bounded, polite, safety-classed breadth-first crawl of one web app with Playwright.

Implements D04 (own explorer; perception, fingerprints, frontier, safety and budgets are code, the LLM only
re-ranks), D28 (egress allowlist at the route layer and at the network layer, widgets never touched, untrusted
text wrapped) and D13/D32 (every navigation and every new state goes through `classify_capture`; only OK
enters the graph).
Cases: 1 egress (route layer, plus a refusing proxy so redirect hops and beacons cannot slip past), 2 widgets,
3 soft-404 judged by rendered identity, 4/6 pinned context and bundle hash at both ends, 5 shortcut glyphs,
7 icon-only controls (named by tooltip, else not clicked), 9 editors once, 10 volatile text, 12 modal stacks
and unrecoverable base state, 13 robots/UA/delay/concurrency 1/stop on rate limiting, 17/67 status and headers
of click-initiated navigations.

Scheduling. Frontier = (screen, element). Each turn picks the screen with the fewest spent "beam" laps, then
the shallowest, then the first discovered, and spends up to `beam` actions on its best candidates before
moving on. That is breadth-first by depth with a per-node fan-out cap, so a 45-action budget reaches the
modals and tabs of every seed instead of the 45 best elements of the first page. A layout control that
already led somewhere is not clicked again from another screen, and neither is a link whose outcome is known
(followed once, a soft-404, or refused): the other screens get an inferred edge (observed=False) instead.
The catch-all screen gets at most CATCH_ALL_ACTIONS actions however many dead links lead to it.

Recovery. Persisted UI state (sidebar and request tabs live in localStorage on Hoppscotch) survives reloads,
so a replayed path restores the state each step needs before taking it, not only the steps. The ladder is
Escape / dismiss selector / tab re-click, then (right after an action) one step of history, then a paced
replay. A replay never re-sends: a step that was a Send is not repeated, the screen behind it is given up
instead. A `beforeunload` prompt is accepted (dismissing it blocks every way out of a page with unsaved
changes); other dialogs are dismissed. A control that is no longer there is a SELECTOR_DRIFT, not an action,
and three in a row give the screen up.

Safety beyond the label. Send is re-gated on the request URL *at click time*; a dialog whose prose is
destructive makes every control in it destructive except Cancel/No/Close; a control with no name and no
tooltip is not clicked unless its icon signature is on the documented safe list; a route that lands on a deny
pattern is never graphed (it is a blocked transition and the page is left again).

Offline replay (case 18) runs the recorded markup with JavaScript disabled, inline handlers stripped, the
same WebSocket handler and a refusing proxy: a recording cannot make the crawler host open a socket.

Output beyond the models.py artifacts, all under crawl/ (or crawl/<artifact_subdir>/ for a bounded re-crawl,
in which case the main crawl's files are not touched):
  stats.json        policy, beam, llm calls, egress blocked per host (route layer and network layer),
                    redirect hops refused, policy-blocked classes, soft-404 mode and urls, counters
  goal_effort.json  per requirement text: screens, actions, candidate names (D08)
and `crawl_env` (`crawl_env_<subdir>`) in the run manifest: viewport, locale, timezone, colour scheme, UA,
chromium, bundle hash, app version.
"""

from __future__ import annotations

import dataclasses
import html as html_lib
import json
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from ..access.capture import append_capture, make_capture
from ..access.classify import Breaker, classify_capture, robots_allows
from ..failures import FailureCode
from ..ids import sha, stable_id
from ..models import AccessResult, AccessVerdict, Capture, CrawlSummary, ScreenState, Transition
from ..redact import redact_text
from ..run import RunContext
from ..targets import TargetConfig
from ..trace import now_iso
from . import perceive
from .fingerprint import (
    CATCH_ALL_PATTERN,
    MERGE_JACCARD,
    element_key,
    fingerprint,
    group_key,
    jaccard,
    key_set,
    layout_key,
    screen_kind,
    url_pattern,
)
from .netguard import Blackhole, Fetched, Watchdog, bypass_list, fetch_checked, hop_allowed
from .perceive import Element, Perception
from .policy import Candidate, GoalEffort, HeuristicPolicy, LLMPolicy, Policy, PolicyContext
from .safety import (
    PROBE_TEXT,
    ActionClass,
    Decision,
    classify_action,
    dialog_is_destructive,
    egress_verdict,
    is_auth_text,
    send_url_allowed,
    url_denied,
)
from .scrub import scrub_url

PROBE_PATH = "/__blastradius_missing__"
QUIET_MS = 300
QUIET_CAP_MS = 3000
HYDRATE_CAP_MS = 9000
RECHECK_MS = 4000
NAV_TIMEOUT_MS = 25_000
CLICK_TIMEOUT_MS = 3000
NAV_WAIT_MS = 3000
BACK_TIMEOUT_MS = 6000  # one step of history is quick or it is not going to happen: then replay
MAX_DRIFTS_IN_A_ROW = 3  # selector drifts on one screen before the screen is given up
RECOVERY_BURN = 2  # actions charged when the base state cannot be re-established (case 12)
MAX_ERRORS_IN_A_ROW = 3  # consecutive HTTP >= 400 navigations before the run stops (case 13)
CATCH_ALL_ACTIONS = 2  # actions spent *from* the catch-all screen, however many dead links lead to it
WATCHDOG_GRACE_S = 45.0  # past wall_clock_s: the longest a single blocked browser call may take
SHELL_MAX_BYTES = 2 * 1024 * 1024
BUNDLE_MAX_BYTES = 32 * 1024 * 1024
MAX_BUNDLES = 6
_CONFIRM_NAME = re.compile(r"(?i)^(save|add|create|add new|ok|confirm|apply|done)\b")
_STATEFUL_ROLES = frozenset({"checkbox", "switch", "radio"})
_TOGGLE_NAME = re.compile(r"(?i)^(enable|disable|toggle|turn (on|off))\b")
_SEND_START = ("send", "run", "connect", "execute", "subscribe", "publish")
_SUBDIR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SENSITIVE_FIELD = re.compile(
    r"(?i)pass(word|code|phrase)?|e-?mail|phone|\btel\b|token|secret|\botp\b|card|cvv|ssn|iban|account"
)
_SENSITIVE_AUTOCOMPLETE = re.compile(r"(?i)pass|email|username|tel|cc-|one-time-code")

_QUIET_JS = """(args) => new Promise((resolve) => {
  const [quiet, cap] = args;
  let timer = null;
  const done = () => { obs.disconnect(); resolve(true); };
  const obs = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(done, quiet); });
  obs.observe(document.documentElement,
              {subtree: true, childList: true, attributes: true, characterData: true});
  timer = setTimeout(done, quiet);
  setTimeout(done, cap);
})"""

_REQUEST_URL_JS = """() => {
  const text = (el) => {
    if (el.value) return el.value;
    const ed = el.closest('.cm-editor') || el;
    const c = ed.querySelector('.cm-content') || ed;
    return (c.innerText || '').trim();
  };
  const fields = '[aria-label*="URL" i], [placeholder*="URL" i], input[type=url]';
  for (const el of document.querySelectorAll(fields)) {
    const t = text(el);
    if (t) return t;
  }
  return null;
}"""

_BAD_SELECTORS_JS = """(sels) => sels.map((s) => {
  try { document.querySelector(s); return ''; } catch (e) { return String((e && e.message) || e); }
})"""

_SCRIPT = re.compile(r"(?is)<script\b.*?</script>|<script\b[^>]*/>")
_ACTIVE_ELEMENT = re.compile(
    r"(?is)<(iframe|object|embed|applet|frameset|frame)\b.*?</\1\s*>"
    r"|<(?:iframe|object|embed|applet|frame)\b[^>]*>"
)
_META_REFRESH = re.compile(r"(?is)<meta\b[^>]*http-equiv\s*=\s*[\"']?refresh[^>]*>")
_ON_ATTRIBUTE = re.compile(r"""(?is)(?<=[\s"'/])on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""")
_SHELL_SRC = re.compile(r"""(?is)<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"']+\.m?js)["']""")


def sanitise_recorded_html(markup: str) -> str:
    """A recorded DOM made inert before it is rendered offline: no script elements, no inline event handlers,
    no frames or plugins, no meta refresh. (JavaScript is also disabled in the replay context; this is the
    second layer, and what ends up in the persisted copy.)"""
    out = _SCRIPT.sub("", markup)
    out = _ACTIVE_ELEMENT.sub("", out)
    out = _META_REFRESH.sub("", out)
    return _ON_ATTRIBUTE.sub("", out)


@dataclass(frozen=True)
class StateKey:
    """What identifies a state for recovery: route, topmost popup, active tabs (element sets may drift)."""

    pattern: str
    popup: str | None
    tabs: tuple[str, ...]

    @classmethod
    def of(cls, perc: Perception) -> StateKey:
        return cls(perc.pattern, perc.dialog_title, tuple(sorted(perc.active_tabs)))


@dataclass(frozen=True)
class Step:
    """One replayable user action on the way to a screen, with the state it must be taken in: persisted UI
    state (a sidebar tab chosen earlier) survives a reload and decides which controls exist. A step that sent
    a request (`side_effect`) is never replayed."""

    kind: Literal["click", "type"]
    element_id: str
    role: str
    name: str
    ordinal: int
    text: str = ""
    before: StateKey | None = None
    side_effect: bool = False

    @classmethod
    def of(
        cls,
        el: Element,
        before: StateKey,
        kind: Literal["click", "type"] = "click",
        text: str = "",
        *,
        side_effect: bool = False,
    ) -> Step:
        o = el.obs
        return cls(kind, o.id, o.role, o.name, o.ordinal, text, before, side_effect)


@dataclass
class ScreenRec:
    state: ScreenState
    keys: frozenset[tuple[str, str]]
    pattern: str
    popup_title: str | None
    tabs: tuple[str, ...]
    entry_url: str
    steps: list[Step]
    order: int
    candidates: list[Candidate] = field(default_factory=list)
    tried: set[str] = field(default_factory=set)
    spent: int = 0
    restore_failures: int = 0
    drifts: int = 0  # consecutive selector drifts: the page is not what this screen says it is
    blocked_logged: set[str] = field(default_factory=set)

    @property
    def id(self) -> str:
        return self.state.id

    @property
    def key(self) -> StateKey:
        return StateKey(self.pattern, self.popup_title, self.tabs)

    def pending(self) -> list[Candidate]:
        return [c for c in self.candidates if c.element.obs.id not in self.tried]


@dataclass
class Observed:
    screen: ScreenRec | None
    is_new: bool
    perception: Perception
    verdict: AccessVerdict


@dataclass
class _CatchAll:
    """What the app renders for a route that does not exist (case 3): a screen identity, not a status."""

    mode: Literal["render", "redirect"] | None = None
    keys: frozenset[tuple[str, str]] = frozenset()
    words: frozenset[str] = frozenset()
    redirect_pattern: str | None = None


@dataclass
class _ScopedRun(RunContext):
    """The run with `path("crawl", ...)` redirected into crawl/<subdir>/ (a bounded goal-directed re-crawl
    writes its own artifacts and leaves the main crawl's alone). Tracer, ledgers and failures are shared."""

    subdir: str = ""

    def path(self, *parts: str) -> Path:
        if self.subdir and parts[:1] == ("crawl",):
            parts = ("crawl", self.subdir, *parts[1:])
        return super().path(*parts)


def scoped_run(run: RunContext, subdir: str | None) -> RunContext:
    if not subdir:
        return run
    if not _SUBDIR.match(subdir) or subdir in (".", "..") or subdir in ("dom", "screens"):
        raise ValueError(f"artifact_subdir must be one plain path component, got {subdir!r}")
    fields = {f.name: getattr(run, f.name) for f in dataclasses.fields(RunContext)}
    return _ScopedRun(**fields, subdir=subdir)


def href_key(href: str | None) -> str:
    """A link target without its fragment, so `#top` variants of one page are one target."""
    return (href or "").split("#", 1)[0]


def _words(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z]{3,}", text.lower()[:4000]))


def _script_urls(html: str, base_url: str) -> list[str]:
    """Absolute script/modulepreload URLs of the HTML shell in document order, without repeats."""
    seen: dict[str, None] = {}
    for m in _SHELL_SRC.findall(html):
        seen.setdefault(urljoin(base_url, m), None)
    return list(seen)


def _shell_bundle_hash(html: str, base_url: str) -> str | None:
    """Hash of the script/modulepreload file names in the HTML shell: the deploy fingerprint (case 6)."""
    names = sorted({urlsplit(u).path for u in _script_urls(html, base_url)})
    return sha(*names, n=16) if names else None


def _probe_target(e: Element) -> bool:
    """The only inputs the explorer types into: plain, enabled text inputs that are not credential-shaped."""
    o = e.obs
    return (
        o.role == "textbox"
        and o.tag == "input"
        and e.input_type == "text"
        and not e.disabled
        and not e.editor
        and not _SENSITIVE_AUTOCOMPLETE.search(e.autocomplete or "")
        and not _SENSITIVE_FIELD.search(" ".join((o.name, o.placeholder or "", o.dom_id or "")))
    )


class Explorer:
    def __init__(
        self,
        run: RunContext,
        cfg: TargetConfig,
        policy: Policy,
        *,
        goals: Sequence[str] | None = None,
        max_actions: int | None = None,
        site: Callable[[str], tuple[int, dict[str, str], str] | None] | None = None,
        robots_txt: str | None | Literal["fetch"] = "fetch",
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        artifact_subdir: str | None = None,
    ):
        """`site` and `robots_txt` are seams for tests: serve pages from memory instead of the network."""
        self.run, self.cfg, self.crawl, self.policy = run, cfg, cfg.crawl, policy
        self.out: RunContext = scoped_run(run, artifact_subdir)
        self.subdir = artifact_subdir or ""
        self.goals = list(goals or [])
        self.budget = max_actions if max_actions is not None else cfg.crawl.max_actions
        self.beam = max(3, min(12, self.budget // 6))
        self.site, self.robots_txt = site, robots_txt
        self.clock, self.sleep = clock, sleep
        self.target = cfg.name
        self.widgets = list(cfg.crawl.widget_selectors)
        self.user_agent = run.settings.user_agent()
        self.breaker = Breaker(2)
        self.effort = GoalEffort(self.goals)
        self.version_rx: re.Pattern[str] | None = None
        self.version_error: str | None = None
        if cfg.crawl.version_regex:
            try:
                self.version_rx = re.compile(cfg.crawl.version_regex)
            except re.error as exc:
                self.version_error = f"version_regex does not compile: {exc}"

        self.screens: dict[str, ScreenRec] = {}
        self.by_group: dict[tuple[str, str, tuple[str, ...]], list[ScreenRec]] = {}
        self.page_tabs: dict[str, list[str]] = {}
        self.url_fp: dict[str, str] = {}
        self.transitions: dict[str, Transition] = {}
        self.captured: set[str] = set()
        self.catch_all = _CatchAll()
        self.layout_counts: Counter[tuple[str, str, str]] = Counter()
        self.acted_names: set[tuple[str, str, str]] = set()
        self.blocked_seen: set[tuple[str, str, str]] = set()
        self.settled: set[tuple[str, str, str]] = set()  # control keys whose click led to a different screen
        self.nav_by_href: dict[str, str] = {}  # link href -> the page screen clicking it reached
        self.refused_by_href: dict[str, str] = {}  # link href -> the verdict that kept it out of the graph
        self.tried_ids: set[str] = set()
        self.effort_by_pattern: Counter[str] = Counter()

        self.actions_spent = 0
        self.counts: Counter[str] = Counter()
        self.egress_blocked: Counter[str] = Counter()
        self.network_refused: dict[str, int] = {}
        self.redirects_refused: Counter[str] = Counter()
        self.policy_blocked: Counter[str] = Counter()
        self.soft_404: list[str] = []
        self.errors_in_a_row = 0
        self.rate_limited = False
        self.stop_reason: str = "frontier_empty"
        self.error: str | None = None
        self.last_status: int | None = None
        self.last_headers: dict[str, str] = {}
        self._nav_response: Any = None
        self.last_action_end = 0.0
        self.t0 = 0.0
        self.page: Any = None
        self.context: Any = None
        self.browser: Any = None
        self.robots_state = "not fetched"
        self._robots: str | None = None
        self.replaying = False  # recorded sessions have no actions to plan
        self.offline = False  # recorded sessions reach nothing at all
        self.replay_refused = False
        self.bundle_start: str | None = None
        self.bundle_end: str | None = None
        self.app_version: str | None = None
        self.blackhole: Blackhole | None = None
        self.watchdog_token = uuid.uuid4().hex
        self.watchdog: Watchdog | None = None

    # ------------------------------------------------------------------------------ output paths
    def _rel(self, *parts: str) -> str:
        """Run-relative path of an artifact (what screens.jsonl and captures.jsonl store)."""
        return "/".join(["crawl", *([self.subdir] if self.subdir else []), *parts])

    # ------------------------------------------------------------------------------ browser setup
    def _route(self, route: Any) -> None:
        verdict = egress_verdict(route.request.url, self.crawl.egress_allow)
        if not verdict.allowed:
            self.egress_blocked[verdict.host] += 1
            route.abort("blockedbyclient")
            return
        hit = self.site(route.request.url) if self.site is not None else None
        if hit is not None:
            status, headers, body = hit
            route.fulfill(status=status, headers=headers, body=body)
        else:
            route.continue_()

    def _offline_route(self, route: Any) -> None:
        self.counts["offline_aborted"] += 1
        route.abort("blockedbyclient")

    def _ws_route(self, ws: Any) -> None:
        """Count and drop a blocked socket; connect an allowed one. Never `ws.close()` here: on Playwright
        1.63 a close() inside the sync handler never returns and the whole crawl hangs. A socket that is not
        connected to its server is a mock that reaches nothing."""
        verdict = egress_verdict(ws.url, self.crawl.egress_allow)
        if self.offline:
            self.counts["offline_websockets"] += 1
        elif not verdict.allowed:
            self.egress_blocked[verdict.host] += 1
        else:
            self.counts["websockets_connected"] += 1
            ws.connect_to_server()

    def _on_response(self, response: Any) -> None:
        """Every response: rate limiting, redirect audit, and the status/headers of main-frame navigations
        (a click that loads a document is classified on *its* response, not on the last `goto`)."""
        status = response.status
        if status == 429 and egress_verdict(response.url, self.crawl.egress_allow).allowed:
            self.rate_limited = True
        if 300 <= status < 400:
            self._audit_redirect(response)
            return
        try:
            request = response.request
            if not request.is_navigation_request() or request.frame != self.page.main_frame:
                return
        except PlaywrightError:
            return
        self._note_navigation(response)

    def _note_navigation(self, response: Any) -> None:
        """The response of a main-frame document load becomes the status/headers its captures are judged on
        (D32, case 67: `cf-mitigated` and `x-amzn-waf-action` on whatever status). Idempotent per response."""
        if response is self._nav_response:
            return
        self._nav_response = response
        self.last_status = response.status
        self.last_headers = {k.lower(): v for k, v in response.headers.items()}
        self.errors_in_a_row = self.errors_in_a_row + 1 if response.status >= 400 else 0

    def _audit_redirect(self, response: Any) -> None:
        """A 3xx is followed by Chromium without asking the route handler; the network layer refuses a hop
        to a non-allowlisted host, and this records which host it was (attribution, not enforcement)."""
        location = response.headers.get("location")
        if not location:
            return
        target = urljoin(response.url, location)
        verdict = egress_verdict(target, self.crawl.egress_allow)
        if not verdict.allowed:
            self.redirects_refused[verdict.host] += 1

    @staticmethod
    def _on_dialog(dialog: Any) -> None:
        """alert/confirm/prompt are dismissed (a confirm() is answered "cancel": the app's destructive path is
        never taken). A `beforeunload` prompt is accepted: dismissing it cancels every navigation away from a
        page with unsaved changes (goto fails at once, history back hangs to its timeout), and the unsaved
        state is the crawler's own."""
        try:
            if dialog.type == "beforeunload":
                dialog.accept()
            else:
                dialog.dismiss()
        except PlaywrightError:
            pass

    def _on_new_page(self, new: Any) -> None:
        if new != self.page:
            self.counts["popups_closed"] += 1
            try:
                new.close()
            except PlaywrightError:
                pass

    def _launch(self, pw: Any) -> Any:
        """Chromium behind the refusing proxy: only allowlisted hosts (default ports) bypass it."""
        self.blackhole = Blackhole()
        return pw.chromium.launch(
            headless=True,
            args=[f"--br-watchdog={self.watchdog_token}"],
            proxy={
                "server": self.blackhole.server,
                "bypass": bypass_list([] if self.offline else self.crawl.egress_allow),
            },
        )

    def _open(self, pw: Any) -> None:
        self.browser = self._launch(pw)
        width, height = self.crawl.viewport
        self.context = self.browser.new_context(
            viewport={"width": width, "height": height},
            locale=self.crawl.locale,
            timezone_id=self.crawl.timezone,
            color_scheme="light",
            accept_downloads=False,
            service_workers="block" if self.crawl.block_service_workers else "allow",
            user_agent=self.user_agent,
            permissions=[],
            java_script_enabled=True,
        )
        self.context.route("**/*", self._route)
        self.context.route_web_socket("**/*", self._ws_route)
        self.page = self.context.new_page()
        self.page.on("dialog", self._on_dialog)
        self.page.on("response", self._on_response)
        self.context.on("page", self._on_new_page)

    def _validate_selectors(self) -> None:
        """An invalid widget selector would silently perceive (and click) every widget; refuse to start."""
        bad: list[str] = []
        if self.widgets:
            errors = self.page.evaluate(_BAD_SELECTORS_JS, self.widgets)
            bad += [f"widget_selectors {s!r}: {e}" for s, e in zip(self.widgets, errors) if e]
        for sel in self.crawl.dismiss_selectors:
            try:
                self.page.locator(sel).count()
            except PlaywrightError as exc:
                bad.append(f"dismiss_selectors {sel!r}: {str(exc).splitlines()[0][:120]}")
        if bad:
            message = "invalid selector in the target config: " + "; ".join(bad)
            self.run.failures.record(FailureCode.CONFIG_INVALID, message)
            raise ValueError(message)

    # ----------------------------------------------------------------------------------- pacing
    def _stop_check(self) -> str | None:
        if (
            self.breaker.consecutive >= self.breaker.threshold
            or self.errors_in_a_row >= MAX_ERRORS_IN_A_ROW
            or self.rate_limited
        ):
            return "circuit_breaker"
        if self.actions_spent >= self.budget:
            return "action_budget"
        if self.clock() - self.t0 >= self.crawl.wall_clock_s:
            return "time_budget"
        return None

    def _pace(self) -> None:
        wait = self.crawl.action_delay_ms / 1000 - (self.clock() - self.last_action_end)
        if wait > 0:
            self.sleep(wait)

    def _quiet(self) -> None:
        try:
            self.page.evaluate(_QUIET_JS, [QUIET_MS, QUIET_CAP_MS])
        except PlaywrightError:
            self.sleep(QUIET_MS / 1000)  # a navigation destroyed the context mid-wait

    def _hydrate(self, cap_ms: int = HYDRATE_CAP_MS) -> None:
        """Wait (bounded) until the app has rendered at least the anchor's number of controls."""
        need, waited = self.crawl.anchor.min_interactive, 0
        while waited < cap_ms:
            try:
                if perceive.collect(self.page, self.widgets)["interactive"] >= need:
                    break
            except PlaywrightError:
                pass
            self.sleep(0.4)
            waited += 400
        self._quiet()

    def _spend(self, pattern: str, n: int = 1) -> None:
        self.actions_spent += n
        self.effort_by_pattern[pattern] += n

    def _burn(self, pattern: str) -> None:
        """A failed recovery costs budget (case 12) but never takes the run past it."""
        self._spend(pattern, min(RECOVERY_BURN, max(self.budget - self.actions_spent, 0)))

    # --------------------------------------------------------------------------------- network
    def _navigate(self, url: str) -> str | None:
        """goto + hydrate. Returns an error string, or None. Never raises on a blocked or failed load. The
        status and headers come from the navigation's response event (`_on_response`)."""
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except PlaywrightError as exc:
            return str(exc).splitlines()[0][:200]
        if resp is not None:
            self._note_navigation(resp)  # normally already done by the response event
        self._hydrate()
        return None

    def _http_get(self, url: str, *, max_bytes: int) -> Fetched:
        """A plain GET with the same egress rule as the browser: redirects only to the base origin or an
        allowlisted host. The in-memory `site` seam answers instead of the network when set."""
        if self.site is not None:
            hit = self.site(url)
            if hit is None:
                return Fetched(url, error="no site entry")
            status, headers, body = hit
            text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
            return Fetched(url, status, {k.lower(): v for k, v in headers.items()}, text)
        return fetch_checked(
            url,
            base_url=self.cfg.base_url,
            allow=self.crawl.egress_allow,
            user_agent=self.user_agent,
            max_bytes=max_bytes,
        )

    def _fetch_robots(self) -> str | None:
        """robots.txt for the base host. 200 -> text; other 4xx -> allow all (RFC 9309); error/5xx, or a
        redirect off the allowlist -> None (unreadable = deny, case 69)."""
        if self.robots_txt != "fetch":
            self.robots_state = "injected"
            return self.robots_txt  # type: ignore[return-value]
        parts = urlsplit(self.cfg.base_url)
        got = self._http_get(f"{parts.scheme}://{parts.netloc}/robots.txt", max_bytes=1024 * 1024)
        if got.error is not None or got.status is None:
            self.robots_state = f"unreadable ({got.error})"
            if got.refused_host:
                self.counts["http_redirects_refused"] += 1
            return None
        self.robots_state = f"HTTP {got.status}"
        if got.status >= 500:
            return None
        if got.status >= 400 or "html" in (got.headers or {}).get("content-type", "").lower():
            return ""
        return got.text

    def _shell_html(self) -> str | None:
        """The HTML shell of the base URL: a plain GET, no browser involved."""
        got = self._http_get(self.cfg.base_url, max_bytes=SHELL_MAX_BYTES)
        return got.text if got.error is None and got.status is not None else None

    def _shell_hash(self) -> str | None:
        html = self._shell_html()
        return _shell_bundle_hash(html, self.cfg.base_url) if html else None

    def _detect_version(self, shell: str | None) -> str | None:
        """cfg.crawl.version_regex (group 1; group 0 when it has no group) over the HTML shell, then over the
        scripts the shell loads, in document order. The first match wins."""
        rx = self.version_rx
        if rx is None or not shell:
            return None

        def first(text: str) -> str | None:
            m = rx.search(text)
            return (m.group(1) if rx.groups else m.group(0)) if m else None

        found = first(shell)
        if found:
            return found
        base, allow = self.cfg.base_url, self.crawl.egress_allow
        for url in _script_urls(shell, base)[:MAX_BUNDLES]:
            if not hop_allowed(url, base, allow):
                continue
            got = self._http_get(url, max_bytes=BUNDLE_MAX_BYTES)
            found = first(got.text) if got.error is None and got.text else None
            if found:
                return found
        return None

    def _note_version(self, text: str) -> None:
        """Fallback when no bundle carried the version: the rendered text (Hoppscotch prints it in the help
        menu). Only the first sighting counts."""
        if self.app_version is None and self.version_rx is not None:
            m = self.version_rx.search(text)
            if m:
                self.app_version = m.group(1) if self.version_rx.groups else m.group(0)

    def _request_url(self) -> str | None:
        try:
            value = self.page.evaluate(_REQUEST_URL_JS)
        except PlaywrightError:
            return None
        return value if isinstance(value, str) and value else None

    # ---------------------------------------------------------------------------------- perceive
    def _pattern_for(self, raw: dict[str, Any]) -> str:
        pattern = url_pattern(raw.get("url", ""))
        ca = self.catch_all
        if ca.mode != "render" or jaccard(perceive.raw_keys(raw, layer=-1), ca.keys) < MERGE_JACCARD:
            return pattern
        if raw.get("popups"):  # judged on the page layer behind the dialog
            return CATCH_ALL_PATTERN
        here = _words(str(raw.get("body", "")))
        return CATCH_ALL_PATTERN if len(here & ca.words) / max(1, len(here | ca.words)) >= 0.8 else pattern

    def _now(self, *, force_pattern: str | None = None) -> Perception:
        raw = perceive.collect(self.page, self.widgets)
        return perceive.build(raw, target=self.target, pattern=force_pattern or self._pattern_for(raw))

    def _fingerprint(self, perc: Perception) -> str:
        return fingerprint(perc.pattern, perc.dialog_title, perc.active_tabs, perc.reachable())

    def _classify(
        self, perc: Perception, fp: str, *, rung: str = "L0", html: str | None = None
    ) -> AccessResult:
        if html is None:
            try:
                html = self.page.content()
            except PlaywrightError:
                html = perc.body_text
        # The catch-all is *expected* to repeat: every dead link renders the same screen (case 3), and that
        # is judged by rendered identity (soft-404), not by the access classifier's collapse guard.
        prior = dict(self.url_fp)
        if not perc.popups and perc.pattern != CATCH_ALL_PATTERN:
            prior[perc.url] = fp
        return classify_capture(
            status=self.last_status,
            headers=self.last_headers,
            title=perc.title,
            body_text=html,
            interactive_count=perc.interactive_count,
            anchor=self.crawl.anchor,
            url=perc.url,
            rung=rung,
            prior_fingerprints=prior,
        )

    # ------------------------------------------------------------------------------------ files
    def _write_jsonl(self, rel: str, obj: Mapping[str, Any]) -> None:
        if self.run.run_dir.exists():
            with self.out.path("crawl", rel).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(obj, ensure_ascii=True) + "\n")

    def _save_artifacts(self, stem: str, *, shot: bool) -> tuple[str | None, str | None]:
        if not self.run.run_dir.exists():
            return None, None
        dom_rel = png_rel = None
        try:
            html = perceive.snapshot_dom(self.page, self.widgets)
            self.out.path("crawl", "dom", f"{stem}.html").write_text(html, encoding="utf-8")
            dom_rel = self._rel("dom", f"{stem}.html")
            if shot:
                self.page.screenshot(path=str(self.out.path("crawl", "screens", f"{stem}.png")))
                png_rel = self._rel("screens", f"{stem}.png")
        except PlaywrightError:
            pass
        return dom_rel, png_rel

    def _persist_capture(self, capture: Capture) -> None:
        if capture.id not in self.captured:
            self.captured.add(capture.id)
            append_capture(self.out, capture)

    # ----------------------------------------------------------------------------------- observe
    def _match_existing(self, perc: Perception, fp: str) -> ScreenRec | None:
        keys = key_set(perc.reachable())
        for rec in self.by_group.get(group_key(perc.pattern, perc.dialog_title, perc.active_tabs), []):
            if rec.state.fingerprint == fp or jaccard(rec.keys, keys) >= MERGE_JACCARD:
                return rec
        return None

    def _merge(self, rec: ScreenRec, perc: Perception, parent: ScreenRec | None) -> None:
        have = {e.id for e in rec.state.elements}
        rec.state.elements.extend(e.obs for e in perc.reachable() if e.obs.id not in have)
        rec.keys = rec.keys | key_set(perc.reachable())
        if parent is not None:
            rec.state.depth = min(rec.state.depth, parent.state.depth + 1)

    def _create(
        self,
        perc: Perception,
        fp: str,
        capture: Capture,
        parent: ScreenRec | None,
        steps: Sequence[Step],
        label: str,
        dom: str | None,
        png: str | None,
    ) -> ScreenRec:
        kind = screen_kind(
            pattern=perc.pattern,
            has_popup=bool(perc.popups),
            active_tabs=perc.active_tabs,
            page_tabs_for_pattern=None if perc.popups else self.page_tabs.get(perc.pattern),
        )
        if not perc.popups and perc.pattern not in self.page_tabs:
            self.page_tabs[perc.pattern] = list(perc.active_tabs)
        navigated = parent is None or (perc.pattern != parent.pattern and not perc.popups)
        state = ScreenState(
            id=stable_id("scr", self.target, fp),
            target=self.target,
            url=scrub_url(perc.url),
            url_pattern=perc.pattern,
            title=perc.title,
            kind=kind,
            fingerprint=fp,
            dialog_title=perc.dialog_title,
            active_tabs=list(perc.active_tabs),
            elements=[e.obs for e in perc.reachable()],
            screenshot=png,
            dom_path=dom,
            capture_id=capture.id,
            first_seen_action=label or None,
            depth=parent.state.depth + 1 if parent is not None else 0,
        )
        rec = ScreenRec(
            state=state,
            keys=key_set(perc.reachable()),
            pattern=perc.pattern,
            popup_title=perc.dialog_title,
            tabs=tuple(sorted(perc.active_tabs)),
            entry_url=perc.url if navigated else parent.entry_url,  # type: ignore[union-attr]
            steps=[] if navigated else [*parent.steps, *steps],
            order=len(self.screens),
        )  # type: ignore[union-attr]
        self.screens[state.id] = rec
        self.by_group.setdefault(group_key(perc.pattern, perc.dialog_title, perc.active_tabs), []).append(rec)
        for key in {layout_key(e) for e in perc.reachable() if layout_key(e)[1]}:
            self.layout_counts[key] += 1
        if perc.truncated:
            self.counts["truncated_screens"] += 1  # more than MAX_ELEMENTS controls: the rest not recorded
        if not self.replaying:
            self._build_candidates(rec, perc)
        self.effort.observe_screen(rec.id, [e.obs.name for e in perc.reachable()])
        self._write_jsonl("screens.jsonl", state.model_dump(mode="json"))
        return rec

    def _policy_verdict(self, perc: Perception) -> AccessResult | None:
        """A route the target denies, reached by a click rather than a link: observed, never graphed."""
        denied = url_denied(perc.url, self.crawl)
        if denied is None:
            return None
        return AccessResult(
            verdict=AccessVerdict.DISALLOWED,
            signals=["deny_url_pattern"],
            reason=f"{urlsplit(perc.url).path} matches deny pattern {denied}",
        )

    def _observe(
        self,
        *,
        parent: ScreenRec | None,
        action: str,
        label: str,
        steps: Sequence[Step] = (),
        force_pattern: str | None = None,
    ) -> Observed:
        """Perceive the page, classify it, persist the capture, register or merge the screen."""
        perc = self._now(force_pattern=force_pattern)
        if perc.interactive_count < self.crawl.anchor.min_interactive:  # a reload or route change in flight
            self._hydrate(RECHECK_MS)
            perc = self._now(force_pattern=force_pattern)
        self._note_version(perc.body_text)
        fp = self._fingerprint(perc)
        policy = self._policy_verdict(perc)
        if policy is not None:
            result = policy
        elif robots_allows(self._robots, self.user_agent, perc.url) != AccessVerdict.OK:
            result = AccessResult(  # an in-app route change onto a disallowed path
                verdict=AccessVerdict.DISALLOWED,
                signals=["robots"],
                reason=f"robots.txt ({self.robots_state}) disallows {urlsplit(perc.url).path}",
            )
        else:
            result = self._classify(perc, fp)
        if not perc.popups and perc.pattern != CATCH_ALL_PATTERN:
            self.url_fp[perc.url] = fp
        parent_cap = parent.state.capture_id if parent else None
        existing = self._match_existing(perc, fp) if result.verdict == AccessVerdict.OK else None
        is_new = result.verdict == AccessVerdict.OK and existing is None
        dom = png = None
        if is_new:
            dom, png = self._save_artifacts(stable_id("scr", self.target, fp), shot=True)
        elif existing is not None:
            dom, png = existing.state.dom_path, existing.state.screenshot
        elif policy is None:  # a denied route's content is not kept at all
            dom, _ = self._save_artifacts("suspect_" + sha(perc.url, parent_cap, action), shot=False)
        capture = make_capture(
            target=self.target,
            url=scrub_url(perc.url),
            final_url=scrub_url(perc.url),
            status=self.last_status,
            headers=self.last_headers,
            title=perc.title,
            html_path=dom,
            screenshot_path=png,
            interactive_count=perc.interactive_count,
            source_kind="live",
            parent_id=parent_cap,
            action=action,
            access=result,
        )
        self._persist_capture(capture)
        screen = existing
        if is_new:
            screen = self._create(perc, fp, capture, parent, steps, label, dom, png)
        elif existing is not None:
            self._merge(existing, perc, parent)
        key = {
            AccessVerdict.BLOCKED: "blocked",
            AccessVerdict.SUSPECT: "suspect",
            AccessVerdict.DISALLOWED: "disallowed",
        }.get(result.verdict)
        if key:
            self.counts[key] += 1
        self.breaker.record(result.verdict)
        return Observed(screen, is_new, perc, result.verdict)

    # -------------------------------------------------------------------------- candidates + policy
    def _decide(
        self, el: Element, perc: Perception, request_url: str | None, popup_destructive: str | None
    ) -> Decision:
        auth_dialog = bool(perc.popups) and is_auth_text(perc.dialog_title or "", self.crawl)
        decision = classify_action(
            el,
            self.crawl,
            base_url=self.cfg.base_url,
            page_url=perc.url,
            popup_is_auth=auth_dialog,
            popup_destructive=popup_destructive,
            request_url=request_url,
        )
        if (
            decision.action_class == ActionClass.NAVIGATE
            and el.obs.href
            and robots_allows(self._robots, self.user_agent, el.obs.href) != AccessVerdict.OK
        ):
            return Decision(ActionClass.DENIED_URL, False, f"robots.txt disallows ({self.robots_state})")
        return decision

    def _candidate_ok(self, el: Element, page_url: str) -> bool:
        o = el.obs
        if (
            el.disabled
            or el.editor
            or o.tag == "select"
            or not o.selector
            or el.input_type in ("hidden", "file")
        ):
            return False
        if el.tab_active or (o.href and href_key(o.href) == href_key(page_url)):
            return False  # the current tab and a link to this very page: no-ops
        if o.role in _STATEFUL_ROLES or _TOGGLE_NAME.match(o.name):
            self.counts["stateful_skipped"] += 1  # toggling persisted settings can desync the base state
            return False
        return not (o.role in ("textbox", "searchbox") and o.tag != "input")  # contenteditable: no typing

    def _build_candidates(self, rec: ScreenRec, perc: Perception) -> None:
        wants_url = any(e.obs.name.lower().startswith(_SEND_START) for e in perc.reachable())
        request_url = self._request_url() if wants_url else None
        popup_destructive = (
            dialog_is_destructive(perc.dialog_title, perc.dialog_prose, self.crawl) if perc.popups else None
        )
        cands: list[Candidate] = []
        blocked_by_class: Counter[str] = Counter()
        for el in perc.reachable():
            if not self._candidate_ok(el, perc.url):
                continue
            decision = self._decide(el, perc, request_url, popup_destructive)
            if decision.allowed:
                cands.append(Candidate(len(cands), el, decision))
                continue
            o = el.obs
            blocked_by_class[str(decision.action_class)] += 1
            seen_key = (str(decision.action_class), *element_key(el))
            if seen_key not in self.blocked_seen:
                self.blocked_seen.add(seen_key)
                self.run.failures.record(
                    FailureCode.ACTION_BLOCKED_BY_POLICY,
                    f"{decision.action_class} refused: {el.obs.role} {el.obs.name or '[icon]'!r} "
                    f"({decision.reason})",
                    screen_id=rec.id,
                    action_class=str(decision.action_class),
                    url_pattern=rec.pattern,
                )
            if "robots.txt" in decision.reason:
                self._record_disallowed(o.href or "", decision.reason, parent=rec)
            if o.id not in rec.blocked_logged:
                rec.blocked_logged.add(o.id)
                self._record_transition(
                    Transition(
                        id=stable_id("tr", rec.id, rec.id, o.id, "click"),
                        from_screen=rec.id,
                        to_screen=rec.id,
                        element_id=o.id,
                        action="click",
                        label=o.name or o.text or f"[icon {el.icon}]",
                        observed=False,
                        blocked=True,
                        reason=f"{str(decision.action_class).lower()}: {decision.reason}",
                    )
                )
        self.policy_blocked.update(blocked_by_class)
        rec.candidates = cands

    def _policy_context(self, rec: ScreenRec) -> PolicyContext:
        return PolicyContext(
            screen_id=rec.id,
            url_pattern=rec.pattern,
            dialog_title=rec.popup_title,
            depth=rec.state.depth,
            tried_ids=self.tried_ids,
            acted_names=self.acted_names,
            layout_counts=self.layout_counts,
            goals=self.goals,
        )

    def _covered(self, cand: Candidate) -> bool:
        """A layout control (it appears on several screens) that was already acted on elsewhere: its effect
        is app-wide, so trying it again here would only spend budget. Keyed on (role, name-or-icon, owner
        scope): two nameless icons of different components are two controls."""
        key = layout_key(cand.element)
        return bool(key[1]) and key in self.settled and self.layout_counts[key] >= 2

    def _href_known(self, cand: Candidate) -> bool:
        """A link whose outcome is already known (followed, soft-404, or refused): another screen gets an
        inferred edge, not another click."""
        href = href_key(cand.element.obs.href)
        return bool(href) and (href in self.nav_by_href or href in self.refused_by_href)

    def _catch_all_spent(self) -> int:
        return sum(r.spent for r in self.screens.values() if r.pattern == CATCH_ALL_PATTERN)

    def _pending(self, rec: ScreenRec) -> list[Candidate]:
        if rec.pattern == CATCH_ALL_PATTERN and self._catch_all_spent() >= CATCH_ALL_ACTIONS:
            return []  # the app's own "not found" screen: Home/Reload told us everything it will
        return [c for c in rec.pending() if not self._covered(c) and not self._href_known(c)]

    def _next_candidate(self, rec: ScreenRec) -> Candidate | None:
        pending = self._pending(rec)
        if not pending:
            return None
        order = self.policy.rank(pending, self._policy_context(rec))
        by_index = {c.index: c for c in pending}
        return by_index[order[0]] if order and order[0] in by_index else pending[0]

    # -------------------------------------------------------------------------------- transitions
    def _record_transition(self, tr: Transition) -> None:
        if tr.id in self.transitions:
            return
        self.transitions[tr.id] = tr
        self._write_jsonl("transitions.jsonl", tr.model_dump(mode="json"))
        self.run.evidence.add(
            "crawl.transition",
            tr.id,
            f"{tr.action} {tr.label!r}: {tr.from_screen} -> {tr.to_screen}"
            + (f" [blocked: {tr.reason}]" if tr.blocked else ""),
            span_id=self.run.tracer.current_span_id,
            from_screen=tr.from_screen,
            to_screen=tr.to_screen,
            observed=tr.observed,
            blocked=tr.blocked,
        )

    def _infer_link_transitions(self) -> None:
        """A link whose href was followed once leads to the same screen from every page that carries it; the
        edge is recorded from the pages where it was not clicked, marked observed=False. A link that was
        refused (SUSPECT, BLOCKED, denied) gets a blocked edge the same way."""
        for rec in list(self.screens.values()):
            for cand in rec.candidates:
                o = cand.element.obs
                if not o.href or o.id in rec.tried:
                    continue
                href = href_key(o.href)
                label = o.name or f"[icon {cand.element.icon}]"
                target = self.nav_by_href.get(href)
                if target is not None and target != rec.id:
                    soft = self.screens[target].pattern == CATCH_ALL_PATTERN
                    self._record_transition(
                        Transition(
                            id=stable_id("tr", rec.id, target, o.id, "click"),
                            from_screen=rec.id,
                            to_screen=target,
                            element_id=o.id,
                            action="click",
                            label=label,
                            observed=False,
                            reason=("soft_404: the route renders the catch-all screen; " if soft else "")
                            + "inferred: the same link was followed from another screen",
                        )
                    )
                elif href in self.refused_by_href:
                    verdict = self.refused_by_href[href]
                    self._record_transition(
                        Transition(
                            id=stable_id("tr", rec.id, rec.id, o.id, f"access:{verdict}"),
                            from_screen=rec.id,
                            to_screen=rec.id,
                            element_id=o.id,
                            action="click",
                            label=label,
                            observed=False,
                            blocked=True,
                            reason=f"access: {verdict} (inferred: the same link was refused elsewhere)",
                        )
                    )

    def _record_disallowed(self, url: str, reason: str, *, parent: ScreenRec | None = None) -> None:
        access = AccessResult(verdict=AccessVerdict.DISALLOWED, signals=["robots"], reason=reason)
        clean = scrub_url(url)
        cap = make_capture(
            target=self.target,
            url=clean,
            final_url=clean,
            access=access,
            action="goto",
            parent_id=parent.state.capture_id if parent else None,
        )
        if cap.id not in self.captured:
            self.counts["disallowed"] += 1
        self._persist_capture(cap)
        self.breaker.record(AccessVerdict.DISALLOWED)

    # --------------------------------------------------------------------------- state recovery
    @staticmethod
    def _same_state(perc: Perception, rec: ScreenRec) -> bool:
        return StateKey.of(perc) == rec.key

    @staticmethod
    def _find(perc: Perception, step: Step) -> Element | None:
        pool = perc.reachable()
        for el in pool:
            if el.obs.id == step.element_id:
                return el
        return next(
            (
                el
                for el in pool
                if (el.obs.role, el.obs.name, el.obs.ordinal) == (step.role, step.name, step.ordinal)
            ),
            None,
        )

    def _click(self, selector: str) -> bool:
        try:
            self.page.locator(selector).first.click(timeout=CLICK_TIMEOUT_MS)
        except PlaywrightError:
            return False
        return True

    def _replay(self, rec: ScreenRec) -> bool:
        """goto the entry url, then redo the steps. True when the screen's state is re-established. A step
        that sent a request is never repeated: the screen behind it is refused, not burned."""
        if any(s.side_effect for s in rec.steps):
            self.replay_refused = True
            self.counts["replay_refused_side_effect"] += 1
            return False
        self.counts["restores"] += 1
        self._pace()
        err = self._navigate(rec.entry_url)
        self.last_action_end = self.clock()
        if err is not None:
            return False
        for step in rec.steps:
            if step.before is not None and not self._reach(step.before):
                return False
            el = self._find(self._now(), step)
            if el is None:
                return False
            if step.kind == "type":
                try:
                    self.page.locator(el.obs.selector).first.fill(step.text, timeout=CLICK_TIMEOUT_MS)
                except PlaywrightError:
                    return False
            elif not self._click(el.obs.selector):
                return False
            self._quiet()
        return self._reach(rec.key)

    def _reach(self, key: StateKey) -> bool:
        """Click persisted tabs back until the page is in `key` (no popup change, no route change)."""
        for _ in range(3):
            perc = self._now()
            if StateKey.of(perc) == key:
                return True
            if perc.popups or perc.pattern != key.pattern or not self._revert_tabs(perc, key):
                return False
            self._quiet()
        return False

    def _revert_tabs(self, perc: Perception, key: StateKey) -> bool:
        clicked = [
            self._click(el.obs.selector)
            for el in perc.elements
            if el.tab and not el.tab_active and el.obs.name in key.tabs
        ]
        return any(clicked)

    def _go_back(self) -> bool:
        """One step of history: cheaper and politer than a full reload when a click only moved the route."""
        self._pace()
        try:
            self.page.go_back(wait_until="domcontentloaded", timeout=BACK_TIMEOUT_MS)
        except PlaywrightError:
            return False
        self.last_action_end = self.clock()
        self.counts["history_back"] += 1
        self._hydrate(RECHECK_MS)
        return True

    def _return_to(self, rec: ScreenRec, *, back: bool = False) -> bool:
        """Cheap ladder first (Escape, a dismiss selector, re-click the original tab), then, when `back` says
        the page was just left from `rec`, one step of history, then a paced full replay."""
        try:
            went_back = False
            for _ in range(4):
                perc = self._now()
                if self._same_state(perc, rec):
                    return True
                if perc.popups:
                    self.page.keyboard.press("Escape")
                    self._quiet()
                    if self._same_state(self._now(), rec):
                        return True
                    for sel in self.crawl.dismiss_selectors:
                        if self.page.locator(sel).count() and self._click(sel):
                            self._quiet()
                            break
                    continue
                if perc.pattern == rec.pattern:
                    if not self._revert_tabs(perc, rec.key):
                        break
                    self._quiet()
                    continue
                if not back or went_back or not self._go_back():
                    break
                went_back = True
            return self._replay(rec)
        except PlaywrightError:
            return False

    def _ensure(self, rec: ScreenRec) -> bool:
        """True when the page is in rec's state *and* rendered: the same route that has not hydrated (after a
        history step, or a reload in flight) is given a bounded wait before it is trusted."""
        self.replay_refused = False
        need = self.crawl.anchor.min_interactive
        try:
            perc = self._now()
            if self._same_state(perc, rec):
                if perc.interactive_count >= need:
                    return True
                self._hydrate(RECHECK_MS)
                perc = self._now()
                if self._same_state(perc, rec) and perc.interactive_count >= need:
                    return True
        except PlaywrightError:
            pass
        return self._return_to(rec)

    def _give_up(self, rec: ScreenRec, why: str) -> None:
        self.run.failures.record(FailureCode.STATE_NOT_RECOVERABLE, why, screen_id=rec.id)
        self._burn(rec.pattern)
        self.counts["unrecoverable"] += 1

    def _lost(self, rec: ScreenRec, why: str) -> None:
        """The screen could not be re-established. When the only way back was to send a request again, the
        screen is dropped without burning budget (nothing was attempted)."""
        if self.replay_refused:
            self.run.failures.record(
                FailureCode.STATE_NOT_RECOVERABLE,
                f"{why}: restoring needs a request to be sent again, which is never replayed",
                screen_id=rec.id,
            )
            rec.tried.update(c.element.obs.id for c in rec.candidates)
            return
        self._give_up(rec, why)

    # ----------------------------------------------------------------------------------- actions
    def _refuse(self, rec: ScreenRec, el: Element, cls: ActionClass, why: str) -> None:
        """An action the gate refused at click time: recorded like any other policy block."""
        o = el.obs
        self.policy_blocked[str(cls)] += 1
        self.run.failures.record(
            FailureCode.ACTION_BLOCKED_BY_POLICY,
            f"{cls} refused at click time: {o.role} {o.name or '[icon]'!r} ({why})",
            screen_id=rec.id,
            action_class=str(cls),
            url_pattern=rec.pattern,
        )
        self._record_transition(
            Transition(
                id=stable_id("tr", rec.id, rec.id, o.id, "click", "refused"),
                from_screen=rec.id,
                to_screen=rec.id,
                element_id=o.id,
                action="click",
                label=o.name or o.text or f"[icon {el.icon}]",
                observed=False,
                blocked=True,
                reason=f"{str(cls).lower()}: {why}",
            )
        )

    def _act(self, rec: ScreenRec, cand: Candidate) -> None:
        o = cand.element.obs
        side_effect = cand.decision.action_class == ActionClass.NETWORK_SIDE_EFFECT
        self.replay_refused = False
        self.tried_ids.add(o.id)
        rec.tried.add(o.id)
        rec.spent += 1
        self._pace()
        with self.run.tracer.span(
            "browser_action",
            f"click {o.name or o.role}",
            action_class=str(cand.decision.action_class),
            role=o.role,
            target_name=o.name,
            screen=rec.id,
            fp_before=rec.state.fingerprint,
        ) as sp:
            before = self._now()
            if not self._same_state(before, rec):  # a late route change or a toast moved us
                self.counts["drift_recovered"] += 1
                if not self._return_to(rec, back=True):
                    self._lost(rec, f"state drifted away from {rec.pattern}")
                    self.last_action_end = self.clock()
                    return
                before = self._now()
            el = next((e for e in before.reachable() if e.obs.id == o.id), None)
            if el is None:  # nothing was done: not an action, not budget
                self._selector_drift(rec, o, sp)
                return
            self._spend(rec.pattern)
            self.acted_names.add(layout_key(cand.element))
            if side_effect:
                self.settled.add(layout_key(cand.element))  # a request goes out once per control, not per tab
            self.effort.record_action(o.name)
            typed = None
            if cand.decision.action_class == ActionClass.LOCAL_WRITE and before.popups:
                typed = self._type_probe(rec, before)
                if typed is None and _CONFIRM_NAME.match(o.name or ""):
                    # never confirm a dialog the probe string could not be put into
                    self._refuse(rec, el, cand.decision.action_class, "dialog could not be filled")
                    sp.set(verdict="refused")
                    self.last_action_end = self.clock()
                    return
            if side_effect and not send_url_allowed(self._request_url(), self.crawl):
                # re-read at click time: an earlier click may have loaded another request into the field
                why = "request url is not in send_allowed_urls now"
                self._refuse(rec, el, cand.decision.action_class, why)
                sp.set(verdict="refused")
                self.last_action_end = self.clock()
                return
            if not self._click(el.obs.selector):
                self._selector_drift(rec, o, sp)
                return
            rec.drifts = 0
            self._await_effect(cand.decision.action_class, before.url)
            obs = self._observe(
                parent=rec,
                action=f"click:{o.id}",
                label=f"click {o.name}".strip(),
                steps=[*([typed] if typed else []), Step.of(el, rec.key, side_effect=side_effect)],
            )
            self._record_outcome(rec, el, obs)
            sp.set(
                verdict=str(obs.verdict),
                to_screen=obs.screen.id if obs.screen else None,
                fp_after=obs.screen.state.fingerprint if obs.screen else None,
                new_screen=obs.is_new,
                url_changed=obs.perception.url != before.url,
            )
            if (
                cand.decision.action_class == ActionClass.LOCAL_WRITE
                and obs.is_new
                and obs.screen is not None
                and obs.perception.popups
            ):
                self._fill_and_confirm(obs.screen, obs.perception)
            self.last_action_end = self.clock()
        if self.breaker.consecutive < self.breaker.threshold and not self._return_to(rec, back=True):
            self._lost(rec, f"could not return to {rec.pattern} after {o.name!r}")

    def _selector_drift(self, rec: ScreenRec, o: Any, sp: Any) -> None:
        self.run.failures.record(
            FailureCode.SELECTOR_DRIFT,
            f"{o.role} {o.name!r} not actionable",
            screen_id=rec.id,
            element_id=o.id,
        )
        sp.set(verdict="selector_drift")
        self.last_action_end = self.clock()
        rec.drifts += 1
        if rec.drifts >= MAX_DRIFTS_IN_A_ROW:  # the page is blank, stale or not this screen: stop asking it
            rec.tried.update(c.element.obs.id for c in rec.candidates)
            self._give_up(rec, f"{rec.drifts} selectors in a row not found on {rec.pattern}")

    def _await_effect(self, action_class: ActionClass, before_url: str) -> None:
        """Wait for what the click was supposed to cause: a route change, a response, or just a quiet DOM."""
        try:
            if action_class == ActionClass.NAVIGATE:
                self.page.wait_for_url(lambda url: url != before_url, timeout=NAV_WAIT_MS)
            elif action_class == ActionClass.NETWORK_SIDE_EFFECT:
                self.page.wait_for_load_state("networkidle", timeout=NAV_WAIT_MS)
        except PlaywrightError:
            pass  # nothing happened within the bound: record that
        if action_class == ActionClass.NAVIGATE:
            self._hydrate()
        else:
            self._quiet()

    def _record_outcome(self, rec: ScreenRec, el: Element, obs: Observed) -> None:
        o = el.obs
        if obs.screen is None:
            if o.href:  # a link that was refused is refused from every screen: no second click
                self.refused_by_href[href_key(o.href)] = str(obs.verdict)
            self._record_transition(
                Transition(
                    id=stable_id("tr", rec.id, rec.id, o.id, f"access:{obs.verdict}"),
                    from_screen=rec.id,
                    to_screen=rec.id,
                    element_id=o.id,
                    action="click",
                    label=o.name,
                    observed=False,
                    blocked=True,
                    reason=f"access: {obs.verdict}",
                )
            )
        elif obs.screen.id == rec.id:
            self.counts["noop_actions"] += 1
            if not o.href:  # a control that changed nothing here changes nothing elsewhere;
                self.settled.add(layout_key(el))  # a link may still lead somewhere from another screen
        else:
            self.settled.add(layout_key(el))
            is_page = obs.screen.state.kind == "page"
            soft = is_page and obs.screen.pattern == CATCH_ALL_PATTERN and rec.pattern != CATCH_ALL_PATTERN
            if is_page and o.href:
                self.nav_by_href[href_key(o.href)] = obs.screen.id
            if soft:
                self.soft_404.append(obs.perception.url)
            self._record_transition(
                Transition(
                    id=stable_id("tr", rec.id, obs.screen.id, o.id, "click"),
                    from_screen=rec.id,
                    to_screen=obs.screen.id,
                    element_id=o.id,
                    action="click",
                    label=o.name or f"[icon {el.icon}]",
                    observed=True,
                    reason="soft_404: the route renders the catch-all screen" if soft else "",
                )
            )

    def _type_probe(self, owner: ScreenRec, perc: Perception) -> Step | None:
        """Type the benign probe string into the first plain text input of the reachable layer (D28: it is the
        only string the explorer ever types, and only in a dialog that creates local state)."""
        field_el = next((e for e in perc.reachable() if _probe_target(e)), None)
        if field_el is None or self.actions_spent >= self.budget:
            return None
        self._pace()
        try:
            self.page.locator(field_el.obs.selector).first.fill(PROBE_TEXT, timeout=CLICK_TIMEOUT_MS)
        except PlaywrightError:
            return None
        self._spend(owner.pattern)
        self._record_transition(
            Transition(
                id=stable_id("tr", owner.id, owner.id, field_el.obs.id, "type"),
                from_screen=owner.id,
                to_screen=owner.id,
                element_id=field_el.obs.id,
                action="type",
                label=PROBE_TEXT,
                observed=True,
            )
        )
        return Step.of(field_el, owner.key, "type", PROBE_TEXT)

    def _fill_and_confirm(self, modal: ScreenRec, perc: Perception) -> None:
        """A LOCAL_WRITE dialog was just opened: fill it and press its own confirm button if allowed."""
        confirm = next((c for c in modal.candidates if _CONFIRM_NAME.match(c.element.obs.name or "")), None)
        if confirm is None or self._stop_check():
            return
        typed = self._type_probe(modal, perc)
        if self.actions_spent >= self.budget:
            return
        el = next((e for e in self._now().reachable() if e.obs.id == confirm.element.obs.id), None)
        if typed is None or el is None or not self._click(el.obs.selector):
            return
        self._spend(modal.pattern)
        self._quiet()
        obs = self._observe(
            parent=modal,
            action=f"click:{el.obs.id}",
            label=f"click {el.obs.name}",
            steps=[typed, Step.of(el, modal.key)],
        )
        modal.tried.add(confirm.element.obs.id)
        self.tried_ids.add(confirm.element.obs.id)
        if obs.screen is not None and obs.screen.id != modal.id:
            self._record_transition(
                Transition(
                    id=stable_id("tr", modal.id, obs.screen.id, el.obs.id, "click"),
                    from_screen=modal.id,
                    to_screen=obs.screen.id,
                    element_id=el.obs.id,
                    action="click",
                    label=el.obs.name,
                    observed=True,
                )
            )

    # ------------------------------------------------------------------------------------ driver
    def _visit_seed(self, url: str, *, probe: bool = False) -> None:
        asked = url_pattern(url)
        if robots_allows(self._robots, self.user_agent, url) != AccessVerdict.OK:
            self._record_disallowed(url, f"robots.txt ({self.robots_state})")
            return
        if url_denied(url, self.crawl):
            self.counts["denied_seeds"] += 1
            return
        self._pace()
        with self.run.tracer.span(
            "browser_action",
            f"goto {asked}",
            action_class="SEED_PROBE" if probe else "SEED",
            role="seed",
            target_name=asked,
            fp_before=None,
        ) as sp:
            self._spend(CATCH_ALL_PATTERN if probe else asked)
            err = self._navigate(url)
            self.last_action_end = self.clock()
            if err is not None:
                self.counts["nav_errors"] += 1
                sp.set(verdict="nav_error")
                return
            landed = url_pattern(self.page.url)
            if probe:
                self._learn_catch_all(asked, landed)
            redirected = (
                self.catch_all.mode == "redirect"
                and landed == self.catch_all.redirect_pattern
                and landed != asked
            )
            obs = self._observe(
                parent=None,
                action="goto",
                label=f"goto {asked}",
                force_pattern=CATCH_ALL_PATTERN if probe and landed == asked else None,
            )
            sp.set(
                verdict=str(obs.verdict),
                to_screen=obs.screen.id if obs.screen else None,
                fp_after=obs.screen.state.fingerprint if obs.screen else None,
                new_screen=obs.is_new,
            )
            if redirected or (
                obs.screen is not None and obs.screen.pattern == CATCH_ALL_PATTERN and not probe
            ):
                self.soft_404.append(url)

    def _learn_catch_all(self, asked: str, landed: str) -> None:
        if landed != asked:
            self.catch_all = _CatchAll(mode="redirect", redirect_pattern=landed)
            return
        perc = self._now(force_pattern=CATCH_ALL_PATTERN)
        self.catch_all = _CatchAll(
            mode="render", keys=key_set(perc.reachable()), words=_words(perc.body_text)
        )

    def _pick(self) -> ScreenRec | None:
        live = [r for r in self.screens.values() if r.state.depth < self.crawl.max_depth and self._pending(r)]
        return min(live, key=lambda r: (r.spent // self.beam, r.state.depth, r.order)) if live else None

    def _explore(self) -> None:
        while True:
            reason = self._stop_check()
            if reason:
                self.stop_reason = reason
                return
            rec = self._pick()
            if rec is None:
                self.stop_reason = "frontier_empty"
                return
            if not self._ensure(rec):
                if self.replay_refused:
                    self._lost(rec, f"cannot re-establish {rec.pattern}")
                else:
                    self._give_up(rec, f"cannot re-establish {rec.pattern}")
                    rec.restore_failures += 1
                    if rec.restore_failures >= 2:
                        rec.tried.update(c.element.obs.id for c in rec.candidates)
                continue
            for _ in range(self.beam):
                cand = self._next_candidate(rec)
                if cand is None or self._stop_check():
                    break
                self._act(rec, cand)
                if self.breaker.consecutive >= self.breaker.threshold:
                    break

    def _crawl_seeds(self) -> None:
        seeds = list(self.crawl.seed_urls) or [self.cfg.base_url]
        for i, url in enumerate(seeds):
            if self._stop_check():
                return
            self._visit_seed(url)
            if i == 0 and not self._stop_check():
                self._visit_seed(urljoin(self.cfg.base_url, PROBE_PATH), probe=True)

    def run_live(self) -> CrawlSummary:
        started = now_iso()
        self.t0 = self.clock()
        if self.version_error:
            self.run.failures.record(FailureCode.CONFIG_INVALID, self.version_error)
        if self.budget <= 0:  # nothing may be spent: no browser, no request
            self.stop_reason = "action_budget"
            self._record_budget_stop()
            return self._finish(started, None)
        self._robots = self._fetch_robots()
        shell = self._shell_html()
        bundle_start = _shell_bundle_hash(shell, self.cfg.base_url) if shell else None
        self.app_version = self._detect_version(shell)
        with sync_playwright() as pw:
            try:
                self._open(pw)
            except PlaywrightError as exc:
                self.run.failures.record(FailureCode.DEP_MISSING, f"browser launch failed: {exc}"[:300])
                self._close_blackhole()
                raise
            self.watchdog = Watchdog(self.watchdog_token, self.crawl.wall_clock_s + WATCHDOG_GRACE_S)
            self.watchdog.start()
            try:
                self._validate_selectors()
            except ValueError:  # a bad selector in the target config: nothing was crawled
                self.watchdog.stop()
                self._close_browser()
                raise
            try:
                self._pin_manifest(bundle_start)
                self._crawl_seeds()
                self._explore()
            except Exception as exc:  # noqa: BLE001 - a crawl that dies still reports what it found
                self.stop_reason, self.error = "error", f"{type(exc).__name__}: {str(exc)[:300]}"
            finally:
                self.watchdog.stop()
                self._close_browser()
            if self.watchdog.fired:  # whatever the blocked call raised, the deadline is why the run ended
                self.stop_reason = "time_budget"
                self.error = "watchdog: a browser call did not return before the hard deadline"
        self._record_budget_stop()
        return self._finish(started, bundle_start)

    def _record_budget_stop(self) -> None:
        if self.stop_reason in ("action_budget", "time_budget"):
            left = sum(1 for r in self.screens.values() if self._pending(r))
            self.run.failures.record(
                FailureCode.BUDGET_EXHAUSTED,
                f"{self.stop_reason}: {self.actions_spent}/{self.budget} actions, "
                f"{left} screens with untried candidates",
            )

    def _close_blackhole(self) -> None:
        if self.blackhole is not None:
            self.network_refused = self.blackhole.refused
            self.blackhole.close()

    def _close_browser(self) -> None:
        try:
            if self.context is not None:
                self.context.close()
            if self.browser is not None:
                self.browser.close()
        except PlaywrightError:
            pass
        finally:
            self._close_blackhole()

    # ----------------------------------------------------------------------------------- finish
    def _pin_manifest(self, bundle_hash: str | None) -> None:
        width, height = self.crawl.viewport
        key = f"crawl_env_{self.subdir}" if self.subdir else "crawl_env"
        self.run.update_manifest(
            **{
                key: {
                    "viewport": [width, height],
                    "locale": self.crawl.locale,
                    "timezone": self.crawl.timezone,
                    "color_scheme": "light",
                    "service_workers": "block" if self.crawl.block_service_workers else "allow",
                    "user_agent": self.user_agent,
                    "chromium": self.browser.version,
                    "bundle_hash_start": bundle_hash,
                    "app_version": self.app_version,
                    "max_actions": self.budget,
                    "beam": self.beam,
                    "policy": self.policy.name,
                    "egress_allow": self.crawl.egress_allow,
                }
            }
        )

    def _soft_404_urls(self) -> list[str]:
        return list(dict.fromkeys(scrub_url(u) for u in self.soft_404))

    def _finish(self, started: str, bundle_start: str | None) -> CrawlSummary:
        self._infer_link_transitions()
        bundle_end = self._shell_hash() if self.browser is not None and not self.offline else None
        self.bundle_start, self.bundle_end = bundle_start, bundle_end
        screens = [r.state for r in self.screens.values()]
        summary = CrawlSummary(
            target=self.target,
            started_at=started,
            finished_at=now_iso(),
            screens=len(screens),
            elements=sum(len(s.elements) for s in screens),
            transitions=len(self.transitions),
            actions_spent=self.actions_spent,
            actions_budget=self.budget,
            wall_clock_s=round(self.clock() - self.t0, 2),
            blocked=self.counts["blocked"],
            suspect=self.counts["suspect"],
            disallowed=self.counts["disallowed"],
            egress_blocked=sum(self.egress_blocked.values()) + sum(self.network_refused.values()),
            policy_blocked=sum(self.policy_blocked.values()),
            soft_404=len(self._soft_404_urls()),
            restores=self.counts["restores"],
            stop_reason=self.stop_reason,  # type: ignore[arg-type]
            app_version=self.app_version,
            bundle_hash=bundle_start,
            bundle_hash_end=bundle_end,
            url_patterns=sorted({s.url_pattern for s in screens}),
            effort_by_url_pattern=dict(self.effort_by_pattern),
        )
        if self.run.run_dir.exists():
            self._rewrite("screens.jsonl", [s.model_dump(mode="json") for s in screens])
            self._rewrite("transitions.jsonl", [t.model_dump(mode="json") for t in self.transitions.values()])
            self.out.path("crawl", "summary.json").write_text(summary.model_dump_json(indent=2))
            self.out.path("crawl", "goal_effort.json").write_text(json.dumps(self.effort.to_json(), indent=2))
            self.out.path("crawl", "stats.json").write_text(json.dumps(self._stats(), indent=2, default=str))
        return summary

    def _rewrite(self, rel: str, rows: list[dict[str, Any]]) -> None:
        path = self.out.path("crawl", rel)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=True) + "\n" for r in rows), encoding="utf-8")
        tmp.replace(path)

    def _stats(self) -> dict[str, Any]:
        return {
            "policy": self.policy.name,
            "beam": self.beam,
            "llm": {
                "calls": getattr(self.policy, "calls", 0),
                "failures": getattr(self.policy, "failures", 0),
            },
            "screen_kinds": dict(Counter(r.state.kind for r in self.screens.values())),
            "egress_blocked": dict(self.egress_blocked),
            "egress_blocked_network": dict(self.network_refused),
            "redirects_refused": dict(self.redirects_refused),
            "policy_blocked": dict(self.policy_blocked),
            "soft_404": {
                "mode": self.catch_all.mode,
                "redirect_pattern": self.catch_all.redirect_pattern,
                "urls": self._soft_404_urls(),
            },
            "robots": self.robots_state,
            "counts": dict(self.counts),
            "rate_limited": self.rate_limited,
            "app_version": self.app_version,
            "bundle_changed": bool(
                self.bundle_end and self.bundle_start and self.bundle_end != self.bundle_start
            ),
            "error": self.error,
        }

    # -------------------------------------------------------------------------------- recorded
    def run_recorded(self, recorded_dir: str | Path) -> CrawlSummary:
        """Replay a RecordedSession offline: same perception, same classifier, no actions (case 18). The
        context has JavaScript disabled, the recorded markup is made inert first, every request is aborted,
        sockets are mocked and the browser sits behind the refusing proxy: a recording reaches nothing."""
        from ..access.source import RecordedSession

        started = now_iso()
        self.t0 = self.clock()
        self.replaying = self.offline = True
        session = RecordedSession(recorded_dir, target=self.target, anchor=self.crawl.anchor)
        root = Path(session.folder)
        with sync_playwright() as pw:
            self.browser = self._launch(pw)
            try:
                width, height = self.crawl.viewport
                self.context = self.browser.new_context(
                    viewport={"width": width, "height": height},
                    locale=self.crawl.locale,
                    timezone_id=self.crawl.timezone,
                    service_workers="block",
                    accept_downloads=False,
                    permissions=[],
                    java_script_enabled=False,
                )
                self.context.route("**/*", self._offline_route)
                self.context.route_web_socket("**/*", self._ws_route)
                self.page = self.context.new_page()
                for cap in session.captures():
                    self._replay_capture(cap, root)
                    if self.breaker.consecutive >= self.breaker.threshold:
                        self.stop_reason = "circuit_breaker"
                        break
            finally:
                self._close_browser()
        return self._finish(started, None)

    @staticmethod
    def _source_file(root: Path, rel: str | None) -> Path | None:
        """A recorded artifact path: relative to the session folder (access.source), or absolute (older
        sources). A path that escapes the folder is not read."""
        if not rel:
            return None
        path = Path(rel)
        path = path if path.is_absolute() else root / path
        try:
            resolved = path.resolve()
            if root.resolve() not in resolved.parents:
                return None
        except OSError:
            return None
        return path if path.is_file() else None

    def _rekeyed(self, cap: Capture, raw: str | None) -> Capture:
        """Capture ids hash (url, parent, action); a recording with neither would collapse every state of one
        url into one capture. The action is derived from the recorded HTML bytes (never from the position in
        the session), so reordering or inserting a state renumbers nothing. Urls are scrubbed."""
        content = raw if raw is not None else f"{cap.url}|{cap.status}|{cap.title}"
        action = cap.action or "recorded:" + sha(content)
        return make_capture(
            target=self.target,
            url=scrub_url(cap.url),
            final_url=scrub_url(cap.final_url),
            status=cap.status,
            headers=cap.headers,
            title=redact_text(cap.title, truncate=False) if cap.title else cap.title,
            interactive_count=cap.interactive_count,
            source_kind="recorded",
            parent_id=cap.parent_id,
            action=action,
            access=cap.access,
            captured_at=cap.captured_at,
        )

    def _replay_capture(self, cap: Capture, root: Path) -> None:
        src = self._source_file(root, cap.html_path)
        raw = src.read_text(encoding="utf-8", errors="replace") if src is not None else None
        cap = self._rekeyed(cap, raw)
        denied = url_denied(cap.final_url, self.crawl)
        if denied is not None and cap.access.verdict == AccessVerdict.OK:
            access = AccessResult(
                verdict=AccessVerdict.DISALLOWED,
                signals=["deny_url_pattern"],
                reason=f"{urlsplit(cap.final_url).path} matches deny pattern {denied}",
            )
            self._count_non_ok(cap.model_copy(update={"access": access}), None)
            return
        if cap.access.verdict != AccessVerdict.OK or raw is None:
            self._count_non_ok(cap, raw)
            return
        html = sanitise_recorded_html(raw)
        base = f'<base href="{html_lib.escape(cap.final_url, quote=True)}">'
        html = (
            re.sub(r"(?i)<head\b[^>]*>", lambda m: m.group(0) + base, html, count=1)
            if re.search(r"(?i)<head\b", html)
            else base + html
        )
        self.page.set_content(html, wait_until="domcontentloaded")
        raw_page = perceive.collect(self.page, self.widgets)
        raw_page["url"] = cap.final_url
        raw_page["title"] = raw_page.get("title") or cap.title or ""
        perc = perceive.build(raw_page, target=self.target, pattern=url_pattern(cap.final_url))
        self._note_version(perc.body_text)
        fp = self._fingerprint(perc)
        self.last_status, self.last_headers = cap.status, dict(cap.headers)
        result = self._classify(perc, fp, rung="RECORDED", html=html)
        if not perc.popups and perc.pattern != CATCH_ALL_PATTERN:
            self.url_fp[perc.url] = fp
        cap = cap.model_copy(update={"access": result, "interactive_count": perc.interactive_count})
        if result.verdict != AccessVerdict.OK:
            self._count_non_ok(cap, raw)
            return
        existing = self._match_existing(perc, fp)
        if existing is None:
            dom, png = self._save_artifacts(stable_id("scr", self.target, fp), shot=True)
            cap = cap.model_copy(update={"html_path": dom, "screenshot_path": png})
            self._create(perc, fp, cap, None, [], "recorded", dom, png)
        else:
            cap = cap.model_copy(
                update={
                    "html_path": existing.state.dom_path,
                    "screenshot_path": existing.state.screenshot,
                }
            )
            self._merge(existing, perc, None)
        self._persist_capture(cap)
        self.breaker.record(AccessVerdict.OK)

    def _count_non_ok(self, cap: Capture, raw: str | None) -> None:
        """A recorded state that is not OK: counted, persisted without the source path, and the (inert,
        redacted) markup kept next to the run so a reviewer can see why."""
        key = {"BLOCKED": "blocked", "SUSPECT": "suspect", "DISALLOWED": "disallowed"}.get(
            str(cap.access.verdict)
        )
        dom_rel = None
        if raw is not None and self.run.run_dir.exists():
            stem = "suspect_" + sha(cap.id)
            body = redact_text(sanitise_recorded_html(raw), truncate=False)
            self.out.path("crawl", "dom", f"{stem}.html").write_text(body, encoding="utf-8")
            dom_rel = self._rel("dom", f"{stem}.html")
        cap = cap.model_copy(update={"html_path": dom_rel, "screenshot_path": None})
        if key and cap.id not in self.captured:
            self.counts[key] += 1
        self._persist_capture(cap)
        self.breaker.record(cap.access.verdict)


def run_crawl(
    run: RunContext,
    cfg: TargetConfig,
    llm: Any,
    *,
    goals: Sequence[str] | None = None,
    source: str = "live",
    recorded_dir: str | Path | None = None,
    max_actions: int | None = None,
    artifact_subdir: str | None = None,
) -> CrawlSummary:
    """Crawl the target and write crawl/{captures,screens,transitions}.jsonl, summary.json, goal_effort.json,
    stats.json, screens/*.png and dom/*.html under the run directory. `llm=None` runs the heuristic policy.

    `artifact_subdir` redirects every one of those files into crawl/<artifact_subdir>/ and leaves the main
    crawl's artifacts untouched (bounded goal-directed re-crawls)."""
    policy: Policy = LLMPolicy(llm) if llm is not None else HeuristicPolicy()
    explorer = Explorer(
        run, cfg, policy, goals=goals, max_actions=max_actions, artifact_subdir=artifact_subdir
    )
    if source == "recorded":
        if recorded_dir is None:
            raise ValueError("source='recorded' needs recorded_dir")
        return explorer.run_recorded(recorded_dir)
    return explorer.run_live()
