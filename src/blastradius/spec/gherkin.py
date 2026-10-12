"""Gherkin spec ingest: one Requirement per scenario, no LLM (D15, D30; cases 20, 22, 27, 73).

    ingest_gherkin(run, cfg) -> list[Requirement]        # dispatched by spec.ingest for spec.kind == "gherkin"

Sylius keeps executable requirements in `features/**/*.feature`. They are already atomic, already carry a
UI-observability verdict (`@ui` / `@no-ui`) and are written by the people who own the behaviour, so this
module reads them with a small deterministic parser instead of a model:

  text           "User can ..." derived from the scenario title by fixed rules (gerund -> verb, negative
                 markers -> polarity `must_not_exist`, otherwise "User can see that <title>")
  quote          the scenario title line, verbatim (keyword included)
  section        the Feature name
  ui_observable  `@ui` -> yes, `@no-ui` -> no, neither -> unknown; a tag on the scenario beats the Feature's
  tags           the Gherkin tags as written (`@ui`, `@api`, ...) plus `area:<dir>`
  requires_login the title or a step says the customer is logged in
  edition        cfg.spec.edition_default
  id             stable_id("req", target, title + feature path), the n-th repeat of a title in one file gets
                 the ordinal appended so two scenarios never collide

Case 73: the public docs are a cross-check source, never merged here, so the requirement layer and the code
layer stay independent even though the Gherkin lives in the code repo.

Fetch: one `gh api` call resolves the ref to a commit, one lists the tree below the common directory of
`cfg.spec.paths`, and one raw fetch per `.feature` file follows (sequential, 100 ms apart), each with the
crawler's `Settings().user_agent()`. Every file is read
at the resolved commit so the requirement set is a snapshot, and `spec_sha` hashes the concatenated sources.
`FeatureSource` is the seam tests (and recorded runs) use instead of the network.

Not handled, said plainly: languages other than English (`# language:` is recorded, keywords are not
translated), `Rule` blocks keep their scenarios but not the rule text, and Scenario Outline rows are not
expanded (one requirement per outline).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from ..config import Settings
from ..failures import FailureCode
from ..ids import sha_full, stable_id
from ..models import Requirement
from ..run import RunContext
from ..targets import TargetConfig

_STEP = re.compile(r"^(Given|When|Then|And|But|\*)\b\s*(.*)$")
_HEADER = re.compile(r"^(Feature|Background|Rule|Scenario Outline|Scenario Template|Scenario|Example|"
                     r"Examples|Scenarios)\s*:\s*(.*)$")
_SCENARIO_KEYWORDS = frozenset({"Scenario", "Scenario Outline", "Scenario Template", "Example"})
_TAG = re.compile(r"@[^\s@#]+")
_LANGUAGE = re.compile(r"^#\s*language\s*:\s*([\w-]+)", re.IGNORECASE)
_LOGGED_IN = re.compile(r"\b(?:logged[ -]?in|log(?:ging|s)? in|sign(?:ed|ing)? in)\b", re.IGNORECASE)
_MAX_SCENARIO_CHARS = 400            # quote and text are bounded; a longer title is cut at a word
_TITLE_FILE_CAP = 2000               # scenarios read from one file (hostile or generated files)
_FETCH_DELAY_S = 0.1


# ---------------------------------------------------------------------------------------------- parser
@dataclass(frozen=True)
class Scenario:
    keyword: str
    title: str
    line: int                        # 1-based line of the title
    tags: tuple[str, ...]            # own tags, as written, in file order
    steps: tuple[str, ...] = ()
    raw: str = ""                    # the title line stripped, e.g. "Scenario: Adding a product"


@dataclass(frozen=True)
class Feature:
    name: str
    tags: tuple[str, ...]
    scenarios: tuple[Scenario, ...]
    background: tuple[str, ...] = ()
    language: str = "en"
    truncated: bool = False


@dataclass
class _Open:
    keyword: str
    title: str
    line: int
    tags: tuple[str, ...]
    raw: str
    steps: list[str] = field(default_factory=list)

    def done(self) -> Scenario:
        return Scenario(self.keyword, self.title, self.line, self.tags, tuple(self.steps), self.raw)


def parse_feature(text: str) -> Feature:
    """Parse one `.feature` file. Deterministic, total (never raises) and linear in the input size."""
    name, language = "", "en"
    feature_tags: tuple[str, ...] = ()
    rule_tags: tuple[str, ...] = ()
    pending: list[str] = []
    scenarios: list[Scenario] = []
    background: list[str] = []
    current: _Open | None = None
    in_background = False
    in_docstring = ""
    seen_feature = False
    truncated = False

    def close() -> None:
        nonlocal current
        if current is not None:
            scenarios.append(current.done())
            current = None

    for number, raw_line in enumerate(text.lstrip("﻿").splitlines(), start=1):
        line = raw_line.strip()
        if in_docstring:
            if line.startswith(in_docstring):
                in_docstring = ""
            continue
        if line.startswith(('"""', "```")):
            in_docstring = line[:3]
            continue
        if not line:
            continue
        if line.startswith("#"):
            lang = _LANGUAGE.match(line)
            if lang and not seen_feature:
                language = lang.group(1).lower()
            continue
        if line.startswith("|"):
            continue                                # data table or Examples row
        if line.startswith("@"):
            pending.extend(_TAG.findall(line.split(" #", 1)[0]))
            continue
        header = _HEADER.match(line)
        if header:
            keyword, title = header.group(1), header.group(2).strip()
            if keyword == "Feature":
                close()
                seen_feature = True
                name, feature_tags, pending = title, tuple(pending), []
                in_background = False
            elif keyword == "Background":
                close()
                in_background, pending = True, []
            elif keyword == "Rule":
                close()
                in_background = False
                rule_tags, pending = tuple(pending), []
            elif keyword in ("Examples", "Scenarios"):
                pending = []                         # tags before an Examples block are not a scenario's
            else:
                close()
                in_background = False
                if len(scenarios) >= _TITLE_FILE_CAP:
                    truncated = True
                    pending = []
                    continue
                current = _Open(keyword, title, number, (*rule_tags, *pending), line)
                pending = []
            continue
        step = _STEP.match(line)
        if step:
            body = f"{step.group(1)} {step.group(2)}".strip()
            if in_background:
                background.append(body)
            elif current is not None:
                current.steps.append(body)
    close()
    return Feature(name=name, tags=feature_tags, scenarios=tuple(scenarios), background=tuple(background),
                   language=language, truncated=truncated)


