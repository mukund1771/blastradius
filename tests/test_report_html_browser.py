"""The HTML report in a real browser (REPORT_SPEC; case 57): responsive, themed, printable, inert.

Skipped when Chromium cannot be launched. Each test opens the rendered file from disk, so the CSP meta tag and
the absence of network access are exercised as a reader would meet them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from report_helpers import item, load_evidence, load_sample

from blastradius.report import page
from blastradius.report.model import ChecklistItem
from blastradius.report.page import render_html

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - no browser on this machine
            pytest.skip(f"chromium is not available: {exc}")
        yield b
        b.close()


@pytest.fixture(scope="module")
def report_file(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("html") / "report.html"
    path.write_text(render_html(load_sample(), load_evidence()), encoding="utf-8")
    return path


def open_page(browser, report_file, *, width=1280, height=900, scheme="light"):
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    pg = ctx.new_page()
    pg.goto(report_file.as_uri())
    return ctx, pg


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [360, 768, 1280])
def test_no_horizontal_scroll_at_any_width(browser, report_file, width, scheme):
    ctx, pg = open_page(browser, report_file, width=width, scheme=scheme)
    try:
        assert pg.evaluate("document.documentElement.scrollWidth") <= width
        pg.evaluate("document.querySelector('details.appendix').open = true")
        assert pg.evaluate("document.documentElement.scrollWidth") <= width      # long paths wrap too
    finally:
        ctx.close()


# One long token that cannot wrap at a space, in each place a person's own words can land. Each case puts the
# token in exactly one field, so a failure names the field. The tokens are the shapes seen in the wild: a defanged
# documentation URL, a 40-character identifier and a 35-character camelCase name (the last two only overflow on
# the narrowest phones, which is where the checklist and headline flex items used to give way).
UNBREAKABLE = {
    "url": "hxxps://docs.hoppscotch.io/documentation/features/environments",
    "identifier40": "environmentVariablesCurrentValueEditorV2",
    "camel35": "getEnvironmentVariablesCurrentValue",
    "x300": "x" * 300,
}
PHONE_WIDTHS = (320, 360, 414)
OTHER_WIDTHS = (768, 1280)


def _item(key: str, tier: str, token: str, **kw):
    return item(key, tier=tier, label=token, screen=None, what="This is changed by this PR.", **kw)


def _with_item(r, which: str, **update):
    items = list(getattr(r, which))
    items[0] = items[0].model_copy(update=update)
    return r.model_copy(update={which: items})


def _with_check(r, **update):
    items = list(r.checklist)
    items[0] = items[0].model_copy(update=update)
    return r.model_copy(update={"checklist": items})


def _with_and(r, **update):
    items = list(r.undetermined)
    items[0] = items[0].model_copy(update=update)
    return r.model_copy(update={"undetermined": items})


def _with_req(r, **update):
    items = list(r.requirements)
    items[0] = items[0].model_copy(update=update)
    return r.model_copy(update={"requirements": items})


def _stored(r, token: str):
    s = _item("s", "stored_data", "Saved data")
    steps = [ChecklistItem(text=f"Export {token} and import it again.", why="x", item_ids=[s.id])]
    return r.model_copy(update={"stored_data": [s], "checklist": steps})


FIELDS = {
    "headline": lambda r, t: r.model_copy(update={"headline": f'Blast radius: PR #6645 "x"\nRisk: MEDIUM. Touches {t}.'}),
    "pr_title": lambda r, t: r.model_copy(update={"pr_title": t}),
    "target": lambda r, t: r.model_copy(update={"target": t}),
    "confidence_line": lambda r, t: r.model_copy(update={"confidence_line": f"Confidence: {t}."}),
    "note": lambda r, t: r.model_copy(update={"snapshot": r.snapshot.model_copy(update={"deployed_vs_indexed": t})}),
    "checklist_text": lambda r, t: _with_check(r, text=f"Open {t} and edit it."),
    "checklist_why": lambda r, t: _with_check(r, why=f"{t} changed."),
    "direct_label": lambda r, t: _with_item(r, "direct", label=t),
    "direct_screen": lambda r, t: _with_item(r, "direct", screen=t),
    "direct_text": lambda r, t: _with_item(r, "direct", what_changed=f"{t} is changed by this PR."),
    "direct_flow": lambda r, t: _with_item(r, "direct", flows=[t]),
    "direct_file": lambda r, t: _with_item(r, "direct", files=[f"packages/{t}"]),
    "indirect_section": lambda r, t: _with_item(r, "indirect", section=t),
    "indirect_via": lambda r, t: _with_item(r, "indirect", via=t),
    "global_label": lambda r, t: r.model_copy(update={"global_changes": [_item("g", "global", t)]}),
    "stored_label": lambda r, t: r.model_copy(update={"stored_data": [_item("s", "stored_data", t)]}),
    "stored_step": lambda r, t: _stored(r, t),
    "requirement_statement": lambda r, t: _with_req(r, statement=f"User can use {t}."),
    "requirement_quote": lambda r, t: _with_req(r, quote=t),
    "requirement_section": lambda r, t: _with_req(r, section=t),
    "undetermined_subject": lambda r, t: _with_and(r, subject=t),
    "undetermined_why": lambda r, t: _with_and(r, why=f"{t} was not found."),
    "undetermined_check": lambda r, t: _with_and(r, what_to_check=f"Look at {t}."),
    "not_checked": lambda r, t: r.model_copy(update={"not_checked": [f"Only {t} was seen."]}),
    "snapshot_repo": lambda r, t: r.model_copy(update={"snapshot": r.snapshot.model_copy(update={"repo": t})}),
}

_OVERFLOWING = """(w) => [...document.querySelectorAll('main *')]
    .filter(e => e.getBoundingClientRect().right > w + 0.5)
    .slice(0, 4).map(e => e.tagName.toLowerCase() + (e.className ? '.' + e.className : ''))"""
_TABLE_SCROLLS = "[...document.querySelectorAll('.tbl')].some(t => t.scrollWidth > t.clientWidth + 0.5)"


@pytest.mark.parametrize("token", sorted(UNBREAKABLE))
@pytest.mark.parametrize("field", sorted(FIELDS))
def test_unbreakable_names_do_not_widen_the_page(browser, field, token):
    r = FIELDS[field](load_sample(), UNBREAKABLE[token])
    doc = render_html(r, load_evidence())
    ctx = browser.new_context(viewport={"width": 360, "height": 900})
    pg = ctx.new_page()
    try:
        pg.set_content(doc)
        pg.evaluate("document.querySelector('details.appendix').open = true")
        for width in (*PHONE_WIDTHS, *OTHER_WIDTHS):
            pg.set_viewport_size({"width": width, "height": 900})
            scroll = pg.evaluate("document.documentElement.scrollWidth")
            assert scroll <= width, (field, token, width, scroll, pg.evaluate(_OVERFLOWING, width))
            assert not pg.evaluate(_TABLE_SCROLLS), (field, token, width, "a table scrolls inside its box")
    finally:
        ctx.close()


def test_unbreakable_evidence_text_does_not_widen_the_page(browser):
    ev = {k: dict(v) for k, v in load_evidence().items()}
    for rec in ev.values():
        rec["summary"] = f"modified {UNBREAKABLE['url']}{'z' * 120}"
        rec["ref"] = "hoppscotch/hoppscotch#6645:" + "packages/" + "deep/" * 4 + "q" * 150
    doc = render_html(load_sample(), ev)
    ctx = browser.new_context(viewport={"width": 320, "height": 900})
    pg = ctx.new_page()
    try:
        pg.set_content(doc)
        pg.evaluate("document.querySelector('details.appendix').open = true")
        assert pg.evaluate("document.documentElement.scrollWidth") <= 320
    finally:
        ctx.close()


def test_tables_become_stacked_cards_on_a_phone(browser, report_file):
    ctx, pg = open_page(browser, report_file, width=360)
    try:
        assert pg.evaluate("getComputedStyle(document.querySelector('tbody td')).display") == "block"
        label = pg.evaluate("getComputedStyle(document.querySelector('tbody td'), '::before').content")
        assert label.strip('"') in {"What changed", "How sure", "What may be affected"}
    finally:
        ctx.close()
    ctx, pg = open_page(browser, report_file, width=1280)
    try:
        assert pg.evaluate("getComputedStyle(document.querySelector('tbody td')).display") == "table-cell"
    finally:
        ctx.close()


@pytest.mark.parametrize(("scheme", "palette"), [("light", page.LIGHT), ("dark", page.DARK)])
def test_the_page_follows_prefers_color_scheme(browser, report_file, scheme, palette):
    ctx, pg = open_page(browser, report_file, scheme=scheme)
    try:
        bg = pg.evaluate("getComputedStyle(document.body).backgroundColor")
        r, g, b = (int(palette["bg"][i:i + 2], 16) for i in (1, 3, 5))
        assert bg == f"rgb({r}, {g}, {b})"
    finally:
        ctx.close()


def test_print_uses_the_light_palette_and_shows_the_appendix(browser, report_file):
    ctx, pg = open_page(browser, report_file, scheme="dark")
    try:
        pg.emulate_media(media="print", color_scheme="dark")
        assert pg.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(255, 255, 255)"
        assert pg.evaluate("getComputedStyle(document.body).color") == "rgb(31, 35, 40)"
        # the appendix is a closed <details> on screen; printing must not hide the evidence behind it
        # (getBoundingClientRect still measures skipped content, so ask whether it is actually rendered)
        assert pg.evaluate("document.querySelector('details.appendix').open") is False
        assert pg.evaluate("document.querySelector('#ev-1').checkVisibility()") is True
        pg.emulate_media(media="screen", color_scheme="dark")
        assert pg.evaluate("document.querySelector('#ev-1').checkVisibility()") is False
    finally:
        ctx.close()


def test_footnote_markers_open_the_appendix_and_land_on_their_entry(browser, report_file):
    ctx, pg = open_page(browser, report_file)
    try:
        assert pg.evaluate("document.querySelector('details.appendix').open") is False
        pg.click("sup.fn a >> nth=0")
        assert pg.evaluate("document.querySelector('details.appendix').open") is True
        assert pg.evaluate("location.hash").startswith("#ev-")
    finally:
        ctx.close()


def test_the_page_loads_nothing_and_runs_nothing(browser, report_file):
    ctx = browser.new_context()
    pg = ctx.new_page()
    requests: list[str] = []
    problems: list[str] = []
    pg.on("request", lambda r: requests.append(r.url))
    pg.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: problems.append(str(e)))
    try:
        pg.goto(report_file.as_uri())
        pg.wait_for_load_state("networkidle")
        assert requests == [report_file.as_uri()]
        assert problems == []
        assert pg.evaluate("document.scripts.length") == 0
    finally:
        ctx.close()


def test_checklist_boxes_are_real_controls(browser, report_file):
    ctx, pg = open_page(browser, report_file)
    try:
        box = pg.locator("ul.checks input[type=checkbox]").first
        assert not box.is_checked()
        pg.locator("ul.checks label").first.click()
        assert box.is_checked()
    finally:
        ctx.close()


def test_risk_is_a_word_and_not_only_a_colour(browser, report_file):
    ctx, pg = open_page(browser, report_file)
    try:
        assert pg.inner_text(".pill").strip().endswith("Risk: MEDIUM")
        assert pg.inner_text(".band-confirmed").strip().endswith("Confirmed")
    finally:
        ctx.close()
