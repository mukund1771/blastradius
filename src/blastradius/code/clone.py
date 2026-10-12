"""Sparse, blob-less checkout of the target repo at a ref (D24; cases 29, 39, 42).

The code index reads exactly three things: `cfg.code.roots`, `cfg.code.locale_files` and
`cfg.code.auto_import_dts`. Git cone mode can only select directories, so the checkout is a superset (the
parent directory of each locale file, plus the files that sit directly in the ancestors of every cone
directory); `adapter.SourceTree` enforces the read allow-list on top of it. A blob-less shallow fetch keeps the
transfer to the commit, its trees, and the blobs of the cone.

Network use is a handful of git requests per run: one `ls-remote` to resolve the ref, one `fetch` when the
resolved commit is not already in the cache, and one batched blob fetch at checkout. The user agent is passed
through so GitHub sees the identifying string from `Settings.user_agent()`.

Safety and concurrency:
- git runs with an allow-listed environment (no API keys or tokens), no hooks, no fsmonitor, no prompts;
- `info/attributes` switches every conversion off, so a checked-out file's bytes are the blob's bytes and the
  index can stamp git's own blob sha;
- every mutation of a cache directory holds an exclusive file lock (`lock_path`), so concurrent runs queue up
  instead of corrupting the index;
- the shared tree `<owner>__<name>` is single-writer state: a reader that must not see it move (the baseline
  being indexed while a PR head is checked out) uses `worktree=<key>`, which is a separate directory
  `<owner>__<name>@<key>` with its own lock and history. `fetch_files` is the PR overlay's entry point: it
  materialises named files of one commit in such a worktree (D24, case 39).
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import re
import subprocess
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from ..config import CACHE_DIR, Settings

_SHA = re.compile(r"^[0-9a-f]{40}$")
_REF = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/+@#=,%-]{0,254}$")
_WORKTREE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
GITHUB = "https://github.com"
LOCK_TIMEOUT_S = 600.0
# What git may see of the caller's environment: enough to find itself, its config and a proxy, nothing else.
_ENV_ALLOW = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "XDG_CONFIG_HOME",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "GIT_SSL_CAINFO", "GIT_SSL_CAPATH",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)
_GIT_CONFIG = ("core.hooksPath=/dev/null", "core.fsmonitor=false", "core.autocrlf=false",
               "advice.detachedHead=false")
# `-text` stops end-of-line conversion, the rest stops filters and re-encoding: checkout bytes == blob bytes.
_ATTRIBUTES = "* -text -eol -filter -ident -working-tree-encoding\n"


class CloneError(RuntimeError):
    """A git step failed; the message carries git's stderr."""


class InvalidSparsePath(CloneError):
    """A configured root or file is not a repo-relative directory: a config error, not a network one."""


def remote_host(url: str) -> str:
    """Host a remote URL talks to (`local` for a path or `file://` URL): for the trace, not for policy."""
    if url.startswith(("/", "file://")):
        return "local"
    parsed = urlparse(url)
    if parsed.hostname:
        return parsed.hostname
    scp = re.match(r"^[\w.-]+@([\w.-]+):", url)
    return scp.group(1) if scp else "unknown"


def git_env(*, lazy_fetch: bool = True) -> dict[str, str]:
    """The environment git runs in: allow-listed variables plus the non-interactive switches."""
    env = {k: os.environ[k] for k in _ENV_ALLOW if k in os.environ}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_ATTR_NOSYSTEM": "1"})
    if not lazy_fetch:
        env["GIT_NO_LAZY_FETCH"] = "1"      # a presence check must not quietly download the object
    return env


def check_ref(ref: str) -> str:
    """Reject anything but a plain branch, tag, `refs/...` name or full sha (no options, no refspecs)."""
    if not _REF.match(ref) or ".." in ref or "//" in ref or ref.endswith(("/", ".lock")):
        raise CloneError(f"invalid ref {ref!r}")
    return ref


