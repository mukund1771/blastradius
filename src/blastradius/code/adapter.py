"""CodeAdapter protocol, adapter lookup, and the read allow-list (D10, D15, D24; cases 29, 30, 35, 40).

An adapter turns a checked-out tree into a `CodeIndex`. It sees the tree only through `SourceTree`, which
lists and reads exactly `cfg.roots`, `cfg.locale_files` and `cfg.auto_import_dts`: the sparse checkout is a
superset (git cone mode selects directories), so the allow-list is what keeps everything else unread.

The checkout of a fork's PR head is untrusted (D06), so the allow-list is also the symlink control: a link is
never followed (not listed, not opened, not as an ancestor of a root), is counted, and is reported by
`run_index` as a failure row. A path can still be swapped for a link between listing and reading, so every
read re-checks with `O_NOFOLLOW` and a real-path containment test.
"""

from __future__ import annotations

import importlib
import os
import stat
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol

from ..models import CodeEdge, CodeFile, CodeIndex, KeyUse, RouteDef, TextLiteral
from ..targets import CodeConfig
from .fileclass import classify_file

__all__ = ["ADAPTERS", "HARD_PROBLEM_KINDS", "MAX_FILE_BYTES", "PARSE_PARTIAL_RATIO", "UNDECODABLE_FILENAME",
           "CodeAdapter", "IndexPart", "PartialIndexer", "RootStatus", "SourceTree", "classify_file", "get_adapter",
           "is_indexable", "parse_partial_reasons"]

ADAPTERS = {"vue": "blastradius.code.adapters.vue:VueAdapter", "twig": "blastradius.code.adapters.twig:TwigAdapter"}
_SKIP_DIRS = frozenset({"node_modules", ".git", "dist", "coverage"})
MAX_FILE_BYTES = 8 * 1024 * 1024        # larger than this is data or a bundle, not source to index

# `stats.parse_partial` (the graph's prune guard reads it) is set only when the index is missing real content:
# a file that could not be read or decoded at all, or parse problems on more than this share of the files read.
# Two valid files tree-sitter cannot fully parse in a thousand are a note in `parse_errors`, not a partial index.
PARSE_PARTIAL_RATIO = 0.02
UNDECODABLE_FILENAME = "undecodable_filename"        # a path that is not valid UTF-8 (Linux allows it): skipped
HARD_PROBLEM_KINDS = frozenset({"undecodable_bytes", "unreadable_file", "oversize_file", UNDECODABLE_FILENAME})


def parse_partial_reasons(pairs: Sequence[tuple[str, str]], indexed_files: int) -> list[str]:
    """Why an index must be treated as partial, `[]` when it may be pruned against.

    `pairs` are the `(file, problem kind)` rows an adapter recorded (`parse_errors` is their count) and
    `indexed_files` the source files it read. A hard kind (undecodable, unreadable, oversize, unnamed) always
    counts; the plain error count counts only above `PARSE_PARTIAL_RATIO` of the files read.
    """
    hard = Counter(kind for _, kind in pairs if kind in HARD_PROBLEM_KINDS)
    reasons = [f"{kind}={n}" for kind, n in sorted(hard.items())]
    if indexed_files and len(pairs) / indexed_files > PARSE_PARTIAL_RATIO:
        reasons.append(f"parse_errors={len(pairs)}/{indexed_files}")
    return reasons


class CodeAdapter(Protocol):
    name: str

    def index(self, repo_root: Path, cfg: CodeConfig, snapshot_id: str, *, target: str | None = None,
              ref: str | None = None) -> CodeIndex: ...


@dataclass
class IndexPart:
    """The facts of a *subset* of files, resolved against a larger path universe (the PR overlay, case 39).

    `files` holds the subset plus any synthetic GENERATED or EXTERNAL node its edges point at. `redirected`
    and `dropped` answer the question for edges of files that were not re-read: an edge that pointed at a
    file that no longer exists is re-resolved from its `via`, or dropped when it resolves to nothing.
    """

    files: list[CodeFile] = field(default_factory=list)
    edges: list[CodeEdge] = field(default_factory=list)
    routes: list[RouteDef] = field(default_factory=list)
    i18n: dict[str, str] | None = None          # None: the locale file was not among the subset
    key_uses: list[KeyUse] = field(default_factory=list)
    literals: list[TextLiteral] = field(default_factory=list)
    issues: dict[str, list[str]] = field(default_factory=dict)       # file -> problem kinds
    redirected: list[CodeEdge] = field(default_factory=list)
    dropped: list[CodeEdge] = field(default_factory=list)
    dts_entries: int | None = None              # rows of the components table used; None when absent
    tag_pairs: int = 0
    tag_pairs_unresolved: int = 0
    symlinks: list[str] = field(default_factory=list)
    oversize: list[str] = field(default_factory=list)


class PartialIndexer(Protocol):
    """An adapter that can index some files of a tree against the complete path set (the overlay needs it)."""

    exts: frozenset[str]            # file extensions the adapter lists (what `is_indexable` is asked with)

    def index_part(self, repo_root: Path, cfg: CodeConfig, *, known_paths: Collection[str],
                   only: Collection[str], dangling: Sequence[CodeEdge] = (),
                   known_keys: Collection[str] = ()) -> IndexPart: ...


def get_adapter(name: str) -> CodeAdapter:
    try:
        module, _, cls = ADAPTERS[name].partition(":")
    except KeyError as exc:
        raise ValueError(f"unknown code adapter {name!r}; known: {sorted(ADAPTERS)}") from exc
    return getattr(importlib.import_module(module), cls)()


