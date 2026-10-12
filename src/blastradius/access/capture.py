"""Capture construction and persistence (D12, D32; cases 17, 58).

One `Capture` per fetch or UI state, carrying its `AccessResult`. Only OK captures may become Screens; every
capture is appended to crawl/captures.jsonl and cited as evidence kind `crawl.capture`; every non-OK one also
lands in failures.jsonl under the matching ACCESS_* code with the signals in its context.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from ..failures import FailureCode
from ..ids import stable_id
from ..models import AccessResult, AccessVerdict, Capture
from ..redact import redact_obj, redact_text
from ..trace import now_iso

if TYPE_CHECKING:
    from ..run import RunContext

# Minimal on purpose: what the classifier reads plus what names the fronting vendor. Never cookies (case 58).
HEADER_ALLOWLIST: frozenset[str] = frozenset({
    "content-type", "content-length", "server", "via", "retry-after",
    "cf-ray", "cf-cache-status", "cf-mitigated", "x-vercel-id", "x-amz-cf-id", "x-amzn-waf-action",
})
# Credentials that HARs and traces carry; stripped from anything imported before it is classified or stored.
SECRET_HEADERS: frozenset[str] = frozenset({"cookie", "set-cookie", "authorization", "proxy-authorization"})

_FAILURE_FOR: dict[AccessVerdict, FailureCode] = {
    AccessVerdict.BLOCKED: FailureCode.ACCESS_BLOCKED,
    AccessVerdict.SUSPECT: FailureCode.ACCESS_SUSPECT,
    AccessVerdict.DISALLOWED: FailureCode.ACCESS_DISALLOWED,
}
_MAX_HEADER_VALUE = 256


def strip_secret_headers(headers: Mapping[str, object]) -> dict[str, str]:
    """Copy of `headers` without credentials; values are normalised to str (lists joined with ', ')."""
    out: dict[str, str] = {}
    for name, value in headers.items():
        if str(name).lower() not in SECRET_HEADERS:
            out[str(name)] = ", ".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)
    return out


def make_capture(*, target: str, url: str, access: AccessResult, final_url: str | None = None,
                 status: int | None = None, headers: Mapping[str, str] | None = None, title: str | None = None,
                 html_path: str | None = None, screenshot_path: str | None = None,
                 interactive_count: int | None = None, source_kind: Literal["live", "recorded"] = "live",
                 parent_id: str | None = None, action: str | None = None,
                 captured_at: str | None = None) -> Capture:
    """Build a Capture. The id is a content hash of (target, final_url, parent_id, action): no clock, no run id."""
    final = final_url or url
    kept = {name.lower(): str(value)[:_MAX_HEADER_VALUE] for name, value in (headers or {}).items()
            if name.lower() in HEADER_ALLOWLIST}
    return Capture(
        id=stable_id("cap", target, final, parent_id, action), target=target, url=url, final_url=final,
        status=status, headers=kept, title=title, html_path=html_path, screenshot_path=screenshot_path,
        captured_at=captured_at or now_iso(), source_kind=source_kind, parent_id=parent_id, action=action,
        interactive_count=interactive_count, access=access,
    )


def append_capture(run: RunContext, capture: Capture) -> None:
    """Persist a capture: jsonl row, evidence row, and a failure row when the verdict is not OK.

    A run without a directory (RunContext.ephemeral) keeps evidence and failures in memory and writes no file.
    Secret-shaped substrings in urls and titles are redacted on the way to disk; ids are computed before that.
    """
    if run.run_dir.exists():
        row = redact_obj(capture.model_dump(mode="json"), truncate=False)
        fd = os.open(run.path("crawl", "captures.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    access = capture.access
    summary = redact_text(f"{access.verdict} {capture.status} {capture.final_url}", truncate=False)
    run.evidence.add("crawl.capture", capture.id, summary, span_id=run.tracer.current_span_id,
                     verdict=str(access.verdict), url=redact_text(capture.final_url, truncate=False),
                     signals=access.signals)
    code = _FAILURE_FOR.get(access.verdict)
    if code is not None:
        run.failures.record(code, f"{access.verdict}: {access.reason}", capture_id=capture.id,
                            url=redact_text(capture.final_url, truncate=False), status=capture.status,
                            vendor=access.vendor, signals=access.signals, rung=access.rung)
