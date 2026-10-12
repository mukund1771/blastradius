"""Access module: classifier, robots, breaker, capture persistence, recorded sessions.

Decisions D11-D14, D32, D33; cases 17, 18, 58, 67, 68, 69, 70. Fixtures in tests/fixtures/access are labelled
'measured' (real responses, 2026-10-12) or 'synthesized from documented markers' (they test the author's
recall of vendor documentation, not the vendor: case 70).

The regression tests carry the wave-1 review finding id in their name: M1..M5 (major), m1..m8 (minor), n1..n2
(nit). Each was written to fail on the pre-fix code and pass after.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import random
import re
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from blastradius.access.capture import (
    HEADER_ALLOWLIST,
    SECRET_HEADERS,
    append_capture,
    make_capture,
    scrub_url,
)
from blastradius.access.classify import (
    COLLAPSE_MIN_URLS,
    ROBOTS_MAX_RULES_PER_GROUP,
    ROBOTS_MAX_URL_CHARS,
    Breaker,
    classify_capture,
    classify_http,
    extract_title,
    normalise_headers,
    robots_allows,
)
from blastradius.access.source import CAP_ACT, CAP_REPLAY, ActingSource, IngestSource, RecordedSession
from blastradius.config import Settings
from blastradius.models import AccessResult, AccessVerdict, Capture
from blastradius.run import RunContext
from blastradius.targets import AnchorConfig, TargetConfig

FIX = Path(__file__).parent / "fixtures" / "access"
SRC = Path(__file__).resolve().parents[1] / "src"
MEASURED = "measured"
SYNTH = "synthesized from documented markers"
OK, SUSPECT, BLOCKED, DISALLOWED = (AccessVerdict.OK, AccessVerdict.SUSPECT, AccessVerdict.BLOCKED,
                                    AccessVerdict.DISALLOWED)
UA = Settings().user_agent()
URL = "https://x/"
HTML = {"content-type": "text/html; charset=utf-8"}
CF = {**HTML, "server": "cloudflare", "cf-ray": "a4944e88eff17eda-MAA"}
PAGE = "<!doctype html><html><head><title>Hoppscotch</title></head><body><div id=\"app\"></div></body></html>"
BEACON = "<script defer src=\"/cdn-cgi/challenge-platform/scripts/jsd/main.js\"></script>"   # JS detections
AMAZON_CHECK = ("<label>Type the characters you see in this image</label>"
                "<form action=\"/errors/validateCaptcha\">")
CF_MARKERS = [          # the interstitial's own markers (not the beacon above)
    "<script>window._cf_chl_opt={cType:'managed'};</script>",
    "<form action=\"/?__cf_chl_tk=abc\" method=\"POST\"></form>",
    "<script src=\"/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1\"></script>",
    "<script src=\"/cdn-cgi/challenge-platform/h/g/orchestrate/jsch/v1\"></script>",
]


def fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def _cases() -> list:
    out = []
    for path in sorted(FIX.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "expected" in data:
            out.append(pytest.param(data, id=path.stem))
    return out


def run_ctx(tmp_path: Path, run_id: str = "access-test") -> RunContext:
    return RunContext.create("hoppscotch", run_id=run_id, runs_dir=tmp_path / "runs", settings=Settings())


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def persisted(run: RunContext) -> str:
    """Everything this module wrote for the run: captures, failures and evidence rows."""
    names = [("crawl", "captures.jsonl"), ("failures.jsonl",), ("evidence.jsonl",)]
    return "".join(run.run_dir.joinpath(*n).read_text() for n in names)


@contextlib.contextmanager
def finishes_within(seconds: float = 1.0):
    """Fail, never hang: the unfixed regexes ran for minutes. SIGALRM interrupts `re` (it polls signals)."""
    def _expired(signum, frame):
        raise AssertionError(f"not finished after {seconds:.1f} s")

    previous = signal.signal(signal.SIGALRM, _expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


# ----------------------------------------------------------------------------------------- the contract
_PATHOLOGICAL = [      # (label, status, headers, body): each used to be, or could be, quadratic or worse
    ("error + 1e6 spaces", 403, CF, "error" + " " * 1_000_000 + "x"),
    ("error code + 1e6 space/tab", 403, CF, "error code" + " \t" * 500_000 + "x"),
    ("error + 1e6 colon/space", 403, CF, "error" + ": " * 500_000),
    ("error + 200k tags", 403, CF, "error " + "<a> " * 200_000),
    ("error<a> x 100k", 403, CF, "error <a>" * 100_000),
    ("cf-error-code + 1e6 spaces", 403, CF, "cf-error-code" + " " * 1_000_000),
    ("akamai reference + 1e6 spaces", 403, {**HTML, "server": "AkamaiGHost"}, "access denied reference"
     + " " * 1_000_000 + "x"),
    ("akamai reference&#32; x 100k", 403, {**HTML, "server": "AkamaiGHost"}, "access denied "
     + "reference&#32;" * 100_000),
    ("press + 1e6 spaces around &", 403, HTML, "press" + " " * 500_000 + "&" + " " * 500_000),
    ("press &amp; x 100k", 403, HTML, "press &amp; " * 100_000),
    ("imperva request + 1e6 spaces", 403, HTML, "request" + " " * 1_000_000 + "x"),
    ("amazon type the characters + 1e6 spaces", 200, HTML, "type the characters" + " " * 1_000_000),
    ("collector- + 1e6 letters", 403, HTML, "collector-" + "a" * 1_000_000),
    ("<title x 100k", 200, HTML, "<title " * 100_000),
    ("<title + 1e6 spaces", 200, HTML, "<title" + " " * 1_000_000),
    ("2 MB of 'error ' on a 429", 429, CF, "error " * 350_000),
]


@pytest.mark.parametrize(("label", "status", "headers", "body"), _PATHOLOGICAL,
                         ids=[c[0] for c in _PATHOLOGICAL])
def test_m3_pathological_bodies_classify_in_linear_time(label, status, headers, body):
    with finishes_within(1.0):
        res = classify_http(status, headers, body, URL)
        cap = classify_capture(status=status, headers=headers, title=None, body_text=body,
                               interactive_count=None, anchor=None, url=URL)
    assert res.verdict is not BLOCKED and cap.verdict is not BLOCKED


def test_signatures_match_the_architecture_doc():
    assert list(inspect.signature(classify_http).parameters) == ["status", "headers", "body", "url"]
    params = inspect.signature(classify_capture).parameters
    assert list(params) == ["status", "headers", "title", "body_text", "interactive_count", "anchor", "url",
                            "rung", "prior_fingerprints", "collapse_exempt_patterns"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())
    assert params["rung"].default == "L0" and params["prior_fingerprints"].default is None
    assert params["collapse_exempt_patterns"].default is None
    assert list(inspect.signature(robots_allows).parameters) == ["robots_txt", "user_agent", "url"]
    assert inspect.signature(Breaker).parameters["threshold"].default == 2
    assert list(inspect.signature(make_capture).parameters)[:3] == ["target", "url", "access"]
    assert inspect.signature(append_capture).return_annotation in ("str", str)


# --------------------------------------------------------------------------------- fixtures: the sweep
@pytest.mark.parametrize("data", _cases())
def test_every_fixture_gets_its_expected_verdict(data):
    want = data["expected"]
    res = classify_http(data["status"], data["headers"], data["body"], data["url"])
    assert res.verdict == want["verdict"] and res.vendor == want["vendor"]
    assert res.rung == "L0"
    if "signal" in want:
        assert want["signal"] in res.signals
    if res.verdict is OK:
        assert res.signals == []
    # the browser-capture entry point agrees (no anchor: only the response itself is judged)
    cap = classify_capture(status=data["status"], headers=data["headers"], title=extract_title(data["body"]),
                           body_text=data["body"], interactive_count=50, anchor=None, url=data["url"])
    assert (cap.verdict, cap.vendor) == (res.verdict, res.vendor)


def test_fixtures_are_labelled_and_vendor_rows_are_not_passed_off_as_measured():
    seen = {}
    for path in sorted(FIX.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["provenance"] in {MEASURED, SYNTH}, path.name
        seen[path.stem] = data["provenance"]
    for name in ("ok_hoppscotch_io_shell", "ok_docs_hoppscotch_io", "ok_demo_usememos_com",
                 "blocked_cloudflare_challenge_403_head", "robots_hoppscotch_io",
                 "robots_docs_hoppscotch_io"):
        assert seen[name] == MEASURED, name
    for name in ("blocked_akamai_403", "blocked_datadome_403", "blocked_perimeterx_403",
                 "blocked_imperva_403", "blocked_amazon_robot_check_200", "blocked_aws_waf_challenge_202",
                 "blocked_aws_waf_captcha_405",
                 "ok_amazon_like_cloudfront_200", "blocked_vercel_challenge_429",
                 "suspect_vercel_checkpoint_title_200", "blocked_cloudflare_block_1010",
                 "ok_cloudflare_beacon_with_just_a_moment_prose_200", "ok_press_and_hold_ui_copy_200"):
        assert seen[name] == SYNTH, name       # D33 / case 70


def test_the_measured_cloudflare_head_has_the_recorded_shape():
    data = fixture("blocked_cloudflare_challenge_403_head")
    assert data["status"] == 403 and data["headers"]["cf-mitigated"] == "challenge"
    assert data["body"] == ""                  # a HEAD: the verdict rests on the header alone (D32)


# ------------------------------------------------------------------- real Hoppscotch data (D13, case 17)
def test_real_hoppscotch_shell_is_5219_bytes_and_ok():
    data = fixture("ok_hoppscotch_io_shell")
    assert len(data["body"].encode("utf-8")) == data["body_bytes"] == 5219        # RECON section 1 and 3
    assert '<div id="app"></div>' in data["body"]
    assert data["headers"]["server"] == "Vercel" and "x-vercel-id" in data["headers"]
    res = classify_http(data["status"], data["headers"], data["body"], data["url"])
    assert res.verdict is OK and res.signals == [] and res.vendor is None


def test_real_hoppscotch_shell_against_the_targets_own_anchor():
    anchor = TargetConfig.load("hoppscotch").crawl.anchor
    data = fixture("ok_hoppscotch_io_shell")
    kw = {"status": 200, "headers": data["headers"], "title": extract_title(data["body"]),
          "body_text": data["body"], "anchor": anchor, "url": data["url"]}
    assert classify_capture(interactive_count=59, **kw).verdict is OK          # 59 at 1024px (RECON)
    assert classify_capture(interactive_count=52, **kw).verdict is OK          # 52 at 375px
    before_hydration = classify_capture(interactive_count=0, **kw)
    assert before_hydration.verdict is SUSPECT and before_hydration.signals == ["anchor:interactive=0<8"]
    wrong_site = classify_capture(**{**kw, "title": "Just a moment..."}, interactive_count=59)
    assert wrong_site.verdict is SUSPECT and "anchor:title-mismatch" in wrong_site.signals


def test_real_cloudflare_fronted_pages_are_ok_even_with_the_jsd_beacon():
    memos = fixture("ok_demo_usememos_com")
    assert "/cdn-cgi/challenge-platform/" in memos["body"] and "cf-ray" in memos["headers"]
    assert classify_http(memos["status"], memos["headers"], memos["body"], memos["url"]).verdict is OK
    docs = fixture("ok_docs_hoppscotch_io")
    assert {"cf-ray", "cf-cache-status", "x-vercel-id"} <= set(docs["headers"])
    assert classify_http(docs["status"], docs["headers"], docs["body"], docs["url"]).verdict is OK


# ------------------------------------------------------------------------------ headers first (D32)
@pytest.mark.parametrize("status", [200, 202, 204, 403, 405, 429, 503])
def test_cf_mitigated_challenge_is_blocked_on_any_status(status):
    res = classify_http(status, {"cf-mitigated": "challenge", "content-type": "application/json"}, "{}", URL)
    assert res.verdict is BLOCKED and res.vendor == "cloudflare"
    assert res.signals == ["header:cf-mitigated=challenge"]


@pytest.mark.parametrize(("action", "status"), [("challenge", 202), ("captcha", 405), ("challenge", 200),
                                                ("captcha", 403), ("Challenge", 202)])
def test_aws_waf_action_is_blocked_on_any_status(action, status):
    res = classify_http(status, {"x-amzn-waf-action": action}, "", URL)
    assert res.verdict is BLOCKED and res.vendor == "aws_waf"
    assert res.signals == [f"header:x-amzn-waf-action={action.lower()}"]


@pytest.mark.parametrize(("action", "status"), [("challenge", 429), ("deny", 403), ("Deny", 200),
                                                ("challenge", 200), ("challenge", 503)])
def test_m4_vercel_mitigation_header_is_blocked_on_any_status(action, status):
    headers = {"server": "Vercel", "x-vercel-id": "bom1::abc", "x-vercel-mitigated": action, **HTML}
    res = classify_http(status, headers, PAGE, URL)
    assert res.verdict is BLOCKED and res.vendor == "vercel"
    assert res.signals == [f"header:x-vercel-mitigated={action.lower()}"]


@pytest.mark.parametrize("value", ["log", "bypass", "persistent-action", ""])
def test_m4_other_vercel_mitigation_values_are_not_a_block(value):
    res = classify_http(200, {**HTML, "x-vercel-mitigated": value, "x-vercel-id": "bom1::abc"}, PAGE, URL)
    assert res.verdict is OK and res.vendor is None


def test_header_names_are_case_insensitive_and_mappings_are_accepted():
    res = classify_http(200, {"CF-Mitigated": "Challenge"}, "", URL)
    assert res.verdict is BLOCKED


def test_only_the_documented_header_values_are_hard_signals():
    assert classify_http(200, {"cf-mitigated": "managed"}, PAGE, URL).verdict is OK
    assert classify_http(200, {"x-amzn-waf-action": "allow"}, PAGE, URL).verdict is OK
    assert classify_http(200, {"x-amzn-waf-action": "count"}, PAGE, URL).verdict is OK


def test_n1_duplicate_case_headers_are_merged_and_list_values_joined():
    for headers in ({"cf-mitigated": "challenge", "CF-Mitigated": "managed"},
                    {"CF-Mitigated": "managed", "cf-mitigated": "challenge"},
                    {"cf-mitigated": ["challenge"]}, {"Cf-Mitigated": ("managed", "challenge")}):
        assert classify_http(200, headers, "", URL).verdict is BLOCKED, headers
    assert classify_http(200, {"x-amzn-waf-action": ("allow", "captcha")}, "", URL).verdict is BLOCKED
    assert normalise_headers({"A": "1", "a": ["2", "3"], "B": b"x"}.items()) == {"a": "1, 2, 3", "b": "x"}
    cap = make_capture(target="t", url=URL, access=_access(),
                       headers={"Server": "a", "server": "b", "Via": ["x", "y"]})
    assert cap.headers == {"server": "a, b", "via": "x, y"}


def test_n1_bytes_and_none_bodies_are_accepted():
    assert classify_http(200, HTML, None, URL).verdict is OK
    assert classify_http(200, HTML, b"", URL).verdict is OK
    res = classify_http(200, HTML, b"\xff\xfe<title>Just a moment...</title>", URL)
    assert res.verdict is SUSPECT and res.signals == ["title:just a moment"]
    cap = classify_capture(status=200, headers={}, title=None, body_text=None, interactive_count=None,
                           anchor=None, url=URL)
    assert cap.verdict is OK


# ----------------------------------------------------------------------- presence is never a block (D13)
@pytest.mark.parametrize("headers", [
    {"server": "cloudflare"}, {"cf-ray": "a4944e88eff17eda-MAA"}, {"cf-cache-status": "HIT"},
    {"x-vercel-id": "bom1::abc"}, {"x-amz-cf-id": "Zq7mQK1xT0w4"},
    {"via": "1.1 abc.cloudfront.net (CloudFront)"},
    {"server": "AkamaiGHost"}, {"x-iinfo": "11-1-0 0NNN RT(1 0)"},
    {"set-cookie": "_abck=abc~0~YAAQ; Path=/"}, {"set-cookie": "datadome=abc; Max-Age=31536000"},
    {"set-cookie": "__cf_bm=abc; HttpOnly"}, {"set-cookie": "_px3=abc; Path=/"},
    {"set-cookie": "visid_incap_1=abc; incap_ses_1_2_3=def"}, {"set-cookie": "aws-waf-token=abc"},
    {"x-datadome": "protected", "x-datadome-cid": "AHrlqAAAAAMA", "x-datadome-response": "200"},
])
def test_vendor_presence_alone_is_ok(headers):
    res = classify_http(200, {**HTML, **headers}, PAGE, URL)
    assert res.verdict is OK and res.vendor is None and res.signals == []


def test_x_datadome_headers_are_not_a_hard_signal_even_on_a_403():
    res = classify_http(403, {**HTML, "x-datadome": "protected", "x-datadome-cid": "AHrl"}, PAGE, URL)
    assert res.verdict is SUSPECT                      # the 403 soft guard, not BLOCKED


def test_vendor_is_only_a_label_on_a_soft_result():
    res = classify_http(403, {**HTML, "server": "cloudflare", "cf-ray": "abc"}, PAGE, URL)
    assert res.verdict is SUSPECT and res.vendor == "cloudflare" and res.signals == ["status:403"]


# --------------------------------------------------------------------------- body size is never a signal
def test_empty_and_huge_ok_pages_are_both_ok():
    assert classify_http(200, HTML, "", URL).verdict is OK
    huge = PAGE + "<!-- " + "x" * 2_000_000 + " -->"
    assert classify_http(200, HTML, huge, URL).verdict is OK


@pytest.mark.parametrize("data", _cases())
def test_padding_a_fixture_to_2mb_never_changes_its_verdict(data):
    base = classify_http(data["status"], data["headers"], data["body"], data["url"])
    padded_body = data["body"] + "\n<!-- " + "x" * 2_000_000 + " -->"
    padded = classify_http(data["status"], data["headers"], padded_body, data["url"])
    assert (padded.verdict, padded.vendor, padded.signals) == (base.verdict, base.vendor, base.signals)


# ----------------------------------------------------- Cloudflare Block page: its own rule (case 68)
CF_PAGE_1020 = ("<html><head><title>Access denied</title></head><body>Error code <span>1020</span> "
                "Cloudflare Ray ID: abc</body></html>")


def test_cloudflare_block_page_without_cf_mitigated_is_blocked():
    data = fixture("blocked_cloudflare_block_1020")
    assert "cf-mitigated" not in data["headers"]
    res = classify_http(403, data["headers"], data["body"], data["url"])
    assert res.verdict is BLOCKED and res.signals == ["body:cloudflare-block-page"]


@pytest.mark.parametrize(("status", "headers", "body"), [
    (403, {"server": "cloudflare"}, CF_PAGE_1020),                                     # error code only
    (403, {"server": "cloudflare"}, "<title>Attention Required! | Cloudflare</title><h1>Sorry</h1>"),
    (429, {"cf-ray": "abc"}, "<title>Access denied</title><span>Error 1015</span> You are rate limited"),
    (403, {}, "<title>Attention Required! | Cloudflare</title>"),                # hint is in the body
    (403, {}, "<title>Attention Required!</title><p>Sorry, you have been blocked</p>"),   # title: no hint
    (403, {}, "<p>Error 1020</p><p>Cloudflare Ray ID: a4944e88eff17eda</p>"),          # code needs a hint
    (403, {"server": "cloudflare"}, "<span class=\"cf-error-code\">1020</span>"),
])
def test_cloudflare_block_page_variants(status, headers, body):
    res = classify_http(status, {**HTML, **headers}, body, URL)
    assert res.verdict is BLOCKED and res.vendor == "cloudflare"


@pytest.mark.parametrize("code", [1006, 1007, 1008, 1009, 1010, 1011, 1012, 1015, 1020])
@pytest.mark.parametrize("status", [403, 429])
def test_m4_cloudflare_block_codes_1006_to_1012_1015_1020_are_blocked(code, status):
    body = f"<title>Access denied</title><h1>Error <span class=\"cf-error-code\">{code}</span></h1>Ray ID"
    res = classify_http(status, {**HTML, "server": "cloudflare"}, body, URL)
    assert res.verdict is BLOCKED and res.signals == ["body:cloudflare-block-page"]
    bare = f"<p>Error code {code}</p><p>Cloudflare Ray ID: abc</p>"
    assert classify_http(status, HTML, bare, URL).verdict is BLOCKED


@pytest.mark.parametrize("code", [1000, 1005, 1013, 1014, 1016, 1019, 1021, 10200])
def test_m4_other_four_digit_codes_are_not_block_codes(code):
    res = classify_http(403, {**HTML, "server": "cloudflare"}, f"<title>Oops</title>Error code {code}", URL)
    assert res.verdict is SUSPECT and res.signals == ["status:403"]


@pytest.mark.parametrize(("status", "headers", "body"), [
    (200, {"server": "cloudflare"}, CF_PAGE_1020),                    # not a 403: docs about the error
    (403, {}, "<title>Forbidden</title><p>Internal error code 1020 for ticket 7</p>"),   # no Cloudflare hint
    (404, {"server": "cloudflare"}, CF_PAGE_1020),
])
def test_cloudflare_block_code_rule_needs_a_403_or_429_and_a_cloudflare_hint(status, headers, body):
    assert classify_http(status, {**HTML, **headers}, body, URL).verdict is not BLOCKED


def test_attention_required_title_is_whole_phrase_for_the_block_rule():
    page = "<title>Attention required: your invoice is overdue</title><p>Pay now</p>"
    res = classify_http(403, HTML, page, URL)
    assert res.verdict is SUSPECT and res.signals == ["status:403"]          # not BLOCKED as Cloudflare


# ------------------------------------------------------ hard body markers need their whole conjunction
def test_m1_beacon_plus_just_a_moment_prose_is_ok():
    """Wave-1 false positive: the JS-detections beacon is on every JSD page; one prose phrase is no wall."""
    pages = {
        "loading hint": f"<html><head><title>Acme Dashboard</title>{BEACON}</head><body>"
                        "<p>Just a moment, loading your workspace...</p></body></html>",
        "noscript": f"<html><head><title>Shop</title>{BEACON}</head><body>"
                    "<noscript>Just a moment...</noscript><main>products</main></body></html>",
        "cloudflare docs": f"<html><head><title>Challenges - Cloudflare docs</title>{BEACON}</head><body>"
                           "<h1>Challenge pages</h1><p>Visitors see a page titled \"Just a moment...\" "
                           "while the challenge runs.</p></body></html>",
        "title that merely starts with the phrase": f"<html><head><title>Just a moment, loading Acme | Acme"
                                                    f"</title>{BEACON}</head><body>x</body></html>",
    }
    for label, body in pages.items():
        res = classify_http(200, CF, body, URL)
        assert res.verdict is OK and res.signals == [], (label, res)
        title = extract_title(body)
        cap = classify_capture(status=200, headers=CF, title=title, body_text=body, interactive_count=20,
                               anchor=None, url=URL)
        assert cap.verdict is OK, (label, cap)


@pytest.mark.parametrize("marker", CF_MARKERS)
def test_m1_interstitial_needs_a_just_a_moment_title_and_a_real_marker(marker):
    wall = f"<html><head><title>Just a moment...</title></head><body>{marker}</body></html>"
    res = classify_http(200, HTML, wall, URL)
    assert res.verdict is BLOCKED and res.vendor == "cloudflare"
    assert res.signals == ["title:just-a-moment+interstitial-marker"]
    # the title alone, or with only the beacon, is the soft title guard
    for body in ("<title>Just a moment...</title><p>hi</p>", f"<title>Just a moment...</title>{BEACON}"):
        soft = classify_http(200, HTML, body, URL)
        assert soft.verdict is SUSPECT and soft.signals == ["title:just a moment"], body
    # the marker under another title is not this rule
    other = classify_http(200, HTML, f"<title>Acme</title><p>Just a moment</p>{marker}", URL)
    assert other.verdict is not BLOCKED


def test_challenge_rule_accepts_the_title_passed_separately_from_visible_text():
    res = classify_capture(status=200, headers={}, title="Just a moment...",
                           body_text="<script src=\"/cdn-cgi/challenge-platform/h/g/orchestrate/jsch/v1\">"
                                     "</script>", interactive_count=0,
                           anchor=None, url=URL)
    assert res.verdict is BLOCKED
    assert classify_capture(status=200, headers={}, title="Just a moment...", body_text=BEACON,
                            interactive_count=0, anchor=None, url=URL).verdict is SUSPECT


def test_akamai_needs_the_server_header_the_phrase_the_reference_and_a_403():
    body = "<TITLE>Access Denied</TITLE><H1>Access Denied</H1>Reference #18.2f4b1c02.1760254412.7a3d9e11"
    assert classify_http(403, {**HTML, "server": "AkamaiGHost"}, body, URL).verdict is BLOCKED
    escaped = body.replace("Reference #", "Reference&#32;&#35;")
    assert classify_http(403, {**HTML, "server": "AkamaiGHost"}, escaped, URL).verdict is BLOCKED
    assert classify_http(403, {**HTML, "server": "nginx"}, body, URL).verdict is SUSPECT
    no_ref = "<TITLE>Access Denied</TITLE><H1>Access Denied</H1>"
    assert classify_http(403, {**HTML, "server": "AkamaiGHost"}, no_ref, URL).verdict is SUSPECT
    assert classify_http(200, {**HTML, "server": "AkamaiGHost"}, PAGE, URL).verdict is OK


def test_datadome_needs_a_403():
    body = "<script src=\"https://ct.captcha-delivery.com/c.js\"></script>"
    assert classify_http(403, HTML, body, URL).verdict is BLOCKED
    assert classify_http(200, HTML, body, URL).verdict is not BLOCKED
    # the XHR shape of the same challenge is JSON; the host name is DataDome-specific, so this stays a block
    xhr = "{\"url\":\"https://geo.captcha-delivery.com/captcha/\"}"
    assert classify_http(403, {"content-type": "application/json"}, xhr, URL).verdict is BLOCKED
    assert classify_http(403, {"content-type": "text/plain"}, body, URL).verdict is not BLOCKED


def test_amazon_robot_check_needs_both_markers():
    both = ("<label>Type the characters you see in this image:</label>"
            "<form action=\"/errors/validateCaptcha\">")
    assert classify_http(200, HTML, both, URL).verdict is BLOCKED
    only_form = "<form action=\"/errors/validateCaptcha\"></form>"
    assert classify_http(200, HTML, only_form, URL).verdict is OK
    only_text = "<p>Type the characters you see in this image</p>"
    assert classify_http(200, HTML, only_text, URL).verdict is OK


def test_result_lists_every_hard_signal_and_attributes_the_first():
    data = fixture("blocked_cloudflare_challenge_403_page")
    res = classify_http(data["status"], data["headers"], data["body"], data["url"])
    assert res.signals == ["header:cf-mitigated=challenge", "title:just-a-moment+interstitial-marker"]
    assert res.vendor == "cloudflare" and "cloudflare" in res.reason


# Single-phrase rules are status-gated (RECON section 3): at any other status the phrase is ordinary copy.
_GATED = [   # (id, extra headers, body, statuses where it is a block, vendor)
    ("px-press-hold", {}, "<p>Press &amp; Hold to confirm you are a human</p>", {403}, "perimeterx"),
    ("px-press-hold-raw", {}, "<button>Press & hold to record</button>", {403}, "perimeterx"),
    ("px-collector", {}, "<script>u='https://collector-pxabc123.perimeterx.net/api'</script>", {403},
     "perimeterx"),
    ("akamai", {"server": "AkamaiGHost"}, "<h1>Access Denied</h1>Reference&#32;&#35;18.2f4b", {403},
     "akamai"),
    ("datadome", {}, "<script src=\"https://ct.captcha-delivery.com/c.js\"></script>", {403}, "datadome"),
    ("imperva-phrase", {}, "Request unsuccessful. Incapsula incident ID: 123", {403}, "imperva"),
    ("imperva-with-x-iinfo", {"x-iinfo": "11-1-0 0NNN"}, "Request unsuccessful. Incapsula incident ID: 1",
     {200, 403}, "imperva"),
    ("imperva-with-cookie", {"set-cookie": "visid_incap_123=abc; Path=/"},
     "Request unsuccessful. Incapsula incident ID: 1", {200, 403}, "imperva"),
    ("cf-block-code", {"server": "cloudflare"}, "<span>Error code 1020</span>", {403, 429}, "cloudflare"),
]


@pytest.mark.parametrize("status", [200, 202, 204, 301, 302, 304, 400, 401, 404, 405, 429, 500, 503, 403])
@pytest.mark.parametrize(("rule", "extra", "body", "gate", "vendor"), _GATED, ids=[g[0] for g in _GATED])
def test_m2_single_phrase_rules_block_only_on_their_status(rule, extra, body, gate, vendor, status):
    page = f"<html><head><title>Page</title></head><body>{body}</body></html>"
    res = classify_http(status, {**HTML, **extra}, page, URL)
    if status in gate:
        assert res.verdict is BLOCKED and res.vendor == vendor, res
    else:
        assert res.verdict is not BLOCKED, res


def test_m2_the_wave1_repros_are_ok():
    voice = ("<html><head><title>Voice notes</title></head><body><button>Press &amp; hold to record</button>"
             "</body></html>")
    assert classify_http(200, HTML, voice, URL).verdict is OK
    docs = "<title>Docs</title><h1>Access Denied</h1><p>Reference #12 in the manual</p>"
    assert classify_http(200, {**HTML, "server": "AkamaiGHost"}, docs, URL).verdict is OK
    kb = "<title>KB</title><p>Error text: Request unsuccessful. Incapsula incident ID: 123</p>"
    assert classify_http(200, HTML, kb, URL).verdict is OK
    marked = classify_http(200, {**HTML, "x-iinfo": "11-1-0"}, kb, URL)
    assert marked.verdict is BLOCKED                                  # with Imperva's own header: a real wall


@pytest.mark.parametrize("ctype", ["text/plain", "text/markdown; charset=utf-8", "application/octet-stream",
                                   "application/json"])
def test_m2_body_marker_rules_apply_to_html_only(ctype):
    walls = [
        ("<p>Press &amp; Hold to confirm you are a human</p>", {}),
        ("<h1>Access Denied</h1>Reference&#32;&#35;18.2f4b", {"server": "AkamaiGHost"}),
        ("Request unsuccessful. Incapsula incident ID: 1", {"x-iinfo": "1"}),
        ("<span>Error code 1020</span>", {"server": "cloudflare"}),
        (AMAZON_CHECK, {}),
        ("<title>Just a moment...</title><script>window._cf_chl_opt={}</script>", {}),
    ]
    for body, extra in walls:
        for status in (200, 403, 429):
            res = classify_http(status, {"content-type": ctype, **extra}, body, URL)
            assert res.verdict is OK, (body, status, res)      # a non-HTML body is data, not a wall


def test_m2_header_rules_still_apply_to_non_html_bodies():
    res = classify_http(200, {"content-type": "text/plain", "cf-mitigated": "challenge"}, "# docs", URL)
    assert res.verdict is BLOCKED


# ----------------------------------------------------------------------------------------- soft guards
@pytest.mark.parametrize("status", [202, 403, 405, 429, 503])
def test_html_with_a_wall_ish_status_is_suspect(status):
    res = classify_http(status, HTML, PAGE, URL)
    assert res.verdict is SUSPECT and res.signals == [f"status:{status}"]


@pytest.mark.parametrize("status", [200, 204, 301, 304, 400, 404, 500, 502, None])
def test_other_statuses_are_not_a_wall(status):
    assert classify_http(status, HTML, PAGE, URL).verdict is OK


def test_non_html_error_bodies_are_not_page_challenges():
    assert classify_http(403, {"content-type": "application/json"}, "{\"error\":\"nope\"}", URL).verdict is OK
    assert classify_http(429, {"content-type": "text/plain"}, "slow down", URL).verdict is OK
    assert classify_http(403, {}, "", URL).verdict is SUSPECT        # unknown type is not exempt


@pytest.mark.parametrize("title", ["Just a moment...", "Attention Required! | Cloudflare", "Access Denied",
                                   "Robot Check", "Are you a robot?", "Verifying you are human",
                                   "Pardon Our Interruption", "Access to this page has been denied",
                                   "Vercel Security Checkpoint", "Security Check", "One more step",
                                   "Request blocked", "  just   a  MOMENT  ",
                                   "Access denied | x.com used Cloudflare to restrict access",
                                   "Checking your browser before accessing x.com"])
def test_challengey_titles_are_suspect_without_a_hard_marker(title):
    res = classify_http(200, HTML, f"<html><head><title>{title}</title></head><body>hi</body></html>", URL)
    assert res.verdict is SUSPECT and res.signals and res.signals[0].startswith("title:")


@pytest.mark.parametrize("title", [
    "Security Checklist | Acme", "Access Denied handling | Docs", "One more step - Acme Onboarding",
    "Request blocked policy settings", "Robot Checkpoint Setup", "Fix Access Denied errors in Windows | Docs",
    "Access Denied: configuring permissions", "Just a moment, loading Acme", "Security Check-in",
    "Robot Checks", "Attention Required for invoice 7", "Are you a robot? A history of chatbots",
    "One more step to go live",
    "Vercel Security Checkpoint settings",
])
def test_m1_titles_that_merely_start_with_a_challenge_phrase_are_ok(title):
    res = classify_http(200, HTML, f"<html><head><title>{title}</title></head><body>hi</body></html>", URL)
    assert res.verdict is OK, (title, res)


def test_ordinary_titles_that_merely_mention_the_words_are_ok():
    page = "<title>Fix Access Denied errors in Windows | Docs</title><p>hi</p>"
    assert classify_http(200, HTML, page, URL).verdict is OK


def test_m2_titles_in_non_html_bodies_are_not_page_titles():
    md = "# Docs\n```html\n<title>Access Denied</title>\n```\nCloudflare shows \"Just a moment...\" first.\n"
    for ctype in ("text/plain", "text/markdown", "text/plain; charset=utf-8"):
        res = classify_http(200, {"content-type": ctype}, md, URL)
        assert res.verdict is OK and res.signals == [], ctype
    res = classify_capture(status=200, headers={"content-type": "text/plain"}, title=None, body_text=md,
                           interactive_count=None, anchor=None, url=URL)
    assert res.verdict is OK
    # the same bytes served as HTML are a page whose title is "Access Denied"
    as_html = "<html><head><title>Access Denied</title></head><body>x</body></html>"
    assert classify_http(200, HTML, as_html, URL).verdict is SUSPECT
    assert classify_http(200, {}, as_html, URL).verdict is SUSPECT           # unknown type is not exempt


def test_n1_custom_elements_are_not_titles():
    assert extract_title("<titlebar>Access Denied</titlebar>") is None
    assert extract_title("<title-x>Access Denied</title-x><p>x</p>") is None
    assert extract_title("<TITLE lang=en>Real</TITLE>") == "Real"
    assert extract_title("<title\n>Spaced</title>") == "Spaced"
    assert classify_http(200, HTML, "<titlebar>Access Denied</titlebar><p>x</p>", URL).verdict is OK


def test_vercel_checkpoint_title_is_soft_and_labels_the_vendor():
    data = fixture("suspect_vercel_checkpoint_title_200")
    res = classify_http(data["status"], data["headers"], data["body"], data["url"])
    assert res.verdict is SUSPECT and res.vendor == "vercel"
    assert res.signals == ["title:vercel security checkpoint"]


def test_anchor_min_interactive_and_title_regex():
    anchor = AnchorConfig(title_regex="(?i)hoppscotch", min_interactive=8)

    def go(title, count):
        return classify_capture(status=200, headers={}, title=title, body_text=PAGE, interactive_count=count,
                                anchor=anchor, url=URL)

    assert go("Hoppscotch • Open source API development ecosystem", 59).verdict is OK
    assert go("Hoppscotch", 8).verdict is OK                                    # boundary: min is inclusive
    low = go("Hoppscotch", 7)
    assert low.verdict is SUSPECT and low.signals == ["anchor:interactive=7<8"]
    wrong = go("Example Domain", 59)
    assert wrong.verdict is SUSPECT and wrong.signals == ["anchor:title-mismatch"]
    # a page with no title at all (body has none either); a blank title string is "unknown", not "no title"
    both = classify_capture(status=200, headers={}, title="", body_text="<html><body></body></html>",
                            interactive_count=0, anchor=anchor, url=URL)
    assert both.verdict is SUSPECT and both.signals == ["anchor:title-mismatch", "anchor:interactive=0<8"]


def test_anchor_checks_are_skipped_when_unknown_or_unconfigured():
    kw = {"status": 200, "headers": {}, "body_text": PAGE, "url": URL}
    assert classify_capture(title="x", interactive_count=0, anchor=None, **kw).verdict is OK
    assert classify_capture(title="x", interactive_count=None, anchor=AnchorConfig(), **kw).verdict is OK
    broken = AnchorConfig(title_regex="(unclosed")
    res = classify_capture(title="x", interactive_count=10, anchor=broken, **kw)
    assert res.verdict is SUSPECT and res.signals == ["anchor:title-regex-invalid"]


def test_rung_is_carried_through():
    res = classify_capture(status=200, headers={}, title="t", body_text=PAGE, interactive_count=None,
                           anchor=None, url=URL, rung="L1")
    assert res.rung == "L1"


# ------------------------------------------------------------------------------- fingerprint collapse
def _collapse(prior, url="https://x/new", exempt=None):
    return classify_capture(status=200, headers={}, title="App", body_text=PAGE, interactive_count=None,
                            anchor=None, url=url, prior_fingerprints=prior, collapse_exempt_patterns=exempt)


def test_fingerprint_collapse_needs_many_distinct_urls_sharing_one_fingerprint():
    assert COLLAPSE_MIN_URLS == 3
    assert _collapse(None).verdict is OK and _collapse({}).verdict is OK
    three_pages = {"https://x/a": "f1", "https://x/b": "f1", "https://x/new": "f1", "https://x/c": "f2"}
    assert _collapse(three_pages).verdict is SUSPECT               # a, b and new share f1
    two_pages = {"https://x/a": "f1", "https://x/new": "f1", "https://x/c": "f2"}
    assert _collapse(two_pages).verdict is OK
    three = {f"https://x/{c}": "f1" for c in "abc"} | {"https://x/new": "f1", "https://x/d": "f2"}
    res = _collapse(three)
    assert res.verdict is SUSPECT and res.signals == ["fingerprint-collapse:4-urls"]


def test_collapse_looks_at_the_captures_own_fingerprint_when_its_url_is_present():
    prior = {f"https://x/{c}": "wall" for c in "abcd"} | {"https://x/real": "real-page"}
    assert _collapse(prior, url="https://x/real").verdict is OK
    assert _collapse(prior, url="https://x/a").verdict is SUSPECT


def test_m3_a_capture_whose_url_is_absent_is_not_implicated_by_other_urls():
    prior = {"https://x/a": "f", "https://x/b": "f", "https://x/c": "f"}
    assert _collapse(prior, url="https://x/real").verdict is OK       # nothing is known about /real
    assert _collapse(prior, url="https://x/a").verdict is SUSPECT     # but /a is one of the three
    assert _collapse(prior, url="https://x/a?tab=2").verdict is SUSPECT   # same page: query is ignored


def test_collapse_counts_pages_not_query_strings_or_fragments():
    prior = {"https://x/a?i=1": "f", "https://x/a?i=2": "f", "https://x/a#top": "f", "https://x/b": "f"}
    assert _collapse(prior, url="https://x/b").verdict is OK       # two pages (/a, /b), not four URLs


def test_m3_templated_routes_can_be_exempted_from_the_collapse_guard():
    prior = {f"https://x/e/{i}": "event-page" for i in (1, 2, 3, 4)} | {"https://x/home": "home"}
    assert _collapse(prior, url="https://x/e/2").verdict is SUSPECT          # without the exemption
    exempt = [r"/e/\d+$"]
    assert _collapse(prior, url="https://x/e/2", exempt=exempt).verdict is OK
    assert _collapse(prior, url="https://x/home", exempt=exempt).verdict is OK
    # exempt urls neither raise the signal nor count toward another page's group
    shared = {"https://x/e/1": "f", "https://x/e/2": "f", "https://x/e/3": "f", "https://x/e/new": "f"}
    assert _collapse(shared, url="https://x/e/new").verdict is SUSPECT
    assert _collapse(shared, url="https://x/e/new", exempt=[r"/e/\d+$"]).verdict is OK
    # a wall that is not exempt still trips
    wall = {f"https://x/{c}": "wall" for c in "abc"}
    assert _collapse(wall, url="https://x/a", exempt=[r"/e/\d+$"]).verdict is SUSPECT
    # a broken pattern is ignored (stricter, never looser) rather than raising
    assert _collapse(prior, url="https://x/e/2", exempt=["(unclosed"]).verdict is SUSPECT


def test_the_collapse_parameter_is_documented():
    assert "collapse_exempt_patterns" in (classify_capture.__doc__ or "")
    assert "modal" in (classify_capture.__doc__ or "").lower()


# ------------------------------------------------------------------------------------------ robots.txt
def test_unreadable_robots_is_disallowed_and_empty_is_allowed():            # case 69
    assert robots_allows(None, UA, "https://hoppscotch.io/") is DISALLOWED
    assert robots_allows("", UA, "https://hoppscotch.io/") is OK
    assert robots_allows("# nothing here\n", UA, "https://hoppscotch.io/") is OK


def test_robots_and_collapse_fail_closed_on_unparseable_urls():
    assert robots_allows("User-agent: *\nAllow: /\n", UA, "http://[::1/") is DISALLOWED
    assert _collapse({"http://[::1/": "f"}).verdict is OK       # a bad prior url must not crash it


def test_real_hoppscotch_io_robots_allows_every_seed():
    robots = fixture("robots_hoppscotch_io")["body"]
    assert "Allow: /" in robots
    for seed in TargetConfig.load("hoppscotch").crawl.seed_urls:
        assert robots_allows(robots, UA, seed) is OK


def test_real_docs_hoppscotch_robots_rules():
    robots = fixture("robots_docs_hoppscotch_io")["body"]
    docs = "https://docs.hoppscotch.io"
    assert robots_allows(robots, UA, f"{docs}/documentation/features/rest-api-testing") is OK
    assert robots_allows(robots, UA, f"{docs}/llms-full.txt") is OK
    assert robots_allows(robots, UA, f"{docs}/cdn-cgi/trace") is DISALLOWED
    assert robots_allows(robots, UA, f"{docs}/_next/static/chunks/a.js") is DISALLOWED
    assert robots_allows(robots, UA, f"{docs}/_next/image?url=%2Fa.png&w=64") is OK       # longer Allow wins


def test_robots_group_selection_and_longest_match():
    robots = """
    User-agent: *
    Disallow: /
    User-agent: blastradius
    Disallow: /private
    Allow: /private/open
    """
    assert robots_allows(robots, UA, "https://x/anything") is OK          # our group, not the * group
    assert robots_allows(robots, UA, "https://x/private/data") is DISALLOWED
    assert robots_allows(robots, UA, "https://x/private/open/page") is OK
    assert robots_allows(robots, "OtherBot/1.0", "https://x/anything") is DISALLOWED
    assert robots_allows(robots, "BLASTRADIUS", "https://x/private") is DISALLOWED     # token: any case


def test_robots_ties_go_to_allow_and_empty_disallow_means_everything():
    assert robots_allows("User-agent: *\nDisallow: /a\nAllow: /a\n", UA, "https://x/a") is OK
    assert robots_allows("User-agent: *\nAllow: /a\nDisallow: /a\n", UA, "https://x/a") is OK
    assert robots_allows("User-agent: *\nDisallow:\n", UA, "https://x/anything") is OK


def test_robots_wildcards_anchors_and_queries():
    robots = "User-agent: *\nDisallow: /*.json$\nDisallow: /search?*sort=\nDisallow: /tmp*/cache\n"
    assert robots_allows(robots, UA, "https://x/data/a.json") is DISALLOWED
    assert robots_allows(robots, UA, "https://x/data/a.json?v=1") is OK          # $ anchors the end
    assert robots_allows(robots, UA, "https://x/data/a.jsonl") is OK
    assert robots_allows(robots, UA, "https://x/search?q=a&sort=asc") is DISALLOWED
    assert robots_allows(robots, UA, "https://x/search?q=a") is OK
    assert robots_allows(robots, UA, "https://x/tmp-1/cache") is DISALLOWED
    assert robots_allows(robots, UA, "https://x/tmp-1/other#frag") is OK


def test_robots_parsing_quirks():
    text = ("\ufeffUser-agent: a\nuser-agent: blastradius  # us\nContent-Signal: ai-train=no\n"
            "DISALLOW: /x\n# comment\n\nSitemap: https://x/s.xml\nUser-agent: *\nDisallow: /y\n")
    assert robots_allows(text, UA, "https://x/x/1") is DISALLOWED        # two UA lines share one group
    assert robots_allows(text, UA, "https://x/y") is OK                   # specific group wins over *
    assert robots_allows(text, "Zed", "https://x/y") is DISALLOWED
    assert robots_allows(text, UA, "https://x") is OK                     # no path means /
    assert robots_allows("Disallow: /\nUser-agent: *\n", UA, "https://x/") is OK    # rule before any agent


def test_robots_groups_with_the_same_agent_are_merged():
    text = ("User-agent: blastradius\nDisallow: /a\n\nUser-agent: *\nDisallow: /b\n\n"
            "User-agent: blastradius\nDisallow: /c\n")
    assert robots_allows(text, UA, "https://x/a") is DISALLOWED
    assert robots_allows(text, UA, "https://x/c") is DISALLOWED
    assert robots_allows(text, UA, "https://x/b") is OK


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_n2_robots_line_endings(newline):
    text = newline.join(["User-agent: *", "Disallow: /a", "Allow: /a/open", ""])
    assert robots_allows(text, UA, "https://x/a/b") is DISALLOWED
    assert robots_allows(text, UA, "https://x/a/open") is OK


def test_n2_unicode_line_separators_do_not_split_rules():
    """RFC 9309 lines end at LF, CR or CRLF only; U+2028, U+0085 and friends stay inside the rule."""
    for sep in ("\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c"):
        text = f"User-agent: *\nDisallow: /ok{sep}Disallow: /\n"
        assert robots_allows(text, UA, "https://x/anything") is OK, repr(sep)


def test_n2_percent_encoded_reserved_characters_are_not_decoded():
    block = "User-agent: *\nDisallow: /a%2Fb\n"
    assert robots_allows(block, UA, "https://x/a/b") is OK               # %2F is data, not a separator
    assert robots_allows(block, UA, "https://x/a%2Fb") is DISALLOWED
    assert robots_allows(block, UA, "https://x/a%2fb") is DISALLOWED     # hex digits are case-insensitive
    assert robots_allows("User-agent: *\nDisallow: /a/b\n", UA, "https://x/a%2Fb") is OK
    # '*' and '$' written as escapes are literals, not the wildcard and the anchor
    assert robots_allows("User-agent: *\nDisallow: /a%2Ab\n", UA, "https://x/aXb") is OK
    assert robots_allows("User-agent: *\nDisallow: /a%24\n", UA, "https://x/a%24b") is DISALLOWED
    assert robots_allows("User-agent: *\nDisallow: /a%24\n", UA, "https://x/a") is OK


def test_n2_unreserved_and_non_ascii_octets_compare_equal_encoded_or_not():
    assert robots_allows("User-agent: *\nDisallow: /%7Euser\n", UA, "https://x/~user/p") is DISALLOWED
    assert robots_allows("User-agent: *\nDisallow: /~user\n", UA, "https://x/%7Euser") is DISALLOWED
    assert robots_allows("User-agent: *\nDisallow: /café\n", UA, "https://x/caf%C3%A9") is DISALLOWED
    assert robots_allows("User-agent: *\nDisallow: /caf%C3%A9\n", UA, "https://x/café") is DISALLOWED
    assert robots_allows("User-agent: *\nDisallow: /a%41\n", UA, "https://x/aA") is DISALLOWED
    assert robots_allows("User-agent: *\nDisallow: /a b\n", UA, "https://x/a%20b") is DISALLOWED


def test_m4_wildcards_do_not_backtrack():
    for wildcards, plen in ((10, 40), (15, 40), (20, 60), (60, 600)):
        robots = "User-agent: *\nDisallow: /" + "*a" * wildcards + "b\n"
        with finishes_within(1.0):
            assert robots_allows(robots, UA, "https://x/" + "a" * plen) is OK
            assert robots_allows(robots, UA, "https://x/" + "a" * plen + "b") is DISALLOWED


def test_m4_a_million_characters_of_whitespace_in_robots_are_cheap():
    text = "User-agent: *\n" + " " * 300_000 + "Disallow: /a\n" + "#" + " " * 100_000 + "\nDisallow: /b\n"
    with finishes_within(1.0):
        assert robots_allows(text, UA, "https://x/a") is DISALLOWED
        assert robots_allows(text, UA, "https://x/b") is DISALLOWED


def test_m4_robots_is_read_up_to_500_kib_and_no_further():
    text = "User-agent: *\nDisallow: /early\n" + "# " + "x" * 600_000 + "\nDisallow: /late\n"
    assert robots_allows(text, UA, "https://x/early") is DISALLOWED
    # past the 512 KiB read limit the unread tail may hold any rule: fail CLOSED (it used to fail open)
    assert robots_allows(text, UA, "https://x/late") is DISALLOWED


def test_m4_thousands_of_rules_and_urls_are_fast():
    robots = "User-agent: *\n" + "".join(f"Disallow: /p{i}/*/x{i}\n" for i in range(5000))
    with finishes_within(1.0):
        for j in range(200):
            assert robots_allows(robots, UA, f"https://x/foo/{j}") is OK


def test_m4_collapsed_star_runs_and_trailing_dollar():
    for pattern in ("/a**b", "/a***b", "/a*b"):
        text = f"User-agent: *\nDisallow: {pattern}\n"
        assert robots_allows(text, UA, "https://x/aXXb") is DISALLOWED
        assert robots_allows(text, UA, "https://x/ab") is DISALLOWED
        assert robots_allows(text, UA, "https://x/aXX") is OK
    anchored = "User-agent: *\nDisallow: /a*b$\n"
    assert robots_allows(anchored, UA, "https://x/aXXb") is DISALLOWED
    assert robots_allows(anchored, UA, "https://x/aXXbc") is OK
    assert robots_allows("User-agent: *\nDisallow: /a*$\n", UA, "https://x/anything") is DISALLOWED
    assert robots_allows("User-agent: *\nDisallow: /*$\n", UA, "https://x/anything") is DISALLOWED
    assert robots_allows("User-agent: *\nDisallow: /ab*b$\n", UA, "https://x/ab") is OK     # no overlap


def _reference_matches(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    parts = (pattern[:-1] if anchored else pattern).split("*")
    regex = ".*".join(re.escape(p) for p in parts) + (r"\Z" if anchored else "")
    return re.match(regex, path, re.DOTALL) is not None


def test_m4_the_linear_matcher_agrees_with_the_regex_it_replaced():
    rng = random.Random(9309)
    for _ in range(3000):
        pattern = rng.choice(["/", "/", "*"]) + "".join(rng.choices("ab*/?=", k=rng.randrange(0, 9)))
        pattern += rng.choice(["", "", "$"])
        path = ("/" + "".join(rng.choices("ab/?=", k=rng.randrange(0, 11)))).rstrip("?")
        want = DISALLOWED if _reference_matches(pattern, path) else OK
        got = robots_allows(f"User-agent: *\nDisallow: {pattern}\n", UA, "https://x" + path)
        assert got is want, (pattern, path)


def test_m4_rule_count_per_group_and_url_length_are_capped():
    n = ROBOTS_MAX_RULES_PER_GROUP
    robots = "User-agent: *\n" + "".join(f"Disallow: /r{i:05d}/\n" for i in range(n + 5))
    assert robots_allows(robots, UA, f"https://x/r{n - 1:05d}/") is DISALLOWED     # the 1000th is read
    # the first unread rule: the group is truncated, so unknown paths are denied (fail closed, was fail open)
    assert robots_allows(robots, UA, f"https://x/r{n:05d}/") is DISALLOWED
    assert robots_allows(robots, UA, "https://x/r00000/") is DISALLOWED
    base = "https://x/"
    at_cap = base + "a" * (ROBOTS_MAX_URL_CHARS - len(base))
    assert len(at_cap) == ROBOTS_MAX_URL_CHARS
    assert robots_allows("User-agent: *\nAllow: /\n", UA, at_cap) is OK
    assert robots_allows("User-agent: *\nAllow: /\n", UA, at_cap + "a") is DISALLOWED      # fail closed
    with finishes_within(1.0):
        assert robots_allows("User-agent: *\nDisallow: /*a*a*a*b\n", UA, base + "a" * 5_000_000) is DISALLOWED


# ------------------------------------------------------------------------------------------- breaker
def test_breaker_trips_after_two_consecutive_blocked():
    b = Breaker()
    assert b.threshold == 2
    assert [b.record(v) for v in (BLOCKED, OK, BLOCKED, BLOCKED)] == [False, False, False, True]


def test_breaker_suspect_and_disallowed_are_neutral():
    b = Breaker()
    assert b.record(BLOCKED) is False
    assert b.record(SUSPECT) is False and b.consecutive == 1       # neither trips nor resets
    assert b.record(DISALLOWED) is False and b.consecutive == 1
    assert b.record(BLOCKED) is True


def test_breaker_ok_resets_and_threshold_is_configurable():
    b = Breaker(threshold=3)
    assert [b.record(BLOCKED) for _ in range(2)] == [False, False]
    b.record(OK)
    assert [b.record(BLOCKED) for _ in range(3)] == [False, False, True]
    assert b.record(OK) is False
    with pytest.raises(ValueError):
        Breaker(threshold=0)


# ------------------------------------------------------------------------------------------- capture
def _access(verdict=OK, **kw) -> AccessResult:
    return AccessResult(verdict=verdict, **kw)


def test_header_allowlist_is_minimal_and_has_no_credentials():
    assert not HEADER_ALLOWLIST & SECRET_HEADERS
    assert {"cookie", "set-cookie", "authorization"} <= SECRET_HEADERS
    read_by_rules = {"cf-mitigated", "x-amzn-waf-action", "x-vercel-mitigated", "server", "content-type"}
    assert read_by_rules <= HEADER_ALLOWLIST
    assert len(HEADER_ALLOWLIST) <= 16 and all(h == h.lower() for h in HEADER_ALLOWLIST)


def test_make_capture_keeps_only_allowlisted_headers():
    cap = make_capture(target="t", url=URL, access=_access(), status=200, headers={
        "Cookie": "sid=1", "Set-Cookie": "a=b", "Authorization": "Bearer abcdefghijklmnop", "X-Api-Key": "k",
        "Server": "Vercel", "CF-Ray": "abc-MAA", "Content-Type": "text/html", "X-Anything": "no",
        "X-Vercel-Mitigated": "challenge"})
    assert cap.headers == {"server": "Vercel", "cf-ray": "abc-MAA", "content-type": "text/html",
                           "x-vercel-mitigated": "challenge"}
    assert "sid=1" not in cap.model_dump_json() and "abcdefghijklmnop" not in cap.model_dump_json()


def test_capture_id_is_a_content_hash_without_clock_or_status():
    base = {"target": "t", "url": "https://x/a", "final_url": "https://x/b", "parent_id": "cap_p",
            "action": "click:Docs"}
    a = make_capture(**base, access=_access(), status=200, title="One", captured_at="2026-01-01T00:00:00Z")
    b = make_capture(**base, access=_access(), status=204, title="Two", captured_at="2027-02-02T00:00:00Z",
                     headers={"server": "x"}, html_path="a.html", interactive_count=3)
    assert a.id == b.id and a.id.startswith("cap_")
    for change in ({"target": "u"}, {"final_url": "https://x/c"}, {"parent_id": "cap_q"},
                   {"action": "click:Home"}):
        assert make_capture(**{**base, **change}, access=_access()).id != a.id
    assert make_capture(**{**base, "url": "https://x/other"}, access=_access()).id == a.id   # url: no input


def test_m7_capture_id_includes_the_verdict():
    base = {"target": "t", "url": "https://x/a", "final_url": "https://x/b", "parent_id": "cap_p",
            "action": "click:Docs"}
    ids = {v: make_capture(**base, access=_access(v)).id for v in (OK, SUSPECT, BLOCKED, DISALLOWED)}
    assert len(set(ids.values())) == 4
    # the signals are not part of the id: the same verdict observed twice is the same capture
    again = make_capture(**base, access=_access(BLOCKED, signals=["header:cf-mitigated=challenge"]))
    assert again.id == ids[BLOCKED]


def test_make_capture_defaults():
    cap = make_capture(target="t", url=URL, access=_access())
    assert cap.final_url == URL and cap.source_kind == "live" and cap.captured_at
    assert cap.status is None and cap.headers == {} and cap.parent_id is None


def test_append_capture_ok_writes_row_and_evidence_but_no_failure(tmp_path: Path):
    run = run_ctx(tmp_path)
    cap = make_capture(target="hoppscotch", url="https://hoppscotch.io/", access=_access(), status=200,
                       title="Hoppscotch", headers={"server": "Vercel", "set-cookie": "a=b"})
    evidence_id = append_capture(run, cap)
    rows = jsonl(run.run_dir / "crawl" / "captures.jsonl")
    assert len(rows) == 1 and Capture.model_validate(rows[0]) == cap
    ev = jsonl(run.run_dir / "evidence.jsonl")
    assert len(ev) == 1 and ev[0]["kind"] == "crawl.capture" and ev[0]["ref"] == cap.id
    assert ev[0]["verdict"] == "OK" and run.evidence.exists(ev[0]["evidence_id"])
    assert evidence_id == ev[0]["evidence_id"]
    assert run.failures.items == [] and not (run.run_dir / "failures.jsonl").read_text()


@pytest.mark.parametrize(("verdict", "code"), [(BLOCKED, "ACCESS_BLOCKED"), (SUSPECT, "ACCESS_SUSPECT"),
                                               (DISALLOWED, "ACCESS_DISALLOWED")])
def test_append_capture_records_the_matching_failure_with_signals(tmp_path: Path, verdict, code):
    run = run_ctx(tmp_path)
    access = _access(verdict, vendor="cloudflare", signals=["header:cf-mitigated=challenge", "status:403"],
                     reason="why")
    cap = make_capture(target="hoppscotch", url="https://hoppscotch.io/graphql", access=access, status=403)
    evidence_id = append_capture(run, cap)
    fails = jsonl(run.run_dir / "failures.jsonl")
    assert len(fails) == 1 and fails[0]["code"] == code and fails[0]["stage"] == "access"
    ctx = fails[0]["context"]
    assert ctx["signals"] == ["header:cf-mitigated=challenge", "status:403"]
    assert ctx["capture_id"] == cap.id and ctx["vendor"] == "cloudflare" and ctx["status"] == 403
    assert ctx["url"] == "https://hoppscotch.io/graphql"
    assert ctx["evidence_id"] == evidence_id           # the gap can cite the capture's evidence row
    assert jsonl(run.run_dir / "crawl" / "captures.jsonl")[0]["access"]["verdict"] == verdict.value


def test_m7_append_capture_returns_the_evidence_id_gaps_cite(tmp_path: Path):
    run = run_ctx(tmp_path)
    cap = make_capture(target="t", url=URL, access=_access(BLOCKED, signals=["s"], reason="r"), status=403)
    evidence_id = append_capture(run, cap)
    assert isinstance(evidence_id, str) and run.evidence.exists(evidence_id)
    assert run.evidence.get(evidence_id)["ref"] == cap.id
    assert append_capture(run, cap) == evidence_id               # idempotent: same row, same id


def test_append_capture_evidence_is_idempotent_and_distinguishes_verdicts(tmp_path: Path):
    run = run_ctx(tmp_path)
    cap = make_capture(target="t", url=URL, access=_access(), status=200)
    append_capture(run, cap)
    append_capture(run, cap)
    assert len(run.evidence) == 1                          # same capture, same summary: one evidence row
    blocked = make_capture(target="t", url=URL, access=_access(BLOCKED), status=200)
    append_capture(run, blocked)
    assert blocked.id != cap.id and len(run.evidence) == 2


def test_m7_a_blocked_reobservation_survives_id_based_dedup(tmp_path: Path):
    """The explorer's idiom is `if capture.id not in captured`: a wall seen after the page must still land."""
    run = run_ctx(tmp_path)
    base = {"target": "hoppscotch", "url": "https://hoppscotch.io/", "final_url": "https://hoppscotch.io/",
            "parent_id": None, "action": "goto"}
    seen: set[str] = set()
    for access, status in ((_access(), 200), (_access(BLOCKED, signals=["header:cf-mitigated=challenge"],
                                                      reason="cf"), 403)):
        cap = make_capture(**base, access=access, status=status)
        if cap.id not in seen:
            seen.add(cap.id)
            append_capture(run, cap)
    rows = jsonl(run.run_dir / "crawl" / "captures.jsonl")
    assert [r["access"]["verdict"] for r in rows] == ["OK", "BLOCKED"]
    assert [f["code"] for f in jsonl(run.run_dir / "failures.jsonl")] == ["ACCESS_BLOCKED"]
    refs = {e["ref"] for e in jsonl(run.run_dir / "evidence.jsonl")}
    assert len(refs) == 2


