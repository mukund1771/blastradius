"""End-to-end explorer tests against an in-memory app served through Playwright routes (no network).

The fixture app (tests/fixtures/crawl/app/shell.html) reproduces what stresses the explorer on Hoppscotch:
Teleported dialogs, `.tab.active` strips, a CodeMirror-like editor, shortcut glyphs, icon-only controls, a
Crisp-like widget, an auth button, destructive buttons, an off-origin link, a denied route, a Send button
gated by the request URL, a soft-404 catch-all that answers 200, and page-initiated requests to loopback, the
cloud metadata address and a tracker. Cases covered: 1, 2, 3, 5, 6, 7, 9, 12, 13, 17.
(The review regressions are in test_crawl_hardening.py and test_crawl_egress.py.)
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "crawl"))
import crawl_helpers as h

from blastradius.crawl.explorer import Explorer, run_crawl
from blastradius.crawl.policy import HeuristicPolicy, LLMPolicy, Ranking
from blastradius.llm import LLM
from blastradius.models import AccessVerdict, CrawlSummary, ScreenState, Transition
from blastradius.trace import load_trace

ROBOTS_OK = "User-agent: *\nAllow: /\n"
GOALS = ["User can import a collection from Postman", "User can change the theme in settings"]


def _crawl(
    tmp_path: Path,
    *,
    site: h.Site | None = None,
    cfg=None,
    robots: str | None = ROBOTS_OK,
    max_actions: int = 40,
    goals=None,
    run_id: str = "crawl-test",
    llm_client=None,
):
    run = h.make_run(tmp_path, run_id)
    site = site or h.Site()
    policy = HeuristicPolicy()
    if llm_client is not None:
        llm = LLM(run, client=llm_client, cache_dir=tmp_path / "llm-cache")
        llm._models["cheap"] = "claude-haiku-4-5"
        policy = LLMPolicy(llm)
    ex = Explorer(
        run, cfg or h.make_cfg(), policy, goals=goals, site=site, robots_txt=robots, max_actions=max_actions
    )
    with run.stage("crawl"):
        summary = ex.run_live()
    d = run.run_dir / "crawl"
    return SimpleNamespace(
        run=run,
        site=site,
        summary=summary,
        dir=d,
        explorer=ex,
        screens=[ScreenState.model_validate(r) for r in h.read_jsonl(d / "screens.jsonl")],
        transitions=[Transition.model_validate(r) for r in h.read_jsonl(d / "transitions.jsonl")],
        captures=h.read_jsonl(d / "captures.jsonl"),
        stats=json.loads((d / "stats.json").read_text()) if (d / "stats.json").exists() else {},
    )


@pytest.fixture(scope="module")
def main(tmp_path_factory):
    return _crawl(tmp_path_factory.mktemp("main"), goals=GOALS, max_actions=50)


# ----------------------------------------------------------------------------------- artifacts
def test_artifacts_follow_the_models_contract(main):
    s = CrawlSummary.model_validate_json((main.dir / "summary.json").read_text())
    assert s.target == "minitest" and s.stop_reason in ("frontier_empty", "action_budget")
    assert 0 < s.actions_spent <= s.actions_budget  # a failed recovery burns, never overshoots
    assert s.screens == len(main.screens) >= 10 and s.transitions == len(main.transitions)
    assert s.elements == sum(len(x.elements) for x in main.screens)
    assert {"/", "/graphql", "/settings", "/*"} <= set(s.url_patterns)
    assert sum(s.effort_by_url_pattern.values()) >= s.actions_spent
    for sc in main.screens:
        assert (main.run.run_dir / sc.screenshot).exists() and (main.run.run_dir / sc.dom_path).exists()
    assert (main.dir / "goal_effort.json").exists() and (main.dir / "stats.json").exists()


def test_every_screen_comes_from_an_ok_capture_and_ids_are_content_hashes(main):
    ok_ids = {c["id"] for c in main.captures if c["access"]["verdict"] == "OK"}
    assert {sc.capture_id for sc in main.screens} <= ok_ids
    assert all(sc.id.startswith("scr_") for sc in main.screens)
    assert len({sc.id for sc in main.screens}) == len(main.screens)
    assert all(e.id.startswith("el_") for sc in main.screens for e in sc.elements)


def test_dom_snapshots_are_masked(main):
    blob = "".join((main.run.run_dir / sc.dom_path).read_text() for sc in main.screens)
    assert "<script" not in blob and "crisp" not in blob.lower() and "Open chat" not in blob
    assert "updated <time>" in blob or "<ms>" in blob


# --------------------------------------------------------------------------- discovery (D04)
def test_modals_tabs_and_pages_are_discovered(main):
    kinds = Counter(sc.kind for sc in main.screens)
    assert kinds["modal"] >= 3 and kinds["tab"] >= 2 and kinds["page"] >= 4
    titles = {sc.dialog_title for sc in main.screens if sc.kind == "modal"}
    assert {"New Collection", "Import"} <= titles
    tab_names = {t for sc in main.screens if sc.kind == "tab" for t in sc.active_tabs}
    assert {"Environments", "History"} <= tab_names


def test_transitions_link_screens_that_exist(main):
    ids = {sc.id for sc in main.screens}
    assert all(t.from_screen in ids and t.to_screen in ids for t in main.transitions)
    observed = [t for t in main.transitions if t.observed and t.from_screen != t.to_screen]
    assert len(observed) >= 8


def test_shortcut_glyphs_never_reach_names(main):
    names = {e.name for sc in main.screens for e in sc.elements}
    assert "Search and commands" in names
    assert not any("⌘" in n or "Ctrl" in n for n in names)


def test_icon_only_controls_are_kept_and_distinguishable(main):
    home = next(sc for sc in main.screens if sc.url_pattern == "/" and sc.kind == "page")
    nameless = [e for e in home.elements if e.role == "button" and not e.name]
    assert len(nameless) >= 2 and len({e.id for e in nameless}) == len(nameless)


def test_editor_is_one_element_without_its_lines(main):
    home = next(sc for sc in main.screens if sc.url_pattern == "/" and sc.kind == "page")
    assert [e.name for e in home.elements if e.role == "textbox" and e.tag == "div"] == ["Request body"]
    assert not any(e.name in ("one", "two") for e in home.elements)


def test_volatile_text_does_not_split_screens(main):
    # the response line changes after Send; the screen set must not contain two copies of the same page
    page_prints = [(sc.url_pattern, tuple(sc.active_tabs)) for sc in main.screens if sc.kind == "page"]
    assert len(page_prints) == len(set(page_prints))


# --------------------------------------------------------------------- safety (D28; cases 1, 2)
def test_forbidden_actions_never_reach_the_app(main):
    hits = set(main.site.hits)
    assert not hits & {"/login", "/chat", "/crisp", "/deleted", "/clear"}
    assert "/created?name=blastradius-probe" in hits  # the only text the explorer types
    assert not any("/created" in x and "blastradius-probe" not in x for x in main.site.hits)


def test_blocked_transitions_carry_their_class(main):
    by_label = {t.label: t.reason for t in main.transitions if t.blocked}
    assert by_label["Login"].startswith("auth")
    assert by_label["Chat with us"].startswith("external_widget")
    assert by_label["Admin users"].startswith("denied_url")
    assert by_label["Docs"].startswith("external_link")
    assert by_label["Delete all history"].startswith("destructive")
    assert by_label["Clear history"].startswith("destructive")
    assert all(t.observed is False for t in main.transitions if t.blocked)
    codes = Counter(f["code"] for f in main.run.failures.items)
    assert codes["ACTION_BLOCKED_BY_POLICY"] >= 5
    assert codes["BUDGET_EXHAUSTED"] == (1 if main.summary.stop_reason == "action_budget" else 0)


def test_denied_and_external_urls_are_never_requested(main):
    assert not any("/admin" in u for u in main.site.requested)
    assert not any("docs.app.test" in u for u in main.site.requested)


def test_egress_escape_attempts_are_aborted_and_counted(main):
    blocked = main.stats["egress_blocked"]
    assert {"127.0.0.1", "169.254.169.254", "tracker.example"} <= set(blocked)
    assert not any(
        u.startswith(("http://127.0.0.1", "http://169.254", "https://tracker.example"))
        for u in main.site.requested
    )


def test_send_runs_only_against_the_allowlisted_url(main):
    assert main.site.hits.count("/send") == 1 and "/echo" in main.site.hits  # on "/" (URL is echo.app.test)
    assert "/gqlsend" not in main.site.hits  # /graphql has no URL field
    send_blocked = [t for t in main.transitions if t.blocked and t.label == "Send"]
    assert send_blocked and send_blocked[0].reason.startswith("network_side_effect")


def test_widget_text_never_reaches_perception(main):
    texts = {t for sc in main.screens for e in sc.elements for t in (e.name, e.text)}
    assert "Open chat" not in texts and not any(
        "crisp" in (e.dom_id or "").lower() for sc in main.screens for e in sc.elements
    )


def test_persisted_settings_are_not_toggled(main):
    assert "/dark" not in main.site.hits  # a checkbox: stateful controls are skipped, not clicked
    assert main.stats["counts"]["stateful_skipped"] >= 1


def test_the_base_state_survives_persisted_tab_changes(main):
    """The fixture persists the sidebar and request tabs like Hoppscotch's localStorage: tab clicks leak into
    later restores, so the explorer must click them back instead of giving a screen up."""
    codes = Counter(f["code"] for f in main.run.failures.items)
    assert codes["STATE_NOT_RECOVERABLE"] == 0 and main.summary.suspect == 0
    tabs = {tuple(sc.active_tabs) for sc in main.screens if sc.url_pattern == "/" and sc.kind != "modal"}
    assert ("Collections", "Parameters") in tabs and ("Environments", "Parameters") in tabs
    assert ("Collections", "Body") in tabs or ("Body", "Collections") in tabs


def test_local_write_types_only_the_probe_string(main):
    typed = [t for t in main.transitions if t.action == "type"]
    assert typed and all(t.label == "blastradius-probe" for t in typed)


# ----------------------------------------------------------------------- soft-404 (case 3)
def test_soft_404_is_judged_by_rendered_identity(main):
    soft = main.stats["soft_404"]
    assert soft["mode"] == "render" and any(u.endswith("/old-page") for u in soft["urls"])
    catch_all = [sc for sc in main.screens if sc.url_pattern == "/*" and sc.kind == "page"]
    assert len(catch_all) == 1  # one screen however many routes 404
    t = next(t for t in main.transitions if t.to_screen == catch_all[0].id and t.label == "Old page")
    assert t.reason.startswith("soft_404")
    assert any(
        t.observed for t in main.transitions if t.to_screen == catch_all[0].id and t.label == "Old page"
    )
    assert not any(sc.url_pattern == "/old-page" for sc in main.screens)
    # every HTTP status was 200: only the rendered screen says the route does not exist
    assert {c["status"] for c in main.captures if c["status"] is not None} == {200}


def test_the_summary_carries_the_counters_that_used_to_live_only_in_stats(main):
    s, stats = main.summary, main.stats
    assert s.egress_blocked >= 3 and s.egress_blocked == sum(stats["egress_blocked"].values()) + sum(
        stats["egress_blocked_network"].values()
    )
    assert s.policy_blocked >= 5 and s.policy_blocked == sum(stats["policy_blocked"].values())
    assert s.soft_404 >= 1 and s.soft_404 == len(stats["soft_404"]["urls"])
    assert s.restores == stats["counts"].get("restores", 0)
    assert s.app_version is None  # this target has no version_regex


# ----------------------------------------------------------------- pinned context and drift (4, 6)
def test_context_is_pinned_in_the_manifest_and_bundle_hash_is_recorded_twice(main):
    env = json.loads((main.run.run_dir / "manifest.json").read_text())["crawl_env"]
    assert env["viewport"] == [1280, 800] and env["locale"] == "en-US" and env["timezone"] == "UTC"
    assert env["service_workers"] == "block" and env["color_scheme"] == "light"
    assert "blastradius/" in env["user_agent"] and env["policy"] == "heuristic"
    assert main.summary.bundle_hash and main.summary.bundle_hash == main.summary.bundle_hash_end


# ------------------------------------------------------------------------ observability (65)
def test_every_action_is_a_traced_browser_action_span_with_its_class(main):
    spans = [
        r
        for r in load_trace(main.run.run_dir / "trace.jsonl")
        if r.get("kind") == "browser_action" and r.get("event") == "end"
    ]
    assert len(spans) >= 20
    a = spans[0]["attributes"]
    assert {"action_class", "role", "target_name", "fp_before", "fp_after", "verdict"} <= set(a)
    assert {s["attributes"]["action_class"] for s in spans} >= {"CLICK_SAFE", "NAVIGATE", "LOCAL_WRITE"}
    kinds = {
        e["kind"]
        for e in (json.loads(x) for x in (main.run.run_dir / "evidence.jsonl").read_text().splitlines())
    }
    assert {"crawl.capture", "crawl.transition"} <= kinds


def test_goal_effort_is_logged_per_requirement(main):
    effort = json.loads((main.dir / "goal_effort.json").read_text())
    assert [g["goal"] for g in effort] == GOALS
    imp = next(g for g in effort if "Postman" in g["goal"])
    assert imp["screens_visited"] >= 1 and "Import" in imp["candidate_names"] and imp["actions_spent"] >= 1


# ------------------------------------------------------------------------------ determinism (14)
def test_two_runs_produce_the_same_screen_and_element_ids(tmp_path_factory, main):
    again = _crawl(tmp_path_factory.mktemp("again"), goals=GOALS, max_actions=50, run_id="crawl-again")
    assert {s.id for s in again.screens} == {s.id for s in main.screens}
    assert {e.id for s in again.screens for e in s.elements} == {
        e.id for s in main.screens for e in s.elements
    }


# --------------------------------------------------------------------------------- budgets (13)
def test_action_budget_is_respected(tmp_path: Path):
    r = _crawl(tmp_path, max_actions=8)
    assert (
        r.summary.stop_reason == "action_budget" and r.summary.actions_spent == 8 == r.summary.actions_budget
    )
    assert sum(1 for f in r.run.failures.items if f["code"] == "BUDGET_EXHAUSTED") == 1


def test_depth_zero_budget_stops_at_the_seeds(tmp_path: Path):
    r = _crawl(tmp_path, cfg=h.make_cfg(max_depth=0))
    assert {s.depth for s in r.screens} == {0} and r.summary.stop_reason == "frontier_empty"


def test_time_budget(tmp_path: Path):
    ticks = iter(range(0, 10_000, 40))  # a fake clock that jumps 40 s per reading
    run = h.make_run(tmp_path)
    ex = Explorer(
        run,
        h.make_cfg(wall_clock_s=100),
        HeuristicPolicy(),
        site=h.Site(),
        robots_txt=ROBOTS_OK,
        max_actions=40,
        clock=lambda: float(next(ticks)),
    )
    assert ex.run_live().stop_reason == "time_budget"


def test_politeness_delay_is_applied_between_actions(tmp_path: Path):
    slept: list[float] = []
    ticks = iter(range(100_000))
    run = h.make_run(tmp_path)
    ex = Explorer(
        run,
        h.make_cfg(action_delay_ms=700),
        HeuristicPolicy(),
        site=h.Site(),
        robots_txt=ROBOTS_OK,
        max_actions=6,
        clock=lambda: next(ticks) / 1000,
        sleep=slept.append,
    )
    ex.run_live()
    assert slept and max(slept) <= 0.7 + 1e-9 and any(abs(s - 0.7) < 0.05 for s in slept)


def test_repeated_http_errors_stop_the_run(tmp_path: Path):
    r = _crawl(tmp_path, site=h.Site(status=404), max_actions=20)
    assert r.summary.stop_reason == "circuit_breaker" and r.summary.actions_spent <= 4


def test_rate_limiting_stops_the_run_at_once_and_is_never_graphed(tmp_path: Path):
    r = _crawl(tmp_path, site=h.Site(status=429), max_actions=20)
    assert r.summary.stop_reason == "circuit_breaker" and r.stats["rate_limited"] is True
    assert r.screens == [] and r.summary.suspect >= 1 and r.summary.actions_spent <= 2


# -------------------------------------------------------------------------- recovery (12)
def test_an_unrecoverable_base_state_is_recorded_and_burns_budget(tmp_path: Path):
    r = _crawl(tmp_path, site=h.Site(break_on="/break"), max_actions=30)
    codes = Counter(f["code"] for f in r.run.failures.items)
    assert codes["STATE_NOT_RECOVERABLE"] >= 1 and r.stats["counts"]["unrecoverable"] >= 1
    assert r.summary.stop_reason != "error" and r.summary.actions_spent <= r.summary.actions_budget


def test_dismiss_selector_closes_a_dialog_that_ignores_escape(tmp_path: Path):
    from playwright.sync_api import sync_playwright

    run = h.make_run(tmp_path)
    cfg = h.make_cfg(dismiss_selectors=["button[aria-label='Close']"])
    ex = Explorer(run, cfg, HeuristicPolicy(), site=h.Site(), robots_txt=ROBOTS_OK, max_actions=10)
    ex._robots = ROBOTS_OK
    with sync_playwright() as pw:
        ex._open(pw)
        try:
            ex._visit_seed("https://app.test/graphql")
            rec = next(iter(ex.screens.values()))
            assert ex._click("#stuck")
            ex._quiet()
            assert ex._now().popups  # Escape will not close this one
            assert ex._return_to(rec) and not ex._now().popups
            assert ex.counts["restores"] == 0  # the cheap ladder was enough
        finally:
            ex.context.close()
            ex.browser.close()


# --------------------------------------------------------------------------- LLM policy (D07)
def test_llm_policy_reranks_and_a_failing_llm_changes_nothing(tmp_path_factory):
    base = _crawl(tmp_path_factory.mktemp("heur"), max_actions=16)
    good = _crawl(
        tmp_path_factory.mktemp("llm"), max_actions=16, llm_client=h.FakeAnthropic(Ranking(ranked=[0]))
    )
    bad = _crawl(
        tmp_path_factory.mktemp("bad"), max_actions=16, llm_client=h.FakeAnthropic(RuntimeError("down"))
    )
    assert good.stats["policy"] == "llm" and 1 <= good.stats["llm"]["calls"] <= 40
    assert good.stats["llm"]["failures"] == 0 and good.summary.stop_reason != "error"
    # no key / API down: same candidates in the same order as the deterministic policy
    assert bad.stats["llm"]["failures"] >= 1
    assert {s.id for s in bad.screens} == {s.id for s in base.screens}
    assert bad.summary.actions_spent == base.summary.actions_spent


# ---------------------------------------------------------------------- robots (13, 69)
def test_robots_disallowed_paths_are_skipped_and_counted(tmp_path: Path):
    r = _crawl(tmp_path, robots="User-agent: *\nDisallow: /graphql\n", max_actions=14)
    assert not any(s.url_pattern == "/graphql" for s in r.screens)
    assert r.summary.disallowed >= 1
    verdicts = Counter(c["access"]["verdict"] for c in r.captures)
    assert verdicts["DISALLOWED"] >= 1 and verdicts["OK"] >= 1
    assert not any("/graphql" in u for u in r.site.requested if "__hit" not in u and "assets" not in u)
    assert any(f["code"] == "ACCESS_DISALLOWED" for f in r.run.failures.items)


def test_unreadable_robots_denies_everything(tmp_path: Path):
    r = _crawl(tmp_path, robots=None, max_actions=10)
    assert r.screens == [] and r.summary.disallowed >= 3 and r.summary.stop_reason == "frontier_empty"
    assert not any(u.endswith("/graphql") for u in r.site.requested)


# --------------------------------------------------------------------- access (17, 67, 68)
def test_a_challenge_page_never_becomes_a_screen_and_trips_the_breaker(tmp_path: Path):
    wall = h.Site(default_headers={"cf-mitigated": "challenge", "server": "cloudflare"})
    r = _crawl(tmp_path, site=wall, max_actions=20)
    assert r.screens == [] and r.summary.blocked >= 2
    assert r.summary.stop_reason == "circuit_breaker"
    assert {c["access"]["verdict"] for c in r.captures} == {"BLOCKED"}
    assert sum(1 for f in r.run.failures.items if f["code"] == "ACCESS_BLOCKED") >= 2
    assert not r.transitions


def test_suspect_pages_are_captured_but_not_graphed(tmp_path: Path):
    thin = "<html><head><title>Mini Scotch</title></head><body><button>only one</button></body></html>"
    site = h.Site(pages={"/hidden": (200, h.HTML, thin)})
    cfg = h.make_cfg(seed_urls=["https://app.test/", "https://app.test/hidden"])
    r = _crawl(tmp_path, site=site, cfg=cfg, max_actions=6)
    assert r.summary.suspect == 1
    assert not any("hidden" in s.url for s in r.screens)
    cap = next(c for c in r.captures if c["url"].endswith("/hidden"))
    assert cap["access"]["verdict"] == "SUSPECT" and any("interactive" in x for x in cap["access"]["signals"])
    assert any(f["code"] == "ACCESS_SUSPECT" for f in r.run.failures.items)


# --------------------------------------------------------------------------- recorded (18)
def _recorded_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "session"
    folder.mkdir()
    base = {"headers": {"content-type": "text/html", "set-cookie": "sid=SECRET"}, "status": 200}
    for i, (name, url) in enumerate(
        [
            ("home", "https://hoppscotch.io/"),
            ("new_collection_modal", "https://hoppscotch.io/"),
            ("login_dialog", "https://hoppscotch.io/"),
            ("import_modal", "https://hoppscotch.io/"),
        ],
        1,
    ):
        (folder / f"{i:03d}.html").write_text((h.FIXTURES / "hoppscotch" / f"{name}.html").read_text())
        (folder / f"{i:03d}.json").write_text(
            json.dumps({**base, "url": url, "title": "Hoppscotch • Open source API"})
        )
    (folder / "005.html").write_text(
        "<html><head><title>Just a moment...</title></head><body>"
        "<script src=/cdn-cgi/challenge-platform/h/b/orchestrate.js></script></body></html>"
    )
    (folder / "005.json").write_text(
        json.dumps(
            {"url": "https://hoppscotch.io/graphql", "status": 200, "headers": {"cf-mitigated": "challenge"}}
        )
    )
    return folder


def test_recorded_session_replays_through_the_same_perception_and_classifier(tmp_path: Path):
    run = h.make_run(tmp_path, "recorded")
    cfg = h.hoppscotch_cfg()
    summary = run_crawl(run, cfg, None, source="recorded", recorded_dir=_recorded_folder(tmp_path))
    screens = [ScreenState.model_validate(r) for r in h.read_jsonl(run.run_dir / "crawl" / "screens.jsonl")]
    assert summary.screens == len(screens) == 4 and summary.blocked == 1 and summary.actions_spent == 0
    assert {s.kind for s in screens} == {"page", "modal"}
    assert {s.dialog_title for s in screens} >= {"New Collection", "Login to Hoppscotch"}
    caps = h.read_jsonl(run.run_dir / "crawl" / "captures.jsonl")
    assert Counter(c["access"]["verdict"] for c in caps) == {"OK": 4, "BLOCKED": 1}
    assert all(c["source_kind"] == "recorded" for c in caps)
    assert "SECRET" not in (run.run_dir / "crawl" / "captures.jsonl").read_text()
    assert AccessVerdict.OK in {AccessVerdict(c["access"]["verdict"]) for c in caps}
