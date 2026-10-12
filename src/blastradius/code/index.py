"""Code index stage: clone -> adapter -> `<run>/code/index.json` (D10, D24; cases 29, 30, 32-36, 40).

`run_index` indexes `ref` (default `cfg.code.ref`) of the target repo. The PR head overlay (cases 29, 39) is a
later wave: a `pr_number` is accepted and recorded in `stats` but nothing is overlaid yet.

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
from ..targets import TargetConfig
from .adapter import get_adapter
from .clone import CloneError, ensure_repo


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


def run_index(run: RunContext, cfg: TargetConfig, *, ref: str | None = None,
              pr_number: int | None = None) -> CodeIndex:
    code = cfg.code
    ref = ref or code.ref
    must_exist = [*code.locale_files, *([code.auto_import_dts] if code.auto_import_dts else [])]
    with run.tracer.span("http_fetch", "git.sparse_checkout", host="github.com", repo=code.repo, ref=ref) as span:
        try:
            repo_dir, sha = ensure_repo(code.repo, ref, code.roots, files=must_exist,
                                        user_agent=run.settings.user_agent())
        except (CloneError, FileNotFoundError) as exc:
            if isinstance(exc, FileNotFoundError):
                run.failures.record(FailureCode.DEP_MISSING, "git is not installed or not on PATH")
            else:
                run.failures.record(FailureCode.CONFIG_INVALID, f"could not check out {code.repo}@{ref}: {exc}")
            raise
        span.set(sha=sha)
    for path in must_exist:
        if not (repo_dir / path).is_file():
            run.failures.record(FailureCode.CONFIG_INVALID, f"configured code file {path} is absent at {sha[:12]}")

    with run.tracer.span("stage", "code.parse", adapter=code.adapter, snapshot=sha) as span:
        index = get_adapter(code.adapter).index(repo_dir, code, sha)
        index = index.model_copy(update={"target": cfg.name, "ref": ref})
        span.set(files=len(index.files), edges=len(index.edges))

    snapshot_evidence = run.evidence.add("code.snapshot", f"{code.repo}@{sha}",
                                         f"{code.adapter} index of {code.repo} at {ref} ({sha[:12]})")
    for edge in index.edges:
        run.evidence.add(*edge_evidence_parts(edge))
    index.stats.update({"snapshot_evidence_id": snapshot_evidence, "ref_requested": ref})
    if pr_number is not None:
        index.stats.update({"pr_number": pr_number, "pr_overlay": "not_applied"})
    run.path("code", "index.json").write_text(index.model_dump_json(), encoding="utf-8")
    run.tracer.event("code_index", files=len(index.files), edges=len(index.edges), snapshot=sha)
    return index
