# Decision log

Every material choice, what it beat, and why. Written **before any code** (2026-10-12) from the plan and a
17-agent research pass (8 researchers, 7 independent verifiers, 2 critics). Evidence for each number is in
[RECON.md](RECON.md). Edge cases are in [CASES.md](CASES.md).

**Status:** `decided` (final) · `decided (user)` (the user made the call) · `revisit` (open) · `P0/P1/P2` (build tier).
Things marked *verifier-reported* were not independently checked on this machine.

## 1. What was asked, and how it landed

| # | Ask | Outcome | Decision |
|---|---|---|---|
| 1 | Crawl, ingest, graph (3 layers), reason on a real PR | Built on Hoppscotch; PRs #6645 / #6641 / #6658 | D05, D06 |
| 2 | WAF-protected apps, "Amazon-class", another set of sites | Detect, quarantine, degrade; never defeat. Sylius v2 slice as second target | D11-D15 |
| 3 | Small observability + trace | JSONL tracer, evidence + cost ledger, failure enum, `doctor` | D16, D27 |
| 4 | LLM judge + CRAG / graph RAG used in the reasoning phase | CRAG-inspired grader at stage 7b, demote-only residue judge; GraphRAG communities cut | D17-D22 |
| 5 | Log approaches, think about missed cases | This file, CASES.md (77 cases), RECON.md | D31 |
| 6 | Time | ~8h, finish by end of day, hard stop on features ~6:45 | D31 |

## 2. Decisions

### Foundations

| ID | Decision | Alternatives (why not) | Status |
|---|---|---|---|
| D01 | **Python 3.14** | TypeScript: friction for LLM and Neo4j code. All wheels resolve on 3.14 (pip dry-run) | decided (user) |
| D02 | **Claude via API.** Model ids live in `config.py`; `doctor` retrieves them | Hard-pinned ids: Haiku 4.5 retirement "not sooner than 2026-10-15" and `claude-haiku-5-5` current (*verifier-reported*) | decided (user) |
| D03 | **Neo4j Community in Docker** | Aura (account); Kuzu (no py3.14 wheel, verified); networkx (kept as an eval-only mirror) | decided (user) |
| D04 | **Playwright + own explorer** | browser-use (opaque trajectory, not a graph artifact); Stagehand; record-replay (seeds only, P1) | decided |
| D05 | **Target: Hoppscotch** | Memos (guest surface ~6 routes, editor behind SSO); Saleor storefront (no consumer PRD, repo in flux); Home Assistant (shadow DOM, 3-4h); PostHog (login-gated, see D34) | decided |
| D06 | **Demo PRs #6645 (primary), #6641, #6658**, all open, fork heads | Merged #6447: already in main, post-hoc only, cannot be predictive | decided |

### Architecture

| ID | Decision | Alternatives (why not) | Status |
|---|---|---|---|
| D07 | **LLM proposes, graph disposes**: LLM output must be schema-valid and cite evidence the code can resolve | LLM as arbiter of what is impacted (breaks verifiability) | decided |
| D08 | **Absence is a `Gap` node carrying search effort** | Missing edge (cannot say "did not look" vs "not allowed to look"); null properties | decided |
| D09 | **Confidence per edge `{value, method, evidence_id}`, human bands, escalation list** | One global score; probabilities without calibration (false precision) | decided |
| D10 | **Code layer = frontend, file granularity** | Function call graph, backend route mapping (P2) | decided |
| D12 | **One vocabulary**: `AccessVerdict {OK, SUSPECT, BLOCKED, DISALLOWED}`, gap kind `ACCESS_RESTRICTED{reason, signals}`, one `Capture`, one `IngestSource` | Four names for one thing across the research digests | decided |
| D21 | **Four independent checkers**: linker judge (generates), runtime grader (retrieval quality), verifier (deterministic), offline judge (never in product path) | One judge for all, which grades the grader against its own prompt | decided |
| D24 | **Index `main` HEAD at crawl time, overlay PR head, stamp drift vs the published source map** | "Clone the deployed commit" (unknowable; deployed != main); index from `sourcesContent` (no git history, no generated files) | decided |
| D30 | **Requirement fields: `edition`, `polarity`, `doc_version`, spec sha, page allowlist** | Raw extraction: manufactures false absences (spec changelog stops at 2026.5.0 vs app 2026.9.0) | decided |

