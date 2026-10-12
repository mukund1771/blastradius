"""Fetch the spec document, split it into pages, choose the pages that may source requirements.

Implements D30 (page allowlist, doc_version, spec stamp), D13 (a non-OK access verdict is never ingested),
D28 / ARCHITECTURE rule 5 (egress stays on the spec host and the target's egress_allow) and cases 21 (doc
lags app), 23 (corpus scope), 24 (sha256 + fetch time).

llms-full.txt layout, measured on https://docs.hoppscotch.io/llms-full.txt (520,910 B, 10,326 lines):

    # <page title>
    Source: <absolute page url>
    <blank>
    <one-paragraph description>
    <blank>
    <MDX body>

and pages are separated by two blank lines (103 of 103 gaps). A `# ` line is a page start only when the
next line is `Source: http...`: the document has 188 column-0 `# ` lines, 104 of them page starts, the other
84 shell comments inside code fences and body headings; there are exactly 104 `Source:` lines.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ..failures import FailureCode
from ..models import AccessVerdict
from ..run import RunContext
from ..trace import now_iso

MAX_BYTES = 8 * 1024 * 1024         # the real document is 0.5 MB; a source that offers more is not a spec
MAX_REDIRECTS = 5
MAX_TITLE_CHARS = 200               # a page title is repeated on every chunk of its page

_PAGE_START = re.compile(r"^# (?P<title>.+)\n^Source: (?P<url>https?://\S+)[ \t]*$", re.MULTILINE)
_CHANGELOG = re.compile(r"(?i)changelog|release[- ]notes")
_VERSION = re.compile(r"\d+(?:\.\d+){1,3}")
_LEADING_VERSION = re.compile(r"^\D{0,3}(\d+(?:\.\d+)*)")
_VERSION_HEADING = re.compile(r"^#{2,3}[ \t]+(.+)$", re.MULTILINE)
# Challenge interstitials are small; scanning all 500 KB of docs for block markers would invite false hits.
_CLASSIFY_HEAD_CHARS = 8192
_BLOCK_STATUS = frozenset({202, 403, 405, 429, 503})    # RECON.md section 3: hard-block statuses
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_CLASSIFIER_MODULES = frozenset({"blastradius.access", "blastradius.access.classify"})
_DATE_STAMP = 100_000                # a first version component this large is a YYYYMMDD / YYYYMM stamp


class SpecFetchError(RuntimeError):
    """The spec could not be fetched or was refused; the failure has already been recorded.

    `code` is the failure code that was recorded; `info` carries what the refused stats.json records: sha256,
    http_status, content_type, final_url, access_verdict (each None when the request never got that far).
    """

    def __init__(self, message: str, code: FailureCode = FailureCode.SPEC_FETCH_FAILED, **info: Any):
        super().__init__(message)
        self.code = code
        self.info: dict[str, Any] = {"sha256": None, "http_status": None, "content_type": None,
                                     "final_url": None, "access_verdict": None, **info}


@dataclass(frozen=True)
class FetchedSpec:
    url: str
    text: str
    sha256: str
    fetched_at: str
    status: int
    size: int
    content_type: str
    verdict: str
    final_url: str = ""


@dataclass(frozen=True)
class Page:
    title: str
    url: str
    body: str

    @property
    def path(self) -> str:
        return urlparse(self.url).path

    @property
    def is_changelog(self) -> bool:
        return bool(_CHANGELOG.search(self.path) or _CHANGELOG.search(self.title))


@dataclass(frozen=True)
class PageDecision:
    page: Page
    included: bool
    reason: str


# ------------------------------------------------------------------------------------------------ fetch
def _classifier(run: RunContext) -> Callable[..., Any] | None:
    """access.classify.classify_http, or None when that module is absent (recorded, not silent).

    Only a missing classifier module takes the weaker status/header path; any other ImportError is a bug
    in the classifier and propagates.
    """
    try:
        from ..access.classify import classify_http
    except ModuleNotFoundError as exc:
        if exc.name not in _CLASSIFIER_MODULES:
            raise
        run.failures.record(FailureCode.DEP_MISSING, "access classifier unavailable: the spec fetch used "
                            "status and header checks only", module=str(exc.name))
        return None
    return classify_http


def _verdict(run: RunContext, status: int, headers: Mapping[str, str], body: str,
             url: str) -> tuple[str, str]:
    """(AccessVerdict value, reason). access.classify when importable, else status and header checks."""
    classify_http = _classifier(run)
    if classify_http is not None:
        res = classify_http(status, headers, body[:_CLASSIFY_HEAD_CHARS], url)
        return str(res.verdict), res.reason or ",".join(res.signals)
    if headers.get("cf-mitigated") or headers.get("x-amzn-waf-action"):
        return str(AccessVerdict.BLOCKED), "challenge header"
    if status in _BLOCK_STATUS:
        return str(AccessVerdict.BLOCKED), f"status {status}"
    return str(AccessVerdict.OK), ""


def _refuse(run: RunContext, span: Any, code: FailureCode, message: str, info: dict[str, Any],
            **context: Any) -> SpecFetchError:
    run.failures.record(code, message, **context)
    span.fail(code, message)
    return SpecFetchError(message, code, **info)


def host_allowed(host: str, spec_host: str, allow: Iterable[str]) -> bool:
    """The spec host itself, or an egress_allow entry or a subdomain of one."""
    host = host.lower().rstrip(".")
    if host == spec_host.lower().rstrip("."):
        return True
    return any(host == (e := entry.lower().strip(".")) or host.endswith("." + e) for entry in allow)


def _parse_http_url(url: str) -> tuple[str, str] | None:
    """(url, host) for an absolute http(s) URL with a host; None for anything else."""
    try:
        parts = urlparse(url)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    return url, host


def _read_capped(resp: httpx.Response) -> bytes | None:
    """The body, or None as soon as it passes MAX_BYTES (decoded bytes, so a gzip bomb is capped too)."""
    try:
        declared = int(resp.headers.get("content-length", "0"))
    except ValueError:
        declared = 0
    if declared > MAX_BYTES:
        return None
    buf = bytearray()
    for part in resp.iter_bytes():
        buf.extend(part)
        if len(buf) > MAX_BYTES:
            return None
    return bytes(buf)


def fetch_spec(run: RunContext, url: str, *, client: httpx.Client | None = None,
               allow_hosts: Iterable[str] = ()) -> FetchedSpec:
    """One GET (redirects followed by hand, host-checked at every hop, body capped at MAX_BYTES).

    Raises SpecFetchError (after recording the failure) when the document must not be ingested.
    `allow_hosts` are the target's egress_allow entries; the spec's own host is always allowed.
    """
    allow = tuple(allow_hosts)
    parsed = _parse_http_url(url)
    with run.tracer.span("http_fetch", "spec", url=url) as sp:
        if parsed is None:
            raise _refuse(run, sp, FailureCode.CONFIG_INVALID,
                          f"spec.url is not an absolute http(s) URL: {url!r}", {}, url=url)
        spec_host = parsed[1]
        headers = {"User-Agent": run.settings.user_agent(),
                   "Accept": "text/plain, text/markdown;q=0.9, */*;q=0.1"}
        owns_client = client is None
        client = client or httpx.Client(timeout=60.0)
        try:
            return _fetch(run, sp, client, url, spec_host, allow, headers)
        finally:
            if owns_client:
                client.close()


def _fetch(run: RunContext, sp: Any, client: httpx.Client, url: str, spec_host: str, allow: tuple[str, ...],
           headers: dict[str, str]) -> FetchedSpec:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        target = _parse_http_url(current)
        if target is None or not host_allowed(target[1], spec_host, allow):
            raise _refuse(run, sp, FailureCode.ACCESS_BLOCKED,
                          f"spec fetch not followed: {current!r} is outside the spec host and egress_allow",
                          {"final_url": current}, url=url, target=current)
        try:
            with client.stream("GET", current, headers=headers, follow_redirects=False) as resp:
                status = resp.status_code
                location = resp.headers.get("location") if status in _REDIRECTS else None
                if status in _REDIRECTS:
                    if not location:
                        raise _refuse(run, sp, FailureCode.SPEC_FETCH_FAILED,
                                      f"spec fetch returned HTTP {status} without a Location",
                                      {"http_status": status, "final_url": current}, url=url, status=status)
                    current = urljoin(current, location)
                    continue
                return _accept(run, sp, resp, url, current)
        except (httpx.HTTPError, httpx.InvalidURL, httpx.StreamError, ValueError) as exc:
            raise _refuse(run, sp, FailureCode.SPEC_FETCH_FAILED, f"spec fetch failed: {type(exc).__name__}",
                          {"final_url": current}, url=url) from exc
    raise _refuse(run, sp, FailureCode.SPEC_FETCH_FAILED,
                  f"spec fetch redirected more than {MAX_REDIRECTS} times", {"final_url": current}, url=url)


def _accept(run: RunContext, sp: Any, resp: httpx.Response, url: str, final_url: str) -> FetchedSpec:
    """Read the body (capped), classify it, and return the stamped document or raise SpecFetchError."""
    content = _read_capped(resp)
    status = resp.status_code
    hdrs = {k.lower(): v for k, v in resp.headers.items()}
    content_type = hdrs.get("content-type", "")
    base = {"http_status": status, "content_type": content_type, "final_url": final_url}
    if content is None:
        raise _refuse(run, sp, FailureCode.SPEC_FETCH_FAILED,
                      f"spec response is larger than {MAX_BYTES // (1024 * 1024)} MB; not read",
                      base, url=url, status=status)
    text = content.decode("utf-8", errors="replace")
    sha = hashlib.sha256(content).hexdigest()
    verdict, reason = _verdict(run, status, hdrs, text, final_url)
    sp.set(status=status, bytes=len(content), access_verdict=verdict, final_url=final_url)
    info = {**base, "sha256": sha, "access_verdict": verdict}
    if verdict != str(AccessVerdict.OK):
        raise _refuse(run, sp, FailureCode.ACCESS_BLOCKED,
                      f"spec not ingested: access verdict {verdict} ({reason})", info,
                      url=url, status=status, verdict=verdict)
    if not 200 <= status < 300:
        raise _refuse(run, sp, FailureCode.SPEC_FETCH_FAILED, f"spec fetch returned HTTP {status}", info,
                      url=url, status=status)
    return FetchedSpec(url=url, text=text, sha256=sha, fetched_at=now_iso(), status=status, size=len(content),
                       content_type=content_type, verdict=verdict, final_url=final_url)


# ------------------------------------------------------------------------------------------------ pages
def split_pages(text: str) -> list[Page]:
    """Pages in document order. Text before the first page start is ignored. Titles are capped."""
    text = text.replace("\r\n", "\n")
    starts = list(_PAGE_START.finditer(text))
    pages = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        pages.append(Page(title=m["title"].strip()[:MAX_TITLE_CHARS], url=m["url"],
                          body=text[m.end():end].strip()))
    return pages


def select_pages(pages: list[Page], include: list[str], exclude: list[str]) -> list[PageDecision]:
    """Allowlist before extraction (case 23). Patterns are searched in the page path and title.

    Changelog pages are never requirement sources whatever the patterns say (they date the docs, see
    doc_version). Raises re.error on an invalid pattern.
    """
    inc = [re.compile(p) for p in include]
    exc = [re.compile(p) for p in exclude]

    def hit(patterns: list[re.Pattern[str]], page: Page) -> str | None:
        for p in patterns:
            if p.search(page.path) or p.search(page.title):
                return p.pattern
        return None

    out = []
    for page in pages:
        if page.is_changelog:
            out.append(PageDecision(page, False, "changelog: version source only"))
        elif (pat := hit(exc, page)) is not None:
            out.append(PageDecision(page, False, f"excluded by /{pat}/"))
        elif inc and (pat := hit(inc, page)) is None:
            out.append(PageDecision(page, False, "not matched by include_pages"))
        else:
            out.append(PageDecision(page, True, f"included by /{pat}/" if inc else "no include list"))
    return out


# ------------------------------------------------------------------------------------------------ versions
def version_tuple(version: str) -> tuple[int, ...]:
    """The leading dotted number of a version string; suffixes (`-beta.1`, `+build.5`) are ignored."""
    m = _LEADING_VERSION.match(version.strip())
    return tuple(int(n) for n in m.group(1).split(".")) if m else ()


def doc_version(pages: list[Page]) -> str | None:
    """Newest version string in the changelog page(s): `## 2026.5.0`, `## 2026.3.0 & 2026.3.1`."""
    found: list[str] = []
    for page in pages:
        if page.is_changelog:
            for heading in _VERSION_HEADING.findall(page.body):
                found.extend(_VERSION.findall(heading))
    return max(found, key=version_tuple) if found else None


def is_stale(doc_ver: str | None, app_ver: str | None) -> bool | None:
    """True when the docs' newest version predates the app's; None if either side is unknown (case 21).

    Versions are compared by their leading dotted number, zero-padded to equal length (`2026.5` equals
    `2026.5.0`). A dotted version against a date stamp (`2026.5.0` vs `20260930`) cannot be compared and
    gives None rather than a verdict.
    """
    if not doc_ver or not app_ver:
        return None
    d, a = version_tuple(doc_ver), version_tuple(app_ver)
    if not d or not a or (d[0] >= _DATE_STAMP) != (a[0] >= _DATE_STAMP):
        return None
    width = max(len(d), len(a))
    d, a = d + (0,) * (width - len(d)), a + (0,) * (width - len(a))
    return d < a
