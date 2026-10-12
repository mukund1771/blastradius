"""PR head overlay (D24; cases 29, 39, 42): the base index with the files a pull request changed replaced.

`run_index` describes the base commit. A pull request edits files and sometimes the component table
(`components.d.ts`) that decides what a template tag means, so a component the PR adds is an orphan in the
base graph (case 39). `run_overlay` fixes that without indexing the head from scratch:

1. the head versions of the *changed, indexable* files plus the component table are fetched from
   `refs/pull/<n>/head` into a worktree keyed by the head sha (`clone.fetch_files`: one shallow blob-less
   fetch, one batched blob fetch, symlinks and submodules reported and never written, nothing executed);
2. only those files are re-parsed, resolved against the head's path set and the head's component table
   (`adapter.index_part`);
3. the result replaces the changed files' nodes and out-edges in the base index, inserts added files (named
   in `stats.overlay_added`), removes deleted ones, and re-resolves edges of *unchanged* files whose target
   was deleted (a renamed component) from their `via`.

An adapter may also expose `context_paths(cfg, files)`: the files whose content decides what a changed file
means (the Twig adapter's hook wiring, routing and catalogues). They are fetched with the changed files and
read as context; without them a changed template would lose the hook edges the unchanged wiring defines.

The baseline worktree is never touched, and the base `CodeIndex` object is not mutated. For a head that is a
direct descendant of the base the result equals a fresh index of the head commit (tested); what an overlay
cannot see is an unchanged file whose own resolution changed through a table edit without its target being
deleted. `stats` keeps the base's numbers for everything that needs every file parsed and says so in
`overlay_stats_from_base`; the counts derivable from files and edges are recomputed.

Failure never raises for network or ref problems: the base index comes back unchanged with
`stats.pr_overlay == "failed"` and a `CODE_CHECKOUT_FAILED` row, so the report can say what it could not
determine (case 42). `head_sha` is the pinned commit; if the pull ref moved, the pinned commit is fetched by
id and `stats.overlay_pull_ref_moved` is set. `changed_paths` should include the old path of a rename.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, cast

from ..failures import FailureCode
from ..models import CodeEdge, CodeFile, CodeIndex, FileClass
from ..run import RunContext
from ..targets import CodeConfig, TargetConfig
from .adapter import HARD_PROBLEM_KINDS, IndexPart, PartialIndexer, get_adapter, is_indexable
from .clone import GITHUB, CloneError, HeadFiles, fetch_files, remote_host
from .index import edge_evidence_id, edge_evidence_parts

_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SAMPLE_CAP = 50
# stats keys recomputed from the merged files and edges; every other base key is carried and listed
_RECOUNTED = ("files_total", "files_in_tree", "files_by_class", "vue_files", "imports", "imports_external",
              "renders", "i18n_keys", "key_uses", "routes", "components_dts_entries")


@dataclass
class _Plan:
    """What the PR does to the indexed file set."""

    wanted: list[str] = field(default_factory=list)       # indexable changed paths, to be fetched
    skipped: Counter[str] = field(default_factory=Counter)
    added: set[str] = field(default_factory=set)
    modified: set[str] = field(default_factory=set)
    deleted: set[str] = field(default_factory=set)
    links: list[str] = field(default_factory=list)        # changed paths that are symlinks at the head

    @property
    def changed(self) -> set[str]:
        return self.added | self.modified

    @property
    def replaced(self) -> set[str]:
        return self.added | self.modified | self.deleted


def _valid_path(path: str) -> bool:
    """A repo-relative POSIX path: no traversal, no NUL, no backslash or drive letter smuggled in."""
    pure = PurePosixPath(path)
    clean = not pure.is_absolute() and ".." not in pure.parts and not any(c in path for c in "\0\\")
    return bool(path) and clean


def _argument_problem(pr_number: int, head_repo: str, head_sha: str) -> str | None:
    if pr_number <= 0:
        return "pull request number must be positive"
    if not _SHA.match(head_sha):
        return f"head sha {head_sha!r} is not a full commit sha"
    if not _REPO.match(head_repo):
        return f"head repo {head_repo!r} is not owner/name"
    return None


def _degraded(base: CodeIndex, pr_number: int, reason: str) -> CodeIndex:
    stats = {**base.stats, "pr_number": pr_number, "pr_overlay": "failed", "pr_overlay_reason": reason}
    return base.model_copy(update={"stats": stats})


def _triage(changed_paths: Sequence[str], code: CodeConfig, exts: frozenset[str]) -> _Plan:
    plan = _Plan()
    for path in sorted(dict.fromkeys(changed_paths)):
        if not _valid_path(path):
            plan.skipped["invalid_path"] += 1
        elif not is_indexable(path, code, exts):
            plan.skipped["unindexed"] += 1
        else:
            plan.wanted.append(path)
    return plan


def _apply_states(plan: _Plan, states: dict[str, str], base_paths: set[str]) -> None:
    """Sort the wanted paths into added / modified / deleted by what the head has and the base had."""
    for path in plan.wanted:
        state = states.get(path, "absent")
        if state == "file":
            (plan.modified if path in base_paths else plan.added).add(path)
            continue
        if state == "symlink":
            plan.links.append(path)
        elif state == "submodule":
            plan.skipped["submodule"] += 1
        elif path not in base_paths:
            plan.skipped["absent_in_both"] += 1
        if path in base_paths:
            plan.deleted.add(path)


def _merge_edges(*groups: Sequence[CodeEdge]) -> list[CodeEdge]:
    """One edge per (kind, src, dst): symbols are unioned, the smallest `via` is kept (the index's rule)."""
    merged: dict[tuple[str, str, str], tuple[set[str], str | None]] = {}
    for group in groups:
        for e in group:
            symbols, via = merged.get((e.kind, e.src, e.dst), (set(), e.via))
            if e.via is not None and (via is None or e.via < via):
                via = e.via
            merged[(e.kind, e.src, e.dst)] = (symbols | set(e.symbols), via)
    return [CodeEdge(src=src, dst=dst, kind=kind, symbols=sorted(symbols), via=via)
            for (kind, src, dst), (symbols, via) in sorted(merged.items())]


def _merge_files(base: CodeIndex, part: IndexPart, plan: _Plan, edges: list[CodeEdge]) -> list[CodeFile]:
    """Real files of the base (minus replaced) and of the part (changed only), plus the leaves edges use."""
    nodes: dict[str, CodeFile] = {f.path: f for f in base.files if f.path not in plan.replaced}
    for f in part.files:
        if f.sha is None or f.path in plan.changed:     # synthetic nodes, and real files the PR changed
            nodes[f.path] = f
    referenced = {e.dst for e in edges}
    kept = (f for f in nodes.values() if f.sha is not None or f.path in referenced)
    return sorted(kept, key=lambda f: f.path)


def _merged_index(base: CodeIndex, part: IndexPart, plan: _Plan, pull_ref: str, locale: str | None) -> CodeIndex:
    gone = plan.replaced
    if part.i18n is not None:           # the head's locale file was read: it replaces the table
        i18n = part.i18n
    else:                               # not touched, or removed (deleted / a link): no messages at the head
        i18n = {} if locale in plan.deleted else base.i18n
    kept_edges = [e for e in base.edges if e.src not in gone and e.dst not in plan.deleted]
    edges = _merge_edges(kept_edges, part.edges, part.redirected)
    routes = [r for r in base.routes if r.file not in gone] + part.routes
    key_uses = [u for u in base.key_uses if u.file not in gone] + part.key_uses
    literals = [t for t in base.literals if t.file not in gone] + part.literals
    return CodeIndex(
        target=base.target, repo=base.repo, ref=pull_ref, snapshot_id=base.snapshot_id, adapter=base.adapter,
        files=_merge_files(base, part, plan, edges), edges=edges, routes=sorted(routes, key=lambda r: r.file),
        i18n=i18n,
        key_uses=sorted(key_uses, key=lambda u: (u.file, u.key, u.dynamic)),
        literals=sorted(literals, key=lambda t: (t.file, t.text)), stats={})


def _recount(index: CodeIndex) -> dict[str, Any]:
    files, edges = index.files, index.edges
    externals = {f.path for f in files if f.kind is FileClass.EXTERNAL}
    internal = sum(1 for e in edges if e.kind == "IMPORTS" and e.dst not in externals)
    renders = sum(1 for e in edges if e.kind == "RENDERS")
    return {
        "files_total": len(files), "files_in_tree": sum(1 for f in files if f.sha is not None),
        "files_by_class": dict(sorted(Counter(f.kind.value for f in files).items())),
        "vue_files": sum(1 for f in files if f.path.endswith(".vue")),
        "imports": internal, "imports_external": len(edges) - internal - renders, "renders": renders,
        "i18n_keys": len(index.i18n), "key_uses": len(index.key_uses), "routes": len(index.routes),
    }


def _stats(base: CodeIndex, part: IndexPart, plan: _Plan, head: HeadFiles, *, pr_number: int, head_repo: str,
           head_has_dts: bool, has_dts_config: bool) -> dict[str, Any]:
    pairs = [(f, k) for f, kinds in sorted(part.issues.items()) for k in kinds]
    stats = {k: v for k, v in base.stats.items() if k != "pr_overlay_reason"}
    head_partial = sorted({k for _, k in pairs if k in HARD_PROBLEM_KINDS})
    stats.update({
        # the base's marker is kept (an overlay never makes an incomplete index complete); a head file that could
        # not be read or decoded adds to it. A few parse errors among changed files are only counted below.
        "parse_partial": bool(base.stats.get("parse_partial")) or bool(head_partial),
        "overlay_parse_partial": bool(head_partial), "overlay_parse_partial_kinds": head_partial,
        "pr_number": pr_number, "pr_overlay": "applied",
        "overlay_head_repo": head_repo, "overlay_head_sha": head.sha,
        "overlay_base_snapshot": base.snapshot_id,
        "overlay_pull_ref_tip": head.ref_tip, "overlay_pull_ref_moved": head.ref_moved,
        "overlay_added": sorted(plan.added), "overlay_modified": sorted(plan.modified),
        "overlay_deleted": sorted(plan.deleted),
        "overlay_skipped": {k: plan.skipped[k] for k in sorted(plan.skipped) if plan.skipped[k]},
        "overlay_edges_reresolved": len(part.redirected), "overlay_edges_dropped": len(part.dropped),
        "overlay_dts_entries_base": base.stats.get("components_dts_entries"),
        "overlay_dts_entries_head": part.dts_entries,
        "overlay_dts_missing_at_head": has_dts_config and not head_has_dts,
        "overlay_tag_pairs": part.tag_pairs, "overlay_tag_pairs_unresolved": part.tag_pairs_unresolved,
        "overlay_parse_errors": len(pairs),
        "overlay_parse_error_kinds": dict(sorted(Counter(k for _, k in pairs).items())),
        "overlay_parse_error_sample": [{"file": f, "kind": k} for f, k in pairs[:_SAMPLE_CAP]],
        "overlay_symlinks_skipped": len(plan.links),
        "overlay_symlinks_sample": sorted(plan.links)[:_SAMPLE_CAP],
        "overlay_stats_from_base": sorted(k for k in base.stats if k not in _RECOUNTED),
    })
    return stats


def _record_problems(run: RunContext, stats: dict[str, Any], pr_number: int) -> None:
    """One CODE_PARSE_PARTIAL row when head files could not be read or parsed completely."""
    errors, links = stats["overlay_parse_errors"], stats["overlay_symlinks_skipped"]
    missing_dts = stats["overlay_dts_missing_at_head"]
    if not (errors or links or missing_dts):
        return
    kinds = stats["overlay_parse_error_kinds"]
    detail = f" ({', '.join(f'{k}={n}' for k, n in kinds.items())})" if kinds else ""
    note = "; components.d.ts is absent at the head, tags use the directory rule" if missing_dts else ""
    run.failures.record(
        FailureCode.CODE_PARSE_PARTIAL,
        f"PR #{pr_number} head: {errors} problem(s){detail}; {links} symlink(s) not read{note}",
        overlay=True, parse_errors=errors, kinds=kinds, sample=stats["overlay_parse_error_sample"][:10],
        symlinks_skipped=links, symlinks_sample=stats["overlay_symlinks_sample"][:10],
        dts_missing=missing_dts)


def _refuse(run: RunContext, base: CodeIndex, pr_number: int, reason: str, code: FailureCode) -> CodeIndex:
    run.failures.record(code, f"PR overlay refused: {reason}", pr=pr_number)
    return _degraded(base, pr_number, reason)


def run_overlay(run: RunContext, cfg: TargetConfig, base_index: CodeIndex, pr_number: int, head_repo: str,
                head_sha: str, changed_paths: Sequence[str], *, cache_root: Path | None = None,
                remote: str | None = None) -> CodeIndex:
    """`base_index` with the PR head's changed files applied (module docstring has the contract).

    `remote` and `cache_root` exist for tests and for a mirrored cache; by default the base repo on GitHub
    and `data/cache/repos` are used. `head_repo` ("owner/name" of the fork) is validated and recorded: the
    commit is fetched from the base repo's `refs/pull/<n>/head`, which is where GitHub publishes fork heads.
    """
    code = cfg.code
    pull_ref = f"refs/pull/{pr_number}/head"
    problem = _argument_problem(pr_number, head_repo, head_sha)
    if problem:
        return _refuse(run, base_index, pr_number, problem, FailureCode.CONFIG_INVALID)
    adapter = get_adapter(code.adapter)
    if not hasattr(adapter, "index_part"):
        reason = f"adapter {code.adapter!r} cannot index a subset of files"
        return _refuse(run, base_index, pr_number, reason, FailureCode.CONFIG_INVALID)
    indexer = cast(PartialIndexer, adapter)

    plan = _triage(changed_paths, code, indexer.exts)
    dts = code.auto_import_dts
    # An adapter whose facts of one file depend on others (Twig: hook wiring, routing, catalogues) names them;
    # they are fetched beside the changed files and read as context, never reported as changed.
    context_of = getattr(adapter, "context_paths", None)
    context = list(context_of(code, base_index.files)) if callable(context_of) else []
    to_fetch = sorted({*plan.wanted, *([dts] if dts else []), *context})
    url = remote or f"{GITHUB}/{code.repo}.git"
    with run.tracer.span("http_fetch", "git.fetch_pr_head", host=remote_host(url), repo=code.repo,
                         ref=pull_ref, sha=head_sha, files=len(to_fetch)) as span:
        try:
            head = fetch_files(code.repo, pull_ref, head_sha, to_fetch, worktree=head_sha,
                               cache_root=cache_root, remote=remote, user_agent=run.settings.user_agent())
        except CloneError as exc:
            message = f"could not fetch {pull_ref} of {code.repo} at {head_sha[:12]}: {exc}"
            run.failures.record(FailureCode.CODE_CHECKOUT_FAILED, message, repo=code.repo, ref=pull_ref,
                                head_sha=head_sha)
            return _degraded(base_index, pr_number, message)
        span.set(ref_moved=head.ref_moved)

    base_paths = {f.path for f in base_index.files if f.sha is not None}
    _apply_states(plan, head.states, base_paths)
    stage = run.tracer.span("stage", "code.overlay", adapter=code.adapter, pr=pr_number, head=head_sha[:12])
    with stage as span:
        only = set(plan.changed)
        head_has_dts = bool(dts) and head.states.get(dts or "") == "file"
        if dts and head_has_dts:
            only.add(dts)
        dangling = [e for e in base_index.edges if e.dst in plan.deleted and e.src not in plan.replaced]
        part = indexer.index_part(head.work, code, known_paths=(base_paths - plan.deleted) | plan.added,
                                  only=only, dangling=dangling, known_keys=base_index.i18n.keys())
        index = _merged_index(base_index, part, plan, pull_ref, (code.locale_files or [None])[0])
        span.set(added=len(plan.added), modified=len(plan.modified), deleted=len(plan.deleted),
                 edges=len(index.edges))

    stats = _stats(base_index, part, plan, head, pr_number=pr_number, head_repo=head_repo,
                   head_has_dts=head_has_dts, has_dts_config=bool(dts))
    stats.update(_recount(index))
    if part.dts_entries is not None:
        stats["components_dts_entries"] = part.dts_entries
    base_evidence = {edge_evidence_id(e) for e in base_index.edges}
    for edge in index.edges:
        if edge_evidence_id(edge) not in base_evidence:
            run.evidence.add(*edge_evidence_parts(edge))
    stats["overlay_evidence_id"] = run.evidence.add(
        "code.overlay", f"{code.repo}#{pr_number}@{head_sha}",
        f"PR #{pr_number} head {head_sha[:12]} ({head_repo}) over {base_index.snapshot_id[:12]}: "
        f"+{len(plan.added)} ~{len(plan.modified)} -{len(plan.deleted)} files")
    index = index.model_copy(update={"stats": stats})
    _record_problems(run, stats, pr_number)
    run.path("code", f"overlay_{pr_number}.json").write_text(index.model_dump_json(), encoding="utf-8")
    run.tracer.event("code_overlay", pr=pr_number, added=len(plan.added), modified=len(plan.modified),
                     deleted=len(plan.deleted), edges=len(index.edges))
    return index
