"""Code index stage: clone -> adapter -> `<run>/code/index.json` (D10, D24; cases 29, 30, 32-36, 40).

`run_index` indexes `ref` (default `cfg.code.ref`) of the target repo. The PR head is applied separately by
`overlay.run_overlay` (case 39), which needs the head repo, sha and changed paths that only the PR stage
knows: `run_index` records a `pr_number` it is given in `stats` together with `pr_overlay="not_applied"` and
why, so no reader can mistake a base index for an overlaid one.

Failures: a checkout that fails is `CODE_CHECKOUT_FAILED` (or `CONFIG_INVALID` for a malformed sparse path,
`DEP_MISSING` for no git); a configured root or file that is missing, a link, or empty is `CONFIG_INVALID`,
and an index with no source files says so (`stats.index_empty`); symlinks that were not followed and files
that could not be parsed completely are one `CODE_PARSE_PARTIAL` row with the counts.

Evidence: one `code.snapshot` row for the commit and one row per edge. Edge evidence ids are recomputable
from the edge alone (`edge_evidence_id`), so a report can cite `A imports B` without a lookup table.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Collection, Iterable

from ..failures import FailureCode
from ..ids import stable_id
from ..models import CodeEdge, CodeIndex
from ..run import RunContext
from ..targets import CodeConfig, TargetConfig
from .adapter import get_adapter
from .clone import GITHUB, CloneError, InvalidSparsePath, ensure_repo, remote_host

_ROOT_PROBLEMS = {
    "missing": "is absent",
    "symlink": "is a symlink (links are never followed)",
    "escapes_checkout": "resolves outside the checkout (a parent directory is a symlink)",
    "not_a_directory": "is not a directory",
    "empty": "holds no indexable source files",
}
_FILE_PROBLEMS = {
    "missing": "is absent",
    "symlink": "is a symlink (links are never followed)",
    "escapes_checkout": "resolves outside the checkout (a parent directory is a symlink)",
    "not_a_file": "is not a regular file",
    "oversize": "is larger than the read limit",
}


def edge_evidence_parts(edge: CodeEdge) -> tuple[str, str, str]:
    """`(kind, ref, summary)` of the evidence row that backs an edge."""
    kind = "code.import" if edge.kind == "IMPORTS" else "code.render"
    verb = "imports" if edge.kind == "IMPORTS" else "renders"
    via = f" via {edge.via}" if edge.via else ""
    symbols = f" symbols={','.join(edge.symbols)}" if edge.symbols else ""
    return kind, f"{edge.src}->{edge.dst}", f"{edge.src} {verb} {edge.dst}{via}{symbols}"


def edge_evidence_id(edge: CodeEdge) -> str:
    kind, ref, summary = edge_evidence_parts(edge)
    return stable_id("ev", kind, ref, summary)


def _closure(adjacent: dict[str, list[str]], seeds: Collection[str], max_hops: int | None) -> dict[str, int]:
    dist = {s: 0 for s in seeds}
    queue = deque(dist)
    while queue:
        node = queue.popleft()
        if max_hops is not None and dist[node] >= max_hops:
            continue
        for nxt in adjacent.get(node, ()):
            if nxt not in dist:
                dist[nxt] = dist[node] + 1
                queue.append(nxt)
    return dist


def reverse_closure(edges: Iterable[CodeEdge], seeds: Collection[str], max_hops: int | None = None) -> dict[str, int]:
    """Files that reach any seed through IMPORTS/RENDERS edges, with their hop distance (seeds are 0).

    This is the plain structural radius: no damping, no symbol filter. The reasoner applies those on top.
    """
    importers: dict[str, list[str]] = {}
    for e in edges:
        importers.setdefault(e.dst, []).append(e.src)
    return _closure(importers, seeds, max_hops)


def render_closure(edges: Iterable[CodeEdge], seeds: Collection[str], max_hops: int | None = None) -> dict[str, int]:
    """Files a seed renders, transitively (RENDERS only): the reachability prior for a page plus its layout (case 32)."""
    rendered: dict[str, list[str]] = {}
    for e in edges:
        if e.kind == "RENDERS":
            rendered.setdefault(e.src, []).append(e.dst)
    return _closure(rendered, seeds, max_hops)


def record_index_problems(run: RunContext, index: CodeIndex, code: CodeConfig, sha: str) -> None:
    """Turn what the adapter reported in `index.stats` into failure rows (nothing is silent)."""
    stats, short = index.stats, sha[:12]
    for root in stats.get("code_roots", []):
        if root["status"] != "ok":
            why = _ROOT_PROBLEMS.get(root["status"], root["status"])
            run.failures.record(FailureCode.CONFIG_INVALID,
                                f"configured code root {root['root']} {why} at {short}",
                                root=root["root"], status=root["status"])
    for path, status in stats.get("configured_files", {}).items():
        if status != "ok":
            run.failures.record(
                FailureCode.CONFIG_INVALID,
                f"configured code file {path} {_FILE_PROBLEMS.get(status, status)} at {short}",
                path=path, status=status)
    if stats.get("index_empty"):
        run.failures.record(
            FailureCode.CONFIG_INVALID,
            f"no source files were indexed for {code.repo} at {short}: every configured root is absent or empty",
            index_empty=True, roots=list(code.roots))
    errors, links = stats.get("parse_errors", 0), stats.get("symlinks_skipped", 0)
    if errors or links:
        kinds = stats.get("parse_error_kinds", {})
        detail = ", ".join(f"{k}={n}" for k, n in kinds.items())
        run.failures.record(
            FailureCode.CODE_PARSE_PARTIAL,
            f"{errors} problem(s) in {stats.get('parse_error_files', 0)} file(s)"
            f"{f' ({detail})' if detail else ''}; {links} symlink(s) not read",
            parse_errors=errors, files=stats.get("parse_error_files", 0), kinds=kinds,
            sample=stats.get("parse_error_sample", [])[:10], symlinks_skipped=links,
            symlinks_sample=stats.get("symlinks_skipped_sample", [])[:10])


def run_index(run: RunContext, cfg: TargetConfig, *, ref: str | None = None,
              pr_number: int | None = None) -> CodeIndex:
    code = cfg.code
    ref = ref or code.ref
    must_exist = [*code.locale_files, *([code.auto_import_dts] if code.auto_import_dts else [])]
    host = remote_host(f"{GITHUB}/{code.repo}.git")
    with run.tracer.span("http_fetch", "git.sparse_checkout", host=host, repo=code.repo, ref=ref) as span:
        try:
            repo_dir, sha = ensure_repo(code.repo, ref, code.roots, files=must_exist,
                                        user_agent=run.settings.user_agent())
        except FileNotFoundError as exc:
            run.failures.record(FailureCode.DEP_MISSING, f"git is not installed or not on PATH: {exc}")
            raise
        except InvalidSparsePath as exc:
            run.failures.record(FailureCode.CONFIG_INVALID, f"code roots/files are not repo-relative: {exc}")
            raise
        except CloneError as exc:
            run.failures.record(FailureCode.CODE_CHECKOUT_FAILED,
                                f"could not check out {code.repo}@{ref}: {exc}", repo=code.repo, ref=ref)
            raise
        span.set(sha=sha)

    with run.tracer.span("stage", "code.parse", adapter=code.adapter, snapshot=sha) as span:
        index = get_adapter(code.adapter).index(repo_dir, code, sha, target=cfg.name, ref=ref)
        span.set(files=len(index.files), edges=len(index.edges))
    record_index_problems(run, index, code, sha)

    snapshot_evidence = run.evidence.add("code.snapshot", f"{code.repo}@{sha}",
                                         f"{code.adapter} index of {code.repo} at {ref} ({sha[:12]})")
    for edge in index.edges:
        run.evidence.add(*edge_evidence_parts(edge))
    index.stats.update({"snapshot_evidence_id": snapshot_evidence, "ref_requested": ref})
    if pr_number is not None:
        index.stats.update({
            "pr_number": pr_number, "pr_overlay": "not_applied",
            "pr_overlay_reason": "run_index indexes the base ref only; the PR head is applied by "
                                 "blastradius.code.overlay.run_overlay (needs head repo, sha, changed paths)"})
    run.path("code", "index.json").write_text(index.model_dump_json(), encoding="utf-8")
    run.tracer.event("code_index", files=len(index.files), edges=len(index.edges), snapshot=sha)
    return index
