"""Access classifier: is this response the app, or a wall in front of it?

Implements D11 (detect, quarantine, degrade; nothing here tries to get past a challenge), D13 (classify on
block signals, never on vendor presence or body size), D32 (headers first, on any status) and D33 (vendor rows
beyond Cloudflare and AWS are synthesized from documented markers). Cases 17, 67, 68, 69, 70.

Verdict semantics. BLOCKED needs a hard marker. SUSPECT is a soft guard: needs review, never enters the graph.
OK means "no block signal", not "healthy": a 404 or 500 is OK here because it is not a wall. Vendor presence
(`server: cloudflare`, `cf-ray`, `x-vercel-id`, cookies, a JS-detections beacon) is never a verdict: it labels
the vendor on a SUSPECT result and, for the numeric Cloudflare error code only, corroborates the marker.

What may fire, and when (RECON section 3 is the table; wave-1 review M1, M2, m2):
- header rules (`cf-mitigated`, `x-amzn-waf-action`, `x-vercel-mitigated`) fire on any status and any content
  type;
- title and body rules fire only when the body is expected to be markup (an HTML-like or unknown content
  type). A text/plain llms-full.txt or a JSON body is data: a code-fenced `<title>Access Denied</title>` in it
  is prose;
- a single-phrase body marker (PerimeterX "Press & Hold", Akamai "Access Denied" + "Reference #", Imperva's
  incident phrase, DataDome's captcha host) is ordinary copy at 200, so it is gated to its documented status
  (403; Imperva also at 200 when its own header or cookie is present);
- the Cloudflare interstitial needs the title AND the interstitial's own markers; the JS-detections beacon
  (`/cdn-cgi/challenge-platform/scripts/jsd/`) is on every JSD-enabled page and is never one.

Known gap (limitation, not a rule): an interstitial delivered at 200 with no header, a localized title and no
Cloudflare marker is OK here; the anchor and fingerprint-collapse guards are what catch it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

from ..models import AccessResult, AccessVerdict
from ..targets import AnchorConfig

SOFT_STATUSES = frozenset({202, 403, 405, 429, 503})   # HTML with one of these where an app is expected
COLLAPSE_MIN_URLS = 3                                  # distinct pages sharing one fingerprint = a collapse
ROBOTS_MAX_RULES_PER_GROUP = 1000                      # rules past this, within one group, are not read
ROBOTS_MAX_URL_CHARS = 4096                            # longer urls are refused (fail closed), not matched
_WAF_ACTIONS = frozenset({"challenge", "captcha"})     # x-amzn-waf-action values: 202 and 405 respectively
_VERCEL_ACTIONS = frozenset({"challenge", "deny"})     # x-vercel-mitigated values
_CF_BLOCK_STATUSES = frozenset({403, 429})             # 1020 is served as 403, 1015 (rate limit) as 429
_ROBOTS_MAX_CHARS = 512 * 1024                         # RFC 9309: parse at least 500 KiB

# Patterns that scan a body bound every quantifier, so no input costs more than a small constant per start
# offset; the title patterns run on one title of at most 300 characters.
_TITLE = re.compile(r"<title(?=[\s>/])[^>]{0,200}>\s*([^<]{0,300})", re.IGNORECASE | re.ASCII)
_TITLE_STRONG = re.compile(      # a phrase that is the whole title, or the title before a site-name suffix
    r"(?P<phrase>just a moment|attention required|access denied|robot check|are you a (?:human|robot)|"
    r"verify(?:ing)? (?:that )?you are (?:a )?human|checking your browser(?: before accessing \S+)?|"
    r"pardon our interruption|access to this page has been denied|vercel security checkpoint)"
    r"[!.?\u2026]*(?:\s*[|\u2022\u00b7\u2013\u2014]\s*\S.*|\s+-\s+\S.*)?\s*$"
)
_TITLE_WEAK = re.compile(        # phrases that also name ordinary UI ("One more step - Acme"): whole title
    r"(?P<phrase>security check|one more step|request blocked)[!.?\u2026]*\s*$"
)
_CF_BLOCK_TITLE = re.compile(r"attention required[!.]*(?:\s*\|\s*cloudflare)?$")
_CF_CODES = r"100[6-9]|101[0-2]|1015|1020"        # block pages: banned ip, country, signature; rate limit
_CF_CODE = re.compile(
    rf"error(?:\s{{1,8}}code)?[\s:]{{0,40}}(?:<[^>]{{0,80}}>\s{{0,40}}){{0,8}}(?:{_CF_CODES})\b"
    rf"|cf-error-code[^>]{{0,40}}>\s{{0,40}}(?:{_CF_CODES})\b"
)
_CF_INTERSTITIAL = re.compile(r"_cf_chl_opt|__cf_chl_|/cdn-cgi/challenge-platform/h/[bg]/orchestrate/")
# Akamai escapes the punctuation of "Reference #"
_AKAMAI_REF = re.compile(r"reference(?:\s|&#32;|&#x20;|&nbsp;){1,16}(?:#|&#35;|&#x23;)")
_PX_PRESS_HOLD = re.compile(r"press\s{1,64}(?:&amp;|&#38;|&#x26;|&)\s{1,64}hold")
_PX_COLLECTOR = re.compile(r"collector-[a-z0-9]{1,64}\.perimeterx\.net")
_IMPERVA = re.compile(r"request\s{1,64}unsuccessful\.\s{1,64}incapsula\s{1,64}incident\s{1,64}id")
_IMPERVA_COOKIE = re.compile(r"(?:visid_incap|incap_ses)_")
_AMAZON_ROBOT = re.compile(
    r"type\s{1,64}the\s{1,64}characters\s{1,64}you\s{1,64}see\s{1,64}in\s{1,64}this\s{1,64}image"
)


def _as_text(body: str | bytes | bytearray | None) -> str:
    if body is None:
        return ""
    if isinstance(body, (bytes, bytearray)):
        return bytes(body).decode("utf-8", errors="replace")
    return body


def extract_title(html: str | bytes | None) -> str | None:
    m = _TITLE.search(_as_text(html))
    return m.group(1).strip() if m else None


def _header_text(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(_header_text(v) for v in value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("latin-1")
    return str(value)


def normalise_headers(items: Iterable[tuple[object, object]]) -> dict[str, str]:
    """Lower-case names and str values. List values are joined with ', ' and so are duplicate names (any
    case), as HTTP defines for repeated fields: {'CF-Mitigated': 'managed', 'cf-mitigated': 'challenge'}
    keeps both."""
    out: dict[str, str] = {}
    for name, value in items:
        key = str(name).lower()
        text = _header_text(value)
        out[key] = f"{out[key]}, {text}" if key in out else text
    return out


def _tokens(value: str) -> set[str]:
    return {t.strip() for t in value.lower().split(",") if t.strip()}


def _expects_markup(h: dict[str, str]) -> bool:
    ctype = h.get("content-type", "").lower()
    return "html" in ctype if ctype else True       # unknown content type: do not exempt it


def _imperva_marker(h: dict[str, str]) -> bool:
    return ("x-iinfo" in h or "imperva" in h.get("x-cdn", "").lower()
            or _IMPERVA_COOKIE.search(h.get("set-cookie", "")) is not None)


def _hard_signals(status: int | None, h: dict[str, str], low: str, title: str,
                  markup: bool) -> list[tuple[str, str]]:
    """(vendor, signal) pairs for hard block markers. `low` is the lower-cased body, `title` the lower-cased,
    whitespace-collapsed title, `h` has lower-case names, `markup` says whether title and body rules apply."""
    out: list[tuple[str, str]] = []
    server = h.get("server", "").lower()
    # D32: headers first, whatever the status or content type (a challenge can arrive as 200, 202, 403, 405)
    if "challenge" in _tokens(h.get("cf-mitigated", "")):
        out.append(("cloudflare", "header:cf-mitigated=challenge"))
    for action in sorted(_tokens(h.get("x-amzn-waf-action", "")) & _WAF_ACTIONS):
        out.append(("aws_waf", f"header:x-amzn-waf-action={action}"))
    for action in sorted(_tokens(h.get("x-vercel-mitigated", "")) & _VERCEL_ACTIONS):
        out.append(("vercel", f"header:x-vercel-mitigated={action}"))
    # DataDome's XHR challenge is JSON ({"url": "https://geo.captcha-delivery.com/..."}) and the host is
    # specific to it, so this rule alone also reads JSON. Never x-datadome-* (internal API headers).
    readable = markup or "json" in h.get("content-type", "").lower()
    if status == 403 and readable and "captcha-delivery.com" in low:
        out.append(("datadome", "body:captcha-delivery+403"))
    if not markup:
        return out
    # Cloudflare interstitial: the title AND the interstitial's own markers. The challenge-platform path
    # alone is the JS-detections beacon that ordinary Cloudflare-fronted pages carry (demo.usememos.com does).
    if title.startswith("just a moment") and _CF_INTERSTITIAL.search(low):
        out.append(("cloudflare", "title:just-a-moment+interstitial-marker"))
    # Cloudflare Block page (case 68): a separate rule, it carries no cf-mitigated header
    cf_hint = "cloudflare" in server or "cf-ray" in h or "cloudflare" in low or "ray id" in low
    if status in _CF_BLOCK_STATUSES and (
            _CF_BLOCK_TITLE.match(title) or (cf_hint and _CF_CODE.search(low))):
        out.append(("cloudflare", "body:cloudflare-block-page"))
    if status == 403 and "akamaighost" in server and "access denied" in low and _AKAMAI_REF.search(low):
        out.append(("akamai", "body:access-denied+reference"))
    if status == 403 and _PX_PRESS_HOLD.search(low):
        out.append(("perimeterx", "body:press-and-hold"))
    if status == 403 and _PX_COLLECTOR.search(low):
        out.append(("perimeterx", "body:px-collector+403"))
    if _IMPERVA.search(low) and (status == 403 or (status == 200 and _imperva_marker(h))):
        out.append(("imperva", "body:incapsula-incident-id"))
    if _AMAZON_ROBOT.search(low) and "/errors/validatecaptcha" in low:
        out.append(("amazon", "body:robot-check"))
    return out


def _presence_vendor(h: dict[str, str]) -> str | None:
    """Who fronts the response. Attribution only: never a verdict input."""
    server = h.get("server", "").lower()
    if "cloudflare" in server or "cf-ray" in h or "cf-cache-status" in h:
        return "cloudflare"
    if "akamai" in server:
        return "akamai"
    if "x-amz-cf-id" in h or "cloudfront" in h.get("via", "").lower():
        return "cloudfront"
    if "x-iinfo" in h:
        return "imperva"
    return "vercel" if "x-vercel-id" in h else None


def _title_phrase(title: str) -> str | None:
    """The challenge phrase a whole (lower-cased, whitespace-collapsed) title consists of, if any."""
    m = _TITLE_STRONG.match(title) or _TITLE_WEAK.match(title)
    return m.group("phrase") if m else None


def _anchor_signals(title: str, interactive_count: int | None, anchor: AnchorConfig | None) -> list[str]:
    if anchor is None:
        return []
    out: list[str] = []
    if anchor.title_regex is not None:
        try:
            if re.search(anchor.title_regex, title) is None:
                out.append("anchor:title-mismatch")
        except re.error:
            out.append("anchor:title-regex-invalid")        # fail toward review, not toward the graph
    if interactive_count is not None and interactive_count < anchor.min_interactive:
        out.append(f"anchor:interactive={interactive_count}<{anchor.min_interactive}")
    return out


def _page_key(url: str) -> str:
    try:
        sp = urlsplit(url)
    except ValueError:
        return url
    return f"{sp.scheme.lower()}://{sp.netloc.lower()}{sp.path or '/'}"      # query and fragment ignored


def _exempt_matcher(patterns: Sequence[str] | None):
    """Predicate over urls. A pattern that is not a valid regex is dropped: stricter guard, not looser."""
    compiled = []
    for pattern in patterns or ():
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            continue
    return lambda url: any(rx.search(url) for rx in compiled)


def _collapse_signals(url: str, prior_fingerprints: Mapping[str, str] | None,
                      exempt_patterns: Sequence[str] | None) -> list[str]:
    """Many distinct pages sharing one fingerprint: the crawl is looking at one wall (or one catch-all).

    Judged on the capture's own page: its fingerprint is the one `prior_fingerprints` holds for its url
    (query and fragment ignored), so the caller should include its own url -> fingerprint. A url that is
    absent is not implicated by other pages' collapse (no signal). Several fingerprints for one page: the
    largest group wins.
    """
    if not prior_fingerprints:
        return []
    exempt = _exempt_matcher(exempt_patterns)
    if exempt(url):
        return []
    key = _page_key(url)
    groups: dict[str, set[str]] = {}
    own: set[str] = set()
    for u, fp in prior_fingerprints.items():
        if exempt(u):
            continue
        page = _page_key(u)
        groups.setdefault(fp, set()).add(page)
        if page == key:
            own.add(fp)
    size = max((len(groups[fp]) for fp in own), default=0)
    return [f"fingerprint-collapse:{size}-urls"] if size >= COLLAPSE_MIN_URLS else []


def _classify(*, status: int | None, headers: Mapping[str, object], title: str | None,
              body: str | bytes | None, interactive_count: int | None, anchor: AnchorConfig | None, url: str,
              rung: str, prior_fingerprints: Mapping[str, str] | None,
              exempt_patterns: Sequence[str] | None) -> AccessResult:
    h = normalise_headers((headers or {}).items())
    text = _as_text(body)
    markup = _expects_markup(h)
    page_title = title if title is not None else ((extract_title(text) or "") if markup else "")
    title_low = " ".join(page_title.lower().split()) if markup else ""
    hard = _hard_signals(status, h, text.lower(), title_low, markup)
    if hard:
        vendor, signals = hard[0][0], [s for _, s in hard]
        return AccessResult(verdict=AccessVerdict.BLOCKED, vendor=vendor, signals=signals, rung=rung,
                            reason=f"{vendor} block or challenge: {', '.join(signals)}")
    soft: list[str] = []
    if status in SOFT_STATUSES and markup:
        soft.append(f"status:{status}")
    if phrase := _title_phrase(title_low):
        soft.append(f"title:{phrase}")
    soft += _anchor_signals(page_title, interactive_count, anchor)
    soft += _collapse_signals(url, prior_fingerprints, exempt_patterns)
    if soft:
        return AccessResult(verdict=AccessVerdict.SUSPECT, vendor=_presence_vendor(h), signals=soft,
                            rung=rung, reason=f"soft guard tripped, no hard marker: {', '.join(soft)}")
    return AccessResult(verdict=AccessVerdict.OK, rung=rung)


def classify_http(status: int | None, headers: Mapping[str, str], body: str, url: str) -> AccessResult:
    """Classify a plain HTTP response (L0). Used by doctor and by any non-browser fetch.

    `body` may also be bytes (decoded as UTF-8, undecodable bytes replaced) or None (empty).
    """
    return _classify(status=status, headers=headers, title=None, body=body, interactive_count=None,
                     anchor=None, url=url, rung="L0", prior_fingerprints=None, exempt_patterns=None)


def classify_capture(*, status: int | None, headers: Mapping[str, str], title: str | None, body_text: str,
                     interactive_count: int | None, anchor: AnchorConfig | None, url: str, rung: str = "L0",
                     prior_fingerprints: Mapping[str, str] | None = None,
                     collapse_exempt_patterns: Sequence[str] | None = None) -> AccessResult:
    """Classify a browser capture after hydration. `body_text` should be the page HTML (page.content()):
    the Cloudflare interstitial rule needs the script markers, which visible text does not contain.

    Fingerprint collapse (`prior_fingerprints`: url -> fingerprint of every page seen so far, the capture's
    own url included). The capture is SUSPECT when its own page shares a fingerprint with at least
    COLLAPSE_MIN_URLS distinct pages (queries and fragments ignored). Notes for callers:

    - a url that is not in the mapping is not judged (no signal): a capture cannot be implicated by other
      pages. A modal or popup capture carries its page's url, so it is judged by that page's group (the
      fingerprint the caller stored for the page, not the modal's own);
    - `collapse_exempt_patterns` is a list of regexes (`re.search` on the full url) for routes whose pages are
      *expected* to share a fingerprint: id-templated routes (`/e/:id`, uuid or numeric slugs, which url
      patterns collapse by design) and catch-all soft-404 routes. Matching urls are ignored by this guard
      entirely: they raise no signal and do not count toward another page's group. A pattern that is not a
      valid regex is dropped (stricter, never looser). A wall confined to exempt routes is still caught by the
      hard markers and the anchor.
    """
    return _classify(status=status, headers=headers, title=title, body=body_text,
                     interactive_count=interactive_count, anchor=anchor, url=url, rung=rung,
                     prior_fingerprints=prior_fingerprints, exempt_patterns=collapse_exempt_patterns)


# ------------------------------------------------------------------------------------------------ robots
_STAR_RUN = re.compile(r"\*+")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_HEX = frozenset(b"0123456789abcdefABCDEF")


@dataclass(frozen=True, slots=True)
class _Rule:
    allow: bool
    parts: tuple[str, ...]      # canonical pattern without its trailing `$`, split on runs of `*`
    anchored: bool              # the pattern ended in `$`
    size: int                   # canonical pattern length: the specificity that settles Allow vs Disallow


_Group = tuple[tuple[str, ...], tuple[_Rule, ...]]


def _agent_token(value: str) -> str:
    m = re.match(r"[A-Za-z0-9_-]*", value.strip())
    return m.group(0).lower() if m else ""


def _canonical(text: str) -> str:
    """RFC 9309 section 2.2.2 comparison form: non-ASCII octets, controls and spaces are percent-encoded, a
    percent-encoded *unreserved* ASCII octet is decoded, and every other escape (%2F, %3F, %24, ...) is kept
    encoded (upper-case hex) so that it is compared as data and never takes its decoded meaning."""
    if text.isascii() and text.isprintable() and " " not in text and "%" not in text:
        return text
    data = text.encode("utf-8", "surrogatepass")
    out: list[str] = []
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b == 0x25 and i + 2 < n and data[i + 1] in _HEX and data[i + 2] in _HEX:
            value = int(data[i + 1:i + 3], 16)
            out.append(chr(value) if chr(value) in _UNRESERVED else f"%{value:02X}")
            i += 3
            continue
        out.append(chr(b) if 0x21 <= b <= 0x7E and b != 0x25 else f"%{b:02X}")
        i += 1
    return "".join(out)


def _make_rule(allow: bool, value: str) -> _Rule:
    pattern = _canonical(value)
    anchored = pattern.endswith("$")
    return _Rule(allow, tuple(_STAR_RUN.split(pattern[:-1] if anchored else pattern)), anchored, len(pattern))


@lru_cache(maxsize=16)
def _parse_robots(text: str) -> tuple[_Group, ...]:
    """Groups as (agent tokens, rules). Only allow/disallow lines end an agent block; empty values are
    skipped; at most ROBOTS_MAX_RULES_PER_GROUP rules are read per group. Lines end at LF, CR or CRLF only."""
    groups: list[tuple[list[str], list[_Rule]]] = []
    in_agents = False
    normalised = text[:_ROBOTS_MAX_CHARS].lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    for raw in normalised.split("\n"):
        field, sep, value = raw.split("#", 1)[0].partition(":")
        if not sep:
            continue
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if not in_agents or not groups:
                groups.append(([], []))
            groups[-1][0].append(_agent_token(value) if value != "*" else "*")
            in_agents = True
        elif field in ("allow", "disallow") and groups:
            in_agents = False
            if value and len(groups[-1][1]) < ROBOTS_MAX_RULES_PER_GROUP:
                groups[-1][1].append(_make_rule(field == "allow", value))
    return tuple((tuple(agents), tuple(rules)) for agents, rules in groups)


def _rule_matches(rule: _Rule, path: str) -> bool:
    """Glob match without backtracking: `*` is any run of characters, a trailing `$` anchors the end, and
    the pattern otherwise matches as a prefix of the path. Greedy leftmost search of the middle segments
    leaves the longest remainder for the last one, so one pass is exact; cost is linear in len(path) +
    len(pattern)."""
    first, *rest = rule.parts
    if not path.startswith(first):
        return False
    pos = len(first)
    if not rest:
        return pos == len(path) if rule.anchored else True
    *middle, last = rest
    for segment in middle:
        found = path.find(segment, pos)
        if found < 0:
            return False
        pos = found + len(segment)
    if rule.anchored:
        return len(path) - len(last) >= pos and path.endswith(last)
    return path.find(last, pos) >= 0


def robots_allows(robots_txt: str | None, user_agent: str, url: str) -> AccessVerdict:
    """RFC 9309 matching: group by product token with `*` fallback, longest rule wins, Allow wins ties.

    None means robots.txt could not be read: DISALLOWED (case 69), as is a url that cannot be parsed or is
    longer than ROBOTS_MAX_URL_CHARS (fail closed; also bounds the matching cost). An empty file allows
    everything.
    Percent-encoded reserved characters are compared encoded (`/a%2Fb` is not `/a/b`); rules past
    ROBOTS_MAX_RULES_PER_GROUP in one group are not read.
    """
    if robots_txt is None or not isinstance(url, str) or len(url) > ROBOTS_MAX_URL_CHARS:
        return AccessVerdict.DISALLOWED
    try:
        sp = urlsplit(url)
    except ValueError:
        return AccessVerdict.DISALLOWED
    token = _agent_token(user_agent)
    groups = _parse_robots(robots_txt)
    chosen = [g for g in groups if token and token in g[0]] or [g for g in groups if "*" in g[0]]
    path = _canonical((sp.path or "/") + (f"?{sp.query}" if sp.query else ""))
    best: tuple[int, bool] | None = None          # (pattern length, allow)
    for _, rules in chosen:
        for rule in rules:
            if _rule_matches(rule, path) and (best is None or (rule.size, rule.allow) > best):
                best = (rule.size, rule.allow)
    return AccessVerdict.DISALLOWED if best is not None and not best[1] else AccessVerdict.OK


class Breaker:
    """Circuit breaker for the crawl: trips after `threshold` consecutive BLOCKED verdicts (case 17).

    OK resets the run. SUSPECT and DISALLOWED are neutral: a soft guard is not evidence the wall is up, and a
    robots skip never touched the site.
    """

    def __init__(self, threshold: int = 2) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        self.threshold = threshold
        self.consecutive = 0

    def record(self, verdict: AccessVerdict) -> bool:
        if verdict == AccessVerdict.BLOCKED:
            self.consecutive += 1
        elif verdict == AccessVerdict.OK:
            self.consecutive = 0
        return self.consecutive >= self.threshold
