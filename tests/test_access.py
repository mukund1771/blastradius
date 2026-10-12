"""Access module: classifier, robots, breaker, capture persistence, recorded sessions.

Decisions D11-D14, D32, D33; cases 17, 18, 67, 68, 69, 70. Fixtures in tests/fixtures/access are labelled
'measured' (real responses, 2026-10-12) or 'synthesized from documented markers' (they test the author's
recall of vendor documentation, not the vendor: case 70).
"""

from __future__ import annotations

import inspect
import json
import random
import time
from pathlib import Path

import pytest

from blastradius.access.capture import HEADER_ALLOWLIST, SECRET_HEADERS, append_capture, make_capture
from blastradius.access.classify import (
    COLLAPSE_MIN_URLS,
    Breaker,
    classify_capture,
    classify_http,
    extract_title,
    robots_allows,
)
from blastradius.access.source import CAP_ACT, CAP_REPLAY, ActingSource, IngestSource, RecordedSession
from blastradius.config import Settings
from blastradius.models import AccessResult, AccessVerdict, Capture
from blastradius.run import RunContext
from blastradius.targets import AnchorConfig, TargetConfig

FIX = Path(__file__).parent / "fixtures" / "access"
MEASURED = "measured"
SYNTH = "synthesized from documented markers"
OK, SUSPECT, BLOCKED, DISALLOWED = (AccessVerdict.OK, AccessVerdict.SUSPECT, AccessVerdict.BLOCKED,
                                    AccessVerdict.DISALLOWED)
UA = Settings().user_agent()
HTML = {"content-type": "text/html; charset=utf-8"}
PAGE = "<!doctype html><html><head><title>Hoppscotch</title></head><body><div id=\"app\"></div></body></html>"


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


# ----------------------------------------------------------------------------------------- the contract
def test_pathological_bodies_do_not_make_classification_quadratic():
    cf = {"server": "cloudflare", **HTML}
    bodies = [("<title " * 100_000, 200), ("error " + "<a> " * 200_000, 403), ("request" + " " * 1_000_000 + "x", 200),
              ("access denied " + "reference " * 100_000, 403), ("press" + " " * 1_000_000, 200)]
    start = time.perf_counter()
    for body, status in bodies:
        assert classify_http(status, cf, body, "https://x/").verdict is not BLOCKED
    assert time.perf_counter() - start < 5.0       # measured: about 0.1 s; the quadratic title scan took 22 s


def test_signatures_match_the_architecture_doc():
    assert list(inspect.signature(classify_http).parameters) == ["status", "headers", "body", "url"]
    params = inspect.signature(classify_capture).parameters
    assert list(params) == ["status", "headers", "title", "body_text", "interactive_count", "anchor", "url",
                            "rung", "prior_fingerprints"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())
    assert params["rung"].default == "L0" and params["prior_fingerprints"].default is None
    assert list(inspect.signature(robots_allows).parameters) == ["robots_txt", "user_agent", "url"]
    assert inspect.signature(Breaker).parameters["threshold"].default == 2


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
                 "blocked_cloudflare_challenge_403_head", "robots_hoppscotch_io", "robots_docs_hoppscotch_io"):
        assert seen[name] == MEASURED, name
    for name in ("blocked_akamai_403", "blocked_datadome_403", "blocked_perimeterx_403", "blocked_imperva_403",
                 "blocked_amazon_robot_check_200", "blocked_aws_waf_challenge_202", "blocked_aws_waf_captcha_405",
                 "ok_amazon_like_cloudfront_200"):
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
    res = classify_http(status, {"cf-mitigated": "challenge", "content-type": "application/json"}, "{}", "https://x/")
    assert res.verdict is BLOCKED and res.vendor == "cloudflare"
    assert res.signals == ["header:cf-mitigated=challenge"]


@pytest.mark.parametrize(("action", "status"), [("challenge", 202), ("captcha", 405), ("challenge", 200),
                                                ("captcha", 403), ("Challenge", 202)])
