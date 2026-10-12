"""Secret scrubbing for everything the crawl persists or prompts (case 58): page-derived text and URLs.

`scrub_text` is the shared redactor; `scrub_url` adds what a regex over free text cannot do: no userinfo,
and no value for a query or fragment parameter whose *name* says it is a credential (`access_token`, `code`,
`sid`, ...), because `redact_text` only sees `token=` after a word boundary. Both are pure functions.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..redact import redact_text

_SENSITIVE_PARAM = re.compile(
    r"(?i)(token|secret|key|code|sid|session|auth|pass|pwd|credential|signature|sig|jwt|bearer|otp|nonce)"
)


def scrub_text(text: str | None) -> str:
    """Secret-shaped substrings out of page-derived text before it is persisted or prompted."""
    return redact_text(text, truncate=False) if text else ""


def scrub_url(url: str) -> str:
    """A URL safe to persist: no userinfo, credential-named parameter values masked. Hash routes
    (`#/path`) pass through."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        netloc = host + (f":{parts.port}" if parts.port else "")
    except ValueError:
        return scrub_text(url)

    def mask(params: str) -> str:
        out = []
        for item in params.split("&"):
            key, sep, value = item.partition("=")
            out.append(f"{key}{sep}[REDACTED]" if sep and value and _SENSITIVE_PARAM.search(key) else item)
        return "&".join(out)

    fragment = mask(parts.fragment) if "=" in parts.fragment else parts.fragment
    clean = parts._replace(netloc=netloc, query=mask(parts.query), fragment=fragment).geturl()
    return scrub_text(clean)
