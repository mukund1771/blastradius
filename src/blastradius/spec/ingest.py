"""Spec ingest: fetch, scope, extract, verify, dedupe, cap, write (D07, D30; cases 21-25, 26, 27).

    run_ingest(run, cfg, llm) -> list[Requirement]

Artifacts under <run>/spec/: requirements.json, pages.json (every page, included or excluded and why) and
stats.json (status, counts, sha256, fetched_at, doc_version, stale flag and why it may be unknown). Each kept
requirement also gets a `spec.quote` evidence row (ref = requirement id, summary = the quote); stats.json maps
requirement id to that evidence id. Re-running is idempotent: ids are content hashes, LLM calls are cached by
content, evidence rows are deduplicated.

Every refused, failed or invalid-config run overwrites requirements.json and pages.json with [] and writes
stats.json with its status, so a re-ingest can never leave the previous run's requirements behind. All
artifact writes are atomic (tmp + rename).

Downstream trust: `Requirement.text`, `.section` and `.quote` all come from an untrusted page (text via the
model). They are shaped and bounded here, but the link and reason stages must still wrap them with
`safety.untrusted` before any prompt.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..failures import FailureCode
from ..ids import stable_id
from ..llm import LLM, LLMBudgetExceeded, LLMError, LLMOfflineMiss
from ..models import Requirement
from ..redact import redact_text
from ..run import RunContext
from ..targets import TargetConfig
from .extract import Chunk, ExtractedItem, chunk_page, extract_chunk
from .fetch import (
    FetchedSpec,
    Page,
    PageDecision,
    SpecFetchError,
    doc_version,
    fetch_spec,
    is_stale,
    select_pages,
    split_pages,
)
from .normalize import normalize, quote_key
from .verify import clean_text, gate_polarity, section_of, supported_edition, verify_quote

_UI_RANK = {"yes": 0, "unknown": 1, "no": 2}
_WHY = {"too_short": "quote too short to anchor a requirement",
        "not_in_source": "quote is not a substring of the source",
        "code_marker": "quote contains the code-sample marker",
        "mid_word": "quote starts or ends inside a word",
        "spans_blocks": "quote joins separate blocks of the page",
        "multi_sentence": "quote spans more than one sentence"}
_STATUS = {FailureCode.ACCESS_BLOCKED: "refused", FailureCode.SPEC_FETCH_FAILED: "failed",
           FailureCode.CONFIG_INVALID: "invalid_config"}


@dataclass
class _Candidate:
    order: tuple[int, int, int]        # (page index, chunk index, item index): document order
    url: str
    quote: str                         # normalised
    text: str
    section: str
    edition: str
    edition_defaulted: bool
    polarity: str
    ui_observable: str
    requires_login: bool
    tags: list[str] = field(default_factory=list)    # polarity:* / edition:multi; also/conflict added later

    @property
    def page_idx(self) -> int:
        return self.order[0]

    @property
    def explicit_edition(self) -> bool:
        """The page named an edition (as opposed to the target default or nothing)."""
        return not self.edition_defaulted and self.edition != "unknown"


def run_ingest(run: RunContext, cfg: TargetConfig, llm: LLM, *, app_version: str | None = None,
               client: httpx.Client | None = None) -> list[Requirement]:
    """Requirements from the target's spec.

    `app_version` (else run.manifest["app_version"], else crawl/summary.json) enables the SPEC_STALE check.
    `client` lets tests and recorded runs supply the HTTP transport. Opens its own `ingest` stage span when
    the caller (the CLI) has none active.
    """
    with _stage(run):
        if cfg.spec.kind == "gherkin":
            return _ingest_gherkin(run, cfg)
        return _ingest_docs(run, cfg, llm, app_version, client)


@contextlib.contextmanager
def _stage(run: RunContext) -> Iterator[None]:
    if run.tracer.current_span_id is not None:
        yield
    else:
        with run.stage("ingest"):
            yield


def _ingest_gherkin(run: RunContext, cfg: TargetConfig) -> list[Requirement]:
    try:
        module = importlib.import_module("blastradius.spec.gherkin")
        ingest_gherkin = module.ingest_gherkin
    except (ImportError, AttributeError) as exc:
        run.failures.record(FailureCode.CONFIG_INVALID, "spec.kind=gherkin needs blastradius.spec.gherkin"
                            f".ingest_gherkin ({type(exc).__name__})")
        return _stop(run, FailureCode.CONFIG_INVALID, None, "gherkin ingester unavailable")
    return list(ingest_gherkin(run, cfg))


def _ingest_docs(run: RunContext, cfg: TargetConfig, llm: LLM, app_version: str | None,
                 client: httpx.Client | None) -> list[Requirement]:
    spec = cfg.spec
    if spec.kind != "llms_full" or not spec.url:
        reason = f"docs ingest needs spec.kind=llms_full and spec.url (got kind={spec.kind}, url={spec.url})"
        run.failures.record(FailureCode.CONFIG_INVALID, reason)
        return _stop(run, FailureCode.CONFIG_INVALID, spec.url, reason)
    try:
        for pattern in (*spec.include_pages, *spec.exclude_pages):
            re.compile(pattern)
    except re.error as exc:
        reason = f"invalid include/exclude pattern: {exc}"
        run.failures.record(FailureCode.CONFIG_INVALID, reason)
        return _stop(run, FailureCode.CONFIG_INVALID, spec.url, reason)
    try:
        fetched = fetch_spec(run, spec.url, client=client, allow_hosts=cfg.crawl.egress_allow)
    except SpecFetchError as exc:
        return _stop(run, exc.code, spec.url, str(exc), exc.info)
    pages = split_pages(fetched.text)
    if not pages:
        reason = "spec response has no `# title` + `Source:` pages"
        run.failures.record(FailureCode.SPEC_FETCH_FAILED, reason, url=spec.url,
                            content_type=fetched.content_type)
        return _stop(run, FailureCode.SPEC_FETCH_FAILED, spec.url, reason, _fetch_info(fetched))
    decisions = select_pages(pages, spec.include_pages, spec.exclude_pages)

    doc_ver = doc_version(pages)
    app_ver, app_source = _app_version(run, app_version)
    stale, stale_reason = _staleness(doc_ver, app_ver)
    if stale:
        run.failures.record(FailureCode.SPEC_STALE, f"docs newest version {doc_ver} predates app {app_ver}",
                            doc_version=doc_ver, app_version=app_ver)

    stats: Counter[str] = Counter()
    per_page: dict[int, Counter[str]] = {}
    cands = _extract_all(run, cfg, llm, decisions, stats, per_page)
    unique = _dedupe(run, cfg, cands, stats)
    kept, stats["dropped_over_cap"] = _rank_and_cap(unique, spec.max_requirements, spec.edition_default)

    reqs = [_to_requirement(cfg, c, fetched.sha256, doc_ver) for c in kept]
    span = run.tracer.current_span_id
    evidence = {r.id: run.evidence.add("spec.quote", r.id, r.quote, span_id=span, source_url=r.source_url,
                                       spec_sha=r.spec_sha) for r in reqs}
    fetch_evidence = run.evidence.add("spec.fetch", fetched.sha256, fetched.final_url or fetched.url,
                                      span_id=span, http_status=fetched.status, fetched_at=fetched.fetched_at)
    _write_outputs(run, cfg, fetched, decisions, per_page, kept, reqs, stats, evidence, {
        "doc_version": doc_ver, "app_version": app_ver, "app_version_source": app_source,
        "stale": stale, "stale_reason": stale_reason, "fetch_evidence_id": fetch_evidence})
    run.update_manifest(spec={"url": fetched.url, "sha256": fetched.sha256, "fetched_at": fetched.fetched_at,
                              "doc_version": doc_ver, "status": "ok"})
    return reqs


# ------------------------------------------------------------------------------------------------ steps
def _app_version(run: RunContext, explicit: str | None) -> tuple[str | None, str | None]:
    """(version, where it came from): parameter, then run.manifest, then crawl/summary.json."""
    if explicit:
        return explicit, "parameter"
    if isinstance(run.manifest.get("app_version"), str) and run.manifest["app_version"]:
        return run.manifest["app_version"], "manifest"
    try:
        summary = json.loads((run.run_dir / "crawl" / "summary.json").read_text())
    except (OSError, ValueError):
        return None, None
    version = summary.get("app_version") if isinstance(summary, dict) else None
    return (version, "crawl_summary") if isinstance(version, str) and version else (None, None)


def _staleness(doc_ver: str | None, app_ver: str | None) -> tuple[bool | None, str | None]:
    """(stale, why it is unknown). Unknown is never reported as 'not stale' (case 21)."""
    if not app_ver:
        return None, "no_app_version"
    if not doc_ver:
        return None, "no_doc_version"
    stale = is_stale(doc_ver, app_ver)
    return stale, None if stale is not None else "incomparable_versions"


def _fetch_info(fetched: FetchedSpec) -> dict[str, Any]:
    return {"sha256": fetched.sha256, "http_status": fetched.status, "content_type": fetched.content_type,
            "final_url": fetched.final_url, "access_verdict": fetched.verdict}


def _stop(run: RunContext, code: FailureCode, url: str | None, reason: str,
          info: dict[str, Any] | None = None) -> list[Requirement]:
    """Refused, failed or invalid: no requirements, and no leftovers from an earlier run in this dir."""
    status = _STATUS.get(code, "failed")
    _write_json(run, "requirements.json", [])
    _write_json(run, "pages.json", [])
    _write_json(run, "stats.json", {
        "status": status, "complete": False, "source_url": url, "reason": reason, "failure_code": str(code),
        "sha256": None, "http_status": None, "content_type": None, "final_url": None, "access_verdict": None,
        **(info or {}), "requirements_kept": 0})
    run.update_manifest(spec={"url": url, "status": status, "reason": reason})
    return []


def _write_json(run: RunContext, name: str, obj: Any) -> None:
    """Atomic: a crash leaves the previous file, never a truncated one."""
    path: Path = run.path("spec", name)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _extract_all(run: RunContext, cfg: TargetConfig, llm: LLM, decisions: list[PageDecision],
                 stats: Counter[str], per_page: dict[int, Counter[str]]) -> list[_Candidate]:
    cands: list[_Candidate] = []
    budget_spent = False
    for page_idx, d in enumerate(decisions):
        if not d.included:
            continue
        page_flat = normalize(d.page.body)
        chunks = chunk_page(d.page.title, d.page.body, cfg.spec.page_char_budget)
        counts = per_page.setdefault(page_idx, Counter())
        counts["chunks"] = len(chunks)
        for chunk in chunks:
            if budget_spent:
                stats["chunks_skipped"] += 1
                continue
            try:
                items = extract_chunk(llm, cfg.display_name, d.page.url, chunk)
            except LLMBudgetExceeded:
                stats["chunks_skipped"] += 1
                budget_spent = True        # every further call would trip the same breaker
                continue
            except LLMOfflineMiss:
                stats["chunks_skipped"] += 1     # a miss costs nothing and later chunks may be cached
                continue
            except LLMError:
                stats["chunks_failed"] += 1      # the LLM layer recorded the failure
                continue
            except Exception as exc:  # noqa: BLE001 - one bad chunk must not lose the other pages
                stats["chunks_failed"] += 1
                code = (FailureCode.LLM_SCHEMA_VIOLATION if isinstance(exc, ValueError)    # incl. pydantic's
                        else FailureCode.LLM_API_ERROR)
                run.failures.record(code, f"extract_requirements: unexpected {type(exc).__name__}",
                                    url=d.page.url, chunk=chunk.index)
                continue
            stats["llm_calls"] += 1
            with run.tracer.span("verifier_check", "spec_quote", url=d.page.url, chunk=chunk.index) as sp:
                got = _verify_items(run, cfg, d.page, chunk, items, page_idx, page_flat, stats)
                sp.set(items=len(items), accepted=len(got))
            counts["extracted"] += len(items)
            cands.extend(got)
    return cands


def _verify_items(run: RunContext, cfg: TargetConfig, page: Page, chunk: Chunk, items: list[ExtractedItem],
                  page_idx: int, page_flat: str, stats: Counter[str]) -> list[_Candidate]:
    """The deterministic half of D07: drop what the page does not back; never trust a model section."""
    out: list[_Candidate] = []
    default = cfg.spec.edition_default
    for item_idx, item in enumerate(items):
        stats["extracted"] += 1
        hit, why = verify_quote(item.quote, chunk, page_flat)
        if hit is None:
            stats[f"dropped_quote_{why}"] += 1
            if why != "context_repeat":      # a benign repeat of text another chunk already offered
                run.failures.record(FailureCode.QUOTE_NOT_IN_SOURCE, _WHY[why], url=page.url,
                                    quote=redact_text(normalize(item.quote)[:400], truncate=False)[:160])
            continue
        text, why = clean_text(item.text)
        if text is None:
            stats[f"dropped_{why}"] += 1
            continue
        eff = item.edition if item.edition not in ("unknown", "all") else default
        polarity, tag = gate_polarity(item.polarity, eff, hit, chunk)
        stats["polarity_downgraded"] += polarity != item.polarity
        stats["polarity_flagged"] += tag == "polarity:review"
        edition, ambiguous = supported_edition(item.edition, polarity, hit, chunk)
        stats["edition_downgraded"] += edition != item.edition
        defaulted = edition == "unknown" and default != "unknown"
        out.append(_Candidate(
            order=(page_idx, chunk.index, item_idx), url=page.url, quote=hit.quote, text=text,
            section=section_of(hit), edition=default if defaulted else edition, edition_defaulted=defaulted,
            polarity=polarity, ui_observable=item.ui_observable, requires_login=item.requires_login,
            tags=[t for t in (tag, "edition:multi" if ambiguous else None) if t]))
    return out


def _dedupe(run: RunContext, cfg: TargetConfig, cands: list[_Candidate],
            stats: Counter[str]) -> list[_Candidate]:
    """One requirement per normalised quote (the id is its hash; case 26).

    The candidate with an explicit edition, then an explicit negative, then the earliest in document order
    wins. Same page: a sibling merge (the model split one sentence). Another page: a duplicate, listed as an
    `also:` url. Disagreement between the candidates is tagged `conflict:*` and recorded as NEEDS_HUMAN.
    """
    groups: dict[str, list[_Candidate]] = {}
    for c in cands:
        groups.setdefault(quote_key(c.quote), []).append(c)
    out = []
    for group in groups.values():
        best = min(group, key=lambda c: (not c.explicit_edition, c.polarity != "must_not_exist", c.order))
        extra = [c for c in group if c is not best]
        stats["dropped_sibling_merge"] += sum(c.url == best.url for c in extra)
        stats["dropped_duplicate"] += sum(c.url != best.url for c in extra)
        tags = set(best.tags) | {f"also:{c.url}" for c in extra if c.url != best.url}
        editions = {c.edition for c in group if c.explicit_edition}
        polarities = {c.polarity for c in group}
        for name, values in (("edition", editions), ("polarity", polarities)):
            if len(values) > 1:
                tags.add(f"conflict:{name}=" + "|".join(sorted(values)))
                stats["conflicts"] += 1
                run.failures.record(FailureCode.NEEDS_HUMAN, f"spec statement disagrees on {name} "
                                    f"({', '.join(sorted(values))}) across pages or sentences",
                                    requirement=stable_id("req", cfg.name, best.quote), url=best.url)
        best.tags = sorted(tags)
        out.append(best)
    return sorted(out, key=lambda c: c.order)


def _rank_and_cap(cands: list[_Candidate], cap: int, default_edition: str) -> tuple[list[_Candidate], int]:
    """At most `cap`: UI-observable first, then in-scope edition, then document order (deterministic).

    Returns the kept candidates in document order and how many were dropped.
    """
    def key(c: _Candidate) -> tuple[int, int, tuple[int, int, int]]:
        in_scope = c.edition in (default_edition, "all")
        return (_UI_RANK[c.ui_observable], 0 if in_scope else 1, c.order)

    kept = sorted(cands, key=key)[:cap]
    return sorted(kept, key=lambda c: c.order), len(cands) - len(kept)


def _to_requirement(cfg: TargetConfig, c: _Candidate, spec_sha: str, doc_ver: str | None) -> Requirement:
    tags = sorted({*c.tags, *(["edition:default"] if c.edition_defaulted else [])})
    return Requirement.model_validate({
        "id": stable_id("req", cfg.name, c.quote), "target": cfg.name, "text": c.text, "quote": c.quote,
        "source_url": c.url, "section": c.section, "doc_version": doc_ver, "edition": c.edition,
        "polarity": c.polarity, "ui_observable": c.ui_observable, "requires_login": c.requires_login,
        "source_kind": "docs", "tags": tags, "spec_sha": spec_sha})


def _write_outputs(run: RunContext, cfg: TargetConfig, fetched: FetchedSpec, decisions: list[PageDecision],
                   per_page: dict[int, Counter[str]], kept: list[_Candidate], reqs: list[Requirement],
                   stats: Counter[str], evidence: dict[str, str], versions: dict[str, Any]) -> None:
    kept_by_page = Counter(c.page_idx for c in kept)
    pages_json = [{
        "index": i, "title": d.page.title, "url": d.page.url, "chars": len(d.page.body),
        "included": d.included, "reason": d.reason,
        "chunks": per_page.get(i, Counter())["chunks"], "extracted": per_page.get(i, Counter())["extracted"],
        "kept": kept_by_page[i],
    } for i, d in enumerate(decisions)]
    dropped = {k.removeprefix("dropped_"): v for k, v in sorted(stats.items())
               if k.startswith("dropped_") and v}
    pages_kept = sum(1 for d in decisions if d.included)

    def tally(values: list[str]) -> dict[str, int]:
        return dict(sorted(Counter(values).items()))

    _write_json(run, "requirements.json", [r.model_dump(mode="json") for r in reqs])
    _write_json(run, "pages.json", pages_json)
    _write_json(run, "stats.json", {
        "status": "ok", "complete": not (stats["chunks_failed"] or stats["chunks_skipped"]),
        "target": cfg.name, "source_url": fetched.url, "final_url": fetched.final_url,
        "http_status": fetched.status, "content_type": fetched.content_type,
        "access_verdict": fetched.verdict, "bytes": fetched.size, "sha256": fetched.sha256,
        "fetched_at": fetched.fetched_at, **versions,
        "pages_seen": len(decisions), "pages_kept": pages_kept, "pages_excluded": len(decisions) - pages_kept,
        "chunks": sum(c["chunks"] for c in per_page.values()),
        "llm_calls": stats["llm_calls"], "chunks_failed": stats["chunks_failed"],
        "chunks_skipped": stats["chunks_skipped"], "requirements_extracted": stats["extracted"],
        "requirements_kept": len(reqs), "requirements_dropped": sum(dropped.values()),
        "dropped_by_reason": dropped, "edition_downgraded": stats["edition_downgraded"],
        "polarity_downgraded": stats["polarity_downgraded"], "polarity_flagged": stats["polarity_flagged"],
        "conflicts": stats["conflicts"],
        "by_edition": tally([r.edition for r in reqs]),
        "by_ui_observable": tally([r.ui_observable for r in reqs]),
        "by_polarity": tally([r.polarity for r in reqs]),
        "requires_login": sum(1 for r in reqs if r.requires_login),
        "evidence_by_requirement": dict(sorted(evidence.items()))})
