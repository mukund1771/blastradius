"""Artifact readers for the link stage (D08): typed, tolerant, and loud about what was missing or invalid.

Reads, when present, the run-directory files models.py lists: crawl/screens.jsonl, crawl/transitions.jsonl,
crawl/summary.json, spec/requirements.json, spec/stats.json and code/index.json. Rows of another target and
rows that fail validation are counted in `notes` and never used; a missing artifact is recorded as a failure
once (it is the user's cue to run the upstream stage) and the stages that need it report `skipped`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ..failures import FailureCode
from ..models import CodeIndex, CrawlSummary, Link, Requirement, ScreenState, Transition
from ..run import RunContext
from ..targets import TargetConfig


@dataclass
class LinkInputs:
    screens: list[ScreenState] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    summary: CrawlSummary | None = None
    requirements: list[Requirement] = field(default_factory=list)
    spec_stats: dict[str, Any] = field(default_factory=dict)
    index: CodeIndex | None = None
    notes: dict[str, int] = field(default_factory=dict)


def read_jsonl[T: BaseModel](path: Path, model: type[T]) -> tuple[list[T], int]:
    """(valid rows, invalid line count). A missing file is an empty list."""
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


def write_jsonl(path: Path, rows: list[BaseModel]) -> None:
    """Atomic write: a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _own(rows: list[Any], target: str, notes: dict[str, int], what: str) -> list[Any]:
    mine = [r for r in rows if r.target == target]
    if len(mine) != len(rows):
        notes[f"{what}_foreign_target"] = len(rows) - len(mine)
    return mine


def load_crawl(run: RunContext, cfg: TargetConfig, inputs: LinkInputs) -> None:
    root = run.run_dir / "crawl"
    screens, bad_s = read_jsonl(root / "screens.jsonl", ScreenState)
    transitions, bad_t = read_jsonl(root / "transitions.jsonl", Transition)
    inputs.screens = sorted(_own(screens, cfg.name, inputs.notes, "screens"), key=lambda s: s.id)
    inputs.transitions = sorted(transitions, key=lambda t: t.id)
    if bad_s or bad_t:
        inputs.notes["crawl_invalid_rows"] = bad_s + bad_t
    summary_path = root / "summary.json"
    if summary_path.exists():
        try:
            inputs.summary = CrawlSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
        except ValidationError:
            inputs.notes["summary_invalid"] = 1


def load_spec(run: RunContext, cfg: TargetConfig, inputs: LinkInputs) -> None:
    path = run.run_dir / "spec" / "requirements.json"
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        reqs, invalid = [], 0
        for item in raw if isinstance(raw, list) else []:
            try:
                reqs.append(Requirement.model_validate(item))
            except ValidationError:
                invalid += 1
        inputs.requirements = _own(reqs, cfg.name, inputs.notes, "requirements")   # ingest's ranked order
        if invalid:
            inputs.notes["requirements_invalid_rows"] = invalid
    stats = run.run_dir / "spec" / "stats.json"
    if stats.exists():
        try:
            inputs.spec_stats = json.loads(stats.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            inputs.notes["spec_stats_invalid"] = 1


def load_index(run: RunContext, cfg: TargetConfig, inputs: LinkInputs) -> None:
    path = run.run_dir / "code" / "index.json"
    if not path.exists():
        return
    index = CodeIndex.model_validate_json(path.read_text(encoding="utf-8"))
    if index.target != cfg.name:
        inputs.notes["index_foreign_target"] = 1
        return
    inputs.index = index


def load_inputs(run: RunContext, cfg: TargetConfig, *, crawl: bool = True, spec: bool = True,
                code: bool = True) -> LinkInputs:
    """Load what a stage needs. Missing upstream artifacts are recorded once as failures."""
    inputs = LinkInputs()
    if crawl:
        load_crawl(run, cfg, inputs)
        if not inputs.screens:
            run.failures.record(FailureCode.CONFIG_INVALID, "no crawl/screens.jsonl rows: run `blastradius crawl`",
                                stage="link")
    if spec:
        load_spec(run, cfg, inputs)
        if not inputs.requirements:
            run.failures.record(FailureCode.SPEC_FETCH_FAILED,
                                "no spec/requirements.json rows: run `blastradius ingest`", stage="link")
    if code:
        load_index(run, cfg, inputs)
        if inputs.index is None:
            run.failures.record(FailureCode.CONFIG_INVALID, "no code/index.json for this target: run "
                                "`blastradius index`", stage="link")
    return inputs


def load_links(run: RunContext) -> list[Link]:
    rows, _ = read_jsonl(run.run_dir / "links" / "links.jsonl", Link)
    return rows
