"""Run-directory readers for the eval kit: typed, tolerant, and loud about what was missing or invalid.

The eval kit reads the product only through files (and the graph store): crawl/screens.jsonl,
links/links.jsonl, code/index.json, evidence.jsonl and reason/<pr>/report.json. A row that fails validation
is counted, never used.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ..models import CodeIndex, Link, ScreenState
from ..run import RunContext


def read_jsonl[T: BaseModel](path: Path, model: type[T]) -> tuple[list[T], int]:
    """(valid rows in file order, count of lines that did not validate). A missing file is empty."""
    rows: list[T] = []
    invalid = 0
    if not path.exists():
        return rows, invalid
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(model.model_validate_json(line))
        except ValidationError:
            invalid += 1
    return rows, invalid


def load_screens(run: RunContext, target: str | None = None) -> list[ScreenState]:
    rows, _ = read_jsonl(run.run_dir / "crawl" / "screens.jsonl", ScreenState)
    return sorted((r for r in rows if target is None or r.target == target), key=lambda s: s.id)


def load_links(run: RunContext) -> list[Link]:
    rows, _ = read_jsonl(run.run_dir / "links" / "links.jsonl", Link)
    return sorted(rows, key=lambda l: (l.kind, l.src, l.dst, l.method))


def load_index(run: RunContext) -> CodeIndex | None:
    path = run.run_dir / "code" / "index.json"
    if not path.exists():
        return None
    try:
        return CodeIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError:
        return None


def indexed_paths(index: CodeIndex | None) -> frozenset[str]:
    """Files that exist in the indexed tree (stub nodes for external packages have no sha)."""
    return frozenset() if index is None else frozenset(f.path for f in index.files if f.sha is not None)


def load_evidence(run: RunContext) -> dict[str, dict[str, Any]]:
    """Every evidence row of the run by id: the file on disk plus anything the live ledger already holds."""
    out: dict[str, dict[str, Any]] = {}
    path = run.run_dir / "evidence.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                out[rec["evidence_id"]] = rec
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    for evidence_id in sorted(run.evidence.ids()):
        rec = run.evidence.get(evidence_id)
        if rec is not None:
            out.setdefault(evidence_id, rec)
    return out


@dataclass(frozen=True)
class PrReport:
    name: str                       # reason/<name>/report.json
    path: Path
    data: dict[str, Any]
    problems: list[str] = field(default_factory=list)


def load_pr_reports(run: RunContext) -> list[PrReport]:
    """Every reason/<pr>/report.json in name order. An unreadable file is returned with `problems` set."""
    out: list[PrReport] = []
    root = run.run_dir / "reason"
    for path in sorted(root.glob("*/report.json")) if root.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            problems = [] if isinstance(data, dict) else ["report.json is not a JSON object"]
            out.append(PrReport(path.parent.name, path, data if isinstance(data, dict) else {}, problems))
        except (OSError, json.JSONDecodeError) as exc:
            out.append(PrReport(path.parent.name, path, {}, [f"unreadable: {type(exc).__name__}"]))
    return out
