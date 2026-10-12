"""CodeAdapter protocol, adapter lookup, and the read allow-list (D10, D15, D24; cases 29, 30).

An adapter turns a checked-out tree into a `CodeIndex`. It sees the tree only through `SourceTree`, which
lists and reads exactly `cfg.roots`, `cfg.locale_files` and `cfg.auto_import_dts`: the sparse checkout is a
superset (git cone mode selects directories), so the allow-list is what keeps everything else unread.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from ..models import CodeIndex
from ..targets import CodeConfig

ADAPTERS = {"vue": "blastradius.code.adapters.vue:VueAdapter", "twig": "blastradius.code.adapters.twig:TwigAdapter"}
_SKIP_DIRS = frozenset({"node_modules", ".git", "dist", "coverage"})


class CodeAdapter(Protocol):
    name: str

    def index(self, repo_root: Path, cfg: CodeConfig, snapshot_id: str) -> CodeIndex: ...


def get_adapter(name: str) -> CodeAdapter:
    try:
        module, _, cls = ADAPTERS[name].partition(":")
    except KeyError as exc:
        raise ValueError(f"unknown code adapter {name!r}; known: {sorted(ADAPTERS)}") from exc
    return getattr(importlib.import_module(module), cls)()


class SourceTree:
    """The files an adapter may read. Reading anything else raises `PermissionError`."""

    def __init__(self, repo_root: Path, cfg: CodeConfig, exts: Iterable[str]):
        self.root = repo_root
        wanted = frozenset(exts)
        found: set[str] = set()
        for source_root in cfg.roots:
            base = repo_root / source_root
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
                for name in filenames:
                    if Path(name).suffix.lower() in wanted:
                        found.add(Path(dirpath, name).relative_to(repo_root).as_posix())
        for extra in [*cfg.locale_files, *([cfg.auto_import_dts] if cfg.auto_import_dts else [])]:
            if (repo_root / extra).is_file():
                found.add(extra)
        self.paths: list[str] = sorted(found)
        self._allowed = frozenset(found)

    def read_bytes(self, path: str) -> bytes:
        if path not in self._allowed:
            raise PermissionError(f"{path} is outside the code index allow-list")
        return (self.root / path).read_bytes()
