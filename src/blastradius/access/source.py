"""Ingest sources: where Captures come from (D11, D12; case 18).

The offline answer to "protected app, no owner cooperation": the owner (or a human in their own browser)
supplies a folder of recorded states and the pipeline replays it through the same Capture interface, with the
same classifier, so a challenge page that was recorded is still quarantined (case 17).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import Capture
from ..targets import AnchorConfig
from .capture import make_capture, strip_secret_headers
from .classify import classify_capture, extract_title

CAP_REPLAY = "replay"   # captures() yields previously recorded states
CAP_ACT = "act"         # act() can drive new transitions (live sources only)


@runtime_checkable
class IngestSource(Protocol):
    kind: str
    capabilities: frozenset[str]

    def captures(self) -> Iterator[Capture]: ...


@runtime_checkable
class ActingSource(IngestSource, Protocol):
    """Optional extension: sources with CAP_ACT in `capabilities` can perform an action and return its state."""

    def act(self, action: str) -> Capture: ...


class RecordedSession:
    """A folder of NNN.json (meta), NNN.html and NNN.png, replayed in numeric order.

    Meta keys: url (required), final_url, status, headers, title, parent_id, action, captured_at, and an
    optional interactive_count. Cookie, Set-Cookie and Authorization are stripped from imported headers
    (HAR and trace exports hold live tokens). Files other than NNN.* are ignored.
    """

    kind = "recorded"
    capabilities = frozenset({CAP_REPLAY})

    def __init__(self, folder: Path | str, *, target: str | None = None, anchor: AnchorConfig | None = None):
        self.folder = Path(folder)
        if not self.folder.is_dir():
            raise FileNotFoundError(f"recorded session folder not found: {self.folder}")
        self.target = target or self.folder.name
        self.anchor = anchor
        self._metas = sorted((p for p in self.folder.glob("*.json") if p.stem.isdigit()),
                             key=lambda p: (int(p.stem), p.name))
        if not self._metas:
            raise ValueError(f"no NNN.json meta files in {self.folder}")

    def captures(self) -> Iterator[Capture]:
        for meta_path in self._metas:
            yield self._load(meta_path)

    def _load(self, meta_path: Path) -> Capture:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{meta_path}: invalid JSON ({exc})") from exc
        if not isinstance(meta, dict) or not meta.get("url"):
            raise ValueError(f"{meta_path}: meta must be an object with a 'url'")
        html_file, png_file = meta_path.with_suffix(".html"), meta_path.with_suffix(".png")
        body = html_file.read_text(encoding="utf-8", errors="replace") if html_file.is_file() else ""
        headers = strip_secret_headers(meta.get("headers") or {})
        url = str(meta["url"])
        final_url = str(meta.get("final_url") or url)
        title = meta.get("title") if meta.get("title") is not None else extract_title(body)
        status = int(meta["status"]) if meta.get("status") is not None else None
        interactive = meta.get("interactive_count")
        access = classify_capture(status=status, headers=headers, title=title, body_text=body,
                                  interactive_count=interactive, anchor=self.anchor, url=final_url,
                                  rung="RECORDED")
        captured_at = meta.get("captured_at") or datetime.fromtimestamp(
            meta_path.stat().st_mtime, UTC).isoformat(timespec="milliseconds")
        return make_capture(
            target=self.target, url=url, final_url=final_url, status=status, headers=headers, title=title,
            access=access, html_path=str(html_file) if html_file.is_file() else None,
            screenshot_path=str(png_file) if png_file.is_file() else None, interactive_count=interactive,
            source_kind="recorded", parent_id=meta.get("parent_id"), action=meta.get("action"),
            captured_at=str(captured_at),
        )
