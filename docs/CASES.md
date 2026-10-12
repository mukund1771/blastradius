# Cases register: what we might have missed

76 cases from an adversarial read of the plan (66 from the red-team pass, 10 added by verifiers and critics).
This is an **appendix, not a TODO list**: about 31 items were tagged "handle now" and would cost more than 4h, so
each case has exactly one build tier. At the end of the build every tier is converted to a final status with a
code or test pointer:

`HANDLED` (pointer) · `DOCUMENTED` (limitation text in DESIGN.md) · `DEFERRED` (next week) · `NOT_HANDLED` (honest)

38 rows are tagged P0, but they cluster into about 17 pieces of work that ride along with the stage they belong to
(for example #1, #2, #3, #13 are four rules in one explorer safety module; #35, #36, #40, #43 are one file-class
table; #55-#59 are one safety bundle). Row count is not hours.

| Tier | Meaning |
|---|---|
| **P0** | Cheap, changes correctness, built in the core slice |
| **P1 / P1b** | Built if the checkpoint is green (P1b = second-target slice) |
| **DOC** | Stated as a known limitation in the design doc |
| **DEFER** | Designed, not built; listed under "another week" |

Stages: `crawl` · `access` · `spec` · `code` · `pr` · `graph` · `reason` · `sec` · `eval` · `ops` · `legal` · `human`

## Crawl

| # | Case | Handling | Tier |
|---|---|---|---|
| 1 | **Egress escape**: Hoppscotch is an API client; a typed URL + Send can reach localhost, metadata IPs, port 9119 | Allowlist at Playwright route level; block private ranges, downloads, permission prompts; log blocked requests | P0 |
| 2 | **Third-party widgets** (Crisp chat, PostHog, Firebase): messages real humans, analytics eats bot traffic | Action class `EXTERNAL_WIDGET` = never interact, exclude text from LLM context; identifiable UA | P0 |
| 3 | **SPA soft-404**: unknown paths, `/health`, `/version.json` return 200 `text/html` | Judge route existence by rendered Screen identity (catch-all page), never HTTP status; filter sitemap seeds (19 `/admin`, 7 `/orgs`) | P0 |
| 4 | Viewport variants: 375px shows 52 interactive elements vs 59 at 1024px | Pin viewport, locale, timezone, colour scheme in the run manifest; mobile pass documented | P0 pin / DOC |
| 5 | Platform-dependent accessible names (`⌘ K`, `alt+T`) differ macOS vs Linux CI | Strip kbd tokens before hashing and matching | P0 |
| 6 | Service worker + persisted state (`persistence.v1:*`) | Fresh context, service workers blocked; record main bundle hash at start and end to detect mid-run deploys | P0 |
| 7 | Icon-only controls (~156 `v-tippy` uses in ~41 files) are nameless | Element identity = (owner SFC, role, ordinal), not name alone | DOC |
| 8 | Frames/embeds (`/e/:id`, share modals), shadow DOM, canvas | Enumerate `page.frames()`; `/e/*` is its own Screen kind; canvas deferred | DOC |
| 9 | Code editors (CodeMirror/Monaco) virtualise lines, DOM partial and huge | Keep masking; cap text per node | DOC |
| 10 | Volatile UI: relative timestamps, response time/size, star counts, What's-new | Exclude from fingerprints, mask digits, deterministic dismissal | DOC |
| 11 | Feature flags: 68 `import.meta.env` matches, placeholder substituted at deploy | Record observed flags; gap reason `CONFIG_DEPENDENT` | DOC |
| 12 | Modal stacks, focus traps, toasts, Escape semantics | Fingerprint topmost dialog; define back-out; "could not return to base" burns budget | DOC |
| 13 | Politeness: rate, 429, repeated 4xx | robots check, identifiable UA, concurrency 1, 500-1000 ms delay, stop on repeated 4xx | P0 |
| 14 | Same state via different action orders yields different fingerprints | Run twice; publish fingerprint overlap as a stability metric (if free) | DOC |
| 15 | 34 locales + persisted locale key | Crawl `en` only; non-`en` strings unmapped, said so | DOC |
| 16 | Infinite scroll, virtualised lists (collections tree, history) | Sample first N per container, record truncation | DEFER |

## Access / WAF

| # | Case | Handling | Tier |
|---|---|---|---|
| 17 | **Challenge page ingested as the UI** (silent graph corruption) | `AccessVerdict` on every Capture; only `OK` creates Screen/UIElement; `SUSPECT` to needs-review; `BLOCKED` creates `ACCESS_RESTRICTED` gap; breaker after 2 | P0 |
| 18 | Protected app, no owner cooperation | `RecordedSession` loader (html/har/png folder) through the same `Capture` interface | P1 |
| 19 | Cooperative modes for the doc: staging, secret-header skip rule, service token, allowlisted egress | Document with vendor sources; non-cooperative bypass is a stated non-goal | DOC |
| 20 | Second target set | Superseded by D15: Sylius slice | P1b |
| 67 | **AWS WAF challenge arrives as `202`** (looks like success), CAPTCHA as `405`; no CORS headers so in-page JS cannot read it | Read `x-amzn-waf-action` at the network layer on every status | P0 |
| 68 | **Cloudflare Block (403/1020) carries no `cf-mitigated`** (only `challenge` does) | Separate hard-block rule: 403 + CF block-page body markers | P0 |
| 69 | robots.txt unreadable or disallowing | Unreadable = deny; verdict `DISALLOWED`, gap says "not crawled by choice", never "not found" | P0 |
| 70 | Vendor fixtures beyond Cloudflare/AWS are synthesized from recall | Label "synthesized from documented markers"; they test the author's memory, not ground truth | DOC |

## Spec ingest

| # | Case | Handling | Tier |
|---|---|---|---|
| 21 | Docs lag the app (changelog tops 2026.5.0 vs app 2026.9.0) | Stamp `doc_version`; absence reason `SPEC_MAY_BE_STALE`, not a bug claim | P0 |
| 22 | Edition/platform-gated requirements ("only in the Desktop App", Enterprise, "coming soon" x3) | `edition` + qualifier; only cloud-web items count toward UI absence; explicit negatives become `CONFIRMED_ABSENT` | P0 |
| 23 | Corpus scope: 104 pages incl. 20 self-host, privacy, terms, security | Page allowlist before extraction; changelog entries are dated evidence, not requirements | P0 |
| 24 | CDN `max-age=86400`, content moves | Record sha256 + fetch time in the manifest | P0 |
| 25 | MDX qualifiers (`<Warning>`, `<Tabs>`) and quote check on raw markup | Normalise (strip tags, collapse whitespace) on both sides before the substring check | P0 |
| 26 | Duplicates and contradictions across feature, getting-started and changelog | Dedupe by normalised-quote hash, keep `source_urls[]`; contradictions to needs-human | DOC |
| 27 | Negative and non-functional statements | `polarity must_exist | must_not_exist`; NFRs `NOT_UI_TESTABLE`; a satisfied negative is a pass, not a gap | P0 |
| 28 | Requirement granularity drifts between runs | Stable id = hash(quote); check against 10 hand-split requirements | DEFER |
| 73 | Sylius: `docs.sylius.com/llms-full.txt` behind OIDC; Gherkin lives in the same repo as the code | Public `/public/llms-full.txt` as PRD-like source, Gherkin as tags + cross-check, to keep layers independent | P1b |

## Code index

| # | Case | Handling | Tier |
|---|---|---|---|
| 29 | **Deployed != main HEAD** (9/367 app files differ; 11/104 main-chunk SFCs; `graphql.ts` generated) | Index main + PR head overlay; stamp "deployed vs indexed: N of M differ" in the report header | P0 |
| 30 | Workspace packages ship as `dist` (`@hoppscotch/data`, kernel, js-sandbox) | Map package `src` to importer edges with a named-import filter. Until then hub touch forces AMBIGUOUS | P1 (fallback P0) |
| 31 | Lazy chunks: main chunk has only the catch-all page + 104 SFCs | Fetch each chunk's map when its route is visited (oracle); the code layer is cloned so routes are present | P1 |
| 32 | Ambiguous copy: 213 of 1,903 `en.json` values shared by several keys (URL x7, Copy x5) | Reachability prior (page + layout closure) before text match; then LLM capped 0.60 | P0 |
| 33 | Dynamic render: 33 `<component :is>` across 20 files | Known holes; effort goes to `:is`, not dynamic key prefixes (<1% of `t()`) | DOC |
| 34 | External UI kit `@hoppscotch/ui` 0.2.6 | Kit text arrives via the caller; kit-internal change is `UNMAPPED_CODE` reason `external-package` | DOC |
| 35 | Generated/derived files (`components.d.ts`, graphql codegen, compiled locale JSON) | Class `GENERATED`; never evidence; re-read as resolver input | P0 |
| 36 | Global styles (`tailwind.config.cjs`, scss) change every screen | Class `STYLE_GLOBAL`, no per-element mapping, shown under "Global change" | P0 |
| 37 | CSR vs SSR: SSR targets (hydration, server components) break DOM-to-file | State as limitation; Hoppscotch is CSR-only | DOC |
| 38 | Runtime-generated text (API responses, backend errors) | Label `RUNTIME_TEXT` | DEFER |

## PR shapes

| # | Case | Handling | Tier |
|---|---|---|---|
| 39 | **Resolver-changing PRs**: #6641 and #6645 edit `components.d.ts` | Re-resolve changed `.vue` against head's table (base index + head overlay), or added components are orphaned | P1 |
| 40 | Classify files before lookup: tests (2 in #6645), generated, lockfile, docs, CI, build-config, data-schema | File-class table; unknown-in-graph for a test file is not needs-human | P0 |
| 41 | Stale base: #6645 base 2026-08-28; main 10 commits / 95 files ahead; overlap on `en.json`, `components.d.ts` | Intersect PR files with `base...main`; flag "diff may not apply as shown"; use merge-base for hunks | P0 |
| 42 | Fork heads (3/3 demo PRs) and force-push | Fetch via `pull/N/head`, pin `head_sha`, re-check at render time | P0 |
| 43 | Data-migration PRs (#6658: collection schema v13): impact is persisted localStorage, sync, import/export, CLI | Class `PERSISTED_DATA` + manual migration checklist; list unassessed packages | P0 |
| 44 | Near-duplicate components: #6641 edits both `components/gql/` and `components/graphql/` Response.vue | Show both; do not merge | DOC |
| 45 | Huge/stacked PRs (>50 files), merge commits, rename-only, dependency bumps, i18n-only, reverts | Per-hunk LLM budget; rename-follow; locale-diff-only path | DEFER |
| 75 | Sylius: all three candidate PRs already merged to 2.2 | Index PR base SHA; post-hoc check, labelled as such | P1b |
| 76 | State-mediated coupling: additive setters write shared state that existing readers consume | Symbol narrowing keeps reader edges as `Possible`; **downgrade, never delete** | P0 |

## Graph

| # | Case | Handling | Tier |
|---|---|---|---|
| 46 | Element duplication: layout and sidebar recur on every Screen | Canonical `UIElement` = (owner SFC, role, name) with `APPEARS_ON`; count distinct screens | P0 |
| 47 | Versioning: reruns overwrite | `run_id`, `bundle_hash`, `doc_sha` on nodes and edges; no MERGE-overwrite; reports pin a snapshot id | P0 |
| 48 | Gap staleness: `NOT_FOUND_WITHIN_BUDGET` becomes false after a deploy | Gaps carry `bundle_hash`, never reused across bundles; ids are content hashes | P0 |
| 49 | Store choice: Kuzu has no py3.14 wheel | Neo4j for the brief; networkx as in-process eval mirror | DOC |

## Reasoning

| # | Case | Handling | Tier |
|---|---|---|---|
| 50 | **Hub explosion**: `layouts/default.vue`, `@hoppscotch/data` imported by 107/413 app files | Degree-normalised weights, depth cap 2, named-import filter, "Global change" category | P0 |
| 51 | Double counting through several paths or flows | Counts are `DISTINCT` screens/flows; noisy-OR for confidence only, never for counts | P0 |
| 52 | Popularity bias: the REST request screen dominates every PR | Rank by changed-symbol overlap and path distance, not centrality; always show the "why" path | DOC |
| 53 | Judge circularity: extractor, linker and narrator share a model family | Silver labels from deterministic links, hand-label a sample, state the caveat (D21) | DOC |
| 54 | Verifier checks citation presence, not truth: a valid-but-irrelevant edge passes | Render the path per claim (P0); sampled entailment check is the offline judge | P1 |
| 61 | Alert fatigue: needs-review balloons from generated/test files | Aggregate by reason, rank, top 5 in the report, rest in appendix; Tier 2 capped at N | P0 |
| 62 | Trust calibration: bands are unvalidated | Publish n and method; avoid false precision; keep "did not look" distinct from "looked, not found" | DOC |

## Security

| # | Case | Handling | Tier |
|---|---|---|---|
| 55 | **PR text injection**: titles, bodies, commit messages, diff comments, `en.json` strings from fork authors; HTML comments hidden on GitHub, visible in body | Strip comments/images, delimit as quoted data, narrator has no tools, schema-constrained output | P0 |
| 56 | **Crawled/doc text injection**: app shows arbitrary API responses and user-named collections; docs publish agent-directed assets (`/.well-known/agent-skills`) | Policy sees element indices only; fetch only `llms-full.txt`; never follow in-page instructions | P0 |
| 57 | Output injection: DOM/PR strings in the HTML report; Cypher | Escape, CSP, no remote images; parameterised Cypher only; read-only role; no LLM-written Cypher | P0 |
| 58 | Secrets and PII: screenshots/DOM can hold environment secret values (#6645 is about editing them); storage has Crisp/PostHog ids | Never persist cookies/localStorage; regex-scan DOM and diffs before any LLM call; prompts hash-only by default; `data/runs` gitignored | P0 |
| 59 | Runaway cost | `--max-usd` breaker, `max_tokens` on every call, bounded 429/529 retries, content-hash cache, step cap, up-front estimate of judge K x elements | P0 |

## Legal, human, eval, ops

| # | Case | Handling | Tier |
|---|---|---|---|
| 60 | robots `Allow` (hoppscotch.io), `Content-Signal: ai-input=yes` (docs), MIT repo and docs repo, all verified | Identify the bot; commit only sample screenshots and excerpts; do not redistribute `llms-full.txt`; attribute. No anti-automation clause found by keyword grep (not exhaustive) | DOC |
| 63 | Circular eval: synthetic-PR recall reuses the links that built the graph | One independent signal: source-map oracle, else 25 hand labels | P1 |
| 64 | Variance: cache hits mask run-to-run variance | Stability runs bypass cache; manifest records model ids, bundle hash, commit shas | DOC |
| 71 | Oracle cannot label UI introduced by the open demo PRs (not in the deployed build) | State in the doc; PR-level scoring restricted to files present on main | DOC |
| 72 | Judge cost is a lower bound (thinking always on); judge shares a vendor family with the generator; seeded corruptions overstate TNR | Report TNR as an upper bound with Wilson CI; state the caveat | DOC |
| 65 | Observability | One `run_id`; JSONL spans per stage and LLM call; failure taxonomy; `trace tree <run_id>` | P0 |
| 66 | Environment: Docker daemon off, no `ANTHROPIC_API_KEY`; system `urllib` fails TLS verification | `doctor` surfaces both; use `httpx`/certifi | DOC |
| 74 | Home Assistant: cold load of `#/lovelace/home` renders empty for 16s+ (3/3) | Seed `#/lovelace/0` and assert a minimum element count before capture (target not built; recorded) | DEFER |