def test_append_capture_redacts_secret_shaped_urls_but_not_the_id(tmp_path: Path):
    run = run_ctx(tmp_path)
    url = "https://app.example.com/cb?token=abcdef1234567890abcdef&tab=1"
    cap = make_capture(target="t", url=url, access=_access(BLOCKED, signals=["s"], reason="r"), status=403)
    append_capture(run, cap)
    blob = persisted(run)
    assert "abcdef1234567890abcdef" not in blob and "token=[REDACTED]" in blob and "tab=1" in blob
    assert cap.id.startswith("cap_") and cap.url == url      # the in-memory object is untouched


OPAQUE = "opaque1234567890abcdef"
_SECRET_URLS = [      # (url, the secret that must not reach disk)
    ("https://app.example.com/cb?token=abcdef1234567890abcdef&tab=1", "abcdef1234567890abcdef"),
    ("https://app.example.com/cb#access_token=opaque1234567890abcdef&token_type=bearer", OPAQUE),
    ("https://app.example.com/cb?refresh_token=opaque1234567890abcdef", "opaque1234567890abcdef"),
    ("https://app.example.com/cb?id_token=opaque1234567890abcdef", "opaque1234567890abcdef"),
    ("https://app.example.com/cb?client_secret=opaque1234567890abcdef", "opaque1234567890abcdef"),
    ("https://app.example.com/cb?code=4%2F0AfJohXn1234567890abcdef&state=xyz", "0AfJohXn1234567890abcdef"),
    ("https://app.example.com/cb?sessionid=opaque1234567890abcdef", "opaque1234567890abcdef"),
    ("https://app.example.com/cb?SID=opaque1234567890abcdef", "opaque1234567890abcdef"),
    ("https://app.example.com/cb?key=opaque1234567890abcdef", "opaque1234567890abcdef"),
    ("https://app.example.com/cb?apikey=opaque1234567890abcdef", "opaque1234567890abcdef"),
    ("https://app.example.com/cb?x-api-key=opaque1234567890abcdef", "opaque1234567890abcdef"),
    ("https://app.example.com/cb?auth=opaque1234567890abcdef", "opaque1234567890abcdef"),
    ("https://app.example.com/reset?password=hunter2hunter2", "hunter2hunter2"),
    ("https://app.example.com/reset?pass=hunter2hunter2", "hunter2hunter2"),
    ("https://app.example.com/dl?X-Amz-Signature=opaque1234567890abcdef&X-Amz-Expires=60", OPAQUE),
    ("https://app.example.com/dl?sig=opaque1234567890abcdef", "opaque1234567890abcdef"),
    ("https://app.example.com/a;jsessionid=opaque1234567890abcdef?x=1", "opaque1234567890abcdef"),
    ("https://app.example.com/cb?Token=opaque1234567890abcdef", "opaque1234567890abcdef"),
    ("https://app.example.com/cb?a=1&access%5Ftoken=opaque1234567890abcdef", "opaque1234567890abcdef"),
    ("https://user:hunter2hunter2@app.example.com/", "hunter2hunter2"),
    ("https://hunter2hunter2@app.example.com/path", "hunter2hunter2"),
    ("https://app.example.com/#/cb?access_token=opaque1234567890abcdef", "opaque1234567890abcdef"),
]


