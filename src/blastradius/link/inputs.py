"""Artifact readers for the link stage (D08): typed, tolerant, and loud about what was missing or invalid.

Reads, when present, the run-directory files models.py lists: crawl/screens.jsonl, crawl/transitions.jsonl,
crawl/summary.json, spec/requirements.json, spec/stats.json and code/index.json. Rows of another target and
rows that fail validation are counted in `notes` and never used; a missing artifact is recorded as a failure
once (it is the user's cue to run the upstream stage) and the stages that need it report `skipped`.

A present but unreadable artifact (truncated JSON, a bad row, bytes that are not UTF-8, a JSON value of the
wrong shape) is not a crash and not silence: the reader records one CONFIG_INVALID failure naming the file
and what to re-run, counts it in `notes`, and the stage carries on as if that artifact were absent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ..failures import FailureCode
from ..ids import sha
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


def read_text(path: Path) -> str | None:
    """The file's text (undecodable bytes replaced, so one bad byte costs a row, not the file), or None when
    it cannot be read at all."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_json(path: Path) -> tuple[Any, str]:
    """(parsed value, problem). `problem` is "" for a valid file, "missing" for none, else what is wrong."""
    if not path.exists():
        return None, "missing"
    text = read_text(path)
    if text is None:
        return None, "unreadable"
    try:
        return json.loads(text), ""
    except (json.JSONDecodeError, RecursionError) as exc:
        return None, f"not valid JSON ({type(exc).__name__}: {str(exc)[:80]})"


def invalid_artifact(run: RunContext, path: Path, problem: str, *, rerun: str,
                     notes: dict[str, int] | None = None, key: str = "") -> None:
    """One CONFIG_INVALID failure for a present-but-unusable upstream artifact."""
    try:
        shown = str(path.relative_to(run.run_dir))
    except ValueError:
        shown = str(path)
    run.failures.record(FailureCode.CONFIG_INVALID, f"{shown}: {problem}; ignored, run `{rerun}` again",
                        stage="link", artifact=shown)
    if notes is not None and key:
        notes[key] = notes.get(key, 0) + 1


def read_jsonl[T: BaseModel](path: Path, model: type[T]) -> tuple[list[T], int]:
    """(valid rows, invalid line count). A missing file is an empty list; an unreadable one, one bad line."""
    rows: list[T] = []
    invalid = 0
    if not path.exists():
        return rows, invalid
    text = read_text(path)
    if text is None:
        return rows, 1
    for line in text.splitlines():
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
    for what, bad in (("screens.jsonl", bad_s), ("transitions.jsonl", bad_t)):
        if bad:
            inputs.notes["crawl_invalid_rows"] = inputs.notes.get("crawl_invalid_rows", 0) + bad
            invalid_artifact(run, root / what, f"{bad} row(s) failed validation or are truncated",
                             rerun="blastradius crawl")
    summary_path = root / "summary.json"
    if summary_path.exists():
        text = read_text(summary_path)
        try:
            if text is None:
                raise ValueError("unreadable")
            inputs.summary = CrawlSummary.model_validate_json(text)
        except (ValidationError, ValueError):
            invalid_artifact(run, summary_path, "does not validate as a crawl summary (corrupt or truncated)",
                             rerun="blastradius crawl", notes=inputs.notes, key="summary_invalid")


def load_spec(run: RunContext, cfg: TargetConfig, inputs: LinkInputs) -> None:
    path = run.run_dir / "spec" / "requirements.json"
    if path.exists():
        raw, problem = read_json(path)
        if problem:
            invalid_artifact(run, path, problem, rerun="blastradius ingest", notes=inputs.notes,
                             key="requirements_invalid")
        elif not isinstance(raw, list):
            invalid_artifact(run, path, f"holds a JSON {type(raw).__name__}, not a list of requirements",
                             rerun="blastradius ingest", notes=inputs.notes, key="requirements_invalid")
        else:
            reqs, invalid = [], 0
            for item in raw:
                try:
                    reqs.append(Requirement.model_validate(item))
                except ValidationError:
                    invalid += 1
            inputs.requirements = _own(reqs, cfg.name, inputs.notes, "requirements")   # ingest's ranked order
            if invalid:
                inputs.notes["requirements_invalid_rows"] = invalid
                invalid_artifact(run, path, f"{invalid} requirement row(s) failed validation",
                                 rerun="blastradius ingest")
    stats = run.run_dir / "spec" / "stats.json"
    if stats.exists():
        value, problem = read_json(stats)
        if problem or not isinstance(value, dict):
            invalid_artifact(run, stats, problem or f"holds a JSON {type(value).__name__}, not an object",
                             rerun="blastradius ingest", notes=inputs.notes, key="spec_stats_invalid")
        else:
            inputs.spec_stats = value


def load_index(run: RunContext, cfg: TargetConfig, inputs: LinkInputs) -> None:
    path = run.run_dir / "code" / "index.json"
    if not path.exists():
        return
    text = read_text(path)
    try:
        if text is None:
            raise ValueError("unreadable")
        index = CodeIndex.model_validate_json(text)
    except (ValidationError, ValueError):
        invalid_artifact(run, path, "does not validate as a code index (corrupt or truncated)",
                         rerun="blastradius index", notes=inputs.notes, key="index_invalid")
        return
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
        if not inputs.screens and not inputs.notes.get("crawl_invalid_rows"):
            run.failures.record(FailureCode.CONFIG_INVALID, "no crawl/screens.jsonl rows: run `blastradius crawl`",
                                stage="link")
    if spec:
        load_spec(run, cfg, inputs)
        if not inputs.requirements and not inputs.notes.get("requirements_invalid"):
            run.failures.record(FailureCode.SPEC_FETCH_FAILED,
                                "no spec/requirements.json rows: run `blastradius ingest`", stage="link")
    if code:
        load_index(run, cfg, inputs)
        if inputs.index is None and not inputs.notes.get("index_invalid"):
            run.failures.record(FailureCode.CONFIG_INVALID, "no code/index.json for this target: run "
                                "`blastradius index`", stage="link")
    return inputs


def crawl_fingerprint(screens: list[ScreenState]) -> str:
    """What a link run was derived from, as far as the crawl goes: the Screen ids (content hashes of their
    fingerprints) of the crawl it read. `link_state.json` stores it so `gaps` can tell a state that belongs to
    another crawl of the same bundle from one that belongs to this crawl."""
    return sha(*sorted(s.id for s in screens), n=16)


def load_links(run: RunContext) -> list[Link]:
    """links/links.jsonl rows that validate. A bad row is one CONFIG_INVALID failure, never a crash."""
    path = run.run_dir / "links" / "links.jsonl"
    rows, invalid = read_jsonl(path, Link)
    if invalid:
        invalid_artifact(run, path, f"{invalid} row(s) failed validation or are truncated",
                         rerun="blastradius link")
    return rows
