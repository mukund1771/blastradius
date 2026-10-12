"""spec/fetch.py: stamp, access verdict, egress, page split, allowlist, versions (D30, D13; cases 21-24)."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import httpx
import pytest
from test_spec_support import EXCERPT, FIXTURES, SPEC_URL, handler_client, real_cfg

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
    version_tuple,
)

URL = SPEC_URL


def _run(tmp_path: Path, name: str = "spec-fetch") -> RunContext:
    return RunContext.create("hoppscotch", run_id=name, runs_dir=tmp_path / "runs", settings=Settings())


def _codes(run: RunContext) -> list[str]:
    return [f["code"] for f in run.failures.items]


# ------------------------------------------------------------------------------------------------ fetch
def test_fetch_stamps_sha256_fetch_time_and_sends_the_project_user_agent(tmp_path: Path):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, content=EXCERPT, headers={"content-type": "text/plain; charset=utf-8"})

    run = _run(tmp_path)
    spec = fetch_spec(run, URL, client=handler_client(handler))
    assert spec.sha256 == hashlib.sha256(EXCERPT).hexdigest() and spec.size == len(EXCERPT)
    assert spec.fetched_at.endswith("Z") or "+" in spec.fetched_at
    assert seen["user-agent"] == run.settings.user_agent() and "blastradius" in seen["user-agent"]
    assert spec.verdict == "OK" and not run.failures.items
    assert spec.final_url == URL and spec.content_type == "text/plain; charset=utf-8"


@pytest.mark.parametrize("status,headers", [
    (403, {"cf-mitigated": "challenge", "content-type": "text/html"}),
    (202, {"x-amzn-waf-action": "challenge", "content-type": "text/html"}),
])
def test_blocked_response_is_refused_and_recorded(tmp_path: Path, status: int, headers: dict[str, str]):
    run = _run(tmp_path)
    body = b"<html><title>Just a moment...</title>"
    client = handler_client(lambda r: httpx.Response(status, content=body, headers=headers))
    with pytest.raises(SpecFetchError) as err:
        fetch_spec(run, URL, client=client)
    assert _codes(run) == ["ACCESS_BLOCKED"]
    info = err.value.info                      # what ingest writes into the refused stats.json
    assert info["http_status"] == status and info["access_verdict"] == "BLOCKED"
    assert info["sha256"] == hashlib.sha256(body).hexdigest() and info["final_url"] == URL
    assert info["content_type"] == "text/html"


def test_without_the_access_classifier_status_and_header_checks_still_refuse(tmp_path: Path, monkeypatch):
    monkeypatch.setitem(sys.modules, "blastradius.access.classify", None)     # makes the lazy import fail
    for status, headers in [(403, {}), (200, {"cf-mitigated": "challenge"}),
                            (202, {"x-amzn-waf-action": "captcha"})]:
        run = _run(tmp_path)
        client = handler_client(lambda r, s=status, h=headers: httpx.Response(s, content=b"x", headers=h))
        with pytest.raises(SpecFetchError):
            fetch_spec(run, URL, client=client)
        assert _codes(run) == ["DEP_MISSING", "ACCESS_BLOCKED"]            # the fallback is not silent
    run = _run(tmp_path)
    ok = fetch_spec(run, URL, client=handler_client(lambda r: httpx.Response(200, content=EXCERPT)))
    assert ok.verdict == "OK" and _codes(run) == ["DEP_MISSING"]


def test_only_a_missing_classifier_module_triggers_the_fallback(tmp_path: Path, monkeypatch):
    """An ImportError from a broken classifier is a bug to surface, not a reason to use the weaker check."""
    import types

    broken = types.ModuleType("blastradius.access.classify")         # no classify_http: ImportError, not
    monkeypatch.setitem(sys.modules, "blastradius.access.classify", broken)   # ModuleNotFoundError
    with pytest.raises(ImportError):
        fetch_spec(_run(tmp_path), URL, client=handler_client(lambda r: httpx.Response(200, content=b"x")))


def test_soft_verdict_from_the_classifier_is_also_not_ingested(tmp_path: Path):
    run = _run(tmp_path)
    page = b"<html><head><title>Just a moment...</title></head></html>"
    with pytest.raises(SpecFetchError):
        fetch_spec(run, URL, client=handler_client(lambda r: httpx.Response(
            200, content=page, headers={"content-type": "text/html"})))
    assert _codes(run) == ["ACCESS_BLOCKED"]


def test_http_error_and_network_error_are_fetch_failures_not_block_claims(tmp_path: Path):
    """C1: SPEC_FETCH_FAILED for transport errors and 4xx/5xx without a block signal."""
    run = _run(tmp_path)
    with pytest.raises(SpecFetchError) as err:
        fetch_spec(run, URL, client=handler_client(lambda r: httpx.Response(404, content=b"nope")))
    assert err.value.info["http_status"] == 404

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(SpecFetchError):
        fetch_spec(run, URL, client=handler_client(boom))
    with pytest.raises(SpecFetchError):
        fetch_spec(run, URL, client=handler_client(lambda r: httpx.Response(500, content=b"oops")))
    assert _codes(run) == ["SPEC_FETCH_FAILED"] * 3


@pytest.mark.parametrize("url", [
    "not a url", "ftp://docs.x.test/llms-full.txt", "http://[::1", "http:///x", ""])
def test_a_malformed_url_is_a_config_error_not_an_httpx_exception(tmp_path: Path, url: str):
    run = _run(tmp_path)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"x")

    with pytest.raises(SpecFetchError):
        fetch_spec(run, url, client=handler_client(handler))
    assert _codes(run) == ["CONFIG_INVALID"] and calls == []


# ------------------------------------------------------------------------------------------------ egress
def _redirecting(location: str, status: int = 301, seen: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if request.url.host == "docs.hoppscotch.io":
            return httpx.Response(status, headers={"location": location})
        return httpx.Response(200, content=EXCERPT, headers={"content-type": "text/plain"})

    return handler


def test_a_redirect_off_the_allowlist_is_not_followed_and_is_a_block(tmp_path: Path):
    """Rule 5: egress is limited to the spec host and egress_allow; the off-list host is never requested."""
    run, seen = _run(tmp_path), []
    client = handler_client(_redirecting("https://evil.example.net/llms-full.txt", seen=seen))
    with pytest.raises(SpecFetchError) as err:
        fetch_spec(run, URL, client=client, allow_hosts=["hoppscotch.io"])
    assert _codes(run) == ["ACCESS_BLOCKED"] and seen == [URL]
    assert "evil.example.net" in str(err.value)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_inside_the_allowance_is_followed_and_recorded(tmp_path: Path, status: int):
    run, seen = _run(tmp_path), []
    client = handler_client(_redirecting("https://hoppscotch.io/llms-full.txt", status, seen))
    spec = fetch_spec(run, URL, client=client, allow_hosts=["hoppscotch.io", "echo.hoppscotch.io"])
    assert spec.final_url == "https://hoppscotch.io/llms-full.txt" and seen[-1] == spec.final_url
    assert spec.url == URL and not run.failures.items


def test_a_relative_redirect_stays_on_the_spec_host(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/llms-full.txt":
            return httpx.Response(302, headers={"location": "/v2/llms-full.txt"})
        return httpx.Response(200, content=EXCERPT)

    spec = fetch_spec(_run(tmp_path), URL, client=handler_client(handler))
    assert spec.final_url == "https://docs.hoppscotch.io/v2/llms-full.txt"


def test_redirect_loops_and_locationless_redirects_fail_closed(tmp_path: Path):
    run = _run(tmp_path)
    loop = handler_client(lambda r: httpx.Response(302, headers={"location": str(r.url)}))
    with pytest.raises(SpecFetchError):
        fetch_spec(run, URL, client=loop)
    with pytest.raises(SpecFetchError):
        fetch_spec(run, URL, client=handler_client(lambda r: httpx.Response(302)))
    assert _codes(run) == ["SPEC_FETCH_FAILED", "SPEC_FETCH_FAILED"]


def test_a_redirect_to_a_non_http_scheme_is_refused(tmp_path: Path):
    run = _run(tmp_path)
    client = handler_client(_redirecting("file:///etc/passwd"))
    with pytest.raises(SpecFetchError):
        fetch_spec(run, URL, client=client, allow_hosts=["hoppscotch.io"])
    assert _codes(run) in (["ACCESS_BLOCKED"], ["SPEC_FETCH_FAILED"])


def test_a_client_that_follows_redirects_itself_is_still_checked(tmp_path: Path):
    """A caller-supplied follow_redirects=True client must not be able to bypass the host check."""
    run, seen = _run(tmp_path), []
    client = httpx.Client(transport=httpx.MockTransport(
        _redirecting("https://evil.example.net/x", seen=seen)), follow_redirects=True)
    with pytest.raises(SpecFetchError):
        fetch_spec(run, URL, client=client, allow_hosts=["hoppscotch.io"])
    assert seen == [URL]


# ------------------------------------------------------------------------------------------------ size cap
def test_the_response_is_streamed_and_cut_at_the_size_cap(tmp_path: Path):
    """An untrusted source cannot make the process buffer an unbounded body."""
    chunk = b"x" * 65536
    produced = {"n": 0}

    def body():
        for _ in range(512):                       # 32 MB on offer
            produced["n"] += len(chunk)
            yield chunk

    run = _run(tmp_path)
    client = handler_client(lambda r: httpx.Response(200, stream=_Stream(body()),
                                                     headers={"content-type": "text/plain"}))
    with pytest.raises(SpecFetchError) as err:
        fetch_spec(run, URL, client=client)
    assert _codes(run) == ["SPEC_FETCH_FAILED"] and "8 MB" in str(err.value)
    cap = 8 * 1024 * 1024
    assert produced["n"] <= cap + 2 * len(chunk)                # stopped reading, did not drain the body


def test_a_declared_content_length_over_the_cap_is_refused_before_reading(tmp_path: Path):
    run = _run(tmp_path)
    over = str(8 * 1024 * 1024 + 1)
    client = handler_client(lambda r: httpx.Response(
        200, content=b"x", headers={"content-length": over, "content-type": "text/plain"}))
    with pytest.raises(SpecFetchError):
        fetch_spec(run, URL, client=client)
    assert _codes(run) == ["SPEC_FETCH_FAILED"]


class _Stream(httpx.SyncByteStream):
    def __init__(self, it):
        self._it = it

    def __iter__(self):
        yield from self._it


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


def test_a_hostile_page_title_is_capped():
    """F9: the title is repeated on every chunk, so an uncapped one multiplied the model input ~70x."""
    text = f"# {'T' * 100_000}\nSource: https://d.test/a\n\nbody text here.\n"
    (page,) = split_pages(text)
    assert len(page.title) == 200


# ------------------------------------------------------------------------------------------------ scope
def _index_pages() -> list[Page]:
    rows = json.loads((FIXTURES / "pages_index.json").read_text())
    return [Page(r["title"], r["url"], "x" * r["chars"]) for r in rows]


def test_allowlist_yield_on_the_real_page_index_and_the_real_yaml():
    """C2: reads targets/hoppscotch.yaml, so editing the allowlist there changes this number on purpose.

    pages_index.json is the title/url list of the real 104-page llms-full.txt (measured 2026-10-12).
    """
    spec = real_cfg().spec
    pages = _index_pages()
    assert len(pages) == 104
    decisions = select_pages(pages, spec.include_pages, spec.exclude_pages)
    kept = [d.page.path for d in decisions if d.included]
    assert (len(kept), len(decisions) - len(kept)) == (49, 55)
    by_area = Counter("/".join(p.split("/")[2:4]) if "/features/" not in p else "features" for p in kept)
    assert by_area == {"features": 26, "clients/web": 2, "getting-started/rest": 10,
                       "getting-started/realtime": 4, "getting-started/graphql": 2,
                       "getting-started/introduction": 1, "getting-started/quick-start": 1,
                       "protocols/rest": 1, "protocols/graphql": 1, "protocols/realtime": 1}
    reasons = Counter(d.reason.split(" /")[0] for d in decisions if not d.included)
    assert reasons["changelog: version source only"] == 1 and reasons["excluded by"] == 24
    assert reasons["not matched by include_pages"] == 30
    assert not [p for p in kept if any(w in p for w in ("self-host", "privacy", "terms", "security"))]


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


@pytest.mark.parametrize("doc_ver,app_ver,expected", [
    ("2026.5.0", "2026.9.0", True),
    ("2026.10.0", "2026.9.0", False),
    ("2026.9.0", "2026.9.0", False),
    (None, "2026.9.0", None), ("2026.5.0", None, None), ("", "2026.9.0", None),
    # F11: equal versions in different shapes are not stale
    ("2026.5", "2026.5.0", False),
    ("2026.5.0", "2026.5", False),
    ("2026.9.0", "2026.9.0+build.5", False),
    ("2026.9.0", "2026.9.0-beta.1", False),
    ("2026.9.0-beta.1", "2026.9.0", False),
    ("v2026.9.0", "2026.9.0", False),
    ("2026.9.0", "2026.9.1", True),
    ("2026.9", "2026.9.1", True),
    # different version schemes are incomparable rather than "always stale"
    ("2026.5.0", "20260930", None),
    ("20260930", "2026.5.0", None),
])
def test_staleness_compares_the_leading_numeric_version(doc_ver, app_ver, expected):
    assert is_stale(doc_ver, app_ver) is expected


def test_version_tuple_takes_only_the_leading_dotted_digits():
    assert version_tuple("2026.9.0-beta.1") == (2026, 9, 0)
    assert version_tuple("2026.9.0+build.5") == (2026, 9, 0)
    assert version_tuple("v2026.9.0") == (2026, 9, 0)
    assert version_tuple("release") == ()
