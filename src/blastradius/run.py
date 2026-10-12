"""RunContext: one run directory holding the trace, ledgers, failures, evidence, and stage artifacts."""

from __future__ import annotations

import contextlib
import json
import os
import platform
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

from .config import PACKAGE_VERSION, ROOT, RUNS_DIR, Settings
from .evidence import EvidenceLedger
from .failures import META, FailureCode
from .ledger import Ledger
from .trace import Span, Tracer, now_iso


class FailureLog:
    def __init__(self, run_dir: Path | None, run_id: str, tracer: Tracer):
        self.run_id = run_id
        self.tracer = tracer
        self.items: list[dict[str, Any]] = []
        self._fd: int | None = None
        if run_dir is not None:
            self._fd = os.open(run_dir / "failures.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)

    def record(self, code: FailureCode, message: str = "", **context: Any) -> dict[str, Any]:
        stage, retryable, action = META[code]
        rec = {"ts": now_iso(), "run_id": self.run_id, "code": str(code), "stage": stage, "retryable": retryable,
               "user_action": action, "message": message[:500], "span_id": self.tracer.current_span_id,
               "context": context}
        self.items.append(rec)
        self.tracer.event("failure", code=str(code), message=message[:200])
        if self._fd is not None:
            os.write(self._fd, (json.dumps(rec, default=str) + "\n").encode())
        return rec


def _git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _versions() -> dict[str, str]:
    out = {}
    for pkg in ("anthropic", "playwright", "neo4j", "pydantic", "tree-sitter", "httpx", "typer"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = "missing"
    return out


@dataclass
class RunContext:
    run_id: str
    run_dir: Path
    target: str
    settings: Settings
    tracer: Tracer
    ledger: Ledger
    evidence: EvidenceLedger
    failures: FailureLog
    manifest: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, target: str, *, run_id: str | None = None, settings: Settings | None = None,
               runs_dir: Path | None = None) -> "RunContext":
        settings = settings or Settings.load()
        runs_dir = runs_dir or RUNS_DIR
        run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{target}"
        run_dir = runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        ctx = cls._build(run_id, run_dir, target, settings)
        ctx.manifest = {
            "run_id": run_id, "target": target, "created_at": now_iso(), "package_version": PACKAGE_VERSION,
            "python": sys.version.split()[0], "platform": platform.platform(), "versions": _versions(),
            "git_sha": _git_sha(), "argv": sys.argv[1:], "trace_content": settings.trace_content,
            "max_usd": settings.max_usd,
        }
        (run_dir / "manifest.json").write_text(json.dumps(ctx.manifest, indent=2))
        latest = runs_dir / "latest"
        with contextlib.suppress(OSError):
            if latest.is_symlink() or latest.exists():
                latest.unlink()
            latest.symlink_to(run_dir.name)
        return ctx

    @classmethod
    def attach(cls, run_id: str = "latest", *, settings: Settings | None = None,
               runs_dir: Path | None = None) -> "RunContext":
        settings = settings or Settings.load()
        runs_dir = runs_dir or RUNS_DIR
        run_dir = (runs_dir / run_id).resolve()
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"no run at {run_dir} (create one with `blastradius crawl`)")
        manifest = json.loads(manifest_path.read_text())
        ctx = cls._build(manifest["run_id"], run_dir, manifest["target"], settings)
        ctx.manifest = manifest
        return ctx

    @classmethod
    def ephemeral(cls, target: str = "test", *, settings: Settings | None = None) -> "RunContext":
        """No files; for unit tests."""
        settings = settings or Settings()
        tracer = Tracer.null()
        return cls("ephemeral", Path("/nonexistent"), target, settings, tracer, Ledger(None, "ephemeral"),
                   EvidenceLedger(None, "ephemeral"), FailureLog(None, "ephemeral", tracer))

    @classmethod
    def _build(cls, run_id: str, run_dir: Path, target: str, settings: Settings) -> "RunContext":
        tracer = Tracer(run_dir, run_id)
        return cls(run_id, run_dir, target, settings, tracer, Ledger(run_dir, run_id),
                   EvidenceLedger(run_dir, run_id), FailureLog(run_dir, run_id, tracer))

    def path(self, *parts: str) -> Path:
        p = self.run_dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @contextlib.contextmanager
    def stage(self, name: str, **attrs: Any) -> Iterator[Span]:
        with self.tracer.span("stage", name, **attrs) as sp:
            yield sp

    def update_manifest(self, **kv: Any) -> None:
        self.manifest.update(kv)
        if self.run_dir.exists():
            (self.run_dir / "manifest.json").write_text(json.dumps(self.manifest, indent=2, default=str))