def check_worktree_key(key: str) -> str:
    if not _WORKTREE_KEY.match(key):
        raise CloneError(f"invalid worktree key {key!r}: use letters, digits, '.', '_' or '-'")
    return key


def cache_dir_for(repo: str, cache_root: Path | None = None, worktree: str | None = None) -> Path:
    """`<root>/<owner>__<name>`, or `<root>/<owner>__<name>@<worktree>` for a keyed (per-sha) checkout."""
    name = repo.replace("/", "__") + (f"@{check_worktree_key(worktree)}" if worktree is not None else "")
    return (cache_root or CACHE_DIR / "repos") / name


def lock_path(work: Path) -> Path:
    """The lock file that serialises writers of `work` (kept outside the directories git manages)."""
    return work.parent / ".locks" / f"{work.name}.lock"


@contextlib.contextmanager
def _locked(work: Path, timeout: float) -> Iterator[None]:
    path = lock_path(work)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CloneError(
                        f"{work.name} is locked by another checkout (waited {timeout:g}s)") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)        # closing the descriptor drops the flock


def cone_dirs(roots: Sequence[str], files: Sequence[str] = ()) -> list[str]:
    """Directories for `git sparse-checkout set --cone`: every root, and the parent directory of each file.

    Cone mode always materialises the files at the top of the repo, so a file whose parent is the repo root
    needs no entry; a *root* of `.` would mean the whole repo and is rejected.
    """
    out: list[str] = []
    wanted = [(r, True) for r in roots] + [(str(PurePosixPath(f).parent), False) for f in files]
    for raw, is_root in wanted:
        path = PurePosixPath(raw.strip().removeprefix("./").rstrip("/"))
        if path.is_absolute() or ".." in path.parts:
            raise InvalidSparsePath(f"sparse path must be a repo-relative directory: {raw!r}")
        if str(path) in ("", "."):
            if is_root:
                raise InvalidSparsePath(f"sparse path must be a repo-relative directory: {raw!r}")
            continue
        if str(path) not in out:
            out.append(str(path))
    return out


def _git(repo_dir: Path, *args: str, user_agent: str, timeout: int = 900, lazy_fetch: bool = True,
         stdin: str | None = None) -> str:
    config = [part for kv in _GIT_CONFIG for part in ("-c", kv)]
    cmd = ["git", "-c", f"http.userAgent={user_agent}", *config, "-C", str(repo_dir), *args]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False, input=stdin,
                              env=git_env(lazy_fetch=lazy_fetch))
    except subprocess.TimeoutExpired as exc:
        raise CloneError(f"git {args[0]} timed out after {timeout}s") from exc
    if done.returncode != 0:
        raise CloneError(f"git {' '.join(args[:2])} failed: {done.stderr.strip()[:400]}")
    return done.stdout.strip()


def _has_commit(repo_dir: Path, sha: str, user_agent: str) -> bool:
    try:
        _git(repo_dir, "cat-file", "-e", f"{sha}^{{commit}}", user_agent=user_agent, lazy_fetch=False)
    except CloneError:
        return False
    return True


def pick_ref(ref: str, ls_remote_lines: Sequence[str]) -> str | None:
    """Choose the commit sha for `ref` from `git ls-remote` output (peeled tags win over tag objects).

    `ls-remote <ref>` matches by path suffix, so `main` would also match `refs/heads/x/main`; this takes the
    exact refname first, then branches, then tags.
    """
    table: dict[str, str] = {}
    for line in ls_remote_lines:
        sha, _, name = line.partition("\t")
        if _SHA.match(sha) and name:
            table[name] = sha
    for candidate in (ref, f"refs/heads/{ref}", f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}", f"refs/{ref}"):
        if candidate in table:
            return table[candidate]
    return None


def resolve_ref(repo_dir: Path, ref: str, user_agent: str) -> str:
    """Full commit sha for `ref`: a full sha is taken as is; anything else is asked of the remote."""
    if _SHA.match(ref):
        return ref
    lines = _git(repo_dir, "ls-remote", "origin", check_ref(ref), user_agent=user_agent).splitlines()
    sha = pick_ref(ref, lines)
    if sha is None:
        raise CloneError(f"ref {ref!r} not found on origin")
    return sha


