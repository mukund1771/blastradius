"""Capture construction and persistence (D12, D32; cases 17, 58).

One `Capture` per fetch or UI state, carrying its `AccessResult`. Only OK captures may become Screens; every
capture is appended to crawl/captures.jsonl and cited as evidence kind `crawl.capture`; every non-OK one also
lands in failures.jsonl under the matching ACCESS_* code with the signals in its context.

Persistence is scrubbed and cannot lose a capture: urls are masked structurally (userinfo and secret-named
query, fragment and path parameters; cut at MAX_URL_CHARS), page titles are stored verbatim, and rows are
written ASCII-only so a lone UTF-16 surrogate (a title cut by JS `slice()` through an emoji) cannot fail the
write. The in-memory Capture is never modified.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal
from urllib.parse import unquote

from ..failures import FailureCode
from ..ids import sha, stable_id
from ..models import AccessResult, AccessVerdict, Capture
from ..redact import redact_obj, redact_text, redact_tokens
from ..trace import now_iso
from .classify import normalise_headers

if TYPE_CHECKING:
    from ..run import RunContext

# Minimal on purpose: what the classifier reads plus what names the fronting vendor. Never cookies (case 58).
HEADER_ALLOWLIST: frozenset[str] = frozenset({
    "content-type", "content-length", "server", "via", "retry-after",
    "cf-ray", "cf-cache-status", "cf-mitigated", "x-vercel-id", "x-vercel-mitigated", "x-amz-cf-id",
    "x-amzn-waf-action", "x-iinfo", "x-cdn",
})
# Credentials that HARs and traces carry; stripped from anything imported before it is classified or stored.
SECRET_HEADERS: frozenset[str] = frozenset({"cookie", "set-cookie", "authorization", "proxy-authorization"})

_FAILURE_FOR: dict[AccessVerdict, FailureCode] = {
    AccessVerdict.BLOCKED: FailureCode.ACCESS_BLOCKED,
    AccessVerdict.SUSPECT: FailureCode.ACCESS_SUSPECT,
    AccessVerdict.DISALLOWED: FailureCode.ACCESS_DISALLOWED,
}
_MAX_HEADER_VALUE = 256

# A parameter whose (percent-decoded, lower-cased) name contains one of these has its value masked. Substring
# match on purpose: over-masking a benign `author=` costs a debugging detail, under-masking leaks a secret.
_SECRET_NAME_PARTS = ("token", "secret", "key", "code", "sid", "session", "auth", "password", "pass", "sig",
                      "signature", "pwd", "credential", "cred", "jwt", "bearer", "otp", "ticket", "assertion",
                      "saml", "cookie")
_SEGMENT_END = re.compile(r"([&?#;])")                  # query, fragment and `;matrix=` parameters end here
_USERINFO = re.compile(r"^((?:[A-Za-z][A-Za-z0-9+.\-]*:)?//)[^/?#]*@")
MAX_URL_CHARS = 4096                                    # a persisted url is cut here (length and hash kept)
_SURROGATE = re.compile("[\ud800-\udfff]")
_MASK = "[REDACTED]"


def _clean(text: str | None) -> str | None:
    """Replace lone UTF-16 surrogates (not encodable as UTF-8, so not hashable or persistable) with U+FFFD."""
    return None if text is None else _SURROGATE.sub("\ufffd", text)


def _mask_segment(segment: str) -> str:
    """`name=value` whose name (text after the last '/', percent-decoded) looks secret: value masked."""
    name, eq, _ = segment.partition("=")
    if eq and any(part in unquote(name.rsplit("/", 1)[-1]).lower() for part in _SECRET_NAME_PARTS):
        return f"{name}={_MASK}"
    return segment


def _mask_url(url: str, *, shared_patterns: bool) -> str:
    """Userinfo and secret-named parameters masked, segment by segment (a segment ends at & ? # ;, so the
    shared redactor's greedy `key=value` pattern cannot run on into the next parameter)."""
    parts = _SEGMENT_END.split(_USERINFO.sub(rf"\1{_MASK}@", url))     # segments at even, delimiters at odd
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            part = _mask_segment(part)
            if shared_patterns:
                part = redact_text(part, truncate=False)
        out.append(part)
    return "".join(out)


def scrub_url(url: str) -> str:
    """Mask credentials in a url without parsing it (so malformed urls are scrubbed too): userinfo, and the
    value of every query, fragment or path-parameter whose name looks secret (token, key, code, sid, ...).
    Everything else, including the host and path, is kept; idempotent and linear in len(url)."""
    return _mask_url(url, shared_patterns=False)


def _persistable_url(url: str | None) -> str:
    """The url as stored: surrogates replaced, credentials masked (named parameters, then the shared secret
    patterns: JWTs, API keys), and cut at MAX_URL_CHARS with its length and hash, like the shared redactor's
    stub, so that one giant url cannot make every later scan slow."""
    text = _clean(url) or ""
    masked = _mask_url(text[:MAX_URL_CHARS], shared_patterns=True)     # cut first: the dropped tail is gone
    if len(text) > MAX_URL_CHARS:
        masked = f"{masked}...[len={len(text)} sha={sha(text)}]"
    return masked


def _scrub_action(action: str) -> str:
    """Actions are labels ("click:Save") or "goto <url>": scrub the tokens that look like urls, keep the
    rest."""
    return " ".join(scrub_url(tok) if any(c in tok for c in "?#@;") else tok for tok in action.split(" "))


def strip_secret_headers(headers: Mapping[str, object]) -> dict[str, str]:
    """Copy of `headers` without credentials: names lower-cased, values str (lists and duplicate names joined
    with ', ')."""
    return {name: value for name, value in normalise_headers(headers.items()).items()
            if name not in SECRET_HEADERS}


def make_capture(*, target: str, url: str, access: AccessResult, final_url: str | None = None,
                 status: int | None = None, headers: Mapping[str, object] | None = None,
                 title: str | None = None, html_path: str | None = None, screenshot_path: str | None = None,
                 interactive_count: int | None = None, source_kind: Literal["live", "recorded"] = "live",
                 parent_id: str | None = None, action: str | None = None,
                 captured_at: str | None = None) -> Capture:
    """Build a Capture. The id is a content hash of (target, final_url, parent_id, action, verdict): no clock,
    no run id, no status. The verdict is part of it so that a BLOCKED re-observation of a state first seen OK
    is a different capture and survives `if capture.id not in captured` dedup. Lone surrogates in any string
    are replaced with U+FFFD before hashing."""
    url, final = _clean(url) or "", _clean(final_url or url) or ""
    parent_id, action = _clean(parent_id), _clean(action)
    kept = {name: value[:_MAX_HEADER_VALUE]
            for name, value in normalise_headers((headers or {}).items()).items() if name in HEADER_ALLOWLIST}
    return Capture(
        id=stable_id("cap", _clean(target), final, parent_id, action, access.verdict.value),
        target=_clean(target) or "", url=url, final_url=final, status=status, headers=kept,
        title=_clean(title), html_path=_clean(html_path), screenshot_path=_clean(screenshot_path),
        captured_at=_clean(captured_at) or now_iso(), source_kind=source_kind, parent_id=parent_id,
        action=action, interactive_count=interactive_count, access=access,
    )


def _row(capture: Capture) -> dict:
    """The persisted form: urls and action scrubbed, shared secret patterns removed from classifier text, the
    title untouched (the shared redactor rewrites ordinary words such as 'Token: how to use')."""
    row = capture.model_dump(mode="json")
    row["url"], row["final_url"] = _persistable_url(row["url"]), _persistable_url(row["final_url"])
    if row["action"] is not None:
        row["action"] = redact_tokens(_scrub_action(row["action"]))
    row["headers"] = redact_obj(row["headers"], truncate=False)
    row["access"] = redact_obj(row["access"], truncate=False)
    return row


def append_capture(run: RunContext, capture: Capture) -> str:
    """Persist a capture: jsonl row, evidence row, and a failure row when the verdict is not OK. Returns the
    evidence id of the capture's row (what a Gap cites; also in the failure's context).

    A run without a directory (RunContext.ephemeral) keeps evidence and failures in memory and writes no file.
    Ids are computed before scrubbing; the in-memory capture is not modified.
    """
    if run.run_dir.exists():
        # ASCII only: a lone surrogate cannot fail the write
        line = json.dumps(_row(capture), ensure_ascii=True) + "\n"
        fd = os.open(run.path("crawl", "captures.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("ascii"))
        finally:
            os.close(fd)
    access = capture.access
    final_url = _persistable_url(capture.final_url)
    signals = [_clean(s) or "" for s in access.signals]
    evidence_id = run.evidence.add(
        "crawl.capture", _clean(capture.id) or "",
        f"{access.verdict} {capture.status} {final_url}",
        span_id=run.tracer.current_span_id, verdict=str(access.verdict), url=final_url, signals=signals)
    code = _FAILURE_FOR.get(access.verdict)
    if code is not None:
        run.failures.record(code, _clean(f"{access.verdict}: {access.reason}") or "",
                            capture_id=_clean(capture.id), url=final_url, status=capture.status,
                            vendor=_clean(access.vendor), signals=signals, rung=access.rung,
                            evidence_id=evidence_id)
    return evidence_id