def test_aws_waf_action_is_blocked_on_any_status(action, status):
    res = classify_http(status, {"x-amzn-waf-action": action}, "", "https://x/")
    assert res.verdict is BLOCKED and res.vendor == "aws_waf"
    assert res.signals == [f"header:x-amzn-waf-action={action.lower()}"]


def test_header_names_are_case_insensitive_and_mappings_are_accepted():
    res = classify_http(200, {"CF-Mitigated": "Challenge"}, "", "https://x/")
    assert res.verdict is BLOCKED


def test_only_the_documented_header_values_are_hard_signals():
    assert classify_http(200, {"cf-mitigated": "managed"}, PAGE, "https://x/").verdict is OK
    assert classify_http(200, {"x-amzn-waf-action": "allow"}, PAGE, "https://x/").verdict is OK
    assert classify_http(200, {"x-amzn-waf-action": "count"}, PAGE, "https://x/").verdict is OK


# ----------------------------------------------------------------------- presence is never a block (D13)
@pytest.mark.parametrize("headers", [
    {"server": "cloudflare"}, {"cf-ray": "a4944e88eff17eda-MAA"}, {"cf-cache-status": "HIT"},
    {"x-vercel-id": "bom1::abc"}, {"x-amz-cf-id": "Zq7mQK1xT0w4"}, {"via": "1.1 abc.cloudfront.net (CloudFront)"},
    {"server": "AkamaiGHost"}, {"x-iinfo": "11-1-0 0NNN RT(1 0)"},
    {"set-cookie": "_abck=abc~0~YAAQ; Path=/"}, {"set-cookie": "datadome=abc; Max-Age=31536000"},
    {"set-cookie": "__cf_bm=abc; HttpOnly"}, {"set-cookie": "_px3=abc; Path=/"},
    {"set-cookie": "visid_incap_1=abc; incap_ses_1_2_3=def"}, {"set-cookie": "aws-waf-token=abc"},
    {"x-datadome": "protected", "x-datadome-cid": "AHrlqAAAAAMA", "x-datadome-response": "200"},
])
def test_vendor_presence_alone_is_ok(headers):
    res = classify_http(200, {**HTML, **headers}, PAGE, "https://x/")
    assert res.verdict is OK and res.vendor is None and res.signals == []


def test_x_datadome_headers_are_not_a_hard_signal_even_on_a_403():
    res = classify_http(403, {**HTML, "x-datadome": "protected", "x-datadome-cid": "AHrl"}, PAGE, "https://x/")
    assert res.verdict is SUSPECT                      # the 403 soft guard, not BLOCKED


def test_vendor_is_only_a_label_on_a_soft_result():
    res = classify_http(403, {**HTML, "server": "cloudflare", "cf-ray": "abc"}, PAGE, "https://x/")
    assert res.verdict is SUSPECT and res.vendor == "cloudflare" and res.signals == ["status:403"]


# --------------------------------------------------------------------------- body size is never a signal
def test_empty_and_huge_ok_pages_are_both_ok():
    assert classify_http(200, HTML, "", "https://x/").verdict is OK
    huge = PAGE + "<!-- " + "x" * 2_000_000 + " -->"
    assert classify_http(200, HTML, huge, "https://x/").verdict is OK


@pytest.mark.parametrize("data", _cases())
def test_padding_a_fixture_to_2mb_never_changes_its_verdict(data):
    base = classify_http(data["status"], data["headers"], data["body"], data["url"])
    padded = classify_http(data["status"], data["headers"], data["body"] + "\n<!-- " + "x" * 2_000_000 + " -->",
                           data["url"])
    assert (padded.verdict, padded.vendor, padded.signals) == (base.verdict, base.vendor, base.signals)


# ----------------------------------------------------- Cloudflare Block page: its own rule (case 68)
CF_PAGE_1020 = "<html><head><title>Access denied</title></head><body>Error code <span>1020</span> Cloudflare Ray ID: abc</body></html>"


def test_cloudflare_block_page_without_cf_mitigated_is_blocked():
    data = fixture("blocked_cloudflare_block_1020")
    assert "cf-mitigated" not in data["headers"]
    res = classify_http(403, data["headers"], data["body"], data["url"])
    assert res.verdict is BLOCKED and res.signals == ["body:cloudflare-block-page"]