def _prepare(work: Path, url: str, agent: str) -> None:
    """Create or re-point the cache repo and switch every content conversion off. Caller holds the lock."""
    if not (work / ".git").exists():
        work.mkdir(parents=True, exist_ok=True)
        _git(work, "init", "-q", user_agent=agent)
        _git(work, "remote", "add", "origin", url, user_agent=agent)
    else:
        _git(work, "remote", "set-url", "origin", url, user_agent=agent)
    info = work / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "attributes").write_text(_ATTRIBUTES)


def ensure_repo(repo: str, ref: str, roots: Sequence[str], *, files: Sequence[str] = (),
                cache_root: Path | None = None, remote: str | None = None, user_agent: str | None = None,
                worktree: str | None = None, lock_timeout: float = LOCK_TIMEOUT_S) -> tuple[Path, str]:
    """Check out `ref` of `repo` ("owner/name") sparsely and return `(worktree_dir, full_sha)`.

    `roots` are directories; `files` are single files that must be present (their parent directory is added
    to the cone). The shared cache directory `data/cache/repos/<owner>__<name>` is reused: a ref that resolves
    to a commit already present costs one `ls-remote` (none for a full sha) and no fetch. Pass `worktree` (for
    example a PR head sha) to use a separate directory instead, so the shared baseline tree is not moved.
    """
    check_ref(ref)
    dirs = cone_dirs(roots, files)
    agent = user_agent or Settings().user_agent()
    work = cache_dir_for(repo, cache_root, worktree)
    url = remote or f"{GITHUB}/{repo}.git"
    with _locked(work, lock_timeout):
        _prepare(work, url, agent)
        # set the cone before the first checkout so only the cone's blobs are ever transferred
        _git(work, "sparse-checkout", "set", "--cone", *dirs, user_agent=agent)
        sha = resolve_ref(work, ref, agent)
        if not _has_commit(work, sha, agent):
            _git(work, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin",
                 sha if _SHA.match(ref) else ref, user_agent=agent)
            fetched = _git(work, "rev-parse", "FETCH_HEAD^{commit}", user_agent=agent)
            if fetched != sha:  # the ref moved between ls-remote and fetch: honour what we actually got
                sha = fetched
        _git(work, "checkout", "-q", "--detach", "--force", sha, user_agent=agent)
    return work, sha


# ------------------------------------------------------------------------------------ PR head files
_KINDS = {"100644": "file", "100755": "file", "120000": "symlink", "160000": "submodule"}


@dataclass(frozen=True)
class HeadFiles:
    """What `fetch_files` found for the requested paths of one commit."""

    work: Path                                  # directory holding the checked-out regular files
    sha: str                                    # the commit the files come from
    states: dict[str, str] = field(default_factory=dict)   # path -> file | symlink | submodule | absent
    ref_tip: str | None = None                  # sha the pull ref pointed at (None: not advertised)

    @property
    def ref_moved(self) -> bool:
        return self.ref_tip != self.sha


def _tree_entries(work: Path, sha: str) -> dict[str, tuple[str, str]]:
    """`path -> (kind, blob oid)` for every blob of the commit (trees are present after a blob-less fetch)."""
    raw = subprocess.run(
        ["git", "-C", str(work), "ls-tree", "-r", "-z", "--full-tree", sha], capture_output=True, check=False,
        env=git_env(lazy_fetch=False), timeout=300)
    if raw.returncode != 0:
        raise CloneError(f"git ls-tree failed: {raw.stderr.decode(errors='replace').strip()[:400]}")
    out: dict[str, tuple[str, str]] = {}
    for entry in raw.stdout.split(b"\0"):
        meta, _, name = entry.partition(b"\t")
        fields = meta.split(b" ")
        if name and len(fields) == 3 and fields[0].decode() in _KINDS:
            kind = _KINDS[fields[0].decode()]
            out[name.decode("utf-8", errors="surrogateescape")] = (kind, fields[2].decode())
    return out


