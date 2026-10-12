"""spec/fetch.py: stamp, access verdict, page split, allowlist, doc_version (D30, D13; cases 21, 23, 24)."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import httpx
import pytest

from blastradius.config import Settings
from blastradius.run import RunContext
from blastradius.spec.fetch import (
    Page,
    SpecFetchError,
    doc_version,
    fetch_spec,
    is_stale,
    select_pages,
    split_pages,
)

FIXTURES = Path(__file__).parent / "fixtures" / "spec"
EXCERPT = (FIXTURES / "llms_excerpt.txt").read_bytes()
URL = "https://docs.hoppscotch.io/llms-full.txt"
# Copy of targets/hoppscotch.yaml spec.include_pages / exclude_pages when the yield below was measured.
INCLUDE = ["documentation/features/", "getting-started/(rest|graphql|realtime)/"]
EXCLUDE = ["self-host", "privacy", "terms", "security", "changelog"]


def _run(tmp_path: Path) -> RunContext:
    return RunContext.create("hoppscotch", run_id="spec-fetch", runs_dir=tmp_path / "runs",
                             settings=Settings())


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _codes(run: RunContext) -> list[str]:
    return [f["code"] for f in run.failures.items]


# ------------------------------------------------------------------------------------------------ fetch
def test_fetch_stamps_sha256_fetch_time_and_sends_the_project_user_agent(tmp_path: Path):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, content=EXCERPT, headers={"content-type": "text/plain; charset=utf-8"})

    run = _run(tmp_path)
    spec = fetch_spec(run, URL, client=_client(handler))
    assert spec.sha256 == hashlib.sha256(EXCERPT).hexdigest() and spec.size == len(EXCERPT)
    assert spec.fetched_at.endswith("Z") or "+" in spec.fetched_at
    assert seen["user-agent"] == run.settings.user_agent() and "blastradius" in seen["user-agent"]
    assert spec.verdict == "OK" and not run.failures.items


@pytest.mark.parametrize("status,headers", [
    (403, {"cf-mitigated": "challenge", "content-type": "text/html"}),
    (202, {"x-amzn-waf-action": "challenge", "content-type": "text/html"}),
])
def test_blocked_response_is_refused_and_recorded(tmp_path: Path, status: int, headers: dict[str, str]):
    run = _run(tmp_path)
    body = b"<html><title>Just a moment...</title>"
    client = _client(lambda r: httpx.Response(status, content=body, headers=headers))
    with pytest.raises(SpecFetchError):
        fetch_spec(run, URL, client=client)
    assert _codes(run) == ["ACCESS_BLOCKED"]


def test_without_the_access_classifier_status_and_header_checks_still_refuse(tmp_path: Path, monkeypatch):
    monkeypatch.setitem(sys.modules, "blastradius.access.classify", None)     # makes the lazy import fail
    for status, headers in [(403, {}), (200, {"cf-mitigated": "challenge"}),
                            (202, {"x-amzn-waf-action": "captcha"})]:
        run = _run(tmp_path)
        client = _client(lambda r, s=status, h=headers: httpx.Response(s, content=b"x", headers=h))
        with pytest.raises(SpecFetchError):
            fetch_spec(run, URL, client=client)
        assert _codes(run) == ["ACCESS_BLOCKED"]
    ok = fetch_spec(_run(tmp_path), URL, client=_client(lambda r: httpx.Response(200, content=EXCERPT)))
    assert ok.verdict == "OK"


def test_soft_verdict_from_the_classifier_is_also_not_ingested(tmp_path: Path):
    run = _run(tmp_path)
    page = b"<html><head><title>Just a moment...</title></head></html>"
    with pytest.raises(SpecFetchError):
        fetch_spec(run, URL, client=_client(lambda r: httpx.Response(
            200, content=page, headers={"content-type": "text/html"})))
    assert _codes(run) == ["ACCESS_BLOCKED"]


def test_http_error_and_network_error_are_refused_without_claiming_a_block(tmp_path: Path):
    run = _run(tmp_path)
    with pytest.raises(SpecFetchError):
        fetch_spec(run, URL, client=_client(lambda r: httpx.Response(404, content=b"nope")))

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(SpecFetchError):
        fetch_spec(run, URL, client=_client(boom))
    assert _codes(run) == ["ACCESS_SUSPECT", "ACCESS_SUSPECT"]


# ------------------------------------------------------------------------------------------------ split
def test_split_pages_on_the_real_excerpt():
    pages = split_pages(EXCERPT.decode())
    assert [p.title for p in pages] == ["Changelog", "Cookies", "Workspaces"]
    assert [p.path for p in pages] == [
        "/documentation/changelog", "/documentation/features/cookies", "/documentation/features/workspaces"]
    assert pages[1].body.startswith("Use the Hoppscotch Cookie Manager")      # description paragraph kept
    assert "Source:" not in pages[1].body and pages[1].body.endswith("as part of the request.")


def test_hash_lines_that_are_not_followed_by_a_source_line_are_not_pages():
    text = ("# One\nSource: https://d.test/a\n\nbody\n\n"
            "```bash\n# a shell comment\n# Source: not a page\n```\n\n"
            "# A real body heading\nmore\n\n\n# Two\nSource: https://d.test/b\n\nsecond\n")
    pages = split_pages(text)
    assert [(p.title, p.url) for p in pages] == [("One", "https://d.test/a"), ("Two", "https://d.test/b")]
    assert "# a shell comment" in pages[0].body and "# A real body heading" in pages[0].body


def test_crlf_and_leading_junk_are_tolerated():
    text = "junk before\r\n# One\r\nSource: https://d.test/a\r\n\r\nbody\r\n"
    assert [p.title for p in split_pages(text)] == ["One"] and split_pages(text)[0].body == "body"


def test_response_without_page_markers_yields_no_pages():
    assert split_pages("<!doctype html><title>Just a moment...</title>") == []


# ------------------------------------------------------------------------------------------------ scope
def _index_pages() -> list[Page]:
    rows = json.loads((FIXTURES / "pages_index.json").read_text())
    return [Page(r["title"], r["url"], "x" * r["chars"]) for r in rows]


def test_allowlist_yield_on_the_real_page_index():
    """pages_index.json is the title/url list of the real 104-page llms-full.txt (measured 2026-10-12)."""
    pages = _index_pages()
    assert len(pages) == 104
    decisions = select_pages(pages, INCLUDE, EXCLUDE)
    kept = [d.page.path for d in decisions if d.included]
    assert len(kept) == 42
    by_area = Counter(p.split("/")[2] if "/features/" in p else "/".join(p.split("/")[2:4]) for p in kept)
    assert by_area == {"features": 26, "getting-started/rest": 10, "getting-started/realtime": 4,
                       "getting-started/graphql": 2}
    reasons = Counter(d.reason.split(" /")[0] for d in decisions if not d.included)
    assert reasons["changelog: version source only"] == 1 and reasons["excluded by"] == 24
    assert reasons["not matched by include_pages"] == 37
    assert not [p for p in kept if "self-host" in p or "privacy" in p or "terms" in p or "security" in p]


def test_changelog_is_never_a_requirement_source_even_if_included():
    page = Page("Changelog", "https://d.test/documentation/features/changelog", "## 1.0.0")
    (d,) = select_pages([page], include=["documentation/features/"], exclude=[])
    assert not d.included and "changelog" in d.reason


def test_no_include_list_means_everything_not_excluded():
    pages = [Page("A", "https://d.test/a", ""), Page("Privacy", "https://d.test/privacy", "")]
    out = select_pages(pages, include=[], exclude=["privacy"])
    assert [d.included for d in out] == [True, False]


def test_invalid_pattern_raises_re_error():
    import re

    with pytest.raises(re.error):
        select_pages([Page("A", "https://d.test/a", "")], include=["("], exclude=[])


# ------------------------------------------------------------------------------------------------ version
def test_doc_version_is_the_newest_released_version_in_the_changelog():
    pages = split_pages(EXCERPT.decode())
    assert doc_version(pages) == "2026.5.0"
    shuffled = Page("Changelog", "https://d.test/changelog",
                    "## 2026.1.0 & 2026.1.1\n\n## 2026.10.0\n\n## 2026.9.2\n\n## Unreleased")
    assert doc_version([shuffled]) == "2026.10.0"      # numeric, not lexical
    assert doc_version([Page("Intro", "https://d.test/intro", "## 2030.1.0")]) is None   # changelogs only


def test_staleness_compares_numerically_and_is_unknown_when_either_side_is():
    assert is_stale("2026.5.0", "2026.9.0") is True
    assert is_stale("2026.10.0", "2026.9.0") is False
    assert is_stale("2026.9.0", "2026.9.0") is False
    assert is_stale(None, "2026.9.0") is None and is_stale("2026.5.0", None) is None