@pytest.mark.parametrize(("status", "headers", "body"), [
    (403, {"server": "cloudflare"}, CF_PAGE_1020),                                     # error code only
    (403, {"server": "cloudflare"}, "<title>Attention Required! | Cloudflare</title><h1>Sorry</h1>"),   # title only
    (429, {"cf-ray": "abc"}, "<title>Access denied</title><span>Error 1015</span> You are being rate limited"),
    (403, {}, "<title>Attention Required! | Cloudflare</title>"),                      # hint is in the body itself
    (403, {}, "<title>Attention Required!</title><p>Sorry, you have been blocked</p>"),   # title needs no hint
    (403, {}, "<p>Error 1020</p><p>Cloudflare Ray ID: a4944e88eff17eda</p>"),          # code needs a hint
    (403, {"server": "cloudflare"}, "<span class=\"cf-error-code\">1020</span>"),
])
def test_cloudflare_block_page_variants(status, headers, body):
    res = classify_http(status, {**HTML, **headers}, body, "https://x/")
    assert res.verdict is BLOCKED and res.vendor == "cloudflare"


@pytest.mark.parametrize(("status", "headers", "body"), [
    (200, {"server": "cloudflare"}, CF_PAGE_1020),                    # not a 403: documentation about the error
    (403, {}, "<title>Forbidden</title><p>Internal error code 1020 for ticket 7</p>"),   # no Cloudflare hint
    (404, {"server": "cloudflare"}, CF_PAGE_1020),
])
def test_cloudflare_block_code_rule_needs_a_403_or_429_and_a_cloudflare_hint(status, headers, body):
    assert classify_http(status, {**HTML, **headers}, body, "https://x/").verdict is not BLOCKED


# ------------------------------------------------------ hard body markers need their whole conjunction
def test_just_a_moment_needs_the_challenge_platform_path_and_vice_versa():
    beacon = "<script src=\"/cdn-cgi/challenge-platform/scripts/jsd/main.js\"></script>"
    assert classify_http(200, HTML, f"<title>Hoppscotch</title>{beacon}", "https://x/").verdict is OK
    prose = "<title>Hoppscotch</title><p>Just a moment, we are loading your workspace.</p>"
    assert classify_http(200, HTML, prose, "https://x/").verdict is OK
    both = f"<title>Just a moment...</title>{beacon}"
    assert classify_http(200, HTML, both, "https://x/").verdict is BLOCKED
    only_title_marker = classify_http(200, HTML, "<title>Just a moment...</title><p>hi</p>", "https://x/")
    assert only_title_marker.verdict is SUSPECT                       # title guard, no hard marker


def test_challenge_rule_accepts_the_title_passed_separately_from_visible_text():
    res = classify_capture(status=200, headers={}, title="Just a moment...", body_text="<script src=\"/cdn-cgi/"
                           "challenge-platform/h/g/orchestrate/chl_page/v1\"></script>", interactive_count=0,
                           anchor=None, url="https://x/")
    assert res.verdict is BLOCKED


def test_akamai_needs_the_server_header_the_phrase_and_the_reference():
    body = "<TITLE>Access Denied</TITLE><H1>Access Denied</H1>Reference #18.2f4b1c02.1760254412.7a3d9e11"
    assert classify_http(403, {**HTML, "server": "AkamaiGHost"}, body, "https://x/").verdict is BLOCKED
    escaped = body.replace("Reference #", "Reference&#32;&#35;")
    assert classify_http(403, {**HTML, "server": "AkamaiGHost"}, escaped, "https://x/").verdict is BLOCKED
    assert classify_http(403, {**HTML, "server": "nginx"}, body, "https://x/").verdict is SUSPECT
    no_ref = "<TITLE>Access Denied</TITLE><H1>Access Denied</H1>"
    assert classify_http(403, {**HTML, "server": "AkamaiGHost"}, no_ref, "https://x/").verdict is SUSPECT
    assert classify_http(200, {**HTML, "server": "AkamaiGHost"}, PAGE, "https://x/").verdict is OK