@pytest.mark.parametrize(("url", "secret"), _SECRET_URLS)
def test_m5_scrub_url_masks_secret_parameters_and_userinfo(url, secret):
    scrubbed = scrub_url(url)
    assert secret not in scrubbed and "[REDACTED]" in scrubbed
    assert scrub_url(scrubbed) == scrubbed                        # idempotent


def test_m5_scrub_url_keeps_ordinary_urls_and_ordinary_parameters():
    for url in ("https://app.example.com/search?q=shoes&page=2&tab=1#top", "https://hoppscotch.io/",
                "https://x/a/b/c", "/relative/path?x=1", "", "mailto:someone@example.com",
                "https://x/?a=1&b=2;c=3"):
        assert scrub_url(url) == url, url
    assert scrub_url("https://x/cb?token=abc&tab=1") == "https://x/cb?token=[REDACTED]&tab=1"
    assert scrub_url("https://u:p@x/p?q=1#frag") == "https://[REDACTED]@x/p?q=1#frag"
    assert scrub_url("http://[::1/?token=abc") == "http://[::1/?token=[REDACTED]"     # unparseable: scrubbed


def test_m5_scrubbing_is_linear_and_giant_urls_are_cut_before_they_are_scanned(tmp_path: Path):
    with finishes_within(1.0):
        scrub_url("a" * 5_000_000)               # no '=' anywhere: a name-then-'=' regex is quadratic on this
        scrub_url("?k=v&" * 20_000)
    run = run_ctx(tmp_path)
    giant = "https://x/?" + "k=v&" * 1_000_000 + "token=SECRETTAIL"
    cap = make_capture(target="t", url=giant, access=_access(), status=200)
    with finishes_within(1.0):
        append_capture(run, cap)
    (row,) = jsonl(run.run_dir / "crawl" / "captures.jsonl")
    assert len(row["url"]) < 4200 and "...[len=" in row["url"] and "SECRETTAIL" not in persisted(run)
    assert cap.url == giant                                       # the in-memory capture is untouched


