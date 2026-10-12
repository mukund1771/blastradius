"""Shared helpers for the crawl tests: an in-memory site served through Playwright routes, a target config
pointing at it, and a run context. Nothing here touches the internet. The egress tests use real sockets on
127.0.0.1 (`Listener`, `LocalServer`, `WsServer`), name them through Chromium's host resolver
(`mapped_playwright`), and run the scenarios that could hang in a child process (`run_isolated`)."""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import signal
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

SRC = Path(__file__).resolve().parents[3] / "src"
if str(SRC) not in sys.path:  # the editable-install .pth can be hidden by macOS file flags
    sys.path.insert(0, str(SRC))

from blastradius.config import Settings
from blastradius.run import RunContext
from blastradius.targets import TargetConfig

FIXTURES = Path(__file__).resolve().parent
SHELL = (FIXTURES / "app" / "shell.html").read_text(encoding="utf-8")
HTML = {"content-type": "text/html; charset=utf-8"}


class Site:
    """`https://app.test` serves the shell for every path (soft-404); `/__hit*` records what the page did."""

    def __init__(
        self,
        shell: str = SHELL,
        *,
        pages: dict[str, tuple[int, dict[str, str], str]] | None = None,
        default_headers: dict[str, str] | None = None,
        status: int = 200,
        break_on: str | None = None,
    ):
        self.shell = shell
        self.pages = pages or {}
        self.default_headers = default_headers or {}
        self.status = status
        self.break_on = break_on  # after the page reports this hit, the app shell stops being served
        self.broken = False
        self.hits: list[str] = []
        self.requested: list[str] = []

    def __call__(self, url: str) -> tuple[int, dict[str, str], str] | None:
        parts = urlsplit(url)
        host = parts.hostname or ""
        self.requested.append(url)
        if host == "echo.app.test":
            self.hits.append("/echo")
            return 200, {"content-type": "application/json", "access-control-allow-origin": "*"}, "{}"
        if host != "app.test":
            return None
        if parts.path.startswith("/__hit"):
            hit = parts.path[len("/__hit") :]
            self.hits.append(hit + (f"?{parts.query}" if parts.query else ""))
            self.broken = self.broken or hit == self.break_on
            return 200, {"content-type": "text/plain"}, "ok"
        if parts.path in self.pages:
            return self.pages[parts.path]
        if parts.path.startswith("/assets/"):
            return 200, {"content-type": "text/javascript"}, ""
        if self.broken:
            return (
                200,
                HTML,
                "<html><head><title>Mini Scotch</title></head><body>down for maintenance</body></html>",
            )
        return self.status, {**HTML, **self.default_headers}, self.shell


def make_cfg(*, base_url: str = "https://app.test", **crawl: Any) -> TargetConfig:
    base: dict[str, Any] = {
        "seed_urls": ["https://app.test/", "https://app.test/graphql", "https://app.test/settings"],
        "egress_allow": ["app.test"],
        "max_actions": 60,
        "max_depth": 3,
        "wall_clock_s": 180,
        "action_delay_ms": 0,
        "send_allowed_urls": ["https://echo.app.test"],
        "widget_selectors": ["[id^='crisp-']"],
        "deny_url_patterns": [r"^https://app\.test/admin"],
        "anchor": {"title_regex": "(?i)mini scotch", "min_interactive": 4},
    }
    base.update(crawl)
    return TargetConfig.model_validate(
        {
            "name": "minitest",
            "display_name": "Mini Scotch",
            "base_url": base_url,
            "crawl": base,
            "spec": {"kind": "llms_full", "url": "https://docs.test/llms-full.txt"},
            "code": {"repo": "o/r", "adapter": "vue", "roots": ["src"]},
        }
    )