def test_datadome_needs_a_403():
    body = "<script src=\"https://ct.captcha-delivery.com/c.js\"></script>"
    assert classify_http(403, HTML, body, "https://x/").verdict is BLOCKED
    assert classify_http(200, HTML, body, "https://x/").verdict is not BLOCKED
    assert classify_http(403, {"content-type": "application/json"}, "{\"url\":\"https://geo.captcha-delivery.com/"
                         "captcha/\"}", "https://x/").verdict is BLOCKED


def test_perimeterx_press_and_hold_any_status_collector_only_on_403():
    hold = "<p>Press &amp; Hold to confirm you are a human</p>"
    assert classify_http(403, HTML, hold, "https://x/").verdict is BLOCKED
    assert classify_http(200, HTML, hold.replace("&amp;", "&"), "https://x/").verdict is BLOCKED
    collector = "<script>var u='https://collector-pxabc123.perimeterx.net/api/v2/collector'</script>"
    assert classify_http(403, HTML, collector, "https://x/").verdict is BLOCKED
    assert classify_http(200, HTML, collector, "https://x/").verdict is OK       # sensor presence, not a block


def test_imperva_incident_phrase_is_blocked_on_any_status():
    body = "<iframe>Request unsuccessful. Incapsula incident ID: 1234-5678</iframe>"
    assert classify_http(200, HTML, body, "https://x/").verdict is BLOCKED
    assert classify_http(403, HTML, body, "https://x/").vendor == "imperva"
    assert classify_http(200, {**HTML, "x-iinfo": "11-1-0 0NNN"}, PAGE, "https://x/").verdict is OK


def test_amazon_robot_check_needs_both_markers():
    both = "<label>Type the characters you see in this image:</label><form action=\"/errors/validateCaptcha\">"
    assert classify_http(200, HTML, both, "https://x/").verdict is BLOCKED
    only_form = "<form action=\"/errors/validateCaptcha\"></form>"
    assert classify_http(200, HTML, only_form, "https://x/").verdict is OK
    only_text = "<p>Type the characters you see in this image</p>"
    assert classify_http(200, HTML, only_text, "https://x/").verdict is OK


def test_result_lists_every_hard_signal_and_attributes_the_first():
    data = fixture("blocked_cloudflare_challenge_403_page")
    res = classify_http(data["status"], data["headers"], data["body"], data["url"])
    assert res.signals == ["header:cf-mitigated=challenge", "body:just-a-moment+challenge-platform"]
    assert res.vendor == "cloudflare" and "cloudflare" in res.reason


# ----------------------------------------------------------------------------------------- soft guards
@pytest.mark.parametrize("status", [202, 403, 405, 429, 503])
def test_html_with_a_wall_ish_status_is_suspect(status):
    res = classify_http(status, HTML, PAGE, "https://x/")
    assert res.verdict is SUSPECT and res.signals == [f"status:{status}"]


@pytest.mark.parametrize("status", [200, 204, 301, 304, 400, 404, 500, 502, None])
def test_other_statuses_are_not_a_wall(status):
    assert classify_http(status, HTML, PAGE, "https://x/").verdict is OK


def test_non_html_error_bodies_are_not_page_challenges():
    assert classify_http(403, {"content-type": "application/json"}, "{\"error\":\"nope\"}", "https://x/").verdict is OK
    assert classify_http(429, {"content-type": "text/plain"}, "slow down", "https://x/").verdict is OK
    assert classify_http(403, {}, "", "https://x/").verdict is SUSPECT        # unknown type is not exempt


@pytest.mark.parametrize("title", ["Just a moment...", "Attention Required! | Cloudflare", "Access Denied",
                                   "Robot Check", "Are you a robot?", "Verifying you are human",
                                   "Pardon Our Interruption", "Access to this page has been denied"])
def test_challengey_titles_are_suspect_without_a_hard_marker(title):
    res = classify_http(200, HTML, f"<html><head><title>{title}</title></head><body>hi</body></html>", "https://x/")
    assert res.verdict is SUSPECT and res.signals and res.signals[0].startswith("title:")


