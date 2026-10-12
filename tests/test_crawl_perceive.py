"""Perception on real hoppscotch.io DOM snapshots (saved with the product's own snapshot code) and on small
synthetic pages. Covers cases 2, 5, 7, 9, 10 and the masked-DOM contract (case 58)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "crawl"))
import crawl_helpers as h
from playwright.sync_api import sync_playwright

from blastradius.crawl import perceive
from blastradius.crawl.perceive import accessible_name, clean_label, mask_volatile, strip_shortcuts


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        pg = browser.new_page(viewport={"width": 1280, "height": 800})
        yield pg
        browser.close()


# ------------------------------------------------------------------------------ text rules (case 5, 10)
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Search and commands ⌘ K", "Search and commands"),
        ("Search and commands Ctrl K", "Search and commands"),
        ("Search and commands ⌘K", "Search and commands"),
        ("Toggle theme alt+T", "Toggle theme"),
        ("Send Request Ctrl + Enter", "Send Request"),
        ("⌘ K Search", "Search"),
        ("Search (⌘ K)", "Search"),
        ("Keyboard shortcuts ⌘ /", "Keyboard shortcuts"),
        ("Help menu ?", "Help menu ?"),  # a bare "?" is not a chord
        ("Option A", "Option A"),  # modifier-looking words that are not chords stay
        ("Alt text", "Alt text"),
        ("Shift left", "Shift left"),
    ],
)
def test_shortcut_chords_are_stripped(raw: str, expected: str):
    assert strip_shortcuts(raw) == expected


def test_macos_and_linux_spellings_hash_to_the_same_name():
    assert clean_label("Search and commands ⌘ K") == clean_label("Search and commands Ctrl K")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Updated 3 minutes ago", "Updated <time>"),
        ("just now", "<time>"),
        ("Response time 245 ms", "Response time <ms>"),
        ("Size 1.2 KB", "Size <size>"),
        ("12.3k stars", "<n> stars"),
        ("Collections (3)", "Collections (#)"),
        ("5 requests", "# requests"),
        ("saved at 2026-10-12T07:01:02Z", "saved at <time>"),
        ("Collection 1", "Collection 1"),  # a name with a digit is not volatile
    ],
)
def test_volatile_values_are_masked(raw: str, expected: str):
    assert mask_volatile(raw) == expected


def test_accessible_name_order_and_degenerate_aria_label():
    assert accessible_name({"aria": "Close", "text": "x", "title": "t"}) == "Close"
    assert accessible_name({"labelledby": "Heading", "title": "t", "text": "x"}) == "Heading"
    assert accessible_name({"title": "Tip", "text": "x"}) == "Tip"
    assert accessible_name({"text": "Send", "placeholder": "p"}) == "Send"
    assert accessible_name({"placeholder": "Search", "alt": "a"}) == "Search"
    assert accessible_name({"alt": "Logo"}) == "Logo"
    # component kits stamp aria-label="button" / "Link" on everything; the visible text must win
    assert accessible_name({"aria": "button", "text": "Save My Workspace"}) == "Save My Workspace"
    assert accessible_name({"aria": "Link", "text": ""}) == ""


# ---------------------------------------------------------------------- real data: hoppscotch.io home
def test_home_snapshot_matches_the_live_measurement(page):
    h.load_snapshot(page, "home")
    perc = h.perceive_page(page, widgets=h.hoppscotch_cfg().crawl.widget_selectors)
    assert perc.interactive_count == 57  # measured live on 2026-10-12 (RECON.md section 1)
    assert perc.active_tabs == ["Untitled", "Parameters", "Collections"]
    assert perc.popups == [] and perc.editors == 3  # URL, Key, Value editors


def test_no_name_carries_a_shortcut_glyph_or_the_degenerate_label(page):
    h.load_snapshot(page, "home")
    perc = h.perceive_page(page)
    names = [e.obs.name for e in perc.elements]
    assert "Search and commands" in names
    assert not any(any(g in n for g in "⌘⌥⇧⌃") for n in names)
    assert "button" not in [n.lower() for n in names] and "link" not in [n.lower() for n in names]


def test_editors_are_recorded_once_and_lines_are_not_enumerated(page):
    h.load_snapshot(page, "home")
    editors = [e for e in h.perceive_page(page).elements if e.editor]
    assert [e.obs.name for e in editors] == ["Enter a URL or paste a cURL command", "Key", "Value"]
    assert all(e.obs.role == "textbox" and e.obs.text == "" for e in editors)


def test_nameless_icon_buttons_get_distinct_ids_from_their_svg_signature(page):
    h.load_snapshot(page, "home")
    perc = h.perceive_page(page)
    nameless = [e for e in perc.elements if e.obs.role == "button" and not e.obs.name]
    assert len(nameless) >= 15
    assert all(e.icon for e in nameless)
    assert len({e.obs.id for e in perc.elements}) == len(perc.elements)  # no collisions at all
    ordinals = [e.obs.ordinal for e in nameless]
    assert ordinals == sorted(ordinals) and len(set(ordinals)) == len(ordinals)  # (role, name) ordinal


def test_ids_do_not_depend_on_dom_order_of_other_icons(page):
    """Removing one icon button must not renumber the ids of the other nameless buttons (icon identity)."""
    h.load_snapshot(page, "home")
    before = {e.icon: e.obs.id for e in h.perceive_page(page).elements if not e.obs.name and e.icon}
    page.evaluate(
        "() => document.querySelector('header button svg[data-br-icon=\"0685a1ac\"]')"
        ".closest('button').remove()"
    )
    after = {e.icon: e.obs.id for e in h.perceive_page(page).elements if not e.obs.name and e.icon}
    shared = set(before) & set(after) - {"22d02b1e", "86277353", "c65ec854", "cbbad7f9"}  # icons that repeat
    assert shared and all(before[i] == after[i] for i in shared)


def test_navigation_links_carry_absolute_hrefs(page):
    h.load_snapshot(page, "home")
    links = {e.obs.name: e.obs.href for e in h.perceive_page(page).elements if e.obs.role == "link"}
    assert links["GraphQL"] == "https://hoppscotch.io/graphql"
    assert links["Settings"] == "https://hoppscotch.io/settings"


@pytest.mark.parametrize(
    "fixture, title, role",
    [
        ("new_collection_modal", "New Collection", "dialog"),
        ("search_modal", "Type a command or search…", "dialog"),
        ("login_dialog", "Login to Hoppscotch", "dialog"),
        ("help_menu", None, "menu"),
    ],
)
def test_popups_are_detected_with_their_titles(page, fixture, title, role):
    h.load_snapshot(page, fixture)
    perc = h.perceive_page(page)
    assert len(perc.popups) == 1 and perc.popups[0]["role"] == role
    assert (perc.dialog_title or None) == (title or "menu")
    # only the popup layer is reachable; the page behind it is not
    assert all(e.popup == 0 for e in perc.reachable()) and 1 <= len(perc.reachable()) < len(perc.elements)


def test_a_modal_ignores_the_tabs_of_the_page_behind_it(page):
    h.load_snapshot(page, "new_collection_modal")
    assert h.perceive_page(page).active_tabs == []


# --------------------------------------------------------------------------- synthetic pages
SYNTH = """<!doctype html><html><head><title>t</title></head><body>
<button id=a aria-label="Open menu">Menu <kbd>Ctrl</kbd><kbd>M</kbd></button>
<div id="crisp-chatbox"><button id=chat>Chat with support</button><p>Hi, how can we help?</p></div>
<div class="cm-editor"><div class="cm-content" role="textbox" aria-label="Body">
  <div class="cm-line"><a href="https://x.test/1">one</a></div>
  <div class="cm-line"><button>two</button></div></div></div>