def test_m5_the_shared_secret_patterns_still_apply_to_urls(tmp_path: Path):
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    github = "ghp_" + "a1B2c3D4e5" * 4
    run = run_ctx(tmp_path)
    for url in (f"https://app.example.com/#{jwt}", f"https://app.example.com/dl/{github}/archive.zip"):
        append_capture(run, make_capture(target="t", url=url, access=_access(), status=200))
    blob = persisted(run)
    assert jwt not in blob and github not in blob and "[REDACTED" in blob


def test_m5_persisted_urls_never_carry_secrets_and_titles_are_untouched(tmp_path: Path):
    run = run_ctx(tmp_path)
    titles = ["Reset password: step 1 of 3", "Token: how to use", "API Key: create new",
              "Secret: Mission Impossible"]
    for i, (url, secret) in enumerate(_SECRET_URLS):
        access = _access(BLOCKED, signals=["s"], reason="r")
        cap = make_capture(target="t", url=url, final_url=url, title=titles[i % len(titles)],
                           action=f"goto {url}", access=access, status=403)
        append_capture(run, cap)
        assert secret not in persisted(run), url
    rows = jsonl(run.run_dir / "crawl" / "captures.jsonl")
    assert len(rows) == len(_SECRET_URLS)
    assert [r["title"] for r in rows] == [titles[i % len(titles)] for i in range(len(rows))]   # not redacted
    assert all("[REDACTED]" in r["url"] and "[REDACTED]" in r["action"] for r in rows)


