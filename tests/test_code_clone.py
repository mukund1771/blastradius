"""Sparse, blob-less checkout (D24, case 29) against a local git origin: no network, no GitHub."""

from __future__ import annotations

import os
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


# ------------------------------------------------------------------ wave-1 review: clone hardening
def test_cone_dirs_skips_the_parent_of_a_root_level_file_but_not_a_root_level_root():
    assert cone_dirs(["src"], ["en.json", "src/components.d.ts"]) == ["src"]
    assert cone_dirs([], ["en.json"]) == []
    with pytest.raises(CloneError):
        cone_dirs(["."])
    with pytest.raises(CloneError):
        cone_dirs(["src"], ["../x.json"])


def test_ensure_repo_checks_out_a_root_level_file(tmp_path: Path, origin: Path):
    work, _ = ensure_repo("acme/mini", "main", ["src"], files=["README.md"], cache_root=tmp_path / "c",
                          remote=f"file://{origin}", user_agent="blastradius-test")
    assert (work / "README.md").read_text() == "readme" and (work / "src/a.txt").exists()


def test_a_ref_that_looks_like_an_option_is_refused_before_git_runs(tmp_path: Path, origin: Path):
    marker = tmp_path / "pwned"
    for ref in (f"--upload-pack=touch {marker}", "-x", "main\nmain", ""):
        with pytest.raises(CloneError, match="invalid ref"):
            ensure_repo("acme/mini", ref, ["src"], cache_root=tmp_path / "c", remote=f"file://{origin}",
                        user_agent="blastradius-test")
    assert not marker.exists() and not (tmp_path / "c").exists()


def test_a_worktree_key_keeps_a_pr_head_checkout_off_the_baseline_tree(tmp_path: Path, origin: Path):
    url, cache = f"file://{origin}", tmp_path / "cache"
    base_sha = git(origin, "rev-parse", "HEAD")
    base, _ = ensure_repo("acme/mini", "main", ["src"], cache_root=cache, remote=url, user_agent="t")
    head = commit_files(origin, {"src/a.txt": "changed by the PR", "src/pr_only.txt": "pr"}, "pr head")
    git(origin, "update-ref", "refs/pull/9/head", head)
    git(origin, "reset", "-q", "--hard", "HEAD~1")

    side, sha = ensure_repo("acme/mini", "refs/pull/9/head", ["src"], cache_root=cache, remote=url,
                            user_agent="t", worktree=head)
    assert sha == head and side != base and side.parent == base.parent
    assert (side / "src/pr_only.txt").exists() and (side / "src/a.txt").read_text() == "changed by the PR"
    assert git(base, "rev-parse", "HEAD") == base_sha         # the baseline was not touched
    assert (base / "src/a.txt").read_text() == "a" and not (base / "src/pr_only.txt").exists()
    assert git(base, "status", "--porcelain") == ""
    # the same key is reused, and a key cannot name a path outside the cache
    again, _ = ensure_repo("acme/mini", head, ["src"], cache_root=cache, remote=url, user_agent="t", worktree=head)
    assert again == side
    for bad in ("../x", "a/b", ""):
        with pytest.raises(CloneError, match="worktree key"):
            ensure_repo("acme/mini", head, ["src"], cache_root=cache, remote=url, user_agent="t", worktree=bad)


def test_a_second_writer_waits_for_the_lock_and_times_out_instead_of_clobbering(tmp_path: Path, origin: Path):
    import fcntl

    cache = tmp_path / "cache"
    work, _ = ensure_repo("acme/mini", "main", ["src"], cache_root=cache, remote=f"file://{origin}", user_agent="t")
    fd = os.open(clone.lock_path(work), os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with pytest.raises(CloneError, match="locked"):
            ensure_repo("acme/mini", "main", ["src"], cache_root=cache, remote=f"file://{origin}",
                        user_agent="t", lock_timeout=0.3)
    finally:
        os.close(fd)
    ensure_repo("acme/mini", "main", ["src"], cache_root=cache, remote=f"file://{origin}", user_agent="t",
                lock_timeout=5)                                # released: works again


def test_concurrent_checkouts_of_different_refs_do_not_corrupt_the_cache(tmp_path: Path, origin: Path):
    from concurrent.futures import ThreadPoolExecutor

    git(origin, "branch", "other")
    head = commit_files(origin, {"src/extra.txt": "x"}, "second")
    git(origin, "branch", "-f", "other", head)
    cache, url = tmp_path / "cache", f"file://{origin}"
    refs = ["main", "other", head, "main", "other", head]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda r: ensure_repo("acme/mini", r, ["src"], cache_root=cache, remote=url,
                                                      user_agent="t")[1], refs))
    assert set(results) <= {git(origin, "rev-parse", "main"), head}
    assert git(cache / "acme__mini", "status", "--porcelain") == ""


def test_checkout_bytes_are_the_blob_bytes_whatever_gitattributes_say(tmp_path: Path, origin: Path):
    commit_files(origin, {".gitattributes": "*.ts text eol=crlf\n*.vue filter=evil\n", "src/a.ts": "a\nb\n"}, "attrs")
    work, sha = ensure_repo("acme/mini", "main", ["src"], cache_root=tmp_path / "c", remote=f"file://{origin}",
                            user_agent="t")
    assert (work / "src/a.ts").read_bytes() == b"a\nb\n"        # no CRLF smudge: sha of the bytes is git's blob sha
    blob = git(origin, "rev-parse", f"{sha}:src/a.ts")
    assert git(work, "hash-object", "src/a.ts") == blob


def test_git_runs_with_a_minimal_environment(tmp_path: Path, origin: Path, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "NEO4J_PASSWORD"):
        monkeypatch.setenv(name, "sentinel-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:3128")
    seen: list[dict[str, str]] = []
    real = subprocess.run

    def spy(cmd, **kw):
        seen.append(dict(kw.get("env") or {}))
        return real(cmd, **kw)

    monkeypatch.setattr(clone.subprocess, "run", spy)
    ensure_repo("acme/mini", "main", ["src"], cache_root=tmp_path / "c", remote=f"file://{origin}", user_agent="t")
    assert seen and all("sentinel-secret" not in v for env in seen for v in env.values())
    assert all(env.get("GIT_TERMINAL_PROMPT") == "0" and "PATH" in env for env in seen)
    assert all(env.get("HTTPS_PROXY") == "http://proxy.example.test:3128" for env in seen)


def test_remote_host_is_derived_from_the_url_not_hard_coded():
    assert clone.remote_host("https://github.com/o/r.git") == "github.com"
    assert clone.remote_host("file:///tmp/origin") == "local"
    assert clone.remote_host("git@example.test:o/r.git") == "example.test"
