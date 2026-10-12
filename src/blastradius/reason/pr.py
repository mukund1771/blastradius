"""Stage 7 input: the pull request as pinned, classified data (D24, D07; cases 40, 41, 42, 55, 45).

`fetch_pr` reads a PR through the authenticated `gh` CLI (never anonymous REST, never the network by any other
route) and returns `PRData`:

 * metadata and the pinned `head_sha` (case 42): `recheck_head` asks again at render time and says when the
   head moved (a force-push makes every claim stale);
 * every changed file with its page of the diff (paginated: GitHub lists up to 3,000, `files_truncated` says
   when the PR has more; a file without a patch is flagged, never guessed at);
 * the file-class table applied BEFORE any graph lookup (case 40): tests, generated files, lockfiles, docs, CI
   and build configuration are set aside, and a file the graph does not know is only a problem when it is
   source code that should be there (`PR_PATH_MISSING_AT_HEAD`); a file the PR *adds* has no baseline node by
   construction and is attached through the head overlay instead;
 * the stale-base check (case 41): `compare/<base>...<indexed commit>` gives "behind by N commits" and the
   changed files both sides touched (`STALE_BASE`: the diff may not apply as shown);
 * the locale diff (`fetch_locale_diffs`): base and head versions of each configured locale file, flattened.

Text written by the fork's author (title, body) is stored only after `safety.clean_untrusted` and is bounded;
raw text is never kept or rendered (case 55). The GitHub client is injected, so tests run on cached fixtures.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol
from urllib.parse import quote

from pydantic import Field

from ..code.fileclass import classify_file
from ..failures import FailureCode
from ..models import FileClass, Strict
from ..redact import redact_text
from ..run import RunContext
from ..safety import clean_untrusted
from ..targets import TargetConfig
from .change import ChangeKind, LocaleDiff, analyse_patch, diff_locale

PAGE_SIZE = 100
MAX_PAGES = 30                       # GitHub lists at most 3,000 files of a pull request
TITLE_CHARS = 200
BODY_CHARS = 4000
GH_TIMEOUT_S = 60

# classes that never reach the graph lookup: they are listed, not looked up (case 40)
SET_ASIDE = frozenset({
    FileClass.TEST, FileClass.GENERATED, FileClass.LOCKFILE, FileClass.DOCS, FileClass.CI,
    FileClass.BUILD_CONFIG, FileClass.EXTERNAL,
})
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REF_CHARS = re.compile(r"^[A-Za-z0-9_./-]+$")


class GhError(RuntimeError):
    """`gh` failed or returned something that is not what the endpoint documents."""


class GhClient(Protocol):
    def api(self, path: str) -> Any: ...
    def raw(self, path: str) -> str: ...


class GhCli:
    """`gh api <path>` through the authenticated CLI. Arguments are a list (no shell), paths are built from the
    target config and integers; the only PR-authored value that reaches a path is a ref checked against
    `_REF_CHARS` by the caller."""

    def __init__(self, *, timeout: int = GH_TIMEOUT_S, runner: Callable[..., Any] = subprocess.run):
        self.timeout = timeout
        self._run = runner

    def _call(self, path: str, accept: str) -> str:
        try:
            out = self._run(["gh", "api", "-H", f"Accept: {accept}", path], capture_output=True, text=True,
                            timeout=self.timeout, check=False)
        except FileNotFoundError as exc:
            raise GhError("the gh CLI is not installed or not on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise GhError(f"gh api timed out after {self.timeout}s: {path.split('?')[0]}") from exc
        if out.returncode != 0:
            raise GhError(f"gh api failed ({out.returncode}) for {path.split('?')[0]}: {out.stderr.strip()[:200]}")
        return out.stdout

    def api(self, path: str) -> Any:
        text = self._call(path, "application/vnd.github+json")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise GhError(f"gh api returned non-JSON for {path.split('?')[0]}") from exc

    def raw(self, path: str) -> str:
        return self._call(path, "application/vnd.github.raw")


# ------------------------------------------------------------------------------------------- data
class Notice(Strict):
    code: str                          # a FailureCode value, or PR_HEAD_MOVED / OVERLAY_UNAVAILABLE / ...
    message: str


class ChangedFile(Strict):
    path: str
    status: str                        # added | modified | removed | renamed | copied | changed
    previous_path: str | None = None
    additions: int = 0
    deletions: int = 0
    file_class: FileClass
    kind: ChangeKind
    touched: list[str] = Field(default_factory=list)
    added_symbols: list[str] = Field(default_factory=list)
    removed_symbols: list[str] = Field(default_factory=list)
    symbols_known: bool = False
    patch_missing: bool = False
    patch: str | None = Field(default=None, exclude=True)    # untrusted diff text: in memory only, never dumped
    in_snapshot: bool | None = None    # None until `mark_snapshot` ran
    stale_overlap: bool = False        # the base ... main range also changes this file (case 41)
    set_aside: bool = False
    indexed: bool = False              # under a configured source root (the code layer can know it)

    @property
    def graph_path(self) -> str:
        """The path the baseline snapshot knows this file by (a rename is looked up under its old name)."""
        return self.previous_path or self.path


class CompareInfo(Strict):
    against: str
    status: str
    behind_by: int                     # commits the compared-to commit has that the PR base lacks
    files_listed: int
    files_truncated: bool = False
    overlap: list[str] = Field(default_factory=list)


class PRData(Strict):
    number: int
    repo: str
    title: str                         # sanitised, bounded
    body: str = ""                     # sanitised, bounded
    state: str = "open"
    draft: bool = False
    base_sha: str
    base_ref: str = ""
    head_sha: str
    head_repo: str | None = None       # None when the fork was deleted
    is_fork: bool = False
    created_at: str = ""
    files: list[ChangedFile] = Field(default_factory=list)
    files_total: int = 0               # what GitHub says the PR changes
    files_truncated: bool = False
    compare: CompareInfo | None = None
    notices: list[Notice] = Field(default_factory=list)
    evidence_id: str | None = None

    def by_path(self) -> dict[str, ChangedFile]:
        return {f.path: f for f in self.files}


class HeadCheck(Strict):
    pinned_sha: str
    current_sha: str | None
    moved: bool
    problem: str | None = None


# ------------------------------------------------------------------------------------------- helpers
def safe_text(text: str | None, limit: int, *, single_line: bool = False) -> str:
    """Untrusted author text, made safe to store: comments, images and control characters removed, bounded."""
    cleaned = redact_text(clean_untrusted(text or ""), truncate=False)     # secrets never persist (case 58)
    cleaned = " ".join(cleaned.split()) if single_line else cleaned.strip()
    return cleaned[:limit]


def _check_repo(repo: str) -> str:
    if not _REPO.match(repo):
        raise GhError(f"repository {repo!r} is not owner/name")
    return repo


def _paged(gh: GhClient, base: str) -> tuple[list[dict[str, Any]], bool]:
    """All pages of a list endpoint; True when the page cap ended the walk with a full last page."""
    rows: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        chunk = gh.api(f"{base}{'&' if '?' in base else '?'}per_page={PAGE_SIZE}&page={page}")
        if not isinstance(chunk, list):
            raise GhError(f"{base.split('?')[0]} did not return a list")
        rows.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            return rows, False
    return rows, True


def _changed_file(row: Mapping[str, Any], cfg: TargetConfig) -> ChangedFile:
    path = str(row["filename"])
    status = str(row.get("status", "modified"))
    file_class = classify_file(path, cfg.code)
    patch = row.get("patch") if isinstance(row.get("patch"), str) else None
    additions, deletions = int(row.get("additions", 0)), int(row.get("deletions", 0))
    facts = analyse_patch(path, status, file_class, patch, additions, deletions)
    under_root = any(path.startswith(r.strip("/") + "/") for r in cfg.code.roots)
    return ChangedFile(
        path=path, status=status, previous_path=row.get("previous_filename"), additions=additions,
        deletions=deletions, file_class=file_class, kind=facts.kind, touched=list(facts.touched),
        added_symbols=list(facts.added), removed_symbols=list(facts.removed), symbols_known=facts.symbols_known,
        patch_missing=facts.patch_missing, patch=patch, set_aside=file_class in SET_ASIDE, indexed=under_root)


def _compare(gh: GhClient, repo: str, base_sha: str, target_ref: str, paths: set[str]) -> CompareInfo | None:
    """`compare/<base>...<target>`: how far the PR base is behind, and which of `paths` both sides touch."""
    if not _REF_CHARS.match(target_ref):
        return None
    url = f"repos/{repo}/compare/{base_sha}...{quote(target_ref, safe='')}"
    files: list[Mapping[str, Any]] = []
    status, behind_by, truncated = "", 0, True
    for page in range(1, MAX_PAGES + 1):
        body = gh.api(f"{url}?per_page={PAGE_SIZE}&page={page}")
        if not isinstance(body, dict):
            raise GhError("compare did not return an object")
        if page == 1:
            status, behind_by = str(body.get("status", "")), int(body.get("ahead_by", 0))
        chunk = list(body.get("files") or [])
        files.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            truncated = False
            break
    names = {str(f.get("filename")) for f in files}
    names |= {str(f["previous_filename"]) for f in files if f.get("previous_filename")}
    return CompareInfo(against=target_ref, status=status, behind_by=behind_by, files_listed=len(files),
                       files_truncated=truncated, overlap=sorted(paths & names))


# ----------------------------------------------------------------------------------------- the stage
def fetch_pr(run: RunContext, cfg: TargetConfig, gh: GhClient, number: int, *,
             compare_to: str | None = None) -> PRData:
    """The pull request, pinned and classified (module docstring). Raises GhError when the PR itself cannot be
    read; every other problem becomes a notice on the result and a failure row."""
    repo = _check_repo(cfg.code.repo)
    with run.tracer.span("http_fetch", "gh.pr", host="api.github.com", repo=repo, pr=number) as span:
        meta = gh.api(f"repos/{repo}/pulls/{int(number)}")
        if not isinstance(meta, dict) or "head" not in meta or "base" not in meta:
            raise GhError(f"pull request {repo}#{number} is not readable (no head/base in the response)")
        rows, capped = _paged(gh, f"repos/{repo}/pulls/{int(number)}/files")
        span.set(files=len(rows))

    head, base = meta["head"], meta["base"]
    head_repo_obj = head.get("repo") or {}
    head_repo = head_repo_obj.get("full_name")
    pr = PRData(
        number=int(number), repo=repo, title=safe_text(meta.get("title"), TITLE_CHARS, single_line=True),
        body=safe_text(meta.get("body"), BODY_CHARS), state=str(meta.get("state", "open")),
        draft=bool(meta.get("draft", False)), base_sha=str(base["sha"]), base_ref=str(base.get("ref", "")),
        head_sha=str(head["sha"]), head_repo=head_repo if head_repo and _REPO.match(head_repo) else None,
        is_fork=bool(head_repo_obj.get("fork")) or (bool(head_repo) and head_repo != repo),
        created_at=str(meta.get("created_at", "")),
        files=[_changed_file(r, cfg) for r in sorted(rows, key=lambda r: str(r.get("filename")))],
        files_total=int(meta.get("changed_files", len(rows))), files_truncated=capped or
        int(meta.get("changed_files", len(rows))) > len(rows))
    notices = pr.notices

    if not _SHA.match(pr.head_sha) or not _SHA.match(pr.base_sha):
        raise GhError("pull request head or base is not a full commit sha")
    if pr.head_repo is None:
        notices.append(Notice(code="HEAD_REPO_GONE", message="The fork this PR came from no longer exists; the "
                              "base repository's pull ref is used to read the PR's files."))
    if pr.files_truncated:
        message = (f"GitHub lists {len(pr.files)} of the {pr.files_total} changed files; the rest were not "
                   "assessed.")
        notices.append(Notice(code=FailureCode.NEEDS_HUMAN.value, message=message))
        run.failures.record(FailureCode.NEEDS_HUMAN, message, pr=number, listed=len(pr.files), total=pr.files_total)
    missing = sorted(f.path for f in pr.files if f.patch_missing and not f.set_aside and f.status != "removed")
    if missing:
        notices.append(Notice(code="PATCH_MISSING", message=f"{len(missing)} changed file(s) have no diff text on "
                              "GitHub (binary or too large); their change kind is unknown."))

    if compare_to:
        pr.compare = _fetch_compare(run, gh, pr, compare_to)
    pr.evidence_id = run.evidence.add(
        "pr.meta", f"{repo}#{number}@{pr.head_sha}",
        f"PR #{number} head {pr.head_sha[:12]} base {pr.base_sha[:12]}{' (fork ' + pr.head_repo + ')' if pr.is_fork and pr.head_repo else ''}: "
        f"{len(pr.files)} of {pr.files_total} files")
    for f in pr.files:
        file_evidence(run, pr, f)
    return pr


def file_evidence(run: RunContext, pr: PRData, f: ChangedFile) -> str:
    """The evidence row of one changed file (idempotent: the same call returns the same id)."""
    return run.evidence.add("pr.file", f"{pr.repo}#{pr.number}:{f.path}",
                            f"{f.status} {f.path} (+{f.additions} -{f.deletions}) class {f.file_class.value} "
                            f"kind {f.kind.value}")


def _fetch_compare(run: RunContext, gh: GhClient, pr: PRData, compare_to: str) -> CompareInfo | None:
    paths = {p for f in pr.files for p in (f.path, f.previous_path) if p}
    try:
        info = _compare(gh, pr.repo, pr.base_sha, compare_to, paths)
    except GhError as exc:
        pr.notices.append(Notice(code="COMPARE_UNAVAILABLE", message=f"Could not compare the PR base with "
                                 f"{compare_to[:12]}: {exc}"))
        return None
    if info is None:
        return None
    overlap = set(info.overlap)
    for f in pr.files:
        f.stale_overlap = f.path in overlap or (f.previous_path or "") in overlap
    if overlap:
        message = (f"The PR base is {info.behind_by} commit(s) behind the indexed main and {len(overlap)} changed "
                   "file(s) were also edited there: the diff may not apply as shown.")
        pr.notices.append(Notice(code=FailureCode.STALE_BASE.value, message=message))
        run.failures.record(FailureCode.STALE_BASE, message, pr=pr.number, behind_by=info.behind_by,
                            overlap=sorted(overlap)[:20])
    run.evidence.add("pr.compare", f"{pr.repo}#{pr.number}:{pr.base_sha[:12]}...{compare_to[:12]}",
                     f"base is {info.behind_by} commit(s) behind; {len(overlap)} overlapping file(s)")
    return info


def mark_snapshot(run: RunContext, pr: PRData, store: Any) -> list[str]:
    """Set `in_snapshot` on every file by asking the graph (`file_exists`); returns the paths of source files that
    are modified or removed by the PR yet unknown to the snapshot (PR_PATH_MISSING_AT_HEAD).

    Set-aside classes and files outside the indexed roots are not looked up (case 40). A file the PR adds is
    expected to be absent: it is not a problem and gets no failure row."""
    wanted = sorted({f.graph_path for f in pr.files if not f.set_aside and f.indexed})
    known: dict[str, bool] = {}
    if wanted:
        for row in store.read("file_exists", paths=wanted):
            known[row["path"]] = bool(row["exists"]) and not row.get("stub", False)
    missing: list[str] = []
    for f in pr.files:
        if f.set_aside or not f.indexed:
            f.in_snapshot = None
            continue
        f.in_snapshot = known.get(f.graph_path, False)
        if not f.in_snapshot and f.status in ("modified", "removed", "renamed", "changed"):
            missing.append(f.path)
    missing.sort()
    if missing:
        message = (f"{len(missing)} changed source file(s) are not in the indexed snapshot (stale base, rename "
                   "or a file outside the index).")
        run.failures.record(FailureCode.PR_PATH_MISSING_AT_HEAD, message, pr=pr.number, paths=missing[:20])
    return missing


def recheck_head(gh: GhClient, pr: PRData) -> HeadCheck:
    """Case 42: has the PR head moved since the analysis pinned it? Never raises."""
    try:
        meta = gh.api(f"repos/{pr.repo}/pulls/{pr.number}")
        current = str(meta["head"]["sha"])
    except (GhError, KeyError, TypeError) as exc:
        return HeadCheck(pinned_sha=pr.head_sha, current_sha=None, moved=False,
                         problem=f"could not re-read the PR: {exc}")
    return HeadCheck(pinned_sha=pr.head_sha, current_sha=current, moved=current != pr.head_sha)


def note_head_moved(run: RunContext, pr: PRData, check: HeadCheck) -> None:
    if not check.moved:
        return
    message = (f"The PR head moved from {check.pinned_sha[:12]} to {str(check.current_sha)[:12]} after this "
               "analysis started: the report describes the older commit.")
    pr.notices.append(Notice(code="PR_HEAD_MOVED", message=message))
    run.failures.record(FailureCode.NEEDS_HUMAN, message, pr=pr.number, pinned=check.pinned_sha,
                        current=check.current_sha, reason="PR_HEAD_MOVED")


# ------------------------------------------------------------------------------------------ locales
def _contents_path(repo: str, path: str, ref: str) -> str:
    return f"repos/{repo}/contents/{quote(path, safe='/')}?ref={quote(ref, safe='')}"


def fetch_locale_diffs(run: RunContext, cfg: TargetConfig, gh: GhClient, pr: PRData) -> list[LocaleDiff]:
    """One `LocaleDiff` per configured locale file the PR changes (other locale files are not assessed: the
    crawl is `en` only, case 15). A version that cannot be read is a `problem`, never an empty diff."""
    wanted = set(cfg.code.locale_files)
    out: list[LocaleDiff] = []
    for f in pr.files:
        if f.path not in wanted:
            continue
        if f.status == "removed":
            diff = LocaleDiff(f.path, problem="locale_file_removed")
        else:
            base = None if f.status == "added" else _read(gh, pr.repo, f.graph_path, pr.base_sha)
            head = _read_head(gh, pr, f.path)
            diff = diff_locale(f.path, base, head)
            if f.status != "added" and base is None:
                diff = LocaleDiff(f.path, problem="base_unreadable")
        out.append(diff)
        run.evidence.add("pr.locale_diff", f"{pr.repo}#{pr.number}:{f.path}@{pr.head_sha}",
                         f"{f.path}: +{len(diff.added)} ~{len(diff.changed)} -{len(diff.removed)} keys"
                         f"{' (end-of-line change only)' if diff.eol_only else ''}"
                         f"{' [' + diff.problem + ']' if diff.problem else ''}")
    return sorted(out, key=lambda d: d.path)


def _safe_repo_path(path: str) -> bool:
    """A repo-relative path with no parent segments: PR-authored paths (a rename's old name) reach API paths."""
    parts = path.split("/")
    return bool(path) and not path.startswith("/") and ".." not in parts and "\\" not in path \
        and not any(c in path for c in "\0\n?#")


def _read(gh: GhClient, repo: str, path: str, ref: str) -> str | None:
    if not _safe_repo_path(path) or not _REF_CHARS.match(ref):
        return None
    try:
        return gh.raw(_contents_path(repo, path, ref))
    except GhError:
        return None


def _read_head(gh: GhClient, pr: PRData, path: str) -> str | None:
    """The head version: the fork first, then the base repository (it holds the PR's commits too)."""
    repos: Sequence[str] = [r for r in (pr.head_repo, pr.repo) if r]
    for repo in dict.fromkeys(repos):
        text = _read(gh, repo, path, pr.head_sha)
        if text is not None:
            return text
    return None