def test_m5_a_round_trip_title_is_byte_identical(tmp_path: Path):
    run = run_ctx(tmp_path)
    cap = make_capture(target="t", url=URL, title="Reset password: step 1 of 3", access=_access(), status=200)
    append_capture(run, cap)
    (row,) = jsonl(run.run_dir / "crawl" / "captures.jsonl")
    assert Capture.model_validate(row) == cap


def test_m5_a_lone_surrogate_in_any_string_field_is_persisted_not_raised(tmp_path: Path):
    """JS `slice()` through an emoji leaves a lone high surrogate; it used to kill append_capture."""
    run = run_ctx(tmp_path)
    cap = make_capture(target="t", url="https://x/\ud83c", title="Launch party \ud83c", action="click:\ud83c",
                       access=_access(), status=200)
    assert not re.search("[\ud800-\udfff]", cap.model_dump_json()) and "\ufffd" in cap.title
    evidence_id = append_capture(run, cap)
    assert run.evidence.exists(evidence_id)
    # a Capture built without make_capture (another producer) must not crash either
    raw = Capture(id="cap_raw", target="t", url="https://x/\ud83c", final_url="https://x/\ud83c?token=abc",
                  title="a\ud83cb", captured_at="2026-10-12T00:00:00Z", action="a\ud800",
                  access=_access(BLOCKED, vendor="v\ud83c", signals=["s\ud83c"], reason="r\ud83c"))
    append_capture(run, raw)
    path = run.run_dir / "crawl" / "captures.jsonl"
    rows = jsonl(path)
    assert len(rows) == 2 and rows[1]["title"] == "a\ud83cb"           # escaped on disk, restored on read
    assert path.read_bytes().isascii()
    codes = [f["code"] for f in jsonl(run.run_dir / "failures.jsonl")]
    assert len(run.evidence) == 2 and codes == ["ACCESS_BLOCKED"]
    assert "token=abc" not in path.read_text()