# ----------------------------------------------------------------------------------------- derivation
_IRREGULAR = {"seeing": "see", "having": "have", "being": "be", "getting": "get", "using": "use",
              "buying": "buy", "trying": "try", "denying": "deny", "staying": "stay", "putting": "put",
              "making": "make", "taking": "take", "giving": "give", "going": "go", "doing": "do"}
_E_STEMS = frozenset({"increas", "decreas", "remov", "brows", "chang", "updat", "us", "mak", "creat", "sav",
                      "writ", "navigat", "paginat", "receiv", "provid", "observ", "purchas", "manag", "mov",
                      "choos", "clos", "referenc", "enabl", "disabl", "displac", "leav", "believ", "apply"})
_NEGATIVE_LEAD = (
    (re.compile(r"^(?:Not being able to|Being unable to|Unable to|Not able to)\s+(.+)$", re.IGNORECASE), "cannot"),
    (re.compile(r"^(?:Cannot|Can't|Can not)\s+(.+)$", re.IGNORECASE), "cannot"),
    (re.compile(r"^Trying to\s+(.+)$", re.IGNORECASE), "cannot"),
    (re.compile(r"^Denying access to\s+(.+)$", re.IGNORECASE), "denied"),
)
_NOT_GERUND = re.compile(r"^Not\s+([A-Za-z]+ing)\s+(.+)$")
_GERUND = re.compile(r"^([A-Za-z]+ing)\b\s*(.*)$")
_LOWER_FIRST = frozenset({"the", "a", "an", "all", "only", "some", "my", "their"})


def _verb(gerund: str) -> str:
    word = gerund.lower()
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    stem = word[:-3]
    if len(stem) >= 4 and stem[-1] == stem[-2] and stem[-1] in "bdgmnprt":
        stem = stem[:-1]                            # getting -> get, stopping -> stop; adding stays add
    if stem in _E_STEMS and not stem.endswith("e"):
        stem += "e"
    return stem