### Access / WAF (user-raised)

| ID | Decision | Alternatives (why not) | Status |
|---|---|---|---|
| D11 | **Detect, quarantine, degrade; never defeat.** Cooperative owner access or offline artifacts only | Stealth/evasion: ToS and legal exposure, brittle, and out of scope by design | decided |
| D13 | **Classify on block signals, never vendor presence** (and never body size) | Presence classifier: would false-block docs.hoppscotch.io and demo.usememos.com, which carry `cf-ray` and serve real pages | decided |
| D14 | **Amazon = pattern-stress model + recorded fixtures; not crawled** | Live crawl: no owner cooperation, third-party ToS unverified, no repo so the Code layer cannot exist anyway | decided |
| D15 | **Second target = Sylius v2 slice to a stated bar**, `target` namespace, `CodeAdapter` protocol | Recon-only (user wants a real slice); Home Assistant; Memos; full depth (breaks Hoppscotch depth) | decided (user) |
| D32 | **Headers first on any status**: AWS challenge arrives as `202`, CAPTCHA as `405`; Cloudflare Block (403/1020) has no `cf-mitigated` | Status-code fallback `{403,429,503}`: misses the 202 (looks like success) | decided |
| D33 | **Vendor rows beyond Cloudflare + AWS labelled "synthesized from documented markers"** | Presenting Akamai/DataDome/Imperva/PerimeterX rows as verified (secondary-sourced; DataDome `x-datadome-*` are internal API headers) | decided |

### Considered after the fact (user-raised)

| ID | Decision | Alternatives (why not) | Status |
|---|---|---|---|
| D34 | **Stay on Hoppscotch; PostHog logged as a P2 "code + PR half only" second target** (repo, docs, source maps, `commit_sha`; no live crawl). User asked "why not PostHog?" and the same five-point checklist was run | Switch to PostHog: guests reach only login/signup, signup is risk-gated and account creation is off-limits for the agent, so the crawl needs a human-made `storage_state` on a real account that an authenticated crawl could mutate; ~9k `.tsx`, 98 products and hundreds of feature flags would eat the budget; docs are an index with ~15-25 guest-observable requirements; open guest-observable PRs: 0 in sample (feasibility 3/10, researcher + verifier agree) | decided (user) |
| D35 | **The testid rung reads configurable attributes** (`data-testid` and `data-attr`), not a fixed one. Costs one config field; PostHog shows `data-attr` in 2,576 files and in the prod DOM, Hoppscotch shows ~0 | Hard-code `data-testid` (the Hoppscotch rung would stay empty and the abstraction would be wrong for the next target) | decided (P1 with the Sylius slice) |

### Reasoning phase (user-raised)