def is_indexable(path: str, cfg: CodeConfig, exts: Iterable[str]) -> bool:
    """Whether a repo-relative path is one `SourceTree` would list: under a root with a wanted extension (and
    outside the skipped directories), or one of the configured locale / auto-import files."""
    if path in cfg.locale_files or path == cfg.auto_import_dts:
        return True
    wanted = frozenset(exts)
    posix = PurePosixPath(path)
    if posix.suffix.lower() not in wanted or _SKIP_DIRS & set(posix.parts):
        return False
    return any(path.startswith(root.strip("/") + "/") for root in cfg.roots)


@dataclass(frozen=True)
class RootStatus:
    root: str
    status: str         # ok | missing | symlink | escapes_checkout | not_a_directory | empty
    files: int = 0


class SourceTree:
    """The files an adapter may read. Reading anything else raises `PermissionError`.

    Attributes besides `paths` say what was *not* read: `symlinks` (links skipped, with the path of the link),
    `oversize` (files above `MAX_FILE_BYTES`), `undecodable_names` (paths that are not valid UTF-8, shown with
    backslash escapes: they cannot be stored in the index, so they are skipped), `roots` (one `RootStatus` per
    configured root) and `configured` (status of each locale / auto-import file: ok | missing | symlink |
    escapes_checkout | ...).
    """

    def __init__(self, repo_root: Path, cfg: CodeConfig, exts: Iterable[str]):
        self.root = repo_root
        self._real_root = os.path.realpath(repo_root)
        self._wanted = frozenset(e.lower() for e in exts)
        self._found: set[str] = set()
        self._links: set[str] = set()
        self._oversize: set[str] = set()
        self._bad_names: set[str] = set()
        self.roots: list[RootStatus] = [self._scan_root(r) for r in cfg.roots]
        self.configured: dict[str, str] = {}
        extras = [*cfg.locale_files, *([cfg.auto_import_dts] if cfg.auto_import_dts else [])]
        for extra in dict.fromkeys(extras):
            self.configured[extra] = self._admit_extra(extra)
        self.paths: list[str] = sorted(self._found)
        self.symlinks: list[str] = sorted(self._links)
        self.oversize: list[str] = sorted(self._oversize)
        self.undecodable_names: list[str] = sorted(self._bad_names)
        self._allowed = frozenset(self._found)

    # ------------------------------------------------------------------------------------ listing
    def _inside(self, path: str) -> bool:
        real = os.path.realpath(path)
        return real == self._real_root or real.startswith(self._real_root + os.sep)

    def _scan_root(self, root: str) -> RootStatus:
        rel = root.strip("/")
        base = Path(self.root, rel)
        try:
            info = os.lstat(base)
        except OSError:
            return RootStatus(root, "missing")
        if stat.S_ISLNK(info.st_mode):
            self._links.add(rel)
            return RootStatus(root, "symlink")
        if not stat.S_ISDIR(info.st_mode):
            return RootStatus(root, "not_a_directory")
        if not self._inside(str(base)):          # an ancestor directory is a link that leaves the checkout
            self._links.add(rel)
            return RootStatus(root, "escapes_checkout")
        files = self._walk(base)
        return RootStatus(root, "ok" if files else "empty", files)

    def _walk(self, base: Path) -> int:
        """List the wanted regular files below `base` without following a link; returns how many."""
        admitted = 0
        stack = [base]
        while stack:
            directory = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                name = entry.name
                rel = Path(entry.path).relative_to(self.root).as_posix()
                try:
                    rel.encode("utf-8")
                except UnicodeEncodeError:      # not UTF-8 on disk: the path cannot be persisted or hashed
                    self._bad_names.add(rel.encode("utf-8", "backslashreplace").decode("utf-8"))
                    continue
                if entry.is_symlink():
                    if name not in _SKIP_DIRS:
                        self._links.add(rel)
                elif entry.is_dir(follow_symlinks=False):
                    if name not in _SKIP_DIRS:
                        stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False) and Path(name).suffix.lower() in self._wanted:
                    if entry.stat(follow_symlinks=False).st_size > MAX_FILE_BYTES:
                        self._oversize.add(rel)
                    else:
                        self._found.add(rel)
                        admitted += 1
        return admitted

    def _admit_extra(self, rel: str) -> str:
        path = Path(self.root, rel)
        try:
            info = os.lstat(path)
        except OSError:
            return "missing"
        if stat.S_ISLNK(info.st_mode):
            self._links.add(rel)
            return "symlink"
        if not stat.S_ISREG(info.st_mode):
            return "not_a_file"
        if not self._inside(str(path)):
            self._links.add(rel)
            return "escapes_checkout"
        if info.st_size > MAX_FILE_BYTES:
            self._oversize.add(rel)
            return "oversize"
        self._found.add(rel)
        return "ok"

    # ------------------------------------------------------------------------------------ reading
    def read_bytes(self, path: str) -> bytes:
        if path not in self._allowed:
            raise PermissionError(f"{path} is outside the code index allow-list")
        full = os.path.join(self.root, path)
        if not self._inside(full):
            raise PermissionError(f"{path} resolves outside the checkout")
        try:
            fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as exc:      # ELOOP: became (or is) a symlink; ENOENT: removed since the listing
            raise PermissionError(f"{path} cannot be opened without following links: {exc.strerror}") from exc
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise PermissionError(f"{path} is not a regular file")
            return handle.read(MAX_FILE_BYTES + 1)[:MAX_FILE_BYTES]