<input id="secret" type="password" value="hunter2-very-secret">
<textarea>sk-ant-api03-CANARYCANARYCANARYCANARY</textarea>
<p style="display:none"><button id="gone">Hidden</button></p>
<span id="stamp">updated 5 minutes ago</span>
<script>window.evil = 1</script>
</body></html>"""


def test_widgets_and_hidden_nodes_are_never_perceived(page):
    page.set_content(SYNTH)
    perc = h.perceive_page(page, url="https://s.test/", widgets=["[id^='crisp-']"])
    names = [e.obs.name for e in perc.elements]
    assert "Open menu" in names  # aria-label wins; the kbd text is not in the name
    assert "Chat with support" not in names and "Hidden" not in names
    assert "Hi, how can we help?" not in perc.body_text and "updated" in perc.body_text


def test_editor_internals_are_not_enumerated(page):
    page.set_content(SYNTH)
    perc = h.perceive_page(page, url="https://s.test/", widgets=["[id^='crisp-']"])
    assert [e.obs.name for e in perc.elements if e.editor] == ["Body"]
    assert "one" not in [e.obs.name for e in perc.elements] and "two" not in [
        e.obs.name for e in perc.elements
    ]


def test_dom_snapshot_is_masked_and_replays_to_the_same_perception(page):
    page.set_content(SYNTH)
    widgets = ["[id^='crisp-']"]
    live = h.perceive_page(page, url="https://s.test/", widgets=widgets)
    html = perceive.snapshot_dom(page, widgets)
    assert "<script" not in html and "hunter2" not in html and "CANARY" not in html
    assert "crisp" not in html.lower() and "Hi, how can we help" not in html
    assert "updated <time>" in html  # volatile text masked in the snapshot
    page.set_content(html)
    replay = h.perceive_page(page, url="https://s.test/", widgets=widgets)
    keys = lambda p: sorted((e.obs.role, e.obs.name) for e in p.elements)
    assert keys(replay) == keys(live)


def test_display_contents_wrappers_do_not_hide_their_children_in_the_snapshot(page):
    """Regression from real data: Hoppscotch wraps the request panel in `display: contents`, and
    checkVisibility() is false for such a wrapper, which hid the whole panel in the first snapshots."""
    page.set_content(
        '<div style="display:contents"><button id=b>Inside</button></div>'
        "<div hidden><button>No</button></div>"
    )
    html = perceive.snapshot_dom(page, [])
    page.set_content(html)
    names = [e.obs.name for e in h.perceive_page(page, url="https://s.test/").elements]
    assert names == ["Inside"]


def test_secrets_in_hrefs_and_snapshots_are_scrubbed(page):
    page.set_content(
        '<a href="https://s.test/cb?token=abcdefghijklmnopqrstuvwxyz0123">Go</a>'
        "<p>Authorization: Bearer abcdefghijklmnop12345678</p>"
    )
    link = h.perceive_page(page, url="https://s.test/").elements[0]
    assert "abcdefghijklmnop" not in (link.obs.href or "")
    assert "abcdefghijklmnop" not in perceive.snapshot_dom(page, [])


# ------------------------------------------------------------- review wave 1: case 5 / 10 gaps
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Send Ctrl ↵", "Send"),
        ("Send ⌘ ↵", "Send"),
        ("Zoom in Ctrl +", "Zoom in"),
        ("Zoom in ⌘ +", "Zoom in"),
        ("Zoom out Ctrl -", "Zoom out"),
        ("Open (Ctrl+O)", "Open"),
        ("Open (⌘ O)", "Open"),
        ("Close Esc", "Close"),
        ("Close ⎋", "Close"),
        ("Esc", "Esc"),  # a lone key name is the whole label, not a chord
        ("Page Up", "Page Up"),
    ],
)
def test_more_shortcut_spellings_are_stripped(raw: str, expected: str):
    assert strip_shortcuts(raw) == expected


def test_the_macos_and_linux_spelling_of_one_control_is_one_name():
    pairs = [("Send ⌘ ↵", "Send Ctrl ↵"), ("Zoom in ⌘ +", "Zoom in Ctrl +"), ("Close ⎋", "Close Esc")]
    for mac, linux in pairs:
        assert clean_label(mac) == clean_label(linux)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Response time 1.2 s", "Response time <ms>"),
        ("Timeout 30s", "Timeout <ms>"),
        ("Took 2 secs", "Took <ms>"),
        ("Took 340ms", "Took <ms>"),
        ("3 seconds ago", "<time>"),  # relative times keep their own token
    ],
)
def test_second_scale_durations_are_masked(raw: str, expected: str):
    assert mask_volatile(raw) == expected


def test_a_duration_that_flips_unit_between_runs_is_one_name():
    assert clean_label("Time: 999 ms") == clean_label("Time: 1.0 s")


# ------------------------------------------------- review wave 1: names, owners, popups (case 7, 23, 58)
def test_a_tooltip_names_an_icon_only_control(page):
    page.set_content(
        '<button aria-label="button" id="a"><svg><path d="M1 1h2"></path></svg></button>'
        '<button id="b"><svg><path d="M2 2h4"></path></svg></button>'
        '<button id="c"><svg><path d="M3 3h6"></path></svg></button>'
        '<span id="tip" role="tooltip">Describe me</span>'
    )
    page.evaluate(
        "() => { document.querySelector('#a')._tippy = {props: {content: 'Copy <kbd>⌘</kbd><kbd>C</kbd>'}};"
        "document.querySelector('#b').setAttribute('aria-describedby', 'tip');"
        "document.querySelector('#c').setAttribute('data-br-tip', 'Rename'); }"
    )
    perc = h.perceive_page(page, url="https://s.test/")
    names = {e.obs.dom_id: e.obs.name for e in perc.elements if e.obs.dom_id in ("a", "b", "c")}
    assert names == {"a": "Copy", "b": "Describe me", "c": "Rename"}
    assert all(e.tooltip for e in perc.elements if e.obs.dom_id in ("a", "b", "c"))


def test_the_snapshot_keeps_the_tooltip_and_the_scope_owner_for_offline_replay(page):
    page.set_content(
        '<div data-v-1a2b3c4d><span><button aria-label="button"><svg><path d="M1 1h2"></path></svg>'
        "</button></span></div><button data-v-0badf00d>Plain</button>"
    )
    page.evaluate(
        "() => { document.querySelector('svg').parentElement._tippy = {props: {content: 'Wiki'}}; }"
    )
    live = h.perceive_page(page, url="https://s.test/")
    html = perceive.snapshot_dom(page, [])
    assert " data-v-1a2b3c4d" not in html  # the raw build-hash attribute is not kept...
    assert 'data-br-owner="data-v-1a2b3c4d"' in html  # ...its value is, under a name of ours
    page.set_content(html)
    replay = h.perceive_page(page, url="https://s.test/")
    expected = [("Plain", "data-v-0badf00d"), ("Wiki", "data-v-1a2b3c4d")]
    for p in (live, replay):
        assert sorted((e.obs.name, e.obs.owner_hint) for e in p.elements) == expected


def test_owner_hint_is_the_nearest_scoped_ancestor_and_none_without_one(page):
    page.set_content(
        '<section data-v-aaaaaaaa><div data-v-bbbbbbbb><button>Inner</button></div><button>Outer</button>'
        "</section><button>Bare</button>"
    )
    owners = {e.obs.name: e.obs.owner_hint for e in h.perceive_page(page, url="https://s.test/").elements}
    assert owners == {"Inner": "data-v-bbbbbbbb", "Outer": "data-v-aaaaaaaa", "Bare": None}


def test_a_persistent_menu_bar_is_the_page_not_a_popup_layer(page):
    page.set_content(
        '<nav><ul role="menu"><li><a role="menuitem" href="/a">Alpha</a></li>'
        '<li><a role="menuitem" href="/b">Beta</a></li></ul></nav><main><button>Do it</button></main>'
    )
    perc = h.perceive_page(page, url="https://s.test/")
    assert perc.popups == []
    assert {"Alpha", "Beta", "Do it"} <= {e.obs.name for e in perc.reachable()}


def test_a_floating_menu_is_still_a_popup_layer(page):
    page.set_content(
        '<nav><button>Open</button><ul role="menu" style="position:absolute;top:30px">'
        '<li><a role="menuitem" href="/a">Alpha</a></li></ul></nav><main><button>Behind</button></main>'
    )
    perc = h.perceive_page(page, url="https://s.test/")
    assert len(perc.popups) == 1 and [e.obs.name for e in perc.reachable()] == ["Alpha"]


def test_a_dialogs_prose_excludes_the_text_of_its_controls(page):
    page.set_content(
        '<div role="dialog" aria-label="Collection"><p>This cannot be undone.</p>'
        '<ul><li><button>Delete everything</button></li><li><a href="/x">Discard</a></li></ul>'
        "<button>Cancel</button></div>"
    )
    perc = h.perceive_page(page, url="https://s.test/")
    assert perc.dialog_prose == "This cannot be undone."


def test_secret_shaped_names_and_text_are_scrubbed_before_they_are_kept(page):
    page.set_content(
        '<button aria-label="Authorization: Bearer abcdefghijklmnop12345678">Copy</button>'
        '<input placeholder="token=abcdefghijklmnopqrstuvwxyz0123">'
    )
    perc = h.perceive_page(page, url="https://s.test/")
    blob = " ".join(
        str(x) for e in perc.elements for x in (e.obs.name, e.obs.text, e.obs.aria_label, e.obs.placeholder)
    )
    assert "abcdefghijklmnop" not in blob and "REDACTED" in blob
