"""Hand-rolled JSONL tracer with OTel-shaped fields (no SDK).

One line per span start and per span end, so a crash leaves the open span visible. Lines are written with a
single O_APPEND write, so concurrent processes (explorer in the background, indexer in the foreground) can
share one trace.jsonl. Everything passes through the redactor before it hits disk.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import secrets
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .redact import redact_obj

_current_span: contextvars.ContextVar[str | None] = contextvars.ContextVar("blastradius_span", default=None)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Span:
    def __init__(self, span_id: str, kind: str, name: str, attrs: dict[str, Any]):
        self.span_id = span_id
        self.kind = kind
        self.name = name
        self.attrs = dict(attrs)
        self.status = "ok"
        self.failure_code: str | None = None
        self.message: str | None = None

    def set(self, **attrs: Any) -> None:
        self.attrs.update(attrs)

    def fail(self, code: str, message: str = "") -> None:
        self.status = "error"
        self.failure_code = str(code)
        self.message = message[:300] or None


class Tracer:
    def __init__(self, run_dir: Path | None, run_id: str):
        self.run_id = run_id
        self.trace_id = hashlib.sha256(run_id.encode()).hexdigest()[:32]
        self._lock = threading.Lock()
        self._fd: int | None = None
        if run_dir is not None:
            run_dir.mkdir(parents=True, exist_ok=True)
            self.path: Path | None = run_dir / "trace.jsonl"
            self._fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        else:
            self.path = None
        self.records: list[dict[str, Any]] = []  # in-memory mirror, handy for tests

    @classmethod
    def null(cls) -> "Tracer":
        return cls(None, "null")

    @property
    def current_span_id(self) -> str | None:
        return _current_span.get()

    def _emit(self, rec: dict[str, Any]) -> None:
        rec = redact_obj(rec)
        self.records.append(rec)
        if self._fd is None:
            return
        line = (json.dumps(rec, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        with self._lock:
            os.write(self._fd, line)

    @contextlib.contextmanager
    def span(self, kind: str, name: str, **attrs: Any) -> Iterator[Span]:
        span_id = secrets.token_hex(8)
        parent = _current_span.get()
        sp = Span(span_id, kind, name, attrs)
        token = _current_span.set(span_id)
        base = {"trace_id": self.trace_id, "run_id": self.run_id, "span_id": span_id,
                "parent_span_id": parent, "kind": kind, "name": name}
        self._emit({**base, "event": "start", "ts": now_iso(), "attributes": sp.attrs})
        t0 = time.monotonic()
        try:
            yield sp
        except BaseException as exc:  # noqa: BLE001 - re-raised after recording
            sp.status = "error"
            sp.failure_code = sp.failure_code or type(exc).__name__
            sp.message = sp.message or str(exc)[:300]
            raise
        finally:
            _current_span.reset(token)
            self._emit({
                **base,
                "event": "end",
                "ts": now_iso(),
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "status": sp.status,
                "failure_code": sp.failure_code,
                "message": sp.message,
                "attributes": sp.attrs,
            })

    def event(self, name: str, **attrs: Any) -> None:
        self._emit({
            "trace_id": self.trace_id, "run_id": self.run_id, "span_id": _current_span.get(),
            "event": "event", "name": name, "ts": now_iso(), "attributes": attrs,
        })


def load_trace(path: Path) -> list[dict[str, Any]]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def render_tree(records: list[dict[str, Any]]) -> str:
    """Text tree of spans. Spans with a start but no end (crash) are marked OPEN."""
    spans: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in records:
        sid = r.get("span_id")
        if not sid or r.get("event") not in ("start", "end"):
            continue
        s = spans.setdefault(sid, {"children": []})
        if sid not in order:
            order.append(sid)
        if r["event"] == "start":
            s.update(kind=r.get("kind"), name=r.get("name"), parent=r.get("parent_span_id"), attrs=r.get("attributes", {}))
        else:
            s.update(status=r.get("status"), ms=r.get("duration_ms"), failure=r.get("failure_code"),
                     end_attrs=r.get("attributes", {}))
    roots = []
    for sid in order:
        parent = spans[sid].get("parent")
        if parent and parent in spans:
            spans[parent]["children"].append(sid)
        else:
            roots.append(sid)
    lines: list[str] = []

    def walk(sid: str, depth: int) -> None:
        s = spans[sid]
        status = s.get("status") or "OPEN"
        extra = ""
        attrs = {**s.get("attrs", {}), **s.get("end_attrs", {})}
        for key in ("model", "cache_hit", "cost_usd", "access_verdict", "url", "purpose"):
            if key in attrs:
                extra += f" {key}={attrs[key]}"
        fail = f" FAIL={s['failure']}" if s.get("failure") else ""
        ms = f" {s['ms']}ms" if s.get("ms") is not None else ""
        lines.append(f"{'  ' * depth}{s.get('kind')}:{s.get('name')} [{status}]{ms}{fail}{extra}")
        for child in s["children"]:
            walk(child, depth + 1)

    for root in roots:
        walk(root, 0)
    return "\n".join(lines)