def test_ordinary_titles_that_merely_mention_the_words_are_ok():
    page = "<title>Fix Access Denied errors in Windows | Docs</title><p>hi</p>"
    assert classify_http(200, HTML, page, "https://x/").verdict is OK


def test_anchor_min_interactive_and_title_regex():
    anchor = AnchorConfig(title_regex="(?i)hoppscotch", min_interactive=8)

    def go(title, count):
        return classify_capture(status=200, headers={}, title=title, body_text=PAGE, interactive_count=count,
                                anchor=anchor, url="https://x/")

    assert go("Hoppscotch • Open source API development ecosystem", 59).verdict is OK
    assert go("Hoppscotch", 8).verdict is OK                                    # boundary: min is inclusive
    low = go("Hoppscotch", 7)
    assert low.verdict is SUSPECT and low.signals == ["anchor:interactive=7<8"]
    wrong = go("Example Domain", 59)
    assert wrong.verdict is SUSPECT and wrong.signals == ["anchor:title-mismatch"]
    both = go("", 0)                                                            # a page with no title at all
    assert both.verdict is SUSPECT and both.signals == ["anchor:title-mismatch", "anchor:interactive=0<8"]


def test_anchor_checks_are_skipped_when_unknown_or_unconfigured():
    kw = {"status": 200, "headers": {}, "body_text": PAGE, "url": "https://x/"}
    assert classify_capture(title="x", interactive_count=0, anchor=None, **kw).verdict is OK
    assert classify_capture(title="x", interactive_count=None, anchor=AnchorConfig(), **kw).verdict is OK
    broken = AnchorConfig(title_regex="(unclosed")
    res = classify_capture(title="x", interactive_count=10, anchor=broken, **kw)
    assert res.verdict is SUSPECT and res.signals == ["anchor:title-regex-invalid"]


def test_rung_is_carried_through():
    res = classify_capture(status=200, headers={}, title="t", body_text=PAGE, interactive_count=None, anchor=None,
                           url="https://x/", rung="L1")
    assert res.rung == "L1"


# ------------------------------------------------------------------------------- fingerprint collapse
def _collapse(prior, url="https://x/new"):
    return classify_capture(status=200, headers={}, title="App", body_text=PAGE, interactive_count=None,
                            anchor=None, url=url, prior_fingerprints=prior)


def test_fingerprint_collapse_needs_many_distinct_urls_sharing_one_fingerprint():
    assert COLLAPSE_MIN_URLS == 3
    assert _collapse(None).verdict is OK and _collapse({}).verdict is OK
    two = {"https://x/a": "f1", "https://x/b": "f1", "https://x/c": "f2"}
    assert _collapse(two).verdict is OK
    three = {f"https://x/{c}": "f1" for c in "abc"} | {"https://x/d": "f2"}
    res = _collapse(three)
    assert res.verdict is SUSPECT and res.signals == ["fingerprint-collapse:3-urls"]


def test_collapse_looks_at_the_captures_own_fingerprint_when_its_url_is_present():
    prior = {f"https://x/{c}": "wall" for c in "abcd"} | {"https://x/real": "real-page"}
    assert _collapse(prior, url="https://x/real").verdict is OK
    assert _collapse(prior, url="https://x/a").verdict is SUSPECT


def test_collapse_counts_pages_not_query_strings_or_fragments():
    prior = {"https://x/a?i=1": "f", "https://x/a?i=2": "f", "https://x/a#top": "f", "https://x/b": "f"}
    assert _collapse(prior).verdict is OK                  # two pages (/a, /b), not four URLs


# ------------------------------------------------------------------------------------------ robots.txt
def test_unreadable_robots_is_disallowed_and_empty_is_allowed():            # case 69
    assert robots_allows(None, UA, "https://hoppscotch.io/") is DISALLOWED
    assert robots_allows("", UA, "https://hoppscotch.io/") is OK
    assert robots_allows("# nothing here\n", UA, "https://hoppscotch.io/") is OK