_PREFETCH_CHUNK = 400


def _prefetch_blobs(work: Path, oids: Sequence[str], agent: str) -> None:
    """Fetch the blobs that are not local yet in a few batched requests.

    `checkout <commit> -- <paths>` fetches missing blobs of a blob-less clone one request each (measured: 14
    files took 10 s, a batch of 14 took 0.8 s). A server that refuses by-id requests just leaves the fetch
    to checkout's own lazy path, which is slower and not wrong.
    """
    if not oids:
        return
    report = _git(work, "cat-file", "--batch-check", user_agent=agent, lazy_fetch=False,
                  stdin="\n".join(oids) + "\n")
    missing = [line.split()[0] for line in report.splitlines() if line.endswith(" missing")]
    for i in range(0, len(missing), _PREFETCH_CHUNK):
        try:
            batch = missing[i:i + _PREFETCH_CHUNK]
            _git(work, "fetch", "-q", "--no-tags", "origin", *batch, user_agent=agent)
        except CloneError:
            return


def fetch_files(repo: str, ref: str, sha: str, paths: Sequence[str], *, worktree: str,
                cache_root: Path | None = None, remote: str | None = None, user_agent: str | None = None,
                lock_timeout: float = LOCK_TIMEOUT_S) -> HeadFiles:
    """Materialise `paths` of commit `sha` (advertised as `ref`, e.g. `refs/pull/N/head`) in a keyed worktree.

    Only regular files are checked out, one batched blob fetch, from a shallow blob-less fetch of the commit.
    A path that is a symlink or a submodule at that commit is reported, never written. If `ref` no longer
    points at `sha` (a force-push) the pinned commit is fetched by id; if that fails too, `CloneError` says
    so.
    Nothing from the checkout is executed: hooks, filters and fsmonitor are off (see `_GIT_CONFIG`).
    """
    check_ref(ref)
    if not _SHA.match(sha):
        raise CloneError(f"invalid commit sha {sha!r}")
    for p in paths:
        pure = PurePosixPath(p)
        if not p or pure.is_absolute() or ".." in pure.parts or "\0" in p or "\\" in p:
            raise CloneError(f"invalid path {p!r}")
    agent = user_agent or Settings().user_agent()
    work = cache_dir_for(repo, cache_root, worktree)
    url = remote or f"{GITHUB}/{repo}.git"
    with _locked(work, lock_timeout):
        _prepare(work, url, agent)
        try:
            tip = pick_ref(ref, _git(work, "ls-remote", "origin", ref, user_agent=agent).splitlines())
        except CloneError:      # offline is fine when the pinned commit is already in this worktree
            if not _has_commit(work, sha, agent):
                raise
            tip = None
        if not _has_commit(work, sha, agent):
            target = ref if tip == sha else sha
            try:
                _git(work, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", target,
                     user_agent=agent)
            except CloneError as exc:
                raise CloneError(f"could not fetch {ref} at {sha[:12]} (the ref is at "
                                 f"{tip[:12] if tip else 'nothing'}): {exc}") from exc
            if _git(work, "rev-parse", "FETCH_HEAD^{commit}", user_agent=agent) != sha:
                raise CloneError(f"{target} is not commit {sha[:12]}")
        entries = _tree_entries(work, sha)
        states = {p: entries[p][0] if p in entries else "absent" for p in dict.fromkeys(paths)}
        regular = [p for p, kind in states.items() if kind == "file"]
        if regular:
            _prefetch_blobs(work, [entries[p][1] for p in regular], agent)
            spec = "\0".join(f":(literal){p}" for p in regular) + "\0"
            _git(work, "checkout", "-q", "--force", sha, "--pathspec-from-file=-", "--pathspec-file-nul",
                 user_agent=agent, stdin=spec)
    return HeadFiles(work, sha, states, tip)
