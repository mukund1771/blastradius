"""Sparse, blob-less checkout (D24, case 29) against a local git origin: no network, no GitHub."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from blastradius.code import clone
from blastradius.code.clone import CloneError, cone_dirs, ensure_repo, pick_ref

SHA_A, SHA_B = "a" * 40, "b" * 40


def git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit_files(origin: Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        (origin / rel).parent.mkdir(parents=True, exist_ok=True)
        (origin / rel).write_text(text)
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", message)
    return git(origin, "rev-parse", "HEAD")


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    path = tmp_path / "origin"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "uploadpack.allowFilter", "true")
    git(path, "config", "uploadpack.allowAnySHA1InWant", "true")
    commit_files(path, {"src/a.txt": "a", "src/deep/b.txt": "b", "conf/app.json": "{}", "other/c.txt": "c",
                        "README.md": "readme"}, "one")
    return path


def test_pick_ref_prefers_exact_then_branch_then_peeled_tag():
    lines = [f"{SHA_A}\trefs/heads/x/main", f"{SHA_B}\trefs/heads/main", f"{'c' * 40}\trefs/tags/main",
             f"{'d' * 40}\trefs/tags/v1", f"{'e' * 40}\trefs/tags/v1^{{}}", f"{'f' * 40}\trefs/pull/7/head"]
    assert pick_ref("main", lines) == SHA_B
    assert pick_ref("v1", lines) == "e" * 40                 # the commit a tag points at, not the tag object
    assert pick_ref("refs/pull/7/head", lines) == "f" * 40
    assert pick_ref("pull/7/head", lines) == "f" * 40
    assert pick_ref("nope", lines) is None


def test_cone_dirs_adds_file_parents_and_rejects_escapes():
    assert cone_dirs(["a/src/", "./b/src"], ["a/locales/en.json", "a/src/components.d.ts"]) == [
        "a/src", "b/src", "a/locales"]
    with pytest.raises(CloneError):
        cone_dirs(["../etc"])
    with pytest.raises(CloneError):
        cone_dirs(["/abs"])


def test_ensure_repo_is_sparse_reuses_the_cache_and_follows_a_moved_ref(tmp_path: Path, origin: Path, monkeypatch):
    cache = tmp_path / "cache"
    url = f"file://{origin}"
    calls: list[str] = []
    real = clone._git

    def spy(repo_dir, *args, **kw):
        calls.append(args[0])
        return real(repo_dir, *args, **kw)

    monkeypatch.setattr(clone, "_git", spy)
    first = git(origin, "rev-parse", "HEAD")

    work, sha = ensure_repo("acme/mini", "main", ["src"], files=["conf/app.json"], cache_root=cache, remote=url,
                            user_agent="blastradius-test")
    assert work == cache / "acme__mini" and sha == first
    assert (work / "src/a.txt").read_text() == "a" and (work / "src/deep/b.txt").exists()
    assert (work / "conf/app.json").exists()                  # a file in the cone via its parent directory
    assert not (work / "other").exists()                      # outside the cone: never checked out
    assert calls.count("fetch") == 1 and "ls-remote" in calls
    assert git(work, "rev-parse", "HEAD") == first            # detached at the resolved commit

    calls.clear()
    assert ensure_repo("acme/mini", "main", ["src"], files=["conf/app.json"], cache_root=cache, remote=url,
                       user_agent="blastradius-test") == (work, first)
    assert "fetch" not in calls                               # same commit: one ls-remote, no transfer

    second = commit_files(origin, {"src/a.txt": "a2", "src/new.txt": "n"}, "two")
    calls.clear()
    work2, sha2 = ensure_repo("acme/mini", "main", ["src"], files=["conf/app.json"], cache_root=cache, remote=url,
                              user_agent="blastradius-test")
    assert sha2 == second != first and calls.count("fetch") == 1
    assert (work2 / "src/a.txt").read_text() == "a2" and (work2 / "src/new.txt").exists()

    calls.clear()                                              # a full sha needs no ls-remote at all
    _, pinned = ensure_repo("acme/mini", first, ["src"], files=["conf/app.json"], cache_root=cache, remote=url,
                            user_agent="blastradius-test")
    assert pinned == first and "ls-remote" not in calls
    assert (work2 / "src/a.txt").read_text() == "a"


def test_ensure_repo_reports_an_unknown_ref(tmp_path: Path, origin: Path):
    with pytest.raises(CloneError, match="not found on origin"):
        ensure_repo("acme/mini", "no-such-branch", ["src"], cache_root=tmp_path / "c", remote=f"file://{origin}",
                    user_agent="blastradius-test")


def test_ensure_repo_can_fetch_a_pull_request_head(tmp_path: Path, origin: Path):
    head = commit_files(origin, {"src/pr.txt": "pr"}, "pr head")
    git(origin, "update-ref", "refs/pull/6645/head", head)
    git(origin, "reset", "-q", "--hard", "HEAD~1")             # main no longer contains it
    work, sha = ensure_repo("acme/mini", "refs/pull/6645/head", ["src"], cache_root=tmp_path / "c",
                            remote=f"file://{origin}", user_agent="blastradius-test")
    assert sha == head and (work / "src/pr.txt").exists()