def test_robots_and_collapse_fail_closed_on_unparseable_urls():
    assert robots_allows("User-agent: *\nAllow: /\n", UA, "http://[::1/") is DISALLOWED
    assert _collapse({"http://[::1/": "f"}).verdict is OK       # a bad prior url must not crash classification


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
    assert robots_allows(robots, "BLASTRADIUS", "https://x/private") is DISALLOWED        # case-insensitive token


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
    text = "User-agent: blastradius\nDisallow: /a\n\nUser-agent: *\nDisallow: /b\n\nUser-agent: blastradius\nDisallow: /c\n"
    assert robots_allows(text, UA, "https://x/a") is DISALLOWED
    assert robots_allows(text, UA, "https://x/c") is DISALLOWED
    assert robots_allows(text, UA, "https://x/b") is OK


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
    assert {"cf-mitigated", "x-amzn-waf-action", "server", "content-type"} <= HEADER_ALLOWLIST   # what rules read
    assert len(HEADER_ALLOWLIST) <= 16 and all(h == h.lower() for h in HEADER_ALLOWLIST)


def test_make_capture_keeps_only_allowlisted_headers():
    cap = make_capture(target="t", url="https://x/", access=_access(), status=200, headers={
        "Cookie": "sid=1", "Set-Cookie": "a=b", "Authorization": "Bearer abcdefghijklmnop", "X-Api-Key": "k",
        "Server": "Vercel", "CF-Ray": "abc-MAA", "Content-Type": "text/html", "X-Anything": "no"})
    assert cap.headers == {"server": "Vercel", "cf-ray": "abc-MAA", "content-type": "text/html"}
    assert "sid=1" not in cap.model_dump_json() and "abcdefghijklmnop" not in cap.model_dump_json()


def test_capture_id_is_a_content_hash_without_clock_or_status():
    base = {"target": "t", "url": "https://x/a", "final_url": "https://x/b", "parent_id": "cap_p",
            "action": "click:Docs"}
    a = make_capture(**base, access=_access(), status=200, title="One", captured_at="2026-01-01T00:00:00Z")
    b = make_capture(**base, access=_access(BLOCKED), status=403, title="Two", captured_at="2027-02-02T00:00:00Z")
    assert a.id == b.id and a.id.startswith("cap_")
    for change in ({"target": "u"}, {"final_url": "https://x/c"}, {"parent_id": "cap_q"}, {"action": "click:Home"}):
        assert make_capture(**{**base, **change}, access=_access()).id != a.id
    assert make_capture(**{**base, "url": "https://x/other"}, access=_access()).id == a.id    # url is not an input


def test_make_capture_defaults():
    cap = make_capture(target="t", url="https://x/", access=_access())
    assert cap.final_url == "https://x/" and cap.source_kind == "live" and cap.captured_at
    assert cap.status is None and cap.headers == {} and cap.parent_id is None


def test_append_capture_ok_writes_row_and_evidence_but_no_failure(tmp_path: Path):
    run = run_ctx(tmp_path)
    cap = make_capture(target="hoppscotch", url="https://hoppscotch.io/", access=_access(), status=200,
                       title="Hoppscotch", headers={"server": "Vercel", "set-cookie": "a=b"})
    append_capture(run, cap)
    rows = jsonl(run.run_dir / "crawl" / "captures.jsonl")
    assert len(rows) == 1 and Capture.model_validate(rows[0]) == cap
    ev = jsonl(run.run_dir / "evidence.jsonl")
    assert len(ev) == 1 and ev[0]["kind"] == "crawl.capture" and ev[0]["ref"] == cap.id
    assert ev[0]["verdict"] == "OK" and run.evidence.exists(ev[0]["evidence_id"])
    assert run.failures.items == [] and not (run.run_dir / "failures.jsonl").read_text()


@pytest.mark.parametrize(("verdict", "code"), [(BLOCKED, "ACCESS_BLOCKED"), (SUSPECT, "ACCESS_SUSPECT"),
                                               (DISALLOWED, "ACCESS_DISALLOWED")])
