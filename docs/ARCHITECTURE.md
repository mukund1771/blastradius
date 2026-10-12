# Architecture and module contracts

Source of truth for how the pieces fit. The *why* is in [DECISIONS.md](DECISIONS.md); evidence is in
[RECON.md](RECON.md); edge cases are in [CASES.md](CASES.md); shared data shapes are
`src/blastradius/models.py`.

## Flow

```
            access (every fetch)                          reason
 live site ──► classify ──► Capture ─┐                 PR ──► retrieve ──► grade ──► narrate ──► verify ──► report
                                     ▼                          ▲   (templated Cypher)  (CRAG-inspired)
 crawl ─► ScreenState/Transition ──► graph (Neo4j) ◄── link ◄── code index ◄── repo @ ref (+ PR head overlay)
                                     ▲                  ▲
 spec ──► Requirement ───────────────┘                  └── gaps (absence + effort), flows
```

Every stage writes artifacts into one run directory and cites evidence ids. Nothing is passed between
stages except the shapes in `models.py`.

## Report model

`report/model.py` defines `Report` (plain data: items per tier, claims with evidence ids, requirements at risk,
undetermined, snapshot). `reason` produces it; `report.render` formats it; `report.lint` and `reason.verify`
enforce the language rules in [REPORT_SPEC.md](REPORT_SPEC.md). Nothing in `Report` is HTML or Markdown.

## Run directory

```
data/runs/<run_id>/
  manifest.json  trace.jsonl  ledger.jsonl  failures.jsonl  evidence.jsonl
  crawl/{captures,screens,transitions}.jsonl  crawl/summary.json  crawl/stats.json  crawl/goal_effort.json
  crawl/screens/*.png  crawl/dom/*.html  crawl/goals/<n>/...   (bounded goal-directed re-crawls, artifact_subdir)
  spec/{requirements.json,pages.json,stats.json}
  code/index.json
  graph/load.json
  links/{links,gaps,flows}.jsonl
  reason/<pr>/{impact.json,grade.json,claims.json,report.json,report.md,report.html,needs_review.json}
  eval/{results.json,eval.md}
```

Shared services (all in `src/blastradius/`): `RunContext` (`run.py`), `LLM` (`llm.py`), `Tracer`,
`Ledger`, `EvidenceLedger`, `FailureLog`, `redact`, `safety`, `TargetConfig` (`targets.py`).

## Module contracts

### access/  (decisions D11-D14, D32, D33; cases 17, 18, 67-70)

```python
# classify.py
def classify_http(status: int | None, headers: Mapping[str, str], body: str, url: str) -> AccessResult
def classify_capture(*, status, headers, title, body_text, interactive_count, anchor: AnchorConfig | None,
                     url: str, rung: str = "L0", prior_fingerprints: Mapping[str, str] | None = None) -> AccessResult
def robots_allows(robots_txt: str | None, user_agent: str, url: str) -> AccessVerdict  # None (unreadable) -> DISALLOWED
class Breaker:  # trips after N consecutive BLOCKED
    def record(self, verdict: AccessVerdict) -> bool   # True when tripped
# capture.py
HEADER_ALLOWLIST: frozenset[str]
def make_capture(*, target, url, access: AccessResult, final_url=None, status=None, headers=None, title=None,
                 html_path=None, screenshot_path=None, interactive_count=None, source_kind="live",
                 parent_id=None, action=None, captured_at=None) -> Capture   # id = hash(target, final_url, parent_id, action)
def append_capture(run: RunContext, capture: Capture) -> None   # jsonl + evidence(kind crawl.capture, ref=Capture.id) + failure for non-OK
def strip_secret_headers(headers) -> dict[str, str]             # Cookie / Set-Cookie / Authorization never persist
# source.py
class IngestSource(Protocol): ...           # captures() iterator, optional act()
class RecordedSession: ...                  # folder of NNN.html / NNN.png / NNN.json
```

Contract notes after independent review (wave 1):

