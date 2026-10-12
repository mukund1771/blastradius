"""Crawl scenarios that run in a child process (`crawl_helpers.run_isolated`): the ones that would hang or
need a different PYTHONHASHSEED. Each takes a JSON-able dict and returns a JSON-able dict. Everything is
local: an in-memory site, and real sockets owned by the parent test (their ports arrive in `args`)."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import crawl_helpers as h


def _summary(r: Any) -> dict[str, Any]:
    return {
        "stop_reason": r.summary.stop_reason,
        "actions_spent": r.summary.actions_spent,
        "screens": r.summary.screens,
        "egress_blocked": r.summary.egress_blocked,
        "hits": r.site.hits,
        "stats": r.stats,
        "error": r.explorer.error,
    }


def websockets(args: dict[str, Any]) -> dict[str, Any]:
    """A page that opens one WebSocket to a non-allowlisted loopback port (a raw listener the parent owns) and
    one to the allowlisted app host (a real handshake server the parent owns, named by host mapping)."""
    from blastradius.crawl import explorer

    explorer.sync_playwright = h.mapped_playwright({"app.test": args["ws_port"]})
    blocked, allowed = args["blocked_port"], "ws://app.test/allowed"
    js = (
        f"try {{ const b = new WebSocket('ws://127.0.0.1:{blocked}/blocked'); "
        "b.onopen = () => hit('/blocked-open'); } catch (e) {}\n"
        f"try {{ const a = new WebSocket('{allowed}'); "
        "a.onopen = () => hit('/allowed-open'); } catch (e) {}"
    )
    site = h.Site(shell=h.with_script(h.SHELL, js))
    cfg = h.make_cfg(base_url="http://app.test", seed_urls=["http://app.test/"], max_depth=1)
    r = h.crawl_once(Path(args["tmp"]), site=site, cfg=cfg, max_actions=6)
    return _summary(r)


def spinner(args: dict[str, Any]) -> dict[str, Any]:
    """A page that starts spinning its main thread shortly after it loaded: every later `page.evaluate`
    blocks, and wall_clock_s is only checked between actions, so only the watchdog can end the run."""
    from blastradius.crawl import explorer

    explorer.WATCHDOG_GRACE_S = 3.0
    shell = h.with_script(h.SHELL, "setTimeout(() => { hit('/spin'); for (;;) {} }, 600);")
    cfg = h.make_cfg(seed_urls=["https://app.test/"], wall_clock_s=4)
    t0 = time.monotonic()
    r = h.crawl_once(Path(args["tmp"]), site=h.Site(shell=shell), cfg=cfg, max_actions=30)
    out = _summary(r)
    out["elapsed"] = round(time.monotonic() - t0, 1)
    return out


def digest(args: dict[str, Any]) -> dict[str, Any]:
    """A small crawl, then a digest of every artifact that must not depend on the process (no timestamps)."""
    r = h.crawl_once(Path(args["tmp"]), cfg=h.make_cfg(max_depth=2), max_actions=int(args["actions"]))
    parts: list[bytes] = []
    for name in ("screens.jsonl", "transitions.jsonl"):
        parts.append((r.dir / name).read_bytes())
    for cap in r.captures:
        cap.pop("captured_at")
        parts.append(json.dumps(cap, sort_keys=True).encode())
    for f in sorted((r.dir / "dom").glob("*.html")):
        parts.append(f.name.encode() + f.read_bytes())
    stats = dict(r.stats)
    parts.append(json.dumps(stats, sort_keys=True).encode())
    return {
        "digest": hashlib.sha256(b"\x1f".join(parts)).hexdigest(),
        "screens": r.summary.screens,
        "transitions": r.summary.transitions,
        "spent": r.summary.actions_spent,
    }
