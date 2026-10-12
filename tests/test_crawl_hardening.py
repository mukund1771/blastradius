"""Explorer correctness under the cases the wave-1 review found (D28, D32, cases 3, 7, 12, 13, 17, 18, 67).

Each test names the finding it closes. They run the real Explorer in Chromium against the in-memory app
(tests/fixtures/crawl/app/shell.html), extended per test with the control or script that provokes the case.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "crawl"))
import crawl_helpers as h

from blastradius.crawl import explorer as explorer_module
from blastradius.crawl.explorer import run_crawl
from blastradius.models import AccessVerdict, CrawlSummary, ScreenState


def _extend(shell: str, *, html: str = "", js: str = "") -> str:
    """The fixture app with extra header controls (`html`) and extra wiring (`js`, run after every render)."""
    chat = '<button id="chat">Chat with us</button>'
    out = shell.replace(chat, chat + html, 1)
    assert html in out
    if js:
        out = h.with_script(
            out, "const __wire = wire; wire = function () { __wire(); " + js + " };\nrender();"
        )
    return out


def _codes(r) -> list[str]:
    return [f["code"] for f in r.failures]


# ------------------------------------------------------------------------- soft-404 (MAJOR 4)
def test_many_dead_links_are_one_catch_all_screen_and_never_suspect(tmp_path: Path):
    links = {"Old page": "/old-page", "Old two": "/old-2", "Old three": "/old-3", "Old four": "/old-4"}
    extra = "".join(f'<a href="{href}">{label}</a>' for label, href in list(links.items())[1:])
    shell = h.SHELL.replace('<a href="/old-page">Old page</a>', '<a href="/old-page">Old page</a>' + extra)
    r = h.crawl_once(tmp_path, site=h.Site(shell=shell), max_actions=60)
    assert r.summary.suspect == 0 and "ACCESS_SUSPECT" not in _codes(r)
    assert len([s for s in r.screens if s.url_pattern == "/*" and s.kind == "page"]) == 1
    soft = {t.label for t in r.transitions if t.observed and t.reason.startswith("soft_404")}
    assert set(links) <= soft
    for label in links:  # a dead link is followed once, not again from every screen that carries it
        assert sum(1 for t in r.transitions if t.label == label and t.observed) == 1, label
    assert r.summary.soft_404 == len(links) == len(r.stats["soft_404"]["urls"])
    cap = getattr(explorer_module, "CATCH_ALL_ACTIONS", 2)
    assert r.summary.effort_by_url_pattern.get("/*", 0) <= 1 + cap  # the probe plus the catch-all cap


# ------------------------------------------------------------------------------ Send gate (MAJOR 5)
SEND_LOGS_URL = (
    "on('#send', () => { hit('/send');",
    "on('#send', () => { hit('/send?u=' + encodeURIComponent($('#urlfield').value));",
)


def test_send_is_gated_again_on_the_request_url_at_click_time(tmp_path: Path):
    shell = h.SHELL.replace(*SEND_LOGS_URL)
    assert SEND_LOGS_URL[1] in shell
    site = h.Site(shell=shell)
    with h.open_explorer(tmp_path, site=site) as ex:
        ex._visit_seed("https://app.test/")
        home = next(r for r in ex.screens.values() if r.pattern == "/")
        send = next(c for c in home.candidates if c.element.obs.name == "Send")
        assert send.decision.allowed  # approved when the screen was perceived, with the echo url in the field
        # something else (a history entry, a sample, a saved request) loads another url afterwards
        metadata = "http://169.254.169.254/latest/meta-data/"
        ex.page.evaluate("(v) => { document.querySelector('#urlfield').value = v; }", metadata)
        assert ex._request_url().startswith("http://169.254.169.254")
        ex._act(home, send)
        assert not [x for x in site.hits if x.startswith("/send")]  # nothing was sent
        blocked = [f for f in ex.run.failures.items if f["code"] == "ACTION_BLOCKED_BY_POLICY"]
        assert any("click time" in f["message"] and "NETWORK_SIDE_EFFECT" in f["message"] for f in blocked)
        assert ex.policy_blocked["NETWORK_SIDE_EFFECT"] >= 1
        assert any(t.blocked and t.label == "Send" for t in ex.transitions.values())


def test_a_step_that_sent_a_request_is_never_replayed(tmp_path: Path):
    site = h.Site()
    with h.open_explorer(tmp_path, site=site) as ex:
        ex._visit_seed("https://app.test/")
        home = next(r for r in ex.screens.values() if r.pattern == "/")
        send = next(c for c in home.candidates if c.element.obs.name == "Send")
        ex._act(home, send)
        assert site.hits.count("/send") == 1
        step = explorer_module.Step(
            "click", send.element.obs.id, "button", "Send", 0, before=home.key, side_effect=True
        )
        behind_send = dataclasses.replace(home, steps=[step])  # a screen that was reached through a Send
        assert ex._replay(behind_send) is False
        assert site.hits.count("/send") == 1  # not sent a second time
        assert ex.counts["restores"] == 0 and ex.replay_refused


def test_a_screen_behind_a_send_is_given_up_without_burning_budget(tmp_path: Path):
    with h.open_explorer(tmp_path) as ex:
        ex._visit_seed("https://app.test/")
        home = next(r for r in ex.screens.values() if r.pattern == "/")
        home.steps = [explorer_module.Step("click", "el_x", "button", "Send", 0, side_effect=True)]
        ex.page.evaluate("() => document.querySelector('nav a[href=\"/graphql\"]').click()")
        ex._quiet()
        assert ex._now().pattern == "/graphql"  # away from home, and history back is not on offer:
        ex._go_back = lambda: False  # a restore would have to replay the steps, Send included
        spent = ex.actions_spent
        assert ex._ensure(home) is False and ex.replay_refused
        ex._lost(home, "test")
        assert ex.actions_spent == spent and not home.pending()


# ----------------------------------------------------------------------- destructive context (MAJOR 6)
CONFIRM = (
    "m.querySelector('#yes').onclick = () => { hit('/DELETED'); m.remove(); }; "
    "m.querySelector('#no').onclick = () => m.remove();"
)


def test_every_control_of_a_destructive_dialog_is_blocked_but_cancel(tmp_path: Path):
    js = (
        "const a = document.querySelector('#actions'); if (a) a.onclick = () => {"
        "const m = modal('Are you sure you want to permanently delete this collection?', "
        "'<button id=\"yes\">Yes</button><button id=\"no\">No</button>'); " + CONFIRM + "};"
    )
    actions = '<button aria-label="Collection actions" id="actions">Actions</button>'
    shell = _extend(h.SHELL, html=actions, js=js)
    r = h.crawl_once(
        tmp_path, site=h.Site(shell=shell), cfg=h.make_cfg(seed_urls=["https://app.test/"]), max_actions=40
    )
    assert "/DELETED" not in r.site.hits  # "Yes" was never pressed
    yes = [t for t in r.transitions if t.label == "Yes"]
    assert yes and all(t.blocked and "destructive dialog" in t.reason for t in yes)
    assert any(s.dialog_title and "Are you sure" in s.dialog_title for s in r.screens)  # it was opened


def test_a_control_with_no_name_is_clicked_only_with_a_tooltip_or_a_safe_icon(tmp_path: Path):
    def icon(d: str) -> str:
        return f'<svg viewBox="0 0 24 24" class="svg-icons"><path d="{d}"></path></svg>'

    grip = "".join(f'<circle cx="{x}" cy="{y}" r="1"></circle>' for x in (9, 15) for y in (12, 5, 19))
    html = (
        f'<button aria-label="button" id="mystery">{icon("M2 3h20")}</button>'
        f'<button aria-label="button" id="tipped">{icon("M4 9h16")}</button>'
        '<button aria-label="button" id="grip">'
        f'<svg viewBox="0 0 24 24" class="svg-icons">{grip}</svg></button>'
    )
    js = (
        "const q = (s) => document.querySelector(s);"
        "q('#mystery').onclick = () => hit('/mystery'); q('#tipped').onclick = () => hit('/tipped');"
        "q('#grip').onclick = () => hit('/grip');"
        "q('#tipped')._tippy = {props: {content: 'Open tipped panel <kbd>T</kbd>'}};"
    )
    shell = _extend(h.SHELL, html=html, js=js)
    r = h.crawl_once(
        tmp_path, site=h.Site(shell=shell), cfg=h.make_cfg(seed_urls=["https://app.test/"]), max_actions=40
    )
    assert "/mystery" not in r.site.hits  # nothing says what it does
    assert "/tipped" in r.site.hits and "/grip" in r.site.hits  # a tooltip names it; the icon is safe-listed
    names = {e.name for s in r.screens for e in s.elements}
    assert "Open tipped panel" in names and "Open tipped panel T" not in names  # <kbd> stripped
    unknown = [t for t in r.transitions if t.blocked and t.reason.startswith("unknown_control")]
    assert unknown and all(not t.observed for t in unknown)
    assert "UNKNOWN_CONTROL" in {
        f["context"].get("action_class") for f in r.failures if f["code"] == "ACTION_BLOCKED_BY_POLICY"
    }


# ---------------------------------------------------------------- navigation status/headers (MAJOR 7)
def test_a_click_that_loads_a_challenged_document_is_classified_on_its_own_response(tmp_path: Path):
    shell = _extend(h.SHELL, html='<a href="/walled" id="walled">Walled area</a>')
    walled = {**h.HTML, "cf-mitigated": "challenge", "server": "cloudflare"}
    site = h.Site(shell=shell, pages={"/walled": (200, walled, shell)})
    cfg = h.make_cfg(seed_urls=["https://app.test/"], max_depth=2)
    r = h.crawl_once(tmp_path, site=site, cfg=cfg, max_actions=30)
    caps = [c for c in r.captures if c["url"].endswith("/walled")]
    assert caps, "the link was never followed"
    assert {c["access"]["verdict"] for c in caps} == {"BLOCKED"}
    assert all(c["status"] == 200 and c["headers"].get("cf-mitigated") == "challenge" for c in caps)
    assert not any("walled" in s.url for s in r.screens) and r.summary.blocked >= 1


# --------------------------------------------------------------- deny after observation (MINOR)
def test_a_control_that_routes_onto_a_denied_path_is_never_graphed(tmp_path: Path):
    html = '<span role="button" id="console">Open console</span>'
    js = (
        "const c = document.querySelector('#console'); if (c) c.onclick = () => {"
        "history.pushState({}, '', '/admin/users'); window.dispatchEvent(new PopStateEvent('popstate')); };"
    )
    shell = _extend(h.SHELL, html=html, js=js)
    cfg = h.make_cfg(seed_urls=["https://app.test/"], max_depth=2)
    r = h.crawl_once(tmp_path, site=h.Site(shell=shell), cfg=cfg, max_actions=30)
    assert not any("admin" in s.url_pattern for s in r.screens)
    assert any(c["access"]["verdict"] == "DISALLOWED" and "deny_url_pattern" in c["access"]["signals"]
               for c in r.captures)
    console = [t for t in r.transitions if t.label == "Open console"]
    assert console and all(t.blocked and t.reason.startswith("access: DISALLOWED") for t in console)
    assert r.summary.disallowed >= 1 and r.summary.screens >= 4  # and the crawl carried on from the base
    assert not any("Reset all" in f.read_text() for f in (r.dir / "dom").glob("*.html"))  # content not kept
    assert "STATE_NOT_RECOVERABLE" not in _codes(r)


# ---------------------------------------------------------------------------- _type_probe (MINOR)
def test_credential_shaped_fields_are_never_typed_into_and_never_confirmed(tmp_path: Path):
    html = '<button id="newsecret">New environment</button>'
    js = (
        "const n = document.querySelector('#newsecret'); if (n) n.onclick = () => {"
        "const m = modal('Secrets', '<input type=\"password\" placeholder=\"Token value\" id=\"pw\">"
        "<input type=\"email\" placeholder=\"Contact\"><button id=\"savesecret\">Save</button>');"
        "m.querySelector('#savesecret').onclick = () => { hit('/saved-secret'); m.remove(); }; };"
    )
    shell = _extend(h.SHELL, html=html, js=js)
    cfg = h.make_cfg(seed_urls=["https://app.test/"], max_depth=3)
    r = h.crawl_once(tmp_path, site=h.Site(shell=shell), cfg=cfg, max_actions=40)
    assert any(s.dialog_title == "Secrets" for s in r.screens)  # the dialog was opened
    assert not [x for x in r.site.hits if x.startswith("/saved-secret")]  # never confirmed unfilled
    modal_ids = {s.id for s in r.screens if s.dialog_title == "Secrets"}
    assert modal_ids and not any(t.action == "type" and t.from_screen in modal_ids for t in r.transitions)


# ----------------------------------------------------------------------- artifact_subdir (feature a)
def test_a_bounded_recrawl_writes_only_under_its_subdir(tmp_path: Path):
    main = h.crawl_once(tmp_path, max_actions=14)
    before = {p.relative_to(main.run.run_dir): p.read_bytes() for p in (main.run.run_dir / "crawl").rglob("*")
              if p.is_file()}
    again = h.crawl_once(tmp_path, run=main.run, max_actions=8, goals=["User can open settings"],
                         subdir="goal-1")
    after = {p.relative_to(main.run.run_dir): p.read_bytes() for p in (main.run.run_dir / "crawl").rglob("*")
             if p.is_file() and "goal-1" not in p.parts}
    assert after == before  # the main crawl's artifacts are untouched
    sub = main.run.run_dir / "crawl" / "goal-1"
    for name in ("screens.jsonl", "transitions.jsonl", "captures.jsonl", "summary.json", "stats.json",
                 "goal_effort.json"):
        assert (sub / name).is_file(), name
    assert again.screens and CrawlSummary.model_validate_json((sub / "summary.json").read_text()).screens
    assert all(s.dom_path.startswith("crawl/goal-1/dom/") and s.screenshot.startswith("crawl/goal-1/screens/")
               for s in again.screens)
    assert all((main.run.run_dir / s.dom_path).is_file() for s in again.screens)
    assert all(c["html_path"] is None or c["html_path"].startswith("crawl/goal-1/") for c in again.captures)
    manifest = json.loads((main.run.run_dir / "manifest.json").read_text())
    assert "crawl_env" in manifest and "crawl_env_goal-1" in manifest


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "dom", "screens", "/abs", "x" * 80])
def test_artifact_subdir_must_be_one_plain_component(tmp_path: Path, bad: str):
    if bad == "":
        pytest.skip("empty means no subdir")
    with pytest.raises(ValueError):
        run_crawl(h.make_run(tmp_path), h.make_cfg(), None, artifact_subdir=bad, max_actions=1)


# ------------------------------------------------------------------------------ app_version (feature b)
VERSION_RX = r'(?:"|\bv)(20\d{2}\.\d{1,2}\.\d{1,2})(?:"|\b)'
BUNDLE = 'function a(){}const x=1,_3="2026.9.0",JsA={viewBox:"0 0 24 24"};'


def test_the_version_regex_is_searched_in_the_shell_and_then_in_the_bundles(tmp_path: Path):
    pages = {
        "/assets/index-AAA111.js": (200, {"content-type": "text/javascript"}, BUNDLE),
        "/assets/vendor-BBB222.js": (200, {"content-type": "text/javascript"}, 'var v="1999.1.1"'),
    }
    cfg = h.make_cfg(version_regex=VERSION_RX, max_depth=0)
    r = h.crawl_once(tmp_path, site=h.Site(pages=pages), cfg=cfg, max_actions=2)
    assert r.summary.app_version == "2026.9.0" and r.stats["app_version"] == "2026.9.0"
    env = json.loads((r.run.run_dir / "manifest.json").read_text())["crawl_env"]
    assert env["app_version"] == "2026.9.0"


def test_the_version_falls_back_to_rendered_text_and_stays_none_without_a_regex(tmp_path: Path):
    shell = _extend(
        h.SHELL,
        html='<div id="ver"></div>',
        js="document.querySelector('#ver').textContent = 'Hoppscotch v' + ['2026', '9', '0'].join('.');",
    )
    cfg = h.make_cfg(version_regex=VERSION_RX, max_depth=0)
    r = h.crawl_once(tmp_path, site=h.Site(shell=shell), cfg=cfg, max_actions=2)
    assert r.summary.app_version == "2026.9.0"
    none = h.crawl_once(tmp_path, site=h.Site(shell=shell), cfg=h.make_cfg(max_depth=0), max_actions=2,
                        run_id="no-regex")
    assert none.summary.app_version is None


def test_the_target_regex_finds_the_version_where_hoppscotch_exposes_it():
    """Evidence (2026-10-12): the entry bundle /assets/index-BVV_7b8U.js holds exactly one `20yy.m.p` string,
    `...,_3="2026.9.0",JsA={viewBox...` (Footer.vue: `import { version } from package.json`, shown in the Help
    menu as "Hoppscotch v2026.9.0"; tests/fixtures/crawl/hoppscotch/help_menu.html has that text)."""
    import re

    rx = re.compile(h.hoppscotch_cfg().crawl.version_regex)
    minified = 'onClick:r},null,8,["icon","label"])])]),_:1},8,["title"])):ut("",!0)}}}),_3="2026.9.0",JsA={'
    menu = (h.FIXTURES / "hoppscotch" / "help_menu.html").read_text()
    assert rx.search(minified).group(1) == "2026.9.0"
    assert rx.search(menu).group(1) == "2026.9.0"
    assert rx.search('"1999.1.1"') is None and rx.search("core-js 3.22.1") is None  # not 20yy.m.p


# --------------------------------------------------------------------- the small ones (MINOR/NIT)
def test_max_actions_zero_spends_nothing_and_touches_nothing(tmp_path: Path):
    site = h.Site()
    r = h.crawl_once(tmp_path, site=site, max_actions=0)
    assert r.summary.actions_spent == 0 and r.summary.stop_reason == "action_budget"
    assert r.summary.screens == 0 and site.requested == []  # not even the shell was fetched


def test_an_invalid_widget_selector_refuses_to_start(tmp_path: Path):
    cfg = h.make_cfg(widget_selectors=["##not-a-selector", ".ok"])
    with pytest.raises(ValueError, match="widget_selectors"):
        h.crawl_once(tmp_path, cfg=cfg, max_actions=4)


def test_one_bad_widget_selector_does_not_blind_the_others(tmp_path: Path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        page = pw.chromium.launch().new_page()
        page.set_content('<div id="crisp-x"><button>chat</button></div><button>real</button>')
        perc = h.perceive_page(page, url="https://s.test/", widgets=["##not-a-selector", "#crisp-x"])
        assert [e.obs.name for e in perc.elements] == ["real"]


def test_seed_visits_are_traced_spans(tmp_path: Path):
    from blastradius.trace import load_trace

    r = h.crawl_once(tmp_path, max_actions=6)
    spans = [x for x in load_trace(r.run.run_dir / "trace.jsonl")
             if x.get("kind") == "browser_action" and x.get("event") == "end"]
    seeds = [x for x in spans if x["attributes"].get("role") == "seed"]
    assert len(seeds) >= 3 and {x["attributes"]["action_class"] for x in seeds} >= {"SEED", "SEED_PROBE"}


def test_a_restore_goes_back_in_history_before_it_reloads(tmp_path: Path):
    with h.open_explorer(tmp_path) as ex:
        ex._visit_seed("https://app.test/")
        home = next(r for r in ex.screens.values() if r.pattern == "/")
        ex.page.evaluate("() => { document.querySelector('nav a[href=\"/graphql\"]').click(); }")
        ex._quiet()
        assert ex._now().pattern == "/graphql"
        assert ex._return_to(home, back=True) and ex._now().pattern == "/"
        assert ex.counts["history_back"] == 1 and ex.counts["restores"] == 0


def test_a_replay_is_paced(tmp_path: Path):
    slept: list[float] = []
    with h.open_explorer(tmp_path, cfg=h.make_cfg(action_delay_ms=500)) as ex:
        ex.sleep = slept.append
        ex._visit_seed("https://app.test/")
        home = next(r for r in ex.screens.values() if r.pattern == "/")
        ex.last_action_end = ex.clock()
        assert ex._replay(home)
        assert slept and 0.45 < slept[0] <= 0.5  # the delay was waited out before the page load


# ------------------------------------------------------------------- recorded sessions (MINOR)
def _marked(label: str) -> str:
    return (
        "<html><head><title>Hoppscotch • Open source API</title></head><body>"
        + "".join(f"<button>Control {i}</button>" for i in range(1, 8))
        + f"<button>Marker {label}</button><!-- note --><p>token=abcdefghijklmnopqrstuvwxyz0123</p>"
        "</body></html>"
    )


def _crawl_recorded(base: Path, labels: list[str], run_id: str):
    root = base / run_id
    root.mkdir(parents=True)
    folder = h.recorded_session(root, {label: _marked(label) for label in labels})
    run = h.make_run(root, run_id)
    summary = run_crawl(run, h.hoppscotch_cfg(), None, source="recorded", recorded_dir=folder)
    d = run.run_dir / "crawl"
    screens = [ScreenState.model_validate(x) for x in h.read_jsonl(d / "screens.jsonl")]
    return run, folder, summary, screens


def test_recorded_capture_ids_follow_the_content_not_the_position(tmp_path: Path):
    _, _, _, first = _crawl_recorded(tmp_path, ["alpha", "beta", "gamma"], "ra")
    _, _, _, second = _crawl_recorded(tmp_path, ["gamma", "alpha", "beta"], "rb")

    def by_marker(screens: list[ScreenState]) -> dict[str, str]:
        return {e.name: s.capture_id for s in screens for e in s.elements if e.name.startswith("Marker ")}

    assert by_marker(first) == by_marker(second) and len(by_marker(first)) == 3


def test_recorded_artifacts_are_run_relative_redacted_and_masked(tmp_path: Path):
    run, folder, summary, screens = _crawl_recorded(tmp_path, ["alpha", "beta"], "rec")
    assert summary.screens == len(screens) >= 1
    for s in screens:
        assert s.dom_path.startswith("crawl/dom/") and (run.run_dir / s.dom_path).is_file()
        assert s.screenshot.startswith("crawl/screens/") and (run.run_dir / s.screenshot).is_file()
    blob = "".join((run.run_dir / s.dom_path).read_text() for s in screens)
    assert "abcdefghijklmnopqrstuvwxyz0123" not in blob and "<!--" not in blob
    for name in ("captures.jsonl", "screens.jsonl"):
        assert str(folder) not in (run.run_dir / "crawl" / name).read_text()


def test_a_blocked_recorded_state_keeps_no_source_path_and_an_inert_copy(tmp_path: Path):
    pages = {
        "ok": _marked("ok"),
        "wall": "<html><head><title>Just a moment...</title></head><body onload=\"alert(1)\">"
        "<script src=/cdn-cgi/challenge-platform/h/b/orchestrate.js></script></body></html>",
    }
    folder = h.recorded_session(tmp_path, pages)
    headers = {"cf-mitigated": "challenge"}
    wall_meta = {"url": "https://hoppscotch.io/graphql", "status": 200, "headers": headers}
    (folder / "002.json").write_text(json.dumps(wall_meta))
    run = h.make_run(tmp_path, "wall")
    summary = run_crawl(run, h.hoppscotch_cfg(), None, source="recorded", recorded_dir=folder)
    caps = h.read_jsonl(run.run_dir / "crawl" / "captures.jsonl")
    wall = next(c for c in caps if c["access"]["verdict"] == AccessVerdict.BLOCKED)
    assert summary.blocked == 1 and wall["screenshot_path"] is None
    assert wall["html_path"].startswith("crawl/dom/suspect_")
    copy = (run.run_dir / wall["html_path"]).read_text()
    assert "onload" not in copy and "<script" not in copy


# ------------------------------------------------------------------------ determinism (rule 4)
def test_crawl_artifacts_are_identical_across_hash_seeds(tmp_path: Path):
    digests = set()
    for seed in (1, 2):
        out = h.run_isolated(
            "crawl_scenarios",
            "digest",
            {"tmp": str(tmp_path / f"seed{seed}"), "actions": 14},
            env={"PYTHONHASHSEED": str(seed)},
            timeout=180,
        )
        digests.add(out["digest"])
        assert out["screens"] >= 3
    assert len(digests) == 1, "crawl artifacts depend on PYTHONHASHSEED"


# ------------------------------------------------------- recovery on a live app (found in the live run)
def test_a_page_with_unsaved_changes_does_not_block_leaving_it(tmp_path: Path):
    """Hoppscotch registers a beforeunload handler once something is edited. Dismissing that prompt cancelled
    every goto (ERR_ABORTED) and made history back hang to its timeout; it is accepted now. A confirm() is
    still answered "cancel"."""
    js = "window.onbeforeunload = (e) => { e.preventDefault(); e.returnValue = 'unsaved'; };"
    with h.open_explorer(tmp_path, site=h.Site(shell=h.with_script(h.SHELL, js))) as ex:
        ex._visit_seed("https://app.test/")
        ex.page.locator("#brand").click()  # user activation: without it Chromium shows no prompt
        assert ex._navigate("https://app.test/settings") is None  # was "net::ERR_ABORTED"
        assert ex._now().pattern == "/settings"
        assert ex._go_back() and ex._now().pattern == "/"
        assert ex.page.evaluate("() => confirm('really delete?')") is False


def test_an_element_that_is_not_there_costs_no_action_and_three_in_a_row_give_the_screen_up(tmp_path: Path):
    with h.open_explorer(tmp_path) as ex:
        ex._visit_seed("https://app.test/")
        home = next(r for r in ex.screens.values() if r.pattern == "/")
        victims = [c for c in home.candidates if c.element.obs.role == "link"][:3]
        assert len(victims) == 3
        ex.page.evaluate(  # selectors are positional: resolve them all before removing any
            "(sels) => sels.map((s) => document.querySelector(s)).forEach((e) => e.remove())",
            [v.element.obs.selector for v in victims],
        )
        spent = ex.actions_spent
        ex._act(home, victims[0])
        assert ex.actions_spent == spent and home.drifts == 1  # nothing was done
        ex._act(home, victims[1])
        assert ex.actions_spent == spent and not ex.counts["unrecoverable"]
        ex._act(home, victims[2])
        assert ex.counts["unrecoverable"] == 1 and not home.pending()  # given up after the third
        codes = [f["code"] for f in ex.run.failures.items]
        assert codes.count("SELECTOR_DRIFT") == 3 and "STATE_NOT_RECOVERABLE" in codes


def test_the_right_route_that_has_not_rendered_is_waited_for_before_it_is_trusted(tmp_path: Path):
    waited: list[int] = []
    with h.open_explorer(tmp_path) as ex:
        ex._visit_seed("https://app.test/")
        home = next(r for r in ex.screens.values() if r.pattern == "/")
        ex.crawl.anchor.min_interactive = 999  # as if the page behind the same route were still blank
        ex._hydrate = lambda cap_ms=0: waited.append(cap_ms)  # type: ignore[method-assign]
        ex._ensure(home)
        assert waited and waited[0] > 0
