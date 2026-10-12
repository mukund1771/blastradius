"""Network-layer egress enforcement (D28; case 1) and checked HTTP fetches.

The route layer sees the first hop of a request. Chromium follows a 3xx internally, so a redirect from an
allowlisted origin to a forbidden one never reaches the route handler (found in review). The browser is
therefore launched behind a proxy that refuses everything, with a bypass list naming only the allowlisted
hosts on their default ports (`<-loopback>` first, so loopback is proxied too): a hop to any other host,
and anything else that slips past the route layer (beacons, preflights, prefetch), fails at the network
layer and is counted by `Blackhole`.

`fetch_checked` is the same rule for the plain HTTP GETs the explorer makes itself (robots.txt, the HTML
shell, the main bundle): redirects are not followed blindly, each hop must stay on the origin it started on
or on an allowlisted host.

`Watchdog` is the hard deadline: Playwright's sync API has no timeout on `page.evaluate`, so a page that spins
on the main thread would block the crawl past wall_clock_s. A thread outside the greenlet kills this run's
Chromium (found by a command-line token no other browser carries) when the deadline passes.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import threading
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from .safety import egress_verdict, is_private_host

MAX_HOPS = 5
_REQUEST_LINE_BYTES = 2048


class Blackhole:
    """A local proxy that accepts a connection, notes the host it was asked for, and drops it.

    Dropping without an answer makes the browser report a network error (`ERR_EMPTY_RESPONSE` for http,
    `ERR_TUNNEL_CONNECTION_FAILED` for CONNECT) instead of handing the page a response it could mistake for
    the server's. Only host names are kept: paths and query strings may carry secrets."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(32)
        self.port: int = self._sock.getsockname()[1]
        self._refused: Counter[str] = Counter()
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(target=self._serve, name="blackhole-proxy", daemon=True)
        self._thread.start()

    @property
    def server(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def refused(self) -> dict[str, int]:
        with self._lock:
            return dict(sorted(self._refused.items()))

    def _serve(self) -> None:
        while not self._closed:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._drop, args=(conn,), daemon=True).start()

    def _drop(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(2.0)
            first = conn.recv(_REQUEST_LINE_BYTES).split(b"\r\n", 1)[0].decode("latin-1")
            with self._lock:
                self._refused[_host_of_request_line(first)] += 1
        except OSError:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass


def _host_of_request_line(line: str) -> str:
    """`CONNECT host:443 HTTP/1.1` or `GET http://host/path HTTP/1.1` -> host; anything else -> `?`."""
    pieces = line.split()
    if len(pieces) < 2:
        return "?"
    target = pieces[1]
    if pieces[0].upper() == "CONNECT":
        return target.rsplit(":", 1)[0].lower().strip("[]") or "?"
    try:
        return (urlsplit(target).hostname or "?").lower()
    except ValueError:
        return "?"


def bypass_list(allow: Sequence[str]) -> str:
    """Chromium proxy-bypass rules: loopback is proxied (`<-loopback>`), each allowlisted public host and its
    subdomains goes direct on ports 80 and 443 only. Private names are never bypassed, whatever is listed."""
    rules = ["<-loopback>"]
    for raw in sorted({a.strip().lower().rstrip(".") for a in allow if a and a.strip()}):
        if any(c in raw for c in "/:*, ") or is_private_host(raw):
            continue
        for port in (80, 443):
            rules += [f"{raw}:{port}", f"*.{raw}:{port}"]
    return ",".join(rules)


@dataclass(frozen=True)
class Fetched:
    """The outcome of `fetch_checked`: a body, or why there is none."""

    url: str
    status: int | None = None
    headers: dict[str, str] | None = None
    text: str = ""
    error: str | None = None
    refused_host: str | None = None


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    port = parts.port or {"http": 80, "https": 443}.get(parts.scheme)
    return parts.scheme, (parts.hostname or "").lower(), port


def _same_origin(a: str, b: str) -> bool:
    return _origin(a) == _origin(b)


def hop_allowed(url: str, base_url: str, allow: Sequence[str]) -> bool:
    """A redirect target may be followed when it stays on the origin of the configured base URL or passes the
    same egress rule as a browser request."""
    return _same_origin(url, base_url) or egress_verdict(url, allow).allowed


def fetch_checked(
    url: str,
    *,
    base_url: str,
    allow: Sequence[str],
    user_agent: str,
    timeout: float = 15.0,
    max_bytes: int = 32 * 1024 * 1024,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> Fetched:
    """GET `url`, following up to MAX_HOPS redirects that pass `hop_allowed`. Never raises on transport
    errors: the outcome says why there is no body."""
    current = url
    with client_factory(
        headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=False
    ) as client:
        for _ in range(MAX_HOPS + 1):
            try:
                with client.stream("GET", current) as resp:
                    if resp.is_redirect and resp.headers.get("location"):
                        nxt = urljoin(current, resp.headers["location"])
                        if not hop_allowed(nxt, base_url, allow):
                            host = (urlsplit(nxt).hostname or "?").lower()
                            return Fetched(
                                current, resp.status_code, error="redirect refused", refused_host=host
                            )
                        current = nxt
                        continue
                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) >= max_bytes:
                            break
                    text = bytes(body[:max_bytes]).decode(resp.encoding or "utf-8", "replace")
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    return Fetched(current, resp.status_code, headers, text)
            except httpx.HTTPError as exc:
                return Fetched(current, error=f"{type(exc).__name__}")
    return Fetched(current, error="too many redirects")


class Watchdog:
    """A hard deadline for the whole crawl, enforced from outside the Playwright greenlet.

    A page that spins on the main thread blocks every sync-API call (`page.evaluate` has no timeout), and
    wall_clock_s is only checked between actions. When `deadline_s` passes without `stop()`, every process
    whose command line carries `token` (the Chromium this run launched, nobody else's) is killed; the
    blocked call then raises and the crawl ends as a time budget stop."""

    def __init__(self, token: str, deadline_s: float) -> None:
        self.token = token
        self.deadline_s = deadline_s
        self.fired = False
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="crawl-watchdog", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._done.set()

    def _run(self) -> None:
        if self._done.wait(self.deadline_s):
            return
        self.fired = True
        kill_marked(self.token)


def kill_marked(token: str) -> int:
    """SIGKILL every process (other than this one) whose command line contains `token`; returns how many."""
    try:
        listing = subprocess.run(
            ["ps", "-axww", "-o", "pid=,command="], capture_output=True, text=True, timeout=10, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    killed = 0
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit() and int(pid) != os.getpid() and token in command:
            try:
                os.kill(int(pid), signal.SIGKILL)
                killed += 1
            except OSError:
                continue
    return killed