def test_append_capture_records_the_matching_failure_with_signals(tmp_path: Path, verdict, code):
    run = run_ctx(tmp_path)
    access = _access(verdict, vendor="cloudflare", signals=["header:cf-mitigated=challenge", "status:403"],
                     reason="why")
    cap = make_capture(target="hoppscotch", url="https://hoppscotch.io/graphql", access=access, status=403)
    append_capture(run, cap)
    fails = jsonl(run.run_dir / "failures.jsonl")
    assert len(fails) == 1 and fails[0]["code"] == code and fails[0]["stage"] == "access"
    ctx = fails[0]["context"]
    assert ctx["signals"] == ["header:cf-mitigated=challenge", "status:403"]
    assert ctx["capture_id"] == cap.id and ctx["vendor"] == "cloudflare" and ctx["status"] == 403
    assert ctx["url"] == "https://hoppscotch.io/graphql"
    assert jsonl(run.run_dir / "crawl" / "captures.jsonl")[0]["access"]["verdict"] == verdict.value


def test_append_capture_evidence_is_idempotent_and_distinguishes_verdicts(tmp_path: Path):
    run = run_ctx(tmp_path)
    cap = make_capture(target="t", url="https://x/", access=_access(), status=200)
    append_capture(run, cap)
    append_capture(run, cap)
    assert len(run.evidence) == 1                          # same capture, same summary: one evidence row
    blocked = make_capture(target="t", url="https://x/", access=_access(BLOCKED), status=200)
    append_capture(run, blocked)
    assert blocked.id == cap.id and len(run.evidence) == 2


def test_append_capture_redacts_secret_shaped_urls_but_not_the_id(tmp_path: Path):
    run = run_ctx(tmp_path)
    url = "https://app.example.com/cb?token=abcdef1234567890abcdef&tab=1"
    cap = make_capture(target="t", url=url, access=_access(BLOCKED, signals=["s"], reason="r"), status=403)
    append_capture(run, cap)
    blob = (run.run_dir / "crawl" / "captures.jsonl").read_text() + (run.run_dir / "failures.jsonl").read_text()
    blob += (run.run_dir / "evidence.jsonl").read_text()
    assert "abcdef1234567890abcdef" not in blob and "token=[REDACTED]" in blob
    assert cap.id.startswith("cap_") and cap.url == url      # the in-memory object is untouched


def test_append_capture_on_an_ephemeral_run_keeps_memory_only():
    run = RunContext.ephemeral("t")
    cap = make_capture(target="t", url="https://x/", access=_access(BLOCKED, signals=["s"]), status=403)
    append_capture(run, cap)
    assert [f["code"] for f in run.failures.items] == ["ACCESS_BLOCKED"] and len(run.evidence) == 1
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
    assert caps[1].access.vendor == "cloudflare" and caps[1].access.signals == ["body:just-a-moment+challenge-platform"]
    assert [c.status for c in caps] == [200, 403, 200]
    assert caps[0].title == "Hoppscotch • Open source API development ecosystem" and caps[2].title == "Memos"
    assert caps[1].title == "Just a moment..." and caps[1].parent_id == "recorded:001"
    assert caps[0].captured_at == "2026-10-12T07:13:59+00:00"
    assert caps[0].html_path.endswith("001.html") and caps[0].screenshot_path.endswith("001.png")
    assert caps[1].screenshot_path is None and caps[2].screenshot_path is None
    assert all(c.target == "recorded_session" for c in caps)


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


def test_recorded_session_numeric_order_and_ignored_files(tmp_path: Path):
    folder = _write_session(tmp_path / "s", [(stem, {"url": f"https://x/{stem}", "status": 200}, PAGE)
                                             for stem in ("10", "2", "001")])
    (folder / "session.json").write_text("{\"not\": \"a capture\"}")
    (folder / "notes.txt").write_text("ignore me")
    assert [c.url for c in RecordedSession(folder).captures()] == ["https://x/001", "https://x/2", "https://x/10"]


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
    "shortcut", "theme", "font", "editor", "query", "parameter", "authorization", "token", "payload", "preview",
    "cookie", "proxy", "interceptor", "sidebar", "search",
]
_TITLES = ["Hoppscotch • Open source API development ecosystem", "Memos", "Amazon.in: Wireless Headphones",
           "Example Domain", "", "Dashboard | Acme", "Docs - Getting started"]