def _bounded(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= _MAX_SCENARIO_CHARS:
        return text
    cut = text[:_MAX_SCENARIO_CHARS].rsplit(" ", 1)[0]
    return cut or text[:_MAX_SCENARIO_CHARS]


def derive(title: str) -> tuple[str, str]:
    """`(text, polarity)` for a scenario title; fixed rules, no model. See the module docstring."""
    title = _bounded(title).rstrip(".")
    for pattern, kind in _NEGATIVE_LEAD:
        hit = pattern.match(title)
        if hit:
            rest = hit.group(1)
            if kind == "denied":
                return f"User is denied access to {rest}", "must_not_exist"
            return f"User cannot {rest}", "must_not_exist"
    negated = _NOT_GERUND.match(title)
    if negated:
        return f"User cannot {_verb(negated.group(1))} {negated.group(2)}", "must_not_exist"
    gerund = _GERUND.match(title)
    if gerund and gerund.group(1).lower() not in ("during", "morning", "evening", "string", "thing"):
        rest = gerund.group(2)
        return f"User can {_verb(gerund.group(1))}{' ' + rest if rest else ''}", "must_exist"
    first = title.split(" ", 1)[0]
    body = title[:1].lower() + title[1:] if first.lower() in _LOWER_FIRST else title
    return f"User can see that {body}", "must_exist"


def ui_observable(own: tuple[str, ...], inherited: tuple[str, ...]) -> str:
    """`@ui` yes, `@no-ui` no; the scenario's own tag beats the Feature's; both or neither is unknown."""
    for tags in (own, inherited):
        has_ui, has_no = "@ui" in tags, "@no-ui" in tags
        if has_ui != has_no:
            return "yes" if has_ui else "no"
        if has_ui and has_no:
            return "unknown"
    return "unknown"


# --------------------------------------------------------------------------------------------- source
@dataclass(frozen=True)
class TreeListing:
    commit: str                      # resolved commit sha
    paths: tuple[str, ...]           # repo-relative blob paths below the listed directory


class FeatureSource(Protocol):
    """Where feature files come from: the GitHub API in production, a folder in tests."""

    def resolve(self, repo: str, ref: str) -> str: ...

    def list_features(self, repo: str, commit: str, directory: str) -> TreeListing: ...

    def read(self, repo: str, commit: str, path: str) -> str: ...


class SourceError(RuntimeError):
    """The source could not answer; `missing_binary` marks a machine without the `gh` CLI."""

    def __init__(self, message: str, *, missing_binary: bool = False):
        super().__init__(message)
        self.missing_binary = missing_binary


class GhSource:
    """`gh api` over the authenticated GitHub CLI: one commit lookup, one tree call, raw file reads."""

    def __init__(self, delay_s: float = _FETCH_DELAY_S, timeout_s: int = 60, user_agent: str | None = None):
        self.delay_s = delay_s
        self.timeout_s = timeout_s
        self.user_agent = user_agent or Settings().user_agent()
        self._reads = 0

    def _gh(self, *args: str) -> str:
        cmd = ["gh", "api", "-H", f"User-Agent: {self.user_agent}", *args]
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=self.timeout_s)
        except FileNotFoundError as exc:
            raise SourceError(f"the GitHub CLI `gh` is not installed: {exc}", missing_binary=True) from exc
        except subprocess.TimeoutExpired as exc:
            raise SourceError(f"gh api timed out after {self.timeout_s}s") from exc
        if done.returncode != 0:
            raise SourceError(f"gh api failed: {done.stderr.strip()[:300]}")
        return done.stdout

    def resolve(self, repo: str, ref: str) -> str:
        sha = self._gh(f"repos/{repo}/commits/{ref}", "--jq", ".sha").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise SourceError(f"{repo}@{ref} did not resolve to a commit sha")
        return sha

    def list_features(self, repo: str, commit: str, directory: str) -> TreeListing:
        spec = f"{commit}:{directory}" if directory else commit
        raw = self._gh(f"repos/{repo}/git/trees/{spec}?recursive=1")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise SourceError("the tree response was not JSON") from exc
        if data.get("truncated"):
            raise SourceError(f"the tree of {repo}@{commit[:12]}:{directory} is truncated by the API")
        base = directory.strip("/")
        paths = sorted(f"{base}/{e['path']}" if base else e["path"]
                       for e in data.get("tree", []) if e.get("type") == "blob")
        return TreeListing(commit, tuple(paths))

    def read(self, repo: str, commit: str, path: str) -> str:
        if self._reads and self.delay_s:
            time.sleep(self.delay_s)
        self._reads += 1
        return self._gh("-H", "Accept: application/vnd.github.raw", f"repos/{repo}/contents/{path}?ref={commit}")


