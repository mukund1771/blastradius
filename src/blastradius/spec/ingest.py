"""Spec ingest: fetch, scope, extract, verify, dedupe, cap, write (D30, D07; cases 21-25, 26, 27).

    run_ingest(run, cfg, llm) -> list[Requirement]

Artifacts under <run>/spec/: requirements.json, pages.json (every page, included or excluded and why) and
stats.json (counts, sha256, fetched_at, doc_version, stale flag). Each kept requirement also gets a
`spec.quote` evidence row (ref = requirement id, summary = the quote). Re-running is idempotent: ids are
content hashes, LLM calls are cached by content, evidence rows are deduplicated.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..failures import FailureCode
from ..ids import stable_id
from ..llm import LLM, LLMBudgetExceeded, LLMError, LLMOfflineMiss
from ..models import Requirement
from ..run import RunContext
from ..targets import TargetConfig
from .extract import (
    Chunk,
    ExtractedItem,
    chunk_page,
    extract_chunk,
    quote_verdict,
    section_for,
    supported_edition,
)
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

_UI_RANK = {"yes": 0, "unknown": 1, "no": 2}
_WHY = {"too_short": "quote too short to anchor a requirement",
        "not_in_source": "quote is not a substring of the source"}


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
    also: list[str] = field(default_factory=list)


def run_ingest(run: RunContext, cfg: TargetConfig, llm: LLM, *, app_version: str | None = None,
               client: httpx.Client | None = None) -> list[Requirement]:
    """Requirements from the target's docs.

    `app_version` (else run.manifest["app_version"], else crawl/summary.json) enables the SPEC_STALE check.
    `client` lets tests and recorded runs supply the HTTP transport.
    """
    spec = cfg.spec
    if spec.kind != "llms_full" or not spec.url:
        run.failures.record(FailureCode.CONFIG_INVALID, "docs ingest needs spec.kind=llms_full and spec.url "
                            f"(got kind={spec.kind}, url={spec.url})")
        return []
    try:
        fetched = fetch_spec(run, spec.url, client=client)
    except SpecFetchError as exc:
        return _refuse(run, spec.url, str(exc))
    pages = split_pages(fetched.text)
    if not pages:
        run.failures.record(FailureCode.ACCESS_SUSPECT, "spec response has no `# title` + `Source:` pages",
                            url=spec.url, content_type=fetched.content_type)
        return _refuse(run, spec.url, "no pages found in the response")
    try:
        decisions = select_pages(pages, spec.include_pages, spec.exclude_pages)
    except re.error as exc:
        run.failures.record(FailureCode.CONFIG_INVALID, f"invalid include/exclude pattern: {exc}")
        return []

    doc_ver, app_ver = doc_version(pages), _app_version(run, app_version)
    stale = is_stale(doc_ver, app_ver)
    if stale:
        run.failures.record(FailureCode.SPEC_STALE, f"docs newest version {doc_ver} predates app {app_ver}",
                            doc_version=doc_ver, app_version=app_ver)

    stats: Counter[str] = Counter()
    per_page: dict[str, Counter[str]] = {}
    cands = _extract_all(run, cfg, llm, decisions, stats, per_page)
    unique = _dedupe(cands, stats)
    kept, stats["dropped_over_cap"] = _rank_and_cap(unique, spec.max_requirements, spec.edition_default)

    reqs = [_to_requirement(cfg, c, fetched.sha256, doc_ver) for c in kept]
    for req in reqs:
        run.evidence.add("spec.quote", req.id, req.quote, span_id=run.tracer.current_span_id,
                         source_url=req.source_url, spec_sha=req.spec_sha)
    _write_outputs(run, cfg, fetched, decisions, per_page, reqs, stats, (doc_ver, app_ver, stale))
    run.update_manifest(spec={"url": fetched.url, "sha256": fetched.sha256, "fetched_at": fetched.fetched_at,
                              "doc_version": doc_ver})
    return reqs


# ------------------------------------------------------------------------------------------------ steps
def _app_version(run: RunContext, explicit: str | None) -> str | None:
    """Parameter, then run.manifest, then the crawl summary written by the crawl stage."""
    if explicit:
        return explicit
    if run.manifest.get("app_version"):
        return str(run.manifest["app_version"])
    try:
        summary = json.loads((run.run_dir / "crawl" / "summary.json").read_text())
    except (OSError, ValueError):
        return None
    return summary.get("app_version") or None


def _refuse(run: RunContext, url: str, reason: str) -> list[Requirement]:
    _write_json(run, "stats.json", {"status": "refused", "source_url": url, "reason": reason})
    return []


def _write_json(run: RunContext, name: str, obj: Any) -> None:
    run.path("spec", name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _extract_all(run: RunContext, cfg: TargetConfig, llm: LLM, decisions: list[PageDecision],
                 stats: Counter[str], per_page: dict[str, Counter[str]]) -> list[_Candidate]:
    cands: list[_Candidate] = []
    stop = False
    for page_idx, d in enumerate(decisions):
        if not d.included:
            continue
        chunks = chunk_page(d.page.title, d.page.body, cfg.spec.page_char_budget)
        counts = per_page.setdefault(d.page.url, Counter())
        counts["chunks"] = len(chunks)
        for chunk in chunks:
            if stop:
                stats["chunks_skipped"] += 1
                continue
            try:
                items = extract_chunk(llm, cfg.display_name, d.page.url, chunk)
            except (LLMBudgetExceeded, LLMOfflineMiss):
                stats["chunks_skipped"] += 1
                stop = True        # every further call would fail the same way; the LLM layer logged it
                continue
            except LLMError:
                stats["chunks_failed"] += 1
                continue
            stats["llm_calls"] += 1
            with run.tracer.span("verifier_check", "spec_quote", url=d.page.url, chunk=chunk.index) as sp:
                got = _verify_items(run, cfg, d.page, chunk, items, page_idx, stats)
                sp.set(items=len(items), accepted=len(got))
            counts["extracted"] += len(items)
            cands.extend(got)
    return cands


def _verify_items(run: RunContext, cfg: TargetConfig, page: Page, chunk: Chunk, items: list[ExtractedItem],
                  page_idx: int, stats: Counter[str]) -> list[_Candidate]:
    """The deterministic half of D07: drop what the page does not back; never trust a model section."""
    out: list[_Candidate] = []
    default = cfg.spec.edition_default
    for item_idx, item in enumerate(items):
        stats["extracted"] += 1
        quote, why = quote_verdict(item.quote, chunk.flat)
        text = normalize(item.text)
        if quote is None:
            stats[f"dropped_quote_{why}"] += 1
            run.failures.record(FailureCode.QUOTE_NOT_IN_SOURCE, _WHY[why], url=page.url,
                                quote=normalize(item.quote)[:160])
            continue
        if not text:
            stats["dropped_empty_text"] += 1
            continue
        edition = supported_edition(item.edition, quote, chunk)
        stats["edition_downgraded"] += edition != item.edition
        defaulted = edition == "unknown" and default != "unknown"
        out.append(_Candidate(
            order=(page_idx, chunk.index, item_idx), url=page.url, quote=quote, text=text,
            section=section_for(chunk, quote, page.title), edition=default if defaulted else edition,
            edition_defaulted=defaulted, polarity=item.polarity, ui_observable=item.ui_observable,
            requires_login=item.requires_login))
    return out


def _dedupe(cands: list[_Candidate], stats: Counter[str]) -> list[_Candidate]:
    """Same normalised quote: keep the first in document order, remember the other urls (case 26)."""
    first: dict[str, _Candidate] = {}
    for c in cands:
        keep = first.setdefault(quote_key(c.quote), c)
        if keep is c:
            continue
        stats["dropped_duplicate"] += 1
        if c.url != keep.url and c.url not in keep.also:
            keep.also.append(c.url)
    return list(first.values())


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
    tags = [f"also:{u}" for u in c.also] + (["edition:default"] if c.edition_defaulted else [])
    return Requirement.model_validate({
        "id": stable_id("req", cfg.name, c.quote), "target": cfg.name, "text": c.text, "quote": c.quote,
        "source_url": c.url, "section": c.section, "doc_version": doc_ver, "edition": c.edition,
        "polarity": c.polarity, "ui_observable": c.ui_observable, "requires_login": c.requires_login,
        "source_kind": "docs", "tags": tags, "spec_sha": spec_sha})


def _write_outputs(run: RunContext, cfg: TargetConfig, fetched: FetchedSpec, decisions: list[PageDecision],
                   per_page: dict[str, Counter[str]], reqs: list[Requirement], stats: Counter[str],
                   versions: tuple[str | None, str | None, bool | None]) -> None:
    doc_ver, app_ver, stale = versions
    kept_by_page = Counter(r.source_url for r in reqs)
    pages_json = [{
        "title": d.page.title, "url": d.page.url, "chars": len(d.page.body), "included": d.included,
        "reason": d.reason, "chunks": per_page.get(d.page.url, Counter())["chunks"],
        "extracted": per_page.get(d.page.url, Counter())["extracted"], "kept": kept_by_page[d.page.url],
    } for d in decisions]
    dropped = {k.removeprefix("dropped_"): v for k, v in sorted(stats.items())
               if k.startswith("dropped_") and v}
    pages_kept = sum(1 for d in decisions if d.included)
    _write_json(run, "requirements.json", [r.model_dump(mode="json") for r in reqs])
    _write_json(run, "pages.json", pages_json)
    _write_json(run, "stats.json", {
        "status": "ok", "complete": not (stats["chunks_failed"] or stats["chunks_skipped"]),
        "target": cfg.name, "source_url": fetched.url, "http_status": fetched.status, "bytes": fetched.size,
        "sha256": fetched.sha256, "fetched_at": fetched.fetched_at, "doc_version": doc_ver,
        "app_version": app_ver, "stale": stale, "pages_seen": len(decisions), "pages_kept": pages_kept,
        "pages_excluded": len(decisions) - pages_kept, "chunks": sum(c["chunks"] for c in per_page.values()),
        "llm_calls": stats["llm_calls"], "chunks_failed": stats["chunks_failed"],
        "chunks_skipped": stats["chunks_skipped"], "requirements_extracted": stats["extracted"],
        "requirements_kept": len(reqs), "requirements_dropped": sum(dropped.values()),
        "dropped_by_reason": dropped, "edition_downgraded": stats["edition_downgraded"],
        "by_edition": dict(Counter(r.edition for r in reqs)),
        "by_ui_observable": dict(Counter(r.ui_observable for r in reqs)),
        "by_polarity": dict(Counter(r.polarity for r in reqs)),
        "requires_login": sum(1 for r in reqs if r.requires_login)})
