"""Allowlist-minded redaction for anything that leaves the process (traces, ledgers, bundles).

Two layers: known-secret patterns are replaced, and long strings are cut to a hash stub so DOM, prompts
and responses never land in a trace verbatim. A canary test greps run dirs for a planted secret.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

MAX_STR = 400

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"), "[REDACTED:anthropic_key]"),
    (re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED:github_token]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws_key]"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"), "[REDACTED:jwt]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization|cookie|set-cookie)\b(\s*[:=]\s*)[^\s'\",;]+"),
        r"\1\2[REDACTED]",
    ),
]

_SENSITIVE_KEYS = {"cookie", "set-cookie", "authorization", "x-api-key", "api_key", "password", "secret", "token"}


def redact_text(text: str, *, truncate: bool = True) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    if truncate and len(text) > MAX_STR:
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]
        text = f"{text[:MAX_STR]}...[len={len(text)} sha={digest}]"
    return text


def redact_obj(obj: Any, *, truncate: bool = True) -> Any:
    if isinstance(obj, str):
        return redact_text(obj, truncate=truncate)
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                out[key] = "[REDACTED]"
            else:
                out[key] = redact_obj(value, truncate=truncate)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, truncate=truncate) for v in obj]
    return obj