def test_append_capture_on_an_ephemeral_run_keeps_memory_only():
    run = RunContext.ephemeral("t")
    cap = make_capture(target="t", url=URL, access=_access(BLOCKED, signals=["s"]), status=403)
    evidence_id = append_capture(run, cap)
    assert [f["code"] for f in run.failures.items] == ["ACCESS_BLOCKED"] and len(run.evidence) == 1
    assert run.failures.items[0]["context"]["evidence_id"] == evidence_id
    assert not Path("/nonexistent").exists()


# ----------------------------------------------------------------------------------- recorded session
SESSION = FIX / "recorded_session"


def _write_session(folder: Path, items: list[tuple[str, dict, str | None]]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for stem, meta, html in items:
        (folder / f"{stem}.json").write_text(json.dumps(meta))
        if html is not None:
            (folder / f"{stem}.html").write_text(html)
    return folder


def test_recorded_session_replays_in_order_with_the_same_classifier():
    session = RecordedSession(SESSION)
    caps = list(session.captures())
    assert [c.url for c in caps] == ["https://hoppscotch.io/", "https://hoppscotch.io/graphql",
                                     "https://demo.usememos.com/"]
    assert [c.access.verdict for c in caps] == [OK, BLOCKED, OK]
    assert {c.access.rung for c in caps} == {"RECORDED"} and {c.source_kind for c in caps} == {"recorded"}
    assert caps[1].access.vendor == "cloudflare"
    assert caps[1].access.signals == ["title:just-a-moment+interstitial-marker"]
    assert [c.status for c in caps] == [200, 403, 200]
    assert caps[0].title == "Hoppscotch • Open source API development ecosystem" and caps[2].title == "Memos"
    assert caps[1].title == "Just a moment..." and caps[1].parent_id == "recorded:001"
    assert caps[0].captured_at == "2026-10-12T07:13:59+00:00"
    assert caps[0].html_path == "001.html" and caps[0].screenshot_path == "001.png"
    assert caps[1].screenshot_path is None and caps[2].screenshot_path is None
    assert all(c.target == "recorded_session" for c in caps)


def test_m6_recorded_paths_are_relative_to_the_session_folder():
    session = RecordedSession(SESSION)
    caps = list(session.captures())
    assert session.folder == SESSION
    for cap in caps:
        for rel in (cap.html_path, cap.screenshot_path):
            assert rel is None or (not Path(rel).is_absolute() and (session.folder / rel).is_file())
    assert str(SESSION) not in "".join(c.model_dump_json() for c in caps)       # no local path leaks


def test_recorded_session_is_deterministic():
    first = [c.model_dump() for c in RecordedSession(SESSION).captures()]
    second = [c.model_dump() for c in RecordedSession(SESSION).captures()]
    assert first == second and len({c["id"] for c in first}) == 3


def test_recorded_session_is_an_ingest_source_without_act():
    s = RecordedSession(SESSION)
    assert isinstance(s, IngestSource) and s.kind == "recorded"
    assert CAP_REPLAY in s.capabilities and CAP_ACT not in s.capabilities
    assert not isinstance(s, ActingSource)


def test_recorded_session_strips_credentials_before_anything_is_kept(tmp_path: Path):
    folder = _write_session(tmp_path / "s", [("001", {
        "url": "https://app.example.com/", "status": 200, "headers": {
            "Cookie": "sid=SECRETCOOKIE", "Set-Cookie": ["a=SECRETSETCOOKIE", "b=2"],
            "Authorization": "Bearer SECRETBEARERTOKEN", "Server": "Vercel", "Content-Type": "text/html"}},
        PAGE)])
    (cap,) = RecordedSession(folder, target="app").captures()
    blob = cap.model_dump_json()
    assert "SECRET" not in blob and cap.headers == {"server": "Vercel", "content-type": "text/html"}
    assert cap.target == "app" and cap.access.verdict is OK


def test_m6_har_style_header_lists_are_normalised_then_stripped(tmp_path: Path):
    meta = {"url": "https://x/", "status": 200, "headers": [
        {"name": "Cookie", "value": "sid=SECRETCOOKIE"}, {"name": "cf-mitigated", "value": "challenge"},
        {"name": "Set-Cookie", "value": "a=SECRETSET"}, {"name": "Content-Type", "value": "text/html"},
        {"name": "Server", "value": "cloudflare"}, {"name": "server", "value": "extra"}]}
    folder = _write_session(tmp_path / "s", [("001", meta, None)])
    (cap,) = RecordedSession(folder).captures()
    assert cap.access.verdict is BLOCKED and cap.access.signals == ["header:cf-mitigated=challenge"]
    assert cap.headers == {"cf-mitigated": "challenge", "content-type": "text/html",
                           "server": "cloudflare, extra"}
    assert "SECRET" not in cap.model_dump_json()


def test_m6_numeric_order_non_ascii_digits_and_ignored_files(tmp_path: Path):
    folder = _write_session(tmp_path / "s", [(stem, {"url": f"https://x/{stem}", "status": 200}, PAGE)
                                             for stem in ("10", "2", "001")])
    (folder / "session.json").write_text("{\"not\": \"a capture\"}")
    (folder / "notes.txt").write_text("ignore me")
    # '\u00b2'.isdigit() is True and int('\u0661') == 1: neither is an ASCII-digit capture number
    (folder / "\u00b2.json").write_text(json.dumps({"url": "https://x/superscript"}))
    (folder / "\u0661.json").write_text(json.dumps({"url": "https://x/arabic-indic"}))
    (folder / "1.json.bak").write_text("{}")
    urls = [c.url for c in RecordedSession(folder).captures()]
    assert urls == ["https://x/001", "https://x/2", "https://x/10"]


@pytest.mark.parametrize(("meta", "field"), [
    ({"url": "https://x/", "title": 404}, "title"),
    ({"url": "https://x/", "status": "200 OK"}, "status"),
    ({"url": "https://x/", "status": True}, "status"),
    ({"url": "https://x/", "headers": "cf-mitigated: challenge"}, "headers"),
    ({"url": "https://x/", "headers": [{"nom": "a"}]}, "headers"),
    ({"url": "https://x/", "headers": [["a", "b"]]}, "headers"),
    ({"url": "https://x/", "interactive_count": "many"}, "interactive_count"),
    ({"url": "https://x/", "action": ["a"]}, "action"),
    ({"url": ["a"]}, "url"),
    ({"url": ""}, "url"),
])
def test_m6_bad_meta_is_a_valueerror_naming_the_file_and_field(tmp_path: Path, meta, field):
    folder = _write_session(tmp_path / "s", [("007", meta, None)])
    with pytest.raises(ValueError, match=rf"007\.json.*{field}"):
        list(RecordedSession(folder).captures())


def test_m6_a_non_object_meta_names_the_file(tmp_path: Path):
    folder = tmp_path / "s"
    folder.mkdir()
    (folder / "003.json").write_text("[1, 2]")
    with pytest.raises(ValueError, match=r"003\.json.*object"):
        list(RecordedSession(folder).captures())


def test_m6_lenient_where_it_is_safe(tmp_path: Path):
    meta = {"url": "https://hoppscotch.io/", "status": "200", "interactive_count": "5",
            "provenance": "measured", "unknown_key": {"a": 1}}
    folder = _write_session(tmp_path / "s", [("001", meta, PAGE)])
    anchor = AnchorConfig(title_regex="(?i)hoppscotch", min_interactive=8)
    (cap,) = RecordedSession(folder, anchor=anchor).captures()
    assert cap.status == 200 and cap.interactive_count == 5
    assert cap.access.verdict is SUSPECT and cap.access.signals == ["anchor:interactive=5<8"]


def test_m6_a_utf8_bom_in_meta_and_html_is_tolerated(tmp_path: Path):
    folder = tmp_path / "s"
    folder.mkdir()
    meta = json.dumps({"url": "https://x/", "status": 200})
    (folder / "001.json").write_bytes(b"\xef\xbb\xbf" + meta.encode())
    (folder / "001.html").write_bytes(b"\xef\xbb\xbf" + PAGE.encode())
    (cap,) = RecordedSession(folder).captures()
    assert cap.url == URL and cap.title == "Hoppscotch" and cap.access.verdict is OK


def test_m6_symlinks_that_resolve_outside_the_folder_are_rejected(tmp_path: Path):
    outside = tmp_path / "outside.html"
    outside.write_text("<title>not part of the session</title>")
    folder = _write_session(tmp_path / "s", [("001", {"url": URL}, None)])
    (folder / "001.html").symlink_to(outside)
    with pytest.raises(ValueError, match=r"001\.html.*outside"):
        list(RecordedSession(folder).captures())
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps({"url": "https://x/other"}))
    (folder / "002.json").symlink_to(elsewhere)
    with pytest.raises(ValueError, match=r"002\.json.*outside"):
        RecordedSession(folder)