# ---------------------------------------------------------------------------------------------- ingest
def _area_of(path: str, roots: list[str]) -> str:
    """Last directory name of the configured path that contains the file (`features/shop/cart` -> cart)."""
    best = max((r for r in roots if path.startswith(r.rstrip("/") + "/")), key=len, default="")
    return PurePosixPath(best).name if best else PurePosixPath(path).parent.name


def _common_dir(paths: list[str]) -> str:
    """Deepest directory that contains every configured path (a `.feature` path counts as its folder)."""
    dirs = [PurePosixPath(p.strip("/")) for p in paths]
    parts = [(d.parent if d.suffix == ".feature" else d).parts for d in dirs]
    common: list[str] = []
    for column in zip(*parts, strict=False):
        if len(set(column)) != 1:
            break
        common.append(column[0])
    return "/".join(common)


def _in_scope(path: str, roots: list[str]) -> bool:
    return path.endswith(".feature") and any(path.startswith(r.strip("/") + "/") or path == r.strip("/")
                                             for r in roots)


def _tally(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _write_json(run: RunContext, name: str, obj: Any) -> None:
    path: Path = run.path("spec", name)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _stop(run: RunContext, code: FailureCode, repo: str | None, reason: str) -> list[Requirement]:
    """Refused or failed: no requirements, and nothing left over from an earlier run in this directory."""
    run.failures.record(code, reason, repo=repo)
    _write_json(run, "requirements.json", [])
    _write_json(run, "pages.json", [])
    _write_json(run, "stats.json", {"status": "failed", "complete": False, "reason": reason,
                                    "failure_code": str(code), "source": repo, "requirements_kept": 0})
    run.update_manifest(spec={"repo": repo, "status": "failed", "reason": reason})
    return []


def _requirement(cfg: TargetConfig, feature: Feature, scenario: Scenario, *, path: str, area: str,
                 occurrence: int, source_url: str, doc_version: str, spec_sha: str) -> Requirement:
    text, polarity = derive(scenario.title)
    steps = (*feature.background, *scenario.steps)
    login = bool(_LOGGED_IN.search(scenario.title) or any(_LOGGED_IN.search(s) for s in steps))
    tags = sorted({*feature.tags, *scenario.tags, f"area:{area}"})
    ident = stable_id("req", cfg.name, scenario.title + path + (f"\x1f#{occurrence}" if occurrence else ""))
    return Requirement.model_validate({
        "id": ident, "target": cfg.name, "text": text, "quote": _bounded(scenario.raw),
        "source_url": f"{source_url}#L{scenario.line}", "section": feature.name or PurePosixPath(path).stem,
        "doc_version": doc_version, "edition": cfg.spec.edition_default, "polarity": polarity,
        "ui_observable": ui_observable(scenario.tags, feature.tags), "requires_login": login,
        "source_kind": "gherkin", "tags": tags, "spec_sha": spec_sha})


def ingest_gherkin(run: RunContext, cfg: TargetConfig, *, source: FeatureSource | None = None) -> list[Requirement]:
    """Requirements from `cfg.spec.paths` of `cfg.spec.repo` at `cfg.spec.ref`; artifacts under <run>/spec/.

    `source` replaces the GitHub API (tests, recorded runs). Never raises for a fetch problem: a failure row
    and empty artifacts say what happened.
    """
    spec = cfg.spec
    if not spec.repo or not spec.paths:
        return _stop(run, FailureCode.CONFIG_INVALID, spec.repo,
                     "spec.kind=gherkin needs spec.repo and at least one entry in spec.paths")
    repo, ref = spec.repo, spec.ref or "HEAD"
    roots = sorted({p.strip("/") for p in spec.paths})
    source = source or GhSource()
    try:
        with run.tracer.span("http_fetch", "github.features", host="api.github.com", repo=repo, ref=ref) as sp:
            commit = source.resolve(repo, ref)
            listing = source.list_features(repo, commit, _common_dir(roots))
            wanted = [p for p in listing.paths if _in_scope(p, roots)]
            texts = {p: source.read(repo, commit, p) for p in wanted}
            sp.set(commit=commit[:12], features=len(wanted))
    except SourceError as exc:
        code = FailureCode.DEP_MISSING if exc.missing_binary else FailureCode.SPEC_FETCH_FAILED
        return _stop(run, code, repo, str(exc))
    if not wanted:
        return _stop(run, FailureCode.SPEC_FETCH_FAILED, repo,
                     f"no .feature files under {roots} at {repo}@{ref} ({commit[:12]})")

    spec_sha = sha_full("".join(f"{p}\x00{texts[p]}\x00" for p in wanted))
    doc_version = f"{ref}@{commit[:12]}"
    reqs: list[Requirement] = []
    pages: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    for path in wanted:
        feature = parse_feature(texts[path])
        area = _area_of(path, roots)
        url = f"https://github.com/{repo}/blob/{commit}/{path}"
        seen: Counter[str] = Counter()
        kept = 0
        for scenario in feature.scenarios:
            if not scenario.title:
                stats["scenarios_untitled"] += 1
                continue
            occurrence = seen[scenario.title]
            seen[scenario.title] += 1
            reqs.append(_requirement(cfg, feature, scenario, path=path, area=area, occurrence=occurrence,
                                     source_url=url, doc_version=doc_version, spec_sha=spec_sha))
            kept += 1
        stats["features_truncated"] += feature.truncated
        stats["features_non_english"] += feature.language != "en"
        pages.append({"index": len(pages), "title": feature.name, "url": url, "path": path, "area": area,
                      "chars": len(texts[path]), "included": True, "reason": "in spec.paths",
                      "scenarios": len(feature.scenarios), "kept": kept, "language": feature.language})

    in_scope = {"yes": 0, "unknown": 1, "no": 2}
    over = max(len(reqs) - spec.max_requirements, 0)
    if over:
        order = {r.id: i for i, r in enumerate(reqs)}
        keep = sorted(sorted(reqs, key=lambda r: (in_scope[r.ui_observable], order[r.id]))[:spec.max_requirements],
                      key=lambda r: order[r.id])
        reqs = keep
    span = run.tracer.current_span_id
    evidence = {r.id: run.evidence.add("spec.quote", r.id, r.quote, span_id=span, source_url=r.source_url,
                                       spec_sha=r.spec_sha) for r in reqs}
    fetch_evidence = run.evidence.add("spec.fetch", f"{repo}@{commit}", f"gherkin {','.join(roots)} ({ref})",
                                      span_id=span, features=len(wanted))
    tag_counts = Counter(t for r in reqs for t in r.tags if t.startswith("@"))
    _write_json(run, "requirements.json", [r.model_dump(mode="json") for r in reqs])
    _write_json(run, "pages.json", pages)
    _write_json(run, "stats.json", {
        "status": "ok", "complete": not stats["features_truncated"], "target": cfg.name,
        "source_url": f"https://github.com/{repo}", "repo": repo, "ref": ref, "commit": commit,
        "sha256": spec_sha, "doc_version": doc_version, "fetch_evidence_id": fetch_evidence,
        "pages_seen": len(pages), "pages_kept": len(pages), "pages_excluded": 0,
        "scenarios_seen": sum(p["scenarios"] for p in pages), "requirements_kept": len(reqs),
        "requirements_dropped": over, "dropped_by_reason": {"over_cap": over} if over else {},
        "scenarios_untitled": stats["scenarios_untitled"], "features_truncated": stats["features_truncated"],
        "features_non_english": stats["features_non_english"],
        "by_edition": _tally(r.edition for r in reqs), "by_ui_observable": _tally(r.ui_observable for r in reqs),
        "by_polarity": _tally(r.polarity for r in reqs),
        "by_area": _tally(next((t[5:] for t in r.tags if t.startswith("area:")), "") for r in reqs),
        "by_tag": dict(sorted(tag_counts.items())),
        "requires_login": sum(1 for r in reqs if r.requires_login),
        "evidence_by_requirement": dict(sorted(evidence.items()))})
    run.update_manifest(spec={"repo": repo, "ref": ref, "commit": commit, "sha256": spec_sha,
                              "doc_version": doc_version, "status": "ok"})
    return reqs