- `Breaker`: only BLOCKED counts toward the threshold; SUSPECT and DISALLOWED are neutral (do not reset, do not
  count), so `[BLOCKED, SUSPECT, BLOCKED]` trips. OK resets.
- `IngestSource` is `(kind, capabilities: frozenset[str], captures())`; acting sources additionally implement the
  `ActingSource` sub-protocol (`act(action) -> Capture`). `RecordedSession` returns html/screenshot paths
  **relative to `session.folder`**.
- `Capture.id` includes the access verdict, so a BLOCKED re-observation of a state first seen OK is a distinct
  capture and is never dropped by id-based dedup. `append_capture` returns the evidence id.
- Robots: `robots_allows(None, ...)` (unreadable) is DISALLOWED; the *caller* maps HTTP statuses to text/None
  (4xx -> allow-all per RFC 9309, 5xx/network error -> None).

Classifier rules: headers first on **any** status (`cf-mitigated: challenge`; `x-amzn-waf-action` arrives as
202/405); Cloudflare Block page 403/1020 is a separate rule; generic title/anchor/fingerprint-collapse guards;
vendor *presence* (`cf-ray`, `server: cloudflare`, `x-vercel-id`) is **never** a block; body size is never a
signal (Hoppscotch's real shell is 5 KB).

### crawl/  (D04, D28; cases 1-16)

```python
# explorer.py
def run_crawl(run, cfg: TargetConfig, llm, *, goals=None, source="live", recorded_dir=None,
              max_actions=None, artifact_subdir=None) -> CrawlSummary
# artifact_subdir: when set, ALL artifacts go under crawl/<artifact_subdir>/ and the main crawl/ artifacts are
# untouched (bounded goal-directed re-crawls). Screen/element ids are content hashes, so screens found by two
# subdirs can share ids; the graph loader merges subdirs explicitly.
```

Deterministic perception, fingerprinting, frontier, safety classes and budgets; the LLM is a *policy* that
re-ranks candidates only (a heuristic policy must work with no API key). Output files per `models.py`, plus
`crawl/stats.json` (egress hosts, soft-404 urls, policy blocks, restores) and `crawl/goal_effort.json`.
Safety classes: NAVIGATE, CLICK_SAFE, LOCAL_WRITE, NETWORK_SIDE_EFFECT (Send re-checked at click time),
AUTH, DESTRUCTIVE (label, href, and dialog context), EXTERNAL_WIDGET, EXTERNAL_LINK, DENIED_URL,
UNKNOWN_CONTROL (nameless icon-only controls are not clicked unless a tooltip name was harvested or the icon is
on the safe list). Egress is enforced at the route layer AND the network layer (redirect hops, WebSockets).

### spec/  (D30; cases 21-27, 73)

```python
# ingest.py
def run_ingest(run, cfg: TargetConfig, llm) -> list[Requirement]
# spec/stats.json: status (ok | refused | failed | invalid_config), complete, sha256, http_status, content_type,
# final_url, access_verdict, doc_version, app_version(+source), stale, stale_reason, dropped_by_reason
# (not_in_source, code_marker, too_short, crosses_sentence, ...), polarity_downgraded, evidence_by_requirement.
# Every refused/failed path overwrites requirements.json and pages.json with [] (no stale artifacts).
# kind == 'gherkin' dispatches to blastradius.spec.gherkin.ingest_gherkin(run, cfg), which writes the artifacts.
```

### code/  (D10, D24; cases 29-36, 38)

```python
# adapter.py
class CodeAdapter(Protocol):
    def index(self, repo_root: Path, cfg: CodeConfig, snapshot_id: str) -> CodeIndex: ...
def classify_file(path: str, cfg: CodeConfig) -> FileClass
# index.py
def run_index(run, cfg: TargetConfig, *, ref=None, pr_number=None) -> CodeIndex
# code/overlay.py
def run_overlay(run, cfg, base_index: CodeIndex, pr_number: int, head_repo: str, head_sha: str,
                changed_paths: list[str], *, cache_root=None, remote=None) -> CodeIndex
# in memory only: replaced files' stale edges must not be merged into the baseline graph. snapshot_id is the BASE
# commit; stats.overlay_head_sha / overlay_added / overlay_modified / overlay_pull_ref_moved describe the head.
# ensure_repo(..., worktree=None, lock_timeout=...): single-writer lock; per-sha worktrees keep a PR head from
# clobbering the baseline checkout. stats.parse_errors / parse_partial describe unparsable files; resolved_tag_rate
# is None when no tag pair was measured; hub_importers excludes tests (hub_importers_with_tests keeps the old number).
```

### graph/  (D08; schema in the plan section 6)

```python
class GraphStore:
    @classmethod
    def from_settings(cls, settings: Settings, run: RunContext | None = None) -> "GraphStore"
    def ensure_schema(self) -> int                    # statements applied
    def clear_target(self, target: str) -> int        # nodes deleted
    def counts(self, target: str | None = None) -> dict[str, int]   # labels and relationship types, zero-filled
    # idempotent MERGE writes keyed by (target, id); every node/edge carries run_id and bundle_hash
    def write_code(self, index: CodeIndex, run_id: str) -> dict
    def write_crawl(self, screens, transitions, run_id: str, bundle_hash: str | None) -> dict
    def write_requirements(self, reqs, run_id: str) -> dict
    def write_links(self, links, run_id: str) -> dict
    def write_gaps(self, gaps, run_id: str) -> dict
    def write_flows(self, flows, run_id: str) -> dict
    def read(self, template: str, **params) -> list[dict]   # named templates only; read transactions only
# Canonical UIElement key = (owner_hint or '', role, casefold(name), ordinal, scope). Keys: CodeFile (target, path),
# I18nKey (target, key), Route (target, pattern), Gap (target, id, bundle_hash). A layer is pruned only when its
# artifact is complete (crawl: summary present, stop_reason frontier_empty, counts match; code: files > 0, not
# parse-partial); otherwise it is loaded without pruning and graph/load.json notices say why.
# queries.py + queries_*.py: named, parameterised Cypher templates (impact, absence_near, hub_degree, ...).
# Every module graph/queries_*.py exposing TEMPLATES is merged (name collisions raise). No f-strings.
# Links carry screen_ids; IMPLEMENTED_BY/REALIZED_BY store them and `impact` counts a screen only when the
# edge's screen_ids is empty or contains it (per-screen attribution of canonical elements).
# load.py
def run_load(run, cfg, store) -> dict
```

### link/, reason/, report/, evalkit/  (wave 2)

`link.run_link`, `link.gaps.run_gaps`, `link.flows.run_flows`; `reason.pipeline.run_pr`;
`report.render.render`; `evalkit.run.run_eval`. `link/` must never import `evalkit/` (enforced by test).

## Rules every module follows

1. **LLM proposes, graph disposes.** An LLM may only produce schema-validated objects via `LLM.structured`.
   Text that came from a page, spec, PR, or locale file is wrapped with `safety.untrusted` and the call's system
   prompt includes `UNTRUSTED_PREAMBLE`.
2. **No API key is required to run the deterministic parts or the tests.** LLM code is tested with a fake
   client (see `tests/test_foundation.py`). Never read or print `ANTHROPIC_API_KEY`.
3. **Every produced fact carries an `evidence_id`** (`run.evidence.add(...)`) and every stage runs inside
   `run.stage(...)` / `run.tracer.span(...)`. Failures use `run.failures.record(FailureCode...)`.
4. **Ids are content hashes** (`ids.stable_id`). Never include run_id, counters, or timestamps in an id.
5. **Egress and safety:** no module opens network connections to hosts outside the target's `egress_allow`
   and the documented public sources (GitHub, docs). Crawlers use `Settings.user_agent()`.
6. **Honest scope:** if a case in `CASES.md` is tagged P0 for your module, either handle it with a test or say
   in your report that you did not.