def test_m6_symlinks_inside_the_folder_are_fine(tmp_path: Path):
    folder = _write_session(tmp_path / "s", [("001", {"url": URL, "status": 200}, PAGE)])
    (folder / "001-copy.html").write_text(PAGE)
    (folder / "002.json").symlink_to(folder / "001.json")
    assert [c.url for c in RecordedSession(folder).captures()] == [URL, URL]


def test_recorded_session_without_html_still_classifies_by_headers_and_status(tmp_path: Path):
    folder = _write_session(tmp_path / "s", [
        ("001", {"url": "https://x/a", "status": 200, "headers": {"cf-mitigated": "challenge"}}, None),
        ("002", {"url": "https://x/b", "status": 202, "headers": {"x-amzn-waf-action": "challenge"}}, None),
        ("003", {"url": "https://x/c", "status": 200, "headers": {}}, None)])
    caps = list(RecordedSession(folder).captures())
    assert [c.access.verdict for c in caps] == [BLOCKED, BLOCKED, OK]
    assert all(c.html_path is None for c in caps)


def test_recorded_session_honours_the_targets_anchor(tmp_path: Path):
    anchor = TargetConfig.load("hoppscotch").crawl.anchor
    caps = list(RecordedSession(SESSION, anchor=anchor).captures())
    assert [c.access.verdict for c in caps] == [OK, BLOCKED, SUSPECT]        # memos is not Hoppscotch
    assert "anchor:title-mismatch" in caps[2].access.signals