def make_run(tmp_path: Path, run_id: str = "crawl-test") -> RunContext:
    return RunContext.create(
        "minitest", run_id=run_id, runs_dir=tmp_path / "runs", settings=Settings(max_usd=1.0)
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return (
        [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    )


# ---------------------------------------------------------------------------------------- browser
def load_snapshot(page: Any, name: str, url: str = "https://hoppscotch.io/") -> str:
    """Put a saved hoppscotch.io DOM snapshot into the page the way the recorded-session replay does."""
    import re

    html = (FIXTURES / "hoppscotch" / f"{name}.html").read_text(encoding="utf-8")
    html = re.sub(r"(?is)<script\b.*?</script>", "", html)
    html = html.replace("<head>", f'<head><base href="{url}">', 1)
    page.set_content(html)
    return url


def hoppscotch_cfg() -> TargetConfig:
    return TargetConfig.load("hoppscotch")


def perceive_page(page: Any, *, url: str = "https://hoppscotch.io/", widgets: list[str] | None = None) -> Any:
    from blastradius.crawl import perceive
    from blastradius.crawl.fingerprint import url_pattern

    raw = perceive.collect(page, widgets or [])
    raw["url"] = url
    return perceive.build(raw, target="hoppscotch", pattern=url_pattern(url))


# ------------------------------------------------------------------------------------------- fake LLM
class FakeAnthropic:
    """The slice of the Anthropic client that `LLM.structured` uses. Outputs are returned in order (the last
    repeats); an Exception instance is raised instead."""

    class _Messages:
        def __init__(self, outputs: tuple[Any, ...]):
            self.outputs, self.calls, self.last = list(outputs), 0, None

        def parse(self, **kw: Any) -> Any:
            self.calls += 1
            self.last = kw
            out = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
            if isinstance(out, Exception):
                raise out

            class Usage:
                input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = (
                    50,
                    10,
                    0,
                    0,
                )

            class Resp:
                parsed_output, stop_reason, stop_details, _request_id, usage = (
                    out,
                    "end_turn",
                    None,
                    "req",
                    Usage(),
                )

            return Resp()

    def __init__(self, *outputs: Any):
        self.messages = self._Messages(outputs)


# --------------------------------------------------------------------------------- crawling helpers
ROBOTS_OK = "User-agent: *\nAllow: /\n"


def with_script(shell: str, js: str) -> str:
    """The shell with `js` appended to its (last) inline script."""
    head, sep, tail = shell.rpartition("</script>")
    assert sep, "shell has no inline script"
    return f"{head}\n{js}\n{sep}{tail}"


def crawl_once(
    tmp_path: Path,
    *,
    site: Site | None = None,
    cfg: TargetConfig | None = None,
    robots: str | None = ROBOTS_OK,
    max_actions: int = 40,
    goals: list[str] | None = None,
    run_id: str = "crawl-test",
    run: RunContext | None = None,
    subdir: str | None = None,
) -> SimpleNamespace:
    """Explorer.run_live against the in-memory site, heuristic policy; the artifacts parsed back."""
    from blastradius.crawl.explorer import Explorer
    from blastradius.crawl.policy import HeuristicPolicy
    from blastradius.models import ScreenState, Transition

    run = run or make_run(tmp_path, run_id)
    site = site or Site()
    extra = {"artifact_subdir": subdir} if subdir else {}
    ex = Explorer(
        run,
        cfg or make_cfg(),
        HeuristicPolicy(),
        goals=goals,
        site=site,
        robots_txt=robots,
        max_actions=max_actions,
        **extra,
    )
    with run.stage("crawl"):
        summary = ex.run_live()
    d = run.run_dir / "crawl" / (subdir or "")
    return SimpleNamespace(
        run=run,
        site=site,
        summary=summary,
        dir=d,
        explorer=ex,
        screens=[ScreenState.model_validate(r) for r in read_jsonl(d / "screens.jsonl")],
        transitions=[Transition.model_validate(r) for r in read_jsonl(d / "transitions.jsonl")],
        captures=read_jsonl(d / "captures.jsonl"),
        stats=json.loads((d / "stats.json").read_text()) if (d / "stats.json").exists() else {},
        failures=run.failures.items,
    )


@contextmanager
def open_explorer(
    tmp_path: Path,
    *,
    site: Site | None = None,
    cfg: TargetConfig | None = None,
    robots: str | None = ROBOTS_OK,
    max_actions: int = 20,
    run_id: str = "open",
) -> Iterator[Any]:
    """An Explorer with a live browser context and nothing visited yet, for tests that drive its methods."""
    from playwright.sync_api import sync_playwright

    from blastradius.crawl.explorer import Explorer
    from blastradius.crawl.policy import HeuristicPolicy

    ex = Explorer(
        make_run(tmp_path, run_id),
        cfg or make_cfg(),
        HeuristicPolicy(),
        site=site or Site(),
        robots_txt=robots,
        max_actions=max_actions,
    )
    ex._robots = robots
    ex.t0 = ex.clock()
    with sync_playwright() as pw:
        ex._open(pw)
        try:
            yield ex
        finally:
            ex.context.close()
            ex.browser.close()
            if getattr(ex, "blackhole", None) is not None:
                ex.blackhole.close()


def recorded_session(tmp_path: Path, pages: dict[str, str], *, url: str = "https://hoppscotch.io/") -> Path:
    """A RecordedSession folder: pages is {label: html}; files are numbered in the dict's order."""
    folder = tmp_path / "session"
    folder.mkdir()
    for i, html in enumerate(pages.values(), 1):
        (folder / f"{i:03d}.html").write_text(html)
        meta = {"url": url, "status": 200, "title": "Hoppscotch • Open source API"}
        (folder / f"{i:03d}.json").write_text(json.dumps(meta))
    return folder


# ---------------------------------------------------------------------------------- real sockets
class Listener:
    """A raw listening socket on 127.0.0.1: records the first bytes of every connection and answers 200."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port: int = self.sock.getsockname()[1]
        self.hits: list[bytes] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                conn.settimeout(1.5)
                self.hits.append(conn.recv(512))
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\naccess-control-allow-origin: *\r\n\r\nok"
                )
            except OSError:
                self.hits.append(b"")
            finally:
                conn.close()

    def close(self) -> None:
        self.sock.close()


class LocalServer:
    """A real HTTP server: `routes` maps a path to (status, headers, body); every GET path is recorded."""

    def __init__(self, routes: dict[str, tuple[int, dict[str, str], bytes]]) -> None:
        self.routes, self.paths = routes, []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                outer.paths.append(self.path)
                status, headers, body = outer.routes.get(
                    self.path.split("?")[0], (404, {"content-type": "text/plain"}, b"missing")
                )
                self.send_response(status)
                for k, v in {"content-length": str(len(body)), **headers}.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port: int = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.httpd.shutdown()


class WsServer:
    """The smallest WebSocket server that completes a handshake: counts upgrades, keeps the socket open."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port: int = self.sock.getsockname()[1]
        self.upgrades = 0
        self._open: list[socket.socket] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                conn.settimeout(2.0)
                request = conn.recv(2048).decode("latin-1")
                key = next(
                    (line.split(":", 1)[1].strip() for line in request.split("\r\n")
                     if line.lower().startswith("sec-websocket-key")),
                    None,
                )
                if key is None:
                    conn.close()
                    continue
                accept = base64.b64encode(
                    hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
                ).decode()
                conn.sendall(
                    ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode()
                )
                self.upgrades += 1
                self._open.append(conn)
            except OSError:
                conn.close()

    def close(self) -> None:
        for conn in self._open:
            conn.close()
        self.sock.close()


def mapped_playwright(mapping: dict[str, int]) -> Any:
    """A drop-in for `sync_playwright` whose Chromium resolves each hostname to 127.0.0.1:<port>, so a test
    can give a *real* local server a public-looking name (http://app.test/) with default ports."""
    from playwright.sync_api import sync_playwright as real

    rules = ", ".join(f"MAP {host} 127.0.0.1:{port}" for host, port in sorted(mapping.items()))

    class _Chromium:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def launch(self, **kw: Any) -> Any:
            # LNA would refuse a page without an address (route-fulfilled) reaching a mapped loopback port
            kw["args"] = [
                *kw.get("args", []),
                f"--host-resolver-rules={rules}",
                "--disable-features=LocalNetworkAccessChecks",
            ]
            return self._inner.launch(**kw)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    class _Playwright:
        def __init__(self, inner: Any) -> None:
            self._inner, self.chromium = inner, _Chromium(inner.chromium)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    class _Manager:
        def __enter__(self) -> Any:
            self._cm = real()
            return _Playwright(self._cm.__enter__())

        def __exit__(self, *exc: object) -> Any:
            return self._cm.__exit__(*exc)

    return lambda: _Manager()


_RUNNER = """
import importlib, json, sys
sys.path.insert(0, {fixtures!r}); sys.path.insert(0, {src!r})
fn = getattr(importlib.import_module({module!r}), {func!r})
print("RESULT " + json.dumps(fn(json.loads({args!r})), default=str))
"""


def run_isolated(
    module: str, func: str, args: dict[str, Any], *, timeout: float = 120.0, env: dict[str, str] | None = None
) -> Any:
    """Run `module.func(args)` in a child process and return its JSON result. A hang (the failure mode of the
    deadlocks these tests guard against) becomes an AssertionError after `timeout`, and the whole process
    group (the child's Chromium included) is killed."""
    code = _RUNNER.format(
        fixtures=str(FIXTURES), src=str(SRC), module=module, func=func, args=json.dumps(args)
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env={**os.environ, "PYTHONPATH": str(SRC), **(env or {})},
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise AssertionError(f"{module}.{func} did not finish within {timeout:.0f}s (hung)") from None
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    lines = [line for line in out.splitlines() if line.startswith("RESULT ")]
    assert lines, f"{module}.{func} produced no result (rc={proc.returncode}): {err[-1500:]}"
    return json.loads(lines[-1][len("RESULT ") :])
