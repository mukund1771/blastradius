"""Sparse, blob-less checkout of the target repo at a ref (D24; case 29).

The code index reads exactly three things: `cfg.code.roots`, `cfg.code.locale_files` and
`cfg.code.auto_import_dts`. Git cone mode can only select directories, so the checkout is a superset (the
parent directory of each locale file, plus the files that sit directly in the ancestors of every cone
directory); `index.py` enforces the read allow-list on top of it. A blob-less shallow fetch keeps the
transfer to the commit, its trees, and the blobs of the cone.

Network use is a handful of git requests per run: one `ls-remote` to resolve the ref, one `fetch` when the
resolved commit is not already in the cache, and one batched blob fetch at checkout. The user agent is passed
through so GitHub sees the identifying string from `Settings.user_agent()`.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from ..config import CACHE_DIR, Settings

_SHA = re.compile(r"^[0-9a-f]{40}$")
GITHUB = "https://github.com"


class CloneError(RuntimeError):
    """A git step failed; the message carries git's stderr."""


def cache_dir_for(repo: str, cache_root: Path | None = None) -> Path:
    return (cache_root or CACHE_DIR / "repos") / repo.replace("/", "__")


def cone_dirs(roots: Sequence[str], files: Sequence[str] = ()) -> list[str]:
    """Directories for `git sparse-checkout set --cone`: every root, and the parent directory of each file."""
    out: list[str] = []
    for raw in [*roots, *(str(PurePosixPath(f).parent) for f in files)]:
        path = PurePosixPath(raw.strip().removeprefix("./").rstrip("/"))
        if path.is_absolute() or ".." in path.parts or str(path) in ("", "."):
            raise CloneError(f"sparse path must be a repo-relative directory: {raw!r}")
        if str(path) not in out:
            out.append(str(path))
    return out


def _git(repo_dir: Path, *args: str, user_agent: str, timeout: int = 900, lazy_fetch: bool = True) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1", "GIT_CONFIG_NOSYSTEM": "1"}
    if not lazy_fetch:
        env["GIT_NO_LAZY_FETCH"] = "1"      # a presence check must not quietly download the object (partial clone)
    cmd = ["git", "-c", f"http.userAgent={user_agent}", "-C", str(repo_dir), *args]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False)
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
    lines = _git(repo_dir, "ls-remote", "origin", ref, user_agent=user_agent).splitlines()
    sha = pick_ref(ref, lines)
    if sha is None:
        raise CloneError(f"ref {ref!r} not found on origin")
    return sha


def ensure_repo(repo: str, ref: str, roots: Sequence[str], *, files: Sequence[str] = (),
                cache_root: Path | None = None, remote: str | None = None,
                user_agent: str | None = None) -> tuple[Path, str]:
    """Check out `ref` of `repo` ("owner/name") sparsely and return `(worktree, full_sha)`.

    `roots` are directories; `files` are single files that must be present (their parent directory is added
    to the cone). The cache directory `data/cache/repos/<owner>__<name>` is reused: a ref that resolves to a
    commit already present costs one `ls-remote` (none for a full sha) and no fetch.
    """
    agent = user_agent or Settings().user_agent()
    work = cache_dir_for(repo, cache_root)
    url = remote or f"{GITHUB}/{repo}.git"
    if not (work / ".git").exists():
        work.mkdir(parents=True, exist_ok=True)
        _git(work, "init", "-q", user_agent=agent)
        _git(work, "remote", "add", "origin", url, user_agent=agent)
    else:
        _git(work, "remote", "set-url", "origin", url, user_agent=agent)
    # set the cone before the first checkout so only the cone's blobs are ever transferred
    _git(work, "sparse-checkout", "set", "--cone", *cone_dirs(roots, files), user_agent=agent)
    sha = resolve_ref(work, ref, agent)
    if not _has_commit(work, sha, agent):
        _git(work, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", sha if _SHA.match(ref) else ref,
             user_agent=agent)
        fetched = _git(work, "rev-parse", "FETCH_HEAD^{commit}", user_agent=agent)
        if fetched != sha:  # the ref moved between ls-remote and fetch: honour what we actually got
            sha = fetched
    _git(work, "checkout", "-q", "--detach", "--force", sha, user_agent=agent)
    return work, sha