def test_recorded_session_rejects_bad_folders_and_metas(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        RecordedSession(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="no NNN.json"):
        RecordedSession(tmp_path / "empty")
    bad = _write_session(tmp_path / "bad", [("001", {"url": "https://x/"}, None)])
    (bad / "002.json").write_text("{not json")
    with pytest.raises(ValueError, match="002.json"):
        list(RecordedSession(bad).captures())
    nourl = _write_session(tmp_path / "nourl", [("001", {"status": 200}, None)])
    with pytest.raises(ValueError, match="url"):
        list(RecordedSession(nourl).captures())


def test_recorded_challenge_is_quarantined_end_to_end(tmp_path: Path):
    """Case 17 on the offline path: a recorded wall becomes a gap-bearing failure, never a UI state."""
    run = run_ctx(tmp_path)
    breaker = Breaker()
    admitted = []
    for cap in RecordedSession(SESSION).captures():
        append_capture(run, cap)
        breaker.record(cap.access.verdict)
        if cap.access.verdict is OK:
            admitted.append(cap.url)
    assert admitted == ["https://hoppscotch.io/", "https://demo.usememos.com/"]
    assert [r["code"] for r in jsonl(run.run_dir / "failures.jsonl")] == ["ACCESS_BLOCKED"]
    assert len(jsonl(run.run_dir / "crawl" / "captures.jsonl")) == 3 and len(run.evidence) == 3
    assert breaker.consecutive == 0                                 # OK after the block: not tripped


def test_two_recorded_walls_in_a_row_trip_the_breaker():
    wall = fixture("blocked_cloudflare_just_a_moment_200")
    breaker = Breaker()
    tripped = []
    for _ in range(2):
        res = classify_http(wall["status"], wall["headers"], wall["body"], wall["url"])
        tripped.append(breaker.record(res.verdict))
    assert tripped == [False, True]


# ------------------------------------------------------------------------------------- property-style
_WORDS = [
    "request", "response", "header", "body", "status", "collection", "environment", "variable", "workspace",
    "history", "tab", "setting", "team", "import", "export", "graphql", "realtime", "socket", "stream",
    "shortcut", "theme", "font", "editor", "query", "parameter", "authorization", "token", "payload",
    "preview", "cookie", "proxy", "interceptor", "sidebar", "search",
]
_TITLES = ["Hoppscotch • Open source API development ecosystem", "Memos", "Amazon.in: Wireless Headphones",
           "Example Domain", "", "Dashboard | Acme", "Docs - Getting started",
           "Just a moment, loading your workspace | Acme", "Access Denied handling | Docs",
           "Security Checklist", "One more step - Acme onboarding", "Robot Checkpoint Setup"]
_SERVERS = ["cloudflare", "Vercel", "AkamaiGHost", "nginx", "Apache", "CloudFront", "AmazonS3", "Server",
            "gws"]
_CONTENT_TYPES = ["text/html; charset=utf-8", "text/html", "", "application/xhtml+xml", "application/json",
                  "text/plain", "text/markdown"]
_PRESENCE = {
    "cf-ray": "a4944e88eff17eda-MAA", "cf-cache-status": "HIT", "x-vercel-id": "bom1::abc-123",
    "x-amz-cf-id": "Zq7mQK1xT0w4fRz8", "x-amz-cf-pop": "BLR50", "via": "1.1 abc.cloudfront.net (CloudFront)",
    "x-cache": "Hit from cloudfront", "x-iinfo": "11-1-0 0NNN RT(1 0)", "x-datadome": "protected",
    "x-datadome-cid": "AHrlqAAAAAMA", "x-datadome-response": "200", "x-amzn-waf-action": "allow",
    "cf-mitigated": "bypass",
    "x-vercel-mitigated": "bypass",
}
_COOKIES = ["_abck", "bm_sz", "ak_bmsc", "datadome", "__cf_bm", "cf_clearance", "_px3", "_pxhd", "_pxvid",
            "visid_incap_123", "incap_ses_1_2_3", "aws-waf-token", "session-id"]
_AMAZON_TEXT = "<label>Type the characters you see in this image:</label>"
_AMAZON_FORM = "<form action=\"/errors/validateCaptcha\"></form>"
_CF_CHL = "<script>window._cf_chl_opt={cType:'managed'};</script>"
_FREE_DECOYS = [      # presence, or one half of a conjunction whose other half this generator never emits
    BEACON,
    "<script src=\"/cdn-cgi/scripts/5c5dd728/cloudflare-static/email-decode.min.js\"></script>",
    "<p>This site is protected by Cloudflare.</p>",
    "<p>Just a moment, loading your workspace...</p>",
    "<noscript>Just a moment...</noscript>",
    "<p>Access Denied is the name of our permissions guide.</p>",
    "<p>Type the characters in your invite code.</p>",
    "<script src=\"https://client.perimeterx.net/PXAbC123/main.min.js\"></script>",
    "<script src=\"https://js.datadome.co/tags.js\"></script>",
    "<script>window.awsWafCookieDomainList = [];</script>",
    "<p>Incident ID and request details are listed below.</p>",
    "<p>Press &amp; copy, then hold on.</p>",
    "<p>Error handling: see the error codes table.</p>",
    "<p>Incapsula is a CDN. Request unsuccessful pages are documented elsewhere.</p>",
    _AMAZON_TEXT, _AMAZON_FORM, _CF_CHL,
]
_GATED_DECOYS = [      # (snippet, statuses at which it completes a documented conjunction on its own or with
                       # the page's own headers and title; the generator never emits it at those statuses)
    ("<p>Press &amp; Hold to record a voice note</p>", {403}),
    ("<p>See Reference #12 in the manual</p>", {403}),            # with AkamaiGHost + 'access denied'
    ("<p>Request unsuccessful. Incapsula incident ID: 12</p>", {200, 403}),   # at 200: x-iinfo or a cookie
    ("<script src=\"https://ct.captcha-delivery.com/c.js\"></script>", {403}),
    ("<script>var u='https://collector-pxabc123.perimeterx.net/api'</script>", {403}),
    ("<span>Error code 1020</span>", {403, 429}),                  # with a Cloudflare hint
]
_OK_STATUSES = [200, 200, 200, 204, 301, 302, 304, 400, 404, 500]
_SOFT = [202, 403, 405, 429, 503]


def _random_page(rng: random.Random, size: int, status: int) -> str:
    title = rng.choice(_TITLES)
    pool = list(_FREE_DECOYS) + [snippet for snippet, gate in _GATED_DECOYS if status not in gate]
    chosen = rng.sample(pool, k=rng.randrange(0, 6))
    if _CF_CHL in chosen and title.lower().startswith("just a moment"):
        chosen.remove(_CF_CHL)                       # title half + marker half is the documented conjunction
    if _AMAZON_TEXT in chosen and _AMAZON_FORM in chosen:
        chosen.remove(_AMAZON_FORM)
    chunk = "<p>" + " ".join(rng.choices(_WORDS, k=150)) + "</p>"
    decoys = "".join(chosen)
    core = f"<!doctype html><html><head><title>{title}</title></head><body>{decoys}<div id=\"app\"></div>"
    return (core + chunk * (size // len(chunk) + 1))[:size]


def _random_headers(rng: random.Random) -> dict[str, str]:
    headers = {"content-type": rng.choice(_CONTENT_TYPES), "server": rng.choice(_SERVERS)}
    for name in rng.sample(sorted(_PRESENCE), k=rng.randrange(0, 7)):
        headers[name] = _PRESENCE[name]
    if rng.random() < 0.7:
        cookies = rng.sample(_COOKIES, k=rng.randrange(1, 6))
        headers["set-cookie"] = "; ".join(f"{c}={rng.getrandbits(48):x}" for c in cookies)
    return {k: v for k, v in headers.items() if v}


def _is_html(headers: dict[str, str]) -> bool:
    ctype = headers.get("content-type", "")
    return "html" in ctype.lower() if ctype else True


def test_m8_random_ok_looking_pages_never_become_blocked():
    """OK unless the full documented conjunction and status are present; halves and presence never are."""
    rng = random.Random(20261012)
    sizes = [0, 1, 5219, 2_000_000] + [rng.randrange(0, 2_000_001) for _ in range(150)]
    for size in sizes:
        status = rng.choice(_OK_STATUSES)
        body, headers = _random_page(rng, size, status), _random_headers(rng)
        res = classify_http(status, headers, body, "https://example.test/")
        assert res.verdict is OK, (size, status, headers, res)     # presence + size alone: never a signal
        cap = classify_capture(status=status, headers=headers, title=None, body_text=body,
                               interactive_count=None, anchor=None, url="https://example.test/")
        assert cap.verdict is OK


def test_m8_soft_statuses_are_suspect_never_blocked_without_the_conjunction():
    rng = random.Random(77)
    for _ in range(120):
        status = rng.choice(_SOFT)
        headers = _random_headers(rng)
        body = _random_page(rng, rng.randrange(0, 150_000), status)
        res = classify_http(status, headers, body, "https://e.test/")
        if _is_html(headers):
            assert res.verdict is SUSPECT and res.signals == [f"status:{status}"], (status, headers, res)
        else:
            assert res.verdict is OK, (status, headers, res)


_CONJUNCTIONS = [      # (id, status, extra headers, title, body snippet, vendor): each blocks as documented
    ("cf-interstitial", 200, {}, "Just a moment...", _CF_CHL, "cloudflare"),
    ("cf-block-title", 403, {}, "Attention Required! | Cloudflare", "", "cloudflare"),
    ("cf-block-code", 429, {"server": "cloudflare"}, "Access denied", "<span>Error code 1015</span>",
     "cloudflare"),
    ("akamai", 403, {"server": "AkamaiGHost"}, "Access Denied",
     "<h1>Access Denied</h1>Reference&#32;&#35;18.2f", "akamai"),
    ("datadome", 403, {}, "x", "<script src=\"https://ct.captcha-delivery.com/c.js\"></script>", "datadome"),
    ("px-hold", 403, {}, "Denied", "<p>Press &amp; Hold to confirm</p>", "perimeterx"),
    ("px-collector", 403, {}, "x", "<script>u='https://collector-pxabc.perimeterx.net/a'</script>",
     "perimeterx"),
    ("imperva-403", 403, {}, "x", "<p>Request unsuccessful. Incapsula incident ID: 1</p>", "imperva"),
    ("imperva-200", 200, {"x-iinfo": "11-1-0"}, "x",
     "<p>Request unsuccessful. Incapsula incident ID: 1</p>", "imperva"),
    ("amazon", 200, {}, "Robot Check", _AMAZON_TEXT + _AMAZON_FORM, "amazon"),
    ("cf-header", 200, {"cf-mitigated": "challenge"}, "x", "", "cloudflare"),
    ("aws-header", 202, {"x-amzn-waf-action": "challenge"}, "x", "", "aws_waf"),
    ("vercel-header", 429, {"x-vercel-mitigated": "deny"}, "x", "", "vercel"),
]


@pytest.mark.parametrize(("rid", "status", "extra", "title", "snippet", "vendor"), _CONJUNCTIONS,
                         ids=[c[0] for c in _CONJUNCTIONS])
def test_m8_the_full_conjunction_inside_a_random_page_is_blocked(rid, status, extra, title, snippet, vendor):
    """The positive control for the property test: each documented conjunction fires in a noisy page."""
    rng = random.Random(rid)
    for _ in range(6):
        noise = "<p>" + " ".join(rng.choices(_WORDS, k=rng.randrange(0, 4000))) + "</p>"
        headers = {**HTML, **extra}
        body = f"<html><head><title>{title}</title></head><body>{snippet}<div id=\"app\">{noise}"
        res = classify_http(status, headers, body, URL)
        assert res.verdict is BLOCKED and res.vendor == vendor, (rid, res)


# ----------------------------------------------------------------------------------------- determinism
_DETERMINISM_SCRIPT = textwrap.dedent("""
    import json, sys
    from pathlib import Path
    from blastradius.access.capture import scrub_url
    from blastradius.access.classify import classify_capture, classify_http, robots_allows
    from blastradius.access.source import RecordedSession
    fix = Path(sys.argv[1])
    out = []
    for path in sorted(fix.glob("*.json")):
        data = json.loads(path.read_text())
        if "expected" in data:
            res = classify_http(data["status"], data["headers"], data["body"], data["url"])
            out.append([path.name, res.model_dump()])
    out += [cap.model_dump() for cap in RecordedSession(fix / "recorded_session").captures()]
    prior = {f"https://x/{c}": "f" for c in "abcde"} | {"https://x/e/1": "g", "https://x/e/2": "g",
                                                           "https://x/e/3": "g"}
    out.append(classify_capture(status=200, headers={}, title="t", body_text="", interactive_count=1,
                                anchor=None, url="https://x/a", prior_fingerprints=prior,
                                collapse_exempt_patterns=[r"/e/\\d+$"]).model_dump())
    robots = json.loads((fix / "robots_docs_hoppscotch_io.json").read_text())["body"]
    out.append([robots_allows(robots, "blastradius/1", u).value
                for u in ("https://d/a", "https://d/_next/x", "https://d/cdn-cgi/trace", "https://d/?q=%2F")])
    out.append(scrub_url("https://u:p@h/a?token=1&b=2&sid=3#access_token=3&x=1"))
    print(json.dumps(out))
""")


def test_classification_and_capture_output_is_independent_of_the_hash_seed():
    outputs = []
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(SRC)}
        cmd = [sys.executable, "-c", _DETERMINISM_SCRIPT, str(FIX)]
        done = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120, check=True)
        outputs.append(done.stdout)
    assert outputs[0] and outputs[0] == outputs[1] == outputs[2]


# ---- residual findings from the independent verifier (blank title, secret names, bytes, status range)
def test_blank_title_falls_back_to_the_body_title_so_the_interstitial_still_fires():
    body = "<title>Just a moment...</title><script>window._cf_chl_opt={}</script>"
    kw = {"status": 200, "headers": {"content-type": "text/html", "server": "cloudflare"}, "body_text": body,
          "interactive_count": None, "anchor": None, "url": URL}
    assert classify_capture(title="", **kw).verdict is BLOCKED
    assert classify_capture(title=None, **kw).verdict is BLOCKED


def test_collapse_exempt_patterns_accepts_a_bare_string():
    priors = {f"https://x/{c}": "f" for c in "abc"}
    kw = {"status": 200, "headers": {}, "title": "App", "body_text": "<html></html>",
          "interactive_count": None, "anchor": None}
    assert classify_capture(url="https://x/a", prior_fingerprints=priors, collapse_exempt_patterns="/zzz$",
                            **kw).verdict is SUSPECT        # a string must not be iterated per character
    assert classify_capture(url="https://x/a", prior_fingerprints=priors, collapse_exempt_patterns="/a$",
                            **kw).verdict is OK


def test_robots_accepts_bytes_and_a_missing_user_agent():
    assert robots_allows(b"User-agent: *\nDisallow: /x\n", UA, "https://h/x") is DISALLOWED
    assert robots_allows("User-agent: *\nAllow: /\n", None, "https://h/x") is OK


def test_normalise_headers_decodes_byte_names_and_drops_null_values():
    from blastradius.access.classify import normalise_headers

    out = normalise_headers([(b"CF-Mitigated", b"challenge"), ("Server", None), ("X-Y", "1")])
    assert out == {"cf-mitigated": "challenge", "x-y": "1"}


def test_recorded_session_rejects_out_of_range_status(tmp_path):
    import json

    from blastradius.access.source import RecordedSession

    (tmp_path / "001.json").write_text(json.dumps({"url": "https://x/", "status": 99999}))
    (tmp_path / "001.html").write_text("<html></html>")
    try:
        list(RecordedSession(tmp_path).captures())
    except ValueError as exc:
        assert "100 and 599" in str(exc)
    else:
        raise AssertionError("status 99999 accepted")


def test_persisted_action_keeps_ordinary_words_but_removes_secret_shapes(tmp_path):
    import json

    from blastradius.access.capture import append_capture, make_capture
    from blastradius.models import AccessResult
    from blastradius.run import RunContext

    run = RunContext.create("t", run_id="act-test", runs_dir=tmp_path)
    cap = make_capture(target="t", url="https://x/", access=AccessResult(verdict=OK), status=200,
                       action="click:Reset password: step 1 of 3 sk-ant-api03-CANARYCANARYCANARY")
    append_capture(run, cap)
    row = json.loads((run.run_dir / "crawl" / "captures.jsonl").read_text().splitlines()[0])
    assert "Reset password: step 1 of 3" in row["action"]
    assert "CANARY" not in row["action"]


def test_more_secret_parameter_names_and_scheme_relative_userinfo_are_masked():
    from blastradius.access.capture import scrub_url

    out = scrub_url("//user:pw@host/p?pwd=abc12345&otp=123456&ticket=zz&SAMLResponse=PHNhbWw&q=ok#jwt=eyJabc")
    assert "pw@" not in out and "abc12345" not in out and "123456" not in out
    assert "PHNhbWw" not in out and "q=ok" in out
