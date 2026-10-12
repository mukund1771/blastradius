"""Fetch the spec document, split it into pages, choose the pages that may source requirements.

Implements D30 (page allowlist, doc_version, spec stamp), D13 (a non-OK access verdict is never ingested)
and cases 21 (doc lags app), 23 (corpus scope), 24 (sha256 + fetch time).

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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from ..failures import FailureCode
from ..models import AccessVerdict
from ..run import RunContext
from ..trace import now_iso

_PAGE_START = re.compile(r"^# (?P<title>.+)\n^Source: (?P<url>https?://\S+)[ \t]*$", re.MULTILINE)
_CHANGELOG = re.compile(r"(?i)changelog|release[- ]notes")
_VERSION = re.compile(r"\d+(?:\.\d+){1,3}")
_VERSION_HEADING = re.compile(r"^#{2,3}[ \t]+(.+)$", re.MULTILINE)
# Challenge interstitials are small; scanning all 500 KB of docs for block markers would invite false hits.
_CLASSIFY_HEAD_CHARS = 8192
_BLOCK_STATUS = frozenset({202, 403, 405, 429, 503})    # RECON.md section 3: hard-block statuses


class SpecFetchError(RuntimeError):
    """The spec could not be fetched or was refused; the failure has already been recorded."""


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
def _verdict(status: int, headers: Mapping[str, str], body: str, url: str) -> tuple[str, str]:
    """(AccessVerdict value, reason). access.classify when importable, else status and header checks."""
    try:
        from ..access.classify import classify_http
    except ImportError:
        classify_http = None
    if classify_http is not None:
        res = classify_http(status, headers, body[:_CLASSIFY_HEAD_CHARS], url)
        return str(res.verdict), res.reason or ",".join(res.signals)
    if headers.get("cf-mitigated") or headers.get("x-amzn-waf-action"):
        return str(AccessVerdict.BLOCKED), "challenge header"
    if status in _BLOCK_STATUS:
        return str(AccessVerdict.BLOCKED), f"status {status}"
    return str(AccessVerdict.OK), ""


def _refuse(run: RunContext, span: Any, code: FailureCode, message: str, **context: Any) -> SpecFetchError:
    run.failures.record(code, message, **context)
    span.fail(code, message)
    return SpecFetchError(message)


def fetch_spec(run: RunContext, url: str, *, client: httpx.Client | None = None) -> FetchedSpec:
    """One GET. Raises SpecFetchError (after recording the failure) when the document must not be ingested."""
    headers = {"User-Agent": run.settings.user_agent(),
               "Accept": "text/plain, text/markdown;q=0.9, */*;q=0.1"}
    owns_client = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=60.0)
    try:
        with run.tracer.span("http_fetch", "spec", url=url) as sp:
            try:
                resp = client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                raise _refuse(run, sp, FailureCode.ACCESS_SUSPECT,
                              f"spec fetch failed: {type(exc).__name__}", url=url) from exc
            text = resp.content.decode("utf-8", errors="replace")
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            verdict, reason = _verdict(resp.status_code, hdrs, text, url)
            sp.set(status=resp.status_code, bytes=len(resp.content), access_verdict=verdict)
            if verdict != str(AccessVerdict.OK):
                raise _refuse(run, sp, FailureCode.ACCESS_BLOCKED,
                              f"spec not ingested: access verdict {verdict} ({reason})",
                              url=url, status=resp.status_code, verdict=verdict)
            if not 200 <= resp.status_code < 300:
                raise _refuse(run, sp, FailureCode.ACCESS_SUSPECT,
                              f"spec fetch returned HTTP {resp.status_code}",
                              url=url, status=resp.status_code)
            return FetchedSpec(url=url, text=text, sha256=hashlib.sha256(resp.content).hexdigest(),
                               fetched_at=now_iso(), status=resp.status_code, size=len(resp.content),
                               content_type=hdrs.get("content-type", ""), verdict=verdict)
    finally:
        if owns_client:
            client.close()


# ------------------------------------------------------------------------------------------------ pages
def split_pages(text: str) -> list[Page]:
    """Pages in document order. Text before the first page start is ignored."""
    text = text.replace("\r\n", "\n")
    starts = list(_PAGE_START.finditer(text))
    pages = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        pages.append(Page(title=m["title"].strip(), url=m["url"], body=text[m.end():end].strip()))
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
    return tuple(int(n) for n in re.findall(r"\d+", version))


def doc_version(pages: list[Page]) -> str | None:
    """Newest version string in the changelog page(s): `## 2026.5.0`, `## 2026.3.0 & 2026.3.1`."""
    found: list[str] = []
    for page in pages:
        if page.is_changelog:
            for heading in _VERSION_HEADING.findall(page.body):
                found.extend(_VERSION.findall(heading))
    return max(found, key=version_tuple) if found else None


def is_stale(doc_ver: str | None, app_ver: str | None) -> bool | None:
    """True when the docs' newest version predates the app's; None if either side is unknown (case 21)."""
    if not doc_ver or not app_ver:
        return None
    d, a = version_tuple(doc_ver), version_tuple(app_ver)
    return d < a if d and a else None
