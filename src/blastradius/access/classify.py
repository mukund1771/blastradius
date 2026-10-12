"""Access classifier: is this response the app, or a wall in front of it?

Implements D11 (detect, quarantine, degrade; nothing here tries to get past a challenge), D13 (classify on
block signals, never on vendor presence or body size), D32 (headers first, on any status) and D33 (vendor rows
beyond Cloudflare and AWS are synthesized from documented markers). Cases 17, 67, 68, 69.

Verdict semantics. BLOCKED needs a hard marker. SUSPECT is a soft guard: needs review, never enters the graph.
OK means "no block signal", not "healthy": a 404 or 500 is OK here because it is not a wall. Vendor presence
(`server: cloudflare`, `cf-ray`, `x-vercel-id`, cookies, a JS-detections beacon) is never a verdict: it labels
the vendor on a SUSPECT result and, for the numeric Cloudflare error code only, corroborates the marker.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit

from ..models import AccessResult, AccessVerdict
from ..targets import AnchorConfig

SOFT_STATUSES = frozenset({202, 403, 405, 429, 503})   # HTML with one of these where an app is expected
COLLAPSE_MIN_URLS = 3                                  # distinct pages sharing one fingerprint = a collapse
_WAF_ACTIONS = frozenset({"challenge", "captcha"})     # x-amzn-waf-action values: 202 and 405 respectively
_CF_BLOCK_STATUSES = frozenset({403, 429})             # 1020 is served as 403, 1015 (rate limit) as 429
_ROBOTS_MAX_CHARS = 512 * 1024                         # RFC 9309: parse at least 500 KiB

_TITLE = re.compile(r"<[tT][iI][tT][lL][eE][^>]{0,200}>\s*([^<]{0,300})")     # bounded: no quadratic scans
_CHALLENGE_TITLE = re.compile(
    r"(just a moment|attention required|access denied|robot check|are you a (?:human|robot)|"
    r"verify(?:ing)? (?:that )?you are (?:a )?human|security check|checking your browser|one more step|"
    r"pardon our interruption|access to this page has been denied|request blocked)"
)
_CF_CODE = re.compile(
    r"error(?:\s+code)?\s*:?\s*(?:<[^>]{0,80}>\s*)*(?:1020|1015)\b|cf-error-code[^>]{0,40}>\s*(?:1020|1015)\b"
)
_AKAMAI_REF = re.compile(r"reference(?:\s|&#32;|&#x20;|&nbsp;)+(?:#|&#35;|&#x23;)")   # Akamai escapes " #"
_PX_PRESS_HOLD = re.compile(r"press\s+(?:&|&amp;)\s+hold")
_PX_COLLECTOR = re.compile(r"collector-[a-z0-9]+\.perimeterx\.net")
_IMPERVA = re.compile(r"request\s+unsuccessful\.\s+incapsula\s+incident\s+id")
_AMAZON_ROBOT = re.compile(r"type\s+the\s+characters\s+you\s+see\s+in\s+this\s+image")


def extract_title(html: str) -> str | None:
    m = _TITLE.search(html)
    return m.group(1).strip() if m else None


def _tokens(value: str) -> set[str]:
    return {t.strip() for t in value.lower().split(",") if t.strip()}


def _hard_signals(status: int | None, h: dict[str, str], low: str, title_low: str) -> list[tuple[str, str]]:
    """(vendor, signal) pairs for hard block markers. `low` is the lower-cased body, `h` has lower-case names."""
    out: list[tuple[str, str]] = []
    server = h.get("server", "").lower()
    # D32: headers first, whatever the status (a WAF challenge can arrive as 200, 202, 403 or 405)
    if "challenge" in _tokens(h.get("cf-mitigated", "")):
        out.append(("cloudflare", "header:cf-mitigated=challenge"))
    for action in sorted(_tokens(h.get("x-amzn-waf-action", "")) & _WAF_ACTIONS):
        out.append(("aws_waf", f"header:x-amzn-waf-action={action}"))
    # Cloudflare interstitial: needs both. The challenge-platform path alone is the JS-detections beacon that
    # ordinary Cloudflare-fronted pages carry (demo.usememos.com does).
    if ("just a moment" in low or "just a moment" in title_low) and "/cdn-cgi/challenge-platform/" in low:
        out.append(("cloudflare", "body:just-a-moment+challenge-platform"))
    # Cloudflare Block page (case 68): a separate rule, it carries no cf-mitigated header
    cf_hint = "cloudflare" in server or "cf-ray" in h or "cloudflare" in low or "ray id" in low
    if status in _CF_BLOCK_STATUSES and (
            title_low.startswith("attention required") or (cf_hint and _CF_CODE.search(low))):
        out.append(("cloudflare", "body:cloudflare-block-page"))
    if "akamaighost" in server and "access denied" in low and _AKAMAI_REF.search(low):
        out.append(("akamai", "body:access-denied+reference"))
    if status == 403 and "captcha-delivery.com" in low:         # never x-datadome-* (internal API headers)
        out.append(("datadome", "body:captcha-delivery+403"))
    if _PX_PRESS_HOLD.search(low):
        out.append(("perimeterx", "body:press-and-hold"))
    if status == 403 and _PX_COLLECTOR.search(low):
        out.append(("perimeterx", "body:px-collector+403"))
    if _IMPERVA.search(low):
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


def _expects_html(h: dict[str, str]) -> bool:
    ctype = h.get("content-type", "").lower()
    return "html" in ctype if ctype else True       # unknown content type: do not exempt it


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


def _collapse_signals(url: str, prior_fingerprints: Mapping[str, str] | None) -> list[str]:
    """Many distinct pages sharing one fingerprint: the crawl is looking at one wall (or one catch-all).

    The signature has no fingerprint for the capture itself, so the caller should include its own
    url -> fingerprint in the mapping. If the url is absent, any collapse among the priors flags it.
    """
    if not prior_fingerprints:
        return []
    groups: dict[str, set[str]] = {}
    fp_of: dict[str, str] = {}
    for u, fp in prior_fingerprints.items():
        groups.setdefault(fp, set()).add(_page_key(u))
        fp_of[_page_key(u)] = fp
    own = fp_of.get(_page_key(url))
    size = len(groups[own]) if own is not None else max(len(g) for g in groups.values())
    return [f"fingerprint-collapse:{size}-urls"] if size >= COLLAPSE_MIN_URLS else []


def _classify(*, status: int | None, headers: Mapping[str, str], title: str | None, body: str,
              interactive_count: int | None, anchor: AnchorConfig | None, url: str, rung: str,
              prior_fingerprints: Mapping[str, str] | None) -> AccessResult:
    h = {str(k).lower(): str(v) for k, v in headers.items()}
    page_title = title if title is not None else (extract_title(body) or "")
    hard = _hard_signals(status, h, body.lower(), page_title.lower().strip())
    if hard:
        vendor, signals = hard[0][0], [s for _, s in hard]
        return AccessResult(verdict=AccessVerdict.BLOCKED, vendor=vendor, signals=signals, rung=rung,
                            reason=f"{vendor} block or challenge: {', '.join(signals)}")
    soft: list[str] = []
    if status in SOFT_STATUSES and _expects_html(h):
        soft.append(f"status:{status}")
    if m := _CHALLENGE_TITLE.match(page_title.lower().strip()):
        soft.append(f"title:{m.group(1)}")
    soft += _anchor_signals(page_title, interactive_count, anchor)
    soft += _collapse_signals(url, prior_fingerprints)
    if soft:
        return AccessResult(verdict=AccessVerdict.SUSPECT, vendor=_presence_vendor(h), signals=soft, rung=rung,
                            reason=f"soft guard tripped, no hard marker: {', '.join(soft)}")
    return AccessResult(verdict=AccessVerdict.OK, rung=rung)


def classify_http(status: int | None, headers: Mapping[str, str], body: str, url: str) -> AccessResult:
    """Classify a plain HTTP response (L0). Used by doctor and by any non-browser fetch."""
    return _classify(status=status, headers=headers, title=None, body=body, interactive_count=None, anchor=None,
                     url=url, rung="L0", prior_fingerprints=None)


def classify_capture(*, status: int | None, headers: Mapping[str, str], title: str | None, body_text: str,
                     interactive_count: int | None, anchor: AnchorConfig | None, url: str, rung: str = "L0",
                     prior_fingerprints: Mapping[str, str] | None = None) -> AccessResult:
    """Classify a browser capture after hydration. `body_text` should be the page HTML (page.content()):
    the Cloudflare challenge rule needs the script path, which visible text does not contain."""
    return _classify(status=status, headers=headers, title=title, body=body_text,
                     interactive_count=interactive_count, anchor=anchor, url=url, rung=rung,
                     prior_fingerprints=prior_fingerprints)


# ------------------------------------------------------------------------------------------------ robots
def _agent_token(value: str) -> str:
    m = re.match(r"[A-Za-z0-9_-]*", value.strip())
    return m.group(0).lower() if m else ""


def _parse_robots(text: str) -> list[tuple[list[str], list[tuple[bool, str]]]]:
    """Groups as (agent tokens, [(allow, pattern)]). Only allow/disallow lines end an agent block."""
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    in_agents = False
    for raw in text[:_ROBOTS_MAX_CHARS].lstrip("\ufeff").splitlines():
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
            groups[-1][1].append((field == "allow", value))
    return groups


def _rule_matches(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    parts = (pattern[:-1] if anchored else pattern).split("*")
    regex = ".*".join(re.escape(unquote(p)) for p in parts) + (r"\Z" if anchored else "")
    return re.match(regex, path, re.DOTALL) is not None


def robots_allows(robots_txt: str | None, user_agent: str, url: str) -> AccessVerdict:
    """RFC 9309 matching: group by product token with `*` fallback, longest rule wins, Allow wins ties.

    None means robots.txt could not be read: DISALLOWED (case 69), as is a url that cannot be parsed. An empty
    file allows everything.
    """
    if robots_txt is None:
        return AccessVerdict.DISALLOWED
    try:
        sp = urlsplit(url)
    except ValueError:
        return AccessVerdict.DISALLOWED
    token = _agent_token(user_agent)
    groups = _parse_robots(robots_txt)
    chosen = [g for g in groups if token and token in g[0]] or [g for g in groups if "*" in g[0]]
    path = unquote((sp.path or "/") + (f"?{sp.query}" if sp.query else ""))
    best: tuple[int, bool] | None = None          # (pattern length, allow)
    for _, rules in chosen:
        for allow, pattern in rules:
            if pattern and _rule_matches(pattern, path) and (best is None or (len(pattern), allow) > best):
                best = (len(pattern), allow)
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
