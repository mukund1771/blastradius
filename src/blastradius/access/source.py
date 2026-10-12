"""Ingest sources: where Captures come from (D11, D12; case 18).

The offline answer to "protected app, no owner cooperation": the owner (or a human in their own browser)
supplies a folder of recorded states and the pipeline replays it through the same Capture interface, with the
same classifier, so a challenge page that was recorded is still quarantined (case 17).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..models import Capture
from ..targets import AnchorConfig
from .capture import make_capture, strip_secret_headers
from .classify import classify_capture, extract_title, normalise_headers

CAP_REPLAY = "replay"   # captures() yields previously recorded states
CAP_ACT = "act"         # act() can drive new transitions (live sources only)


@runtime_checkable
class IngestSource(Protocol):
    kind: str
    capabilities: frozenset[str]

    def captures(self) -> Iterator[Capture]: ...


@runtime_checkable
class ActingSource(IngestSource, Protocol):
    """Optional extension: sources with CAP_ACT in `capabilities` can perform an action and return its
    state."""

    def act(self, action: str) -> Capture: ...


class _Meta(BaseModel):
    """One NNN.json. Keys this loader does not use (provenance notes, exporter fields) are ignored; the
    ones it does use are checked, so a bad value is an error naming the file, not a crash further down."""

    model_config = ConfigDict(extra="ignore")

    url: str = Field(min_length=1)
    final_url: str | None = None
    status: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    title: str | None = None
    parent_id: str | None = None
    action: str | None = None
    captured_at: str | None = None
    interactive_count: int | None = None

    @field_validator("status", "interactive_count", mode="before")
    @classmethod
    def _not_a_bool(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("must be an integer, not a boolean")  # noqa: TRY004  (pydantic wants ValueError)
        return value

    @field_validator("status")
    @classmethod
    def _http_status_range(cls, value: int | None) -> int | None:
        if value is not None and not 100 <= value <= 599:
            raise ValueError("must be an HTTP status between 100 and 599")
        return value

    @field_validator("headers", mode="before")
    @classmethod
    def _header_shapes(cls, value: Any) -> dict[str, str]:
        """An object, or a HAR-style list of {name, value}; names are lower-cased, repeated names joined."""
        if value is None:
            return {}
        if isinstance(value, Mapping):
            return normalise_headers(value.items())
        if isinstance(value, list) and all(
                isinstance(e, Mapping) and isinstance(e.get("name"), str) and "value" in e for e in value):
            return normalise_headers((e["name"], e["value"]) for e in value)
        raise ValueError("must be an object or a HAR-style list of {name, value}")


def _meta_error(path: Path, exc: ValidationError) -> ValueError:
    detail = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'meta'}: {e['msg']}" for e in exc.errors())
    return ValueError(f"{path}: {detail}")


class RecordedSession:
    """A folder of NNN.json (meta), NNN.html and NNN.png, replayed in numeric order.

    Meta keys: url (required), final_url, status, headers (an object or a HAR-style list of {name, value}),
    title, parent_id, action, captured_at, and an optional interactive_count. Cookie, Set-Cookie and
    Authorization are stripped from imported headers (HAR and trace exports hold live tokens). Files other
    than NNN.* (ASCII digits only) are ignored; a symlink resolving outside the folder is an error.

    `html_path` and `screenshot_path` of the Captures are RELATIVE to `folder`: a consumer resolves them with
    `session.folder / capture.html_path` (and stores its own run-relative copy): no local path is
    persisted. A HAR file itself is not read (export its entries to NNN.json); only its header-list shape is.
    """

    kind = "recorded"
    capabilities = frozenset({CAP_REPLAY})

    def __init__(self, folder: Path | str, *, target: str | None = None, anchor: AnchorConfig | None = None):
        self.folder = Path(folder)
        if not self.folder.is_dir():
            raise FileNotFoundError(f"recorded session folder not found: {self.folder}")
        self._root = self.folder.resolve()
        self.target = target or self.folder.name
        self.anchor = anchor
        metas = [p for p in self.folder.glob("*.json")
                 if p.stem.isascii() and p.stem.isdecimal() and p.is_file()]
        self._metas = sorted(metas, key=lambda p: (int(p.stem), p.name))
        if not self._metas:
            raise ValueError(f"no NNN.json meta files in {self.folder}")
        for path in self._metas:
            self._inside(path)

    def captures(self) -> Iterator[Capture]:
        for meta_path in self._metas:
            yield self._load(meta_path)

    def _inside(self, path: Path) -> Path:
        if not path.resolve().is_relative_to(self._root):
            raise ValueError(f"{path}: symlink resolves outside the session folder")
        return path

    def _sibling(self, meta_path: Path, suffix: str) -> Path | None:
        path = meta_path.with_suffix(suffix)
        return self._inside(path) if path.is_file() else None

    def _read_meta(self, meta_path: Path) -> _Meta:
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"{meta_path}: invalid JSON ({exc})") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"{meta_path}: meta must be a JSON object")  # noqa: TRY004  (the contract)
        try:
            return _Meta.model_validate(raw)
        except ValidationError as exc:
            raise _meta_error(meta_path, exc) from exc

    def _load(self, meta_path: Path) -> Capture:
        meta = self._read_meta(meta_path)
        html_file, png_file = self._sibling(meta_path, ".html"), self._sibling(meta_path, ".png")
        body = html_file.read_text(encoding="utf-8-sig", errors="replace") if html_file else ""
        headers = strip_secret_headers(meta.headers)
        final_url = meta.final_url or meta.url
        title = meta.title if (meta.title and meta.title.strip()) else extract_title(body)
        access = classify_capture(status=meta.status, headers=headers, title=title, body_text=body,
                                  interactive_count=meta.interactive_count, anchor=self.anchor, url=final_url,
                                  rung="RECORDED")
        captured_at = meta.captured_at or datetime.fromtimestamp(
            meta_path.stat().st_mtime, UTC).isoformat(timespec="milliseconds")
        return make_capture(
            target=self.target, url=meta.url, final_url=final_url, status=meta.status, headers=headers,
            title=title, access=access, html_path=html_file.name if html_file else None,
            screenshot_path=png_file.name if png_file else None, interactive_count=meta.interactive_count,
            source_kind="recorded", parent_id=meta.parent_id, action=meta.action, captured_at=captured_at,
        )