_SERVERS = ["cloudflare", "Vercel", "AkamaiGHost", "nginx", "Apache", "CloudFront", "AmazonS3", "Server", "gws"]
_PRESENCE = {
    "cf-ray": "a4944e88eff17eda-MAA", "cf-cache-status": "HIT", "x-vercel-id": "bom1::abc-123",
    "x-amz-cf-id": "Zq7mQK1xT0w4fRz8", "x-amz-cf-pop": "BLR50", "via": "1.1 abc.cloudfront.net (CloudFront)",
    "x-cache": "Hit from cloudfront", "x-iinfo": "11-1-0 0NNN RT(1 0)", "x-datadome": "protected",
    "x-datadome-cid": "AHrlqAAAAAMA", "x-datadome-response": "200", "x-amzn-waf-action": "allow",
    "cf-mitigated": "bypass",
}
_COOKIES = ["_abck", "bm_sz", "ak_bmsc", "datadome", "__cf_bm", "cf_clearance", "_px3", "_pxhd", "_pxvid",
            "visid_incap_123", "incap_ses_1_2_3", "aws-waf-token", "session-id"]
_DECOYS = [      # presence or one half of a conjunction: never a block on their own
    "<script defer src=\"/cdn-cgi/challenge-platform/scripts/jsd/main.js\"></script>",
    "<script src=\"/cdn-cgi/scripts/5c5dd728/cloudflare-static/email-decode.min.js\"></script>",
    "<p>This site is protected by Cloudflare.</p>",
    "<p>Access Denied is the name of our permissions guide.</p>",
    "<form action=\"/errors/validateCaptcha\"></form>",
    "<script src=\"https://client.perimeterx.net/PXAbC123/main.min.js\"></script>",
    "<script src=\"https://js.datadome.co/tags.js\"></script>",
    "<script>window.awsWafCookieDomainList = [];</script>",
    "<p>Type the characters in your invite code.</p>",
]


def _random_page(rng: random.Random, size: int) -> str:
    title = rng.choice(_TITLES)
    decoys = "".join(rng.sample(_DECOYS, k=rng.randrange(0, 4)))
    chunk = "<p>" + " ".join(rng.choices(_WORDS, k=150)) + "</p>"
    core = f"<!doctype html><html><head><title>{title}</title></head><body>{decoys}<div id=\"app\"></div>"
    return (core + chunk * (size // len(chunk) + 1))[:size]


def _random_headers(rng: random.Random) -> dict[str, str]:
    headers = {"content-type": rng.choice(["text/html; charset=utf-8", "text/html", ""]),
               "server": rng.choice(_SERVERS)}
    for name in rng.sample(sorted(_PRESENCE), k=rng.randrange(0, 7)):
        headers[name] = _PRESENCE[name]
    if rng.random() < 0.7:
        headers["set-cookie"] = "; ".join(f"{c}={rng.getrandbits(48):x}" for c in rng.sample(_COOKIES, k=rng.randrange(1, 6)))
    return {k: v for k, v in headers.items() if v}


def test_random_ok_looking_pages_never_become_blocked():
    rng = random.Random(20261012)
    sizes = [0, 1, 5219, 2_000_000] + [rng.randrange(0, 2_000_001) for _ in range(150)]
    for size in sizes:
        status = rng.choice([200, 200, 200, 204, 301, 302, 304, 404, 500])
        body, headers = _random_page(rng, size), _random_headers(rng)
        res = classify_http(status, headers, body, "https://example.test/")
        assert res.verdict is OK, (size, status, headers, res)       # presence + size alone are never a signal
        cap = classify_capture(status=status, headers=headers, title=None, body_text=body, interactive_count=None,
                               anchor=None, url="https://example.test/")
        assert cap.verdict is OK
    # the same generator under soft statuses may be SUSPECT, never BLOCKED
    for _ in range(60):
        status = rng.choice([202, 403, 405, 429, 503])
        res = classify_http(status, _random_headers(rng), _random_page(rng, rng.randrange(0, 200_000)), "https://e.test/")
        assert res.verdict is not BLOCKED, (status, res)