| ID | Decision | Alternatives (why not) | Status |
|---|---|---|---|
| D17 | **CRAG-inspired deterministic grader at stage 7b**; named "CRAG-inspired" | CRAG proper (fine-tuned T5 scorer + web-search fallback; we have neither); CRAG at link-judging only (user wants it at reasoning) | decided |
| D18 | **Residue judge is demote-only; may nominate only via a probe that yields an evidence row** | LLM expands the radius (breaks D07) | decided |
| D19 | **GraphRAG communities cut; templated Cypher** | Louvain/Leiden summaries: impact queries are path queries, summaries blur file precision, add a hallucination surface; text-to-Cypher: silent truncation and an injection surface | decided |
| D20 | **BM25 + LLM judge with NONE** | Embeddings/Voyage: second key, premature; revisit only if leave-one-out recall@20 < 0.8 | decided |
| D25 | **Hop cap 2 + one forward hop (P0); PPR to P2** | PPR as the cure: gain on #6658 came from forward edges (n=1 for PPR) | decided |
| D29 | **Two-tier report + Global + Persisted-data + Could-not-determine** | Flat list: `@hoppscotch/data` and the layout light every screen | decided |
| D36 | **Direct means hop 0 and a strong link**: direction self or copy, and confidence ≥ 0.5 or a single file. A string in more than three files below 0.5 is not a named control | Treat every hop-0 string match as a control the QA lead must click (the #6645 home page listed dozens) | decided |
| D37 | **New keys are one undetermined row**, not a sentence on every affected screen | Attach "new in this PR" to each screen that shares a file with the key | decided |
| D38 | **The risk line names the direct screens**, then spec sections only when no direct screen is confirmed or likely | Lead with the first requirement section (on #6645 that was AI Features, which the PR does not edit) | decided |
| D39 | **A claim id includes its subject** (and a requirement id for requirement claims). Two screens can share a sentence | Hash the sentence alone (the invariant `ids_unique` failed on the live reports) | decided |
| D40 | **The headline requirement count is the distinct rows in the requirements table** | Count only requirement ids attached to screen items (the table then disagrees with the headline) | decided |
| D41 | **A requirement reached only through shared code or a whole screen is listed only when it shares a word with what the PR changes**; a control the PR's own files implement (hop 0) is always listed. The rest is counted in the not-checked section | Count every requirement realised by any control or screen the change reaches (the live #6645 report listed 48, mostly Collections, History and Mock Servers because `Sidebar.vue` renders the changed environments editor; with the rule: 12, all about environments). Known limit: the relatedness test is a word-overlap heuristic, and a parent that renders a changed component is still graded Likely in the screens tables | decided (measured on #6645, #6641, #6658) |
| D42 | **Bifrost was tried as an LLM gateway in front of the agent system, and is not on the shipped path.** Why it was tried: not to be tied to one provider, routing and fallback for harder cases, and an LLM-as-judge on a different model family than the generator (the same-vendor caveat on every judge number). Every call still goes through `blastradius.llm.LLM` to the Anthropic API; that class holds the content-addressed cache, the spend breaker, the closed failure enum and the schema-bound `messages.parse` call, so it is the single seam where a gateway would sit | Call each provider's SDK from the agents (no single place for cache, budget and redaction); a gateway as the only path (the wrapper relies on Anthropic-specific structured output, `effort` and the workspace header, so a second family needs its own adapter, not only a base-URL change) | tried, not integrated; the judge stays same-family and the caveat is printed |

### Eval

| ID | Decision | Alternatives (why not) | Status |
|---|---|---|---|
| D22 | **Offline judge thin: evidence-only, binary items, seeded corruptions, Wilson CI** | Pairwise "usefulness" LLM judge (unvalidated number is a liability) -> deterministic report lint | decided |
| D23 | **Oracle = published source map + vnode walk, test-only** | data-v scope-id as a product rung (covers 11.9% of SFCs, none under `environments/`, and makes the oracle circular); local dev server (monorepo install chain, gql codegen) | decided |
| D26 | **No sampling params on 5.5 models; determinism = cache + schema + k-agreement** | `temperature=0` (400s on 5.5 per verifier); relying on it as a stability claim | decided |

### Ops / safety

| ID | Decision | Alternatives (why not) | Status |
|---|---|---|---|
| D16 | **JSONL tracer, OTel-shaped fields, no SDK** | OTel SDK (cross-process `SAMPLED`-flag trap); Phoenix/Langfuse/Logfire/Weave (server or account breaks "zero-infra reviewer") | decided |
| D27 | **Cache key = hash(model, prompt, schema, effort, access verdict), never run_id; hits still emit spans** | Per-run cache: cost looks falsely low, stability runs cannot be reconstructed | decided |
| D28 | **Safety bundle**: egress allowlist, `EXTERNAL_WIDGET` class, untrusted-text wrapper, parameterised Cypher, secret scan, `--max-usd` breaker | None: Hoppscotch is an API client and `Send` can reach localhost | decided |
| D31 | **~8h, end of day, hard stop on features ~6:45, docs + Loom protected** | 6h (drops runtime judge), 6.5h thin P0 (drops oracle + second target) | decided (user) |

## 3. Approaches considered (per major decision)

| Area | Chosen | Rejected or deferred (one line why) |
|---|---|---|
| Crawler | Playwright + LLM policy over a deterministic explorer | browser-use (trajectory is not a graph artifact, per-step cost); Stagehand (act/observe caching, later); NL-test export à la Testsigma (P2, 3h) |
| Graph store | Neo4j | Kuzu (no py3.14 wheel); networkx (eval mirror only) |
| UI -> Code | Text/i18n evidence + reachability prior | Source-map/data-v/vnode as product rungs (only works for owners publishing maps; circular with oracle); build-time attribute injection (the clean answer for owner-run CI mode, P2) |
| Req -> UI | BM25 candidates + LLM judge with NONE | Embeddings (premature); LLM-only (non-reproducible) |
| Impact | Static import/render closure with hub damping | Test-coverage-based selection (4h, no tests mapped to UI here); runtime JS coverage per screen via source map (independent eval signal, P2) |
| Retrieval | Templated Cypher, hop cap 2 | Free-form text-to-Cypher; community summaries |
| Judge | Invariants first, then evidence-only judge for the semantic residue | Single LLM judge; pairwise usefulness judge |
| Access | Classifier + RecordedSession replay | TraceImport (trace.zip delta formats V3..V10 are brittle, P2); Web Bot Auth signing (P2) |
| Observability | JSONL + ledger + doctor | Phoenix/OTLP sink (optional, P2); HTML viewer (P2) |

## 4. Contradictions found between research digests, and how they resolved

| Conflict | Resolution |
|---|---|
| Red-team: data-v rung as the **top** rung. Oracle verifier: 11.9% of SFCs scoped, none in `environments/` | **Cut** as product rung; kept as drift detector only |
| Red-team: index from `sourcesContent`. Oracle verifier: PR paths and git history needed; `graphql.ts` generated and absent from repo | Clone for the code layer; source map for oracle + drift % only. State "deployed != main" |
| Observability: use OTel SDK. Verifier: cross-process parent needs `TraceFlags.SAMPLED` or children vanish silently | Plain JSONL with OTel field names; OTLP is P2 |
| CRAG research: PPR cures under-reach. Verifier: gain came from forward edges; PPR wins only on #6645 vs hop-2 BFS, n=1 | Forward edges P0, PPR P2 |
| Second-target research: pin Sylius to 2.2 / v2.2.10. Verifier: live footer predates both; version unresolved | Index each PR's base SHA; record footer fingerprint as drift evidence |
| Red-team: CRAG at link-judging only. User: "when the reasoning phase begins" | CRAG-inspired grader at 7b; stage 4 unchanged |
| User intent "elements Amazon uses could be useful" vs ToS and no repo | Amazon as a pattern list + fixtures; the deployed case is the customer's own app |

## 5. Numbers that must **not** reach the design doc (corrected by their own verifiers)

| Claim | Correction |
|---|---|
| "Closure reaches 47% of the app" | That is **3 hops**. 2 hops = 28%, unbounded = 75%. Always state the hop cap |
| "PPR cures #6658 under-reach" | Forward edges did (undirected 1-hop BFS matches); PPR n=1 |
| "networkx Leiden returns 839 singletons, so skip Leiden" | Default-resolution artifact; true Leiden resolves. Skip for other reasons (D19) |
| "#6641 adds 26 i18n keys" | 24 leaf strings + 2 object braces; plus an EOL-only hunk |
| "Sonnet 5.5 cache read $0.20/MTok" | $0.10 (*verifier-reported*) |
| "Deployed == main HEAD" | False: 11/104 main-chunk SFCs deviate; 6 absent from public main |
| "Pin Sylius to branch 2.2" | Refuted (footer predates it) |
| "`tracing/video unsuitable` for connectOverCDP" | Not in the docs; only "lower fidelity" is |
| "Cloudflare `cf-mitigated` marks any block" | Only `challenge`; Block pages (403/1020) carry no such header |
| "85 open PRs in window" | 43 created in the window |

## 6. Open questions (carried into the design doc)

1. Which commit is deployed on hoppscotch.io? File-blob matching against git history would pin it; direction unverified.
2. Do the 4 unmatched `data-v` ids belong to lazy chunks or to `@hoppscotch/ui`? Fetching the other chunk maps would say.
3. Is Playwright `page.coverage` + source-map decoding viable as an independent eval signal? Untested.
4. Does hoppscotch.io's ToS contain anti-automation clauses? Only keyword-grepped on the docs terms page.
5. Is `claude-haiku-5-5` retrievable with this key, and is Opus 5.5 available at our rate limits? `doctor` answers it.
6. Does the Playwright-driven explorer survive Teleport modals, the service worker and soft-404s? Largest unmeasured risk.
