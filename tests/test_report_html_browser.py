"""The HTML report in a real browser (REPORT_SPEC; case 57): responsive, themed, printable, inert.

Skipped when Chromium cannot be launched. Each test opens the rendered file from disk, so the CSP meta tag and
the absence of network access are exercised as a reader would meet them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from report_helpers import load_evidence, load_sample

from blastradius.report import page
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


@pytest.mark.parametrize("width", [360, 1280])
def test_unbreakable_names_do_not_widen_the_page(browser, tmp_path, width):
    word = "x" * 300
    r = load_sample()
    long_item = r.direct[0].model_copy(update={"label": word, "screen": word, "what_changed": f"{word} is "
                                               "changed by this PR."})
    doc = render_html(r.model_copy(update={"direct": [long_item, *r.direct[1:]], "pr_title": word}),
                      load_evidence())
    path = tmp_path / "long.html"
    path.write_text(doc, encoding="utf-8")
    ctx, pg = open_page(browser, path, width=width)
    try:
        pg.evaluate("document.querySelector('details.appendix').open = true")
        assert pg.evaluate("document.documentElement.scrollWidth") <= width
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
