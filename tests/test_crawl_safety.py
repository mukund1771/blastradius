"""Explorer safety (D28; cases 1, 2, 3): egress allowlist, action classes, send gating. Real hoppscotch.io
elements are used wherever the case is about what the app really renders."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "crawl"))
import crawl_helpers as h
from playwright.sync_api import sync_playwright

from blastradius.crawl.perceive import Element
from blastradius.crawl.safety import (
    SAFE_ICON_SIGNATURES,
    ActionClass,
    classify_action,
    dialog_is_destructive,
    egress_verdict,
    host_allowed,
    is_auth_text,
    is_private_host,
    normalise_url,
    send_url_allowed,
    url_denied,
)
from blastradius.crawl.scrub import scrub_url
from blastradius.models import UIElementObs

ALLOW = ["hoppscotch.io", "echo.hoppscotch.io"]
CFG = h.hoppscotch_cfg().crawl
BASE = "https://hoppscotch.io"


# ------------------------------------------------------------------------------------ egress (case 1)
@pytest.mark.parametrize(
    "url",
    [
        "https://hoppscotch.io/",
        "https://echo.hoppscotch.io/",
        "https://api.hoppscotch.io/graphql",
        "wss://echo-websocket.hoppscotch.io/",
        "data:text/plain,hi",
        "blob:https://hoppscotch.io/abc",
        "about:blank",
    ],
)
def test_allowed_egress(url: str):
    assert egress_verdict(url, ALLOW).allowed


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9119/",
        "http://localhost/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.9/",
        "http://metadata.google.internal/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://[::ffff:127.0.0.1]/",
        "http://printer.local/",
        "http://intranet/",
        "https://hoppscotch.io:9119/",
        "https://hoppscotch.io.evil.example/",
        "https://evil.example/hoppscotch.io",
        "https://client.crisp.chat/l.js",
        "https://eu.posthog.com/e/",
        "ftp://hoppscotch.io/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user@evil.example/",
    ],
)
def test_blocked_egress(url: str):
    assert not egress_verdict(url, ALLOW).allowed


def test_private_ranges_are_blocked_even_when_allowlisted():
    assert not egress_verdict("http://127.0.0.1/", ["127.0.0.1"]).allowed
    assert not egress_verdict("http://10.1.2.3/", ["10.1.2.3"]).allowed
    assert is_private_host("fe80::1") and is_private_host("0.0.0.0") and not is_private_host("hoppscotch.io")


def test_subdomain_matching_is_label_aware():
    assert host_allowed("a.b.hoppscotch.io", ALLOW) and not host_allowed("nothoppscotch.io", ALLOW)


# --------------------------------------------------------------------------------------- send gating
def test_send_is_allowed_only_for_allowlisted_urls():
    assert send_url_allowed("https://echo.hoppscotch.io", CFG)
    assert send_url_allowed("HTTPS://Echo.Hoppscotch.io/", CFG)
    assert not send_url_allowed("https://echo.hoppscotch.io/path", CFG)
    assert not send_url_allowed("http://localhost:9119", CFG)
    assert not send_url_allowed(None, CFG) and not send_url_allowed("", CFG)
    assert normalise_url("https://A.test/x/") == "https://a.test/x"


def _el(name: str, role: str = "button", *, href: str | None = None, target: str | None = None) -> Element:
    return Element(
        obs=UIElementObs(
            id=f"el_{name}", role=role, name=name, href=href, selector="#x", tag="a" if href else "button"
        ),
        target=target,
    )


def _decide(name: str, **kw):
    popup_is_auth = kw.pop("popup_is_auth", False)
    popup_destructive = kw.pop("popup_destructive", None)
    request_url = kw.pop("request_url", None)
    return classify_action(
        _el(name, **kw),
        CFG,
        base_url=BASE,
        page_url=f"{BASE}/",
        popup_is_auth=popup_is_auth,
        popup_destructive=popup_destructive,
        request_url=request_url,
    )


def test_send_class_depends_on_the_request_url_field():
    ok = _decide("Send", request_url="https://echo.hoppscotch.io")
    assert ok.action_class == ActionClass.NETWORK_SIDE_EFFECT and ok.allowed
    bad = _decide("Send", request_url="http://169.254.169.254/latest")
    assert bad.action_class == ActionClass.NETWORK_SIDE_EFFECT and not bad.allowed
    assert not _decide("Connect").allowed and not _decide("Run", request_url=None).allowed


@pytest.mark.parametrize(
    "name, cls",
    [
        ("Login", ActionClass.AUTH),
        ("Sign in with SSO", ActionClass.AUTH),
        ("Continue with Google", ActionClass.AUTH),
        ("Delete collection", ActionClass.DESTRUCTIVE),
        ("Clear all", ActionClass.DESTRUCTIVE),
        ("Reset environment", ActionClass.DESTRUCTIVE),
        ("Log out", ActionClass.DESTRUCTIVE),
        ("Chat with us", ActionClass.EXTERNAL_WIDGET),
    ],
)
def test_blocked_classes_are_never_allowed(name: str, cls: ActionClass):
    d = _decide(name)
    assert d.action_class == cls and not d.allowed


@pytest.mark.parametrize(
    "name, cls",
    [
        ("Settings", ActionClass.CLICK_SAFE),
        ("Payload", ActionClass.CLICK_SAFE),  # "pay" inside a word
        ("New", ActionClass.LOCAL_WRITE),
        ("Add new", ActionClass.LOCAL_WRITE),
        ("New Collection", ActionClass.LOCAL_WRITE),
        ("Add request", ActionClass.LOCAL_WRITE),
        ("Create an organization", ActionClass.CLICK_SAFE),
        ("Save", ActionClass.LOCAL_WRITE),
        ("Newsletter", ActionClass.CLICK_SAFE),
    ],
)
def test_safe_and_local_write_classes(name: str, cls: ActionClass):
    d = _decide(name)
    assert d.action_class == cls and d.allowed


def test_local_writes_can_be_switched_off():
    cfg = CFG.model_copy(update={"allow_local_writes": False})
    d = classify_action(_el("New"), cfg, base_url=BASE, page_url=f"{BASE}/")
    assert d.action_class == ActionClass.LOCAL_WRITE and not d.allowed


def test_link_classes():
    nav = _decide("GraphQL", role="link", href=f"{BASE}/graphql")
    assert nav.action_class == ActionClass.NAVIGATE and nav.allowed
    assert (
        _decide("Docs", role="link", href="https://docs.hoppscotch.io/").action_class
        == ActionClass.EXTERNAL_LINK
    )
    assert _decide("Mail", role="link", href="mailto:a@b.c").action_class == ActionClass.EXTERNAL_LINK
    assert _decide("Admin", role="link", href=f"{BASE}/admin/users").action_class == ActionClass.DENIED_URL
    assert _decide("Orgs", role="link", href=f"{BASE}/orgs/new").action_class == ActionClass.DENIED_URL
    blank = _decide("Twitter", role="link", href=f"{BASE}/twitter", target="_blank")
    assert blank.action_class == ActionClass.EXTERNAL_LINK and not blank.allowed
    anchor = classify_action(
        _el("Top", role="link", href=f"{BASE}/#top"), CFG, base_url=BASE, page_url=f"{BASE}/"
    )
    assert anchor.action_class == ActionClass.CLICK_SAFE  # same document, not a navigation


def test_auth_dialogs_poison_everything_inside_them():
    d = _decide("Close", popup_is_auth=True)
    assert d.action_class == ActionClass.AUTH and not d.allowed
    assert is_auth_text("Login to Hoppscotch", CFG) and not is_auth_text("New Collection", CFG)


def test_deny_patterns_from_the_target_config():
    assert url_denied("https://hoppscotch.io/admin/users", CFG)
    assert url_denied("https://hoppscotch.io/enter", CFG) and url_denied(
        "https://hoppscotch.io/profile/x", CFG
    )
    assert url_denied("https://hoppscotch.io/graphql", CFG) is None


# ------------------------------------------------------------------- real hoppscotch.io DOM snapshots
@pytest.fixture(scope="module")
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        yield browser.new_page(viewport={"width": 1280, "height": 800})
        browser.close()


def _classes(page, fixture: str, request_url: str | None = None) -> dict[str, ActionClass]:
    from blastradius.crawl.safety import is_auth_text as auth

    h.load_snapshot(page, fixture)
    perc = h.perceive_page(page)
    popup_auth = bool(perc.popups) and auth(perc.dialog_title or "", CFG)
    out = {}
    for e in perc.reachable():
        d = classify_action(
            e, CFG, base_url=BASE, page_url=perc.url, popup_is_auth=popup_auth, request_url=request_url
        )
        out[e.obs.name or f"[{e.icon}]"] = d.action_class
    return out


def test_real_home_classes(page):
    c = _classes(page, "home", request_url="https://echo.hoppscotch.io")
    assert c["Login"] == ActionClass.AUTH
    assert c["GraphQL"] == ActionClass.NAVIGATE and c["Realtime"] == ActionClass.NAVIGATE
    assert c["Send"] == ActionClass.NETWORK_SIDE_EFFECT
    assert c["New"] == ActionClass.LOCAL_WRITE and c["Add new"] == ActionClass.LOCAL_WRITE
    assert c["Collections"] == ActionClass.CLICK_SAFE and c["Documentation"] == ActionClass.EXTERNAL_LINK


def test_real_login_dialog_is_entirely_blocked(page):
    c = _classes(page, "login_dialog")
    assert c["Continue with Google"] == ActionClass.AUTH and c["Continue with Email"] == ActionClass.AUTH
    assert set(c.values()) <= {ActionClass.AUTH, ActionClass.EXTERNAL_LINK}


def test_real_help_menu_blocks_the_chat_widget_and_external_links(page):
    c = _classes(page, "help_menu")
    assert c["Chat with us"] == ActionClass.EXTERNAL_WIDGET
    assert c["GitHub"] == ActionClass.EXTERNAL_LINK and c["Twitter"] == ActionClass.EXTERNAL_LINK
    assert c["Keyboard shortcuts"] == ActionClass.CLICK_SAFE


def test_real_new_collection_dialog_save_is_a_local_write(page):
    c = _classes(page, "new_collection_modal")
    assert c["Save"] == ActionClass.LOCAL_WRITE and c["Cancel"] == ActionClass.CLICK_SAFE


# ---------------------------------------------- review wave 1: destructive context and nameless controls
def _classify(el: Element, **kw):
    return classify_action(el, CFG, base_url=BASE, page_url=f"{BASE}/", **kw)


def test_destructive_words_are_matched_on_the_href_too():
    d = _classify(_el("Exit", role="link", href=f"{BASE}/logout"))
    assert d.action_class in (ActionClass.AUTH, ActionClass.DESTRUCTIVE) and not d.allowed
    wipe = _classify(_el("Continue", role="link", href=f"{BASE}/account/delete-everything"))
    assert wipe.action_class == ActionClass.DESTRUCTIVE and not wipe.allowed
    assert _classify(_el("Undelete requests")).action_class != ActionClass.DESTRUCTIVE  # whole words only


@pytest.mark.parametrize(
    "name",
    [
        "Discard changes",
        "Leave team",
        "Wipe data",
        "Erase history",
        "Empty trash",
        "Disconnect",
        "Unlink account",
        "Terminate session",
        "Uninstall extension",
    ],
)
def test_the_builtin_word_floor_covers_what_the_default_list_missed(name: str):
    d = _decide(name)
    assert d.action_class == ActionClass.DESTRUCTIVE and not d.allowed


def test_a_target_config_can_add_destructive_words_but_not_remove_the_floor():
    cfg = CFG.model_copy(update={"destructive_words": ["archive"]})
    assert classify_action(_el("Archive"), cfg, base_url=BASE, page_url=BASE + "/").action_class == (
        ActionClass.DESTRUCTIVE
    )
    assert classify_action(_el("Delete"), cfg, base_url=BASE, page_url=BASE + "/").action_class == (
        ActionClass.DESTRUCTIVE
    )


@pytest.mark.parametrize(
    "title, prose, expected",
    [
        ("Delete collection", "", "delete"),
        ("Confirm", "Are you sure you want to continue?", "are you sure"),
        ("Confirm", "This cannot be undone.", "cannot be undone"),
        ("Edit environment", "Variables are stored locally. Leave empty to inherit.", None),
        ("New Collection", "", None),
        ("Import", "Choose a file", None),
    ],
)
def test_a_dialog_is_destructive_by_its_title_or_prose(title: str, prose: str, expected: str | None):
    assert dialog_is_destructive(title, prose, CFG) == expected


def test_inside_a_destructive_dialog_everything_but_cancel_is_destructive():
    word = "delete"
    for name in ("Yes", "OK", "Confirm", "Continue", "Proceed"):
        d = _decide(name, popup_destructive=word)
        assert d.action_class == ActionClass.DESTRUCTIVE and not d.allowed, name
    for name in ("Cancel", "No", "Close", "Dismiss", "Go back"):
        d = _decide(name, popup_destructive=word)
        assert d.allowed, name
    assert _decide("Yes", popup_destructive=None).allowed


def test_a_nameless_control_needs_a_tooltip_or_a_safe_icon():
    def nameless(icon: str | None, tooltip: str = "", **kw) -> Element:
        el = _el("", **kw)
        return Element(obs=el.obs, icon=icon, tooltip=tooltip, target=el.target)

    unknown = _classify(nameless("deadbeef"))
    assert unknown.action_class == ActionClass.UNKNOWN_CONTROL and not unknown.allowed
    assert _classify(nameless("deadbeef", tooltip="Copy")).allowed  # a tooltip is a name
    assert _classify(nameless("95e94a38")).allowed  # the documented drag handle
    assert "95e94a38" in SAFE_ICON_SIGNATURES
    assert _classify(nameless(None)).action_class == ActionClass.UNKNOWN_CONTROL  # not even an icon
    assert _classify(nameless("deadbeef", role="textbox")).allowed  # fields are entered, not pressed
    link = Element(obs=UIElementObs(id="el_l", role="link", name="", href=f"{BASE}/x", selector="#l"))
    assert _classify(link).action_class == ActionClass.NAVIGATE  # a link has its destination


def test_a_tooltip_that_says_delete_is_destructive():
    el = _el("")
    tipped = Element(obs=el.obs, icon="deadbeef", tooltip="Delete collection")
    assert _classify(tipped).action_class == ActionClass.DESTRUCTIVE


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://app.example.com/cb#access_token=opaque123456&token_type=bearer", "access_token=[REDACTED]"),
        ("https://app.example.com/cb?code=abc123&state=x", "code=[REDACTED]"),
        ("https://u:pw@app.example.com/p?sid=9", "sid=[REDACTED]"),
        ("https://hoppscotch.io/#/lovelace/0", "https://hoppscotch.io/#/lovelace/0"),
        ("https://hoppscotch.io/graphql?tab=1", "https://hoppscotch.io/graphql?tab=1"),
    ],
)
def test_persisted_urls_lose_credential_values_and_userinfo(url: str, expected: str):
    out = scrub_url(url)
    assert expected in out and "pw@" not in out and "opaque123456" not in out and "abc123" not in out
