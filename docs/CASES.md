# Cases register: what we might have missed

77 cases from an adversarial read of the plan (66 from the red-team pass, 11 added by verifiers, critics and a follow-up target check).
This is an **appendix, not a TODO list**. The Tier column is the build plan. The Status column is the
disposition after run `crawl-gate-150`:

`HANDLED` (pointer) · `DOCUMENTED` (limitation, stated in DESIGN.md) · `DEFERRED` (another week) · `NOT_HANDLED` (honest)

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

| # | Case | Handling | Tier | Status |
|---|---|---|---|---|
| 1 | **Egress escape**: Hoppscotch is an API client; a typed URL + Send can reach localhost, metadata IPs, port 9119 | Allowlist at Playwright route level; block private ranges, downloads, permission prompts; log blocked requests | P0 | HANDLED (`crawl` egress allowlist) |
| 2 | **Third-party widgets** (Crisp chat, PostHog, Firebase): messages real humans, analytics eats bot traffic | Action class `EXTERNAL_WIDGET` = never interact, exclude text from LLM context; identifiable UA | P0 | HANDLED (action class `EXTERNAL_WIDGET`) |
| 3 | **SPA soft-404**: unknown paths, `/health`, `/version.json` return 200 `text/html` | Judge route existence by rendered Screen identity (catch-all page), never HTTP status; filter sitemap seeds (19 `/admin`, 7 `/orgs`) | P0 | HANDLED (screen identity, not HTTP status) |
| 4 | Viewport variants: 375px shows 52 interactive elements vs 59 at 1024px | Pin viewport, locale, timezone, colour scheme in the run manifest; mobile pass documented | P0 pin / DOC | HANDLED (viewport pinned in the manifest); mobile pass documented, not run |
| 5 | Platform-dependent accessible names (`⌘ K`, `alt+T`) differ macOS vs Linux CI | Strip kbd tokens before hashing and matching | P0 | HANDLED (kbd tokens stripped before the name hash) |
| 6 | Service worker + persisted state (`persistence.v1:*`) | Fresh context, service workers blocked; record main bundle hash at start and end to detect mid-run deploys | P0 | HANDLED (fresh context; bundle hash at start and end) |
| 7 | Icon-only controls (~156 `v-tippy` uses in ~41 files) are nameless | Element identity = (owner SFC, role, ordinal), not name alone | DOC | DOCUMENTED (identity is owner, role, ordinal) |
| 8 | Frames/embeds (`/e/:id`, share modals), shadow DOM, canvas | Enumerate `page.frames()`; `/e/*` is its own Screen kind; canvas deferred | DOC | DOCUMENTED |
| 9 | Code editors (CodeMirror/Monaco) virtualise lines, DOM partial and huge | Keep masking; cap text per node | DOC | DOCUMENTED (text per node is capped) |
| 10 | Volatile UI: relative timestamps, response time/size, star counts, What's-new | Exclude from fingerprints, mask digits, deterministic dismissal | DOC | DOCUMENTED (excluded from fingerprints) |
| 11 | Feature flags: 68 `import.meta.env` matches, placeholder substituted at deploy | Record observed flags; gap reason `CONFIG_DEPENDENT` | DOC | DOCUMENTED (`CONFIG_DEPENDENT`) |
| 12 | Modal stacks, focus traps, toasts, Escape semantics | Fingerprint topmost dialog; define back-out; "could not return to base" burns budget | DOC | DOCUMENTED |
| 13 | Politeness: rate, 429, repeated 4xx | robots check, identifiable UA, concurrency 1, 500-1000 ms delay, stop on repeated 4xx | P0 | HANDLED (robots, identifiable UA, concurrency 1) |
| 14 | Same state via different action orders yields different fingerprints | Run twice; publish fingerprint overlap as a stability metric (if free) | DOC | DOCUMENTED (a second crawl for overlap was not run) |
| 15 | 34 locales + persisted locale key | Crawl `en` only; non-`en` strings unmapped, said so | DOC | DOCUMENTED (crawl is `en` only) |
| 16 | Infinite scroll, virtualised lists (collections tree, history) | Sample first N per container, record truncation | DEFER | DEFERRED |

## Access / WAF

| # | Case | Handling | Tier | Status |
|---|---|---|---|---|
| 17 | **Challenge page ingested as the UI** (silent graph corruption) | `AccessVerdict` on every Capture; only `OK` creates Screen/UIElement; `SUSPECT` to needs-review; `BLOCKED` creates `ACCESS_RESTRICTED` gap; breaker after 2 | P0 | HANDLED (`AccessVerdict`; only `OK` creates screens) |
| 18 | Protected app, no owner cooperation | `RecordedSession` loader (folder of NNN.json + .html + .png; HAR-style header lists accepted, a whole `.har` is not read) through the same `Capture` interface | P1 | HANDLED (`access/source.py` `RecordedSession`); no recorded demo was needed for this public app |
| 19 | Cooperative modes for the doc: staging, secret-header skip rule, service token, allowlisted egress | Document with vendor sources; non-cooperative bypass is a stated non-goal | DOC | DOCUMENTED (bypass is a non-goal) |
| 20 | Second target set | Superseded by D15: Sylius slice | P1b | NOT_HANDLED (Twig adapter is in the tree; no Sylius crawl or PR report) |
| 67 | **AWS WAF challenge arrives as `202`** (looks like success), CAPTCHA as `405`; no CORS headers so in-page JS cannot read it | Read `x-amzn-waf-action` at the network layer on every status | P0 | HANDLED (AWS WAF action header on any status) |
| 68 | **Cloudflare Block (403/1020) carries no `cf-mitigated`** (only `challenge` does) | Separate hard-block rule: 403 + CF block-page body markers | P0 | HANDLED (Cloudflare block page is separate from `cf-mitigated`) |
| 69 | robots.txt unreadable or disallowing | Unreadable = deny; verdict `DISALLOWED`, gap says "not crawled by choice", never "not found" | P0 | HANDLED (unreadable robots is deny) |
| 70 | Vendor fixtures beyond Cloudflare/AWS are synthesized from recall | Label "synthesized from documented markers"; they test the author's memory, not ground truth | DOC | DOCUMENTED (fixtures are labelled as recalled markers) |

## Spec ingest

| # | Case | Handling | Tier | Status |
|---|---|---|---|---|
| 21 | Docs lag the app (changelog tops 2026.5.0 vs app 2026.9.0) | Stamp `doc_version`; absence reason `SPEC_MAY_BE_STALE`, not a bug claim | P0 | HANDLED (`doc_version` 2026.5.0 vs app 2026.9.0 on this run) |
| 22 | Edition/platform-gated requirements ("only in the Desktop App", Enterprise, "coming soon" x3) | `edition` + qualifier; only cloud-web items count toward UI absence; explicit negatives become `CONFIRMED_ABSENT` | P0 | HANDLED (edition gate; 13 requirements skipped for login) |
| 23 | Corpus scope: 104 pages incl. 20 self-host, privacy, terms, security | Page allowlist before extraction; changelog entries are dated evidence, not requirements | P0 | HANDLED (49 of 104 pages extracted) |
| 24 | CDN `max-age=86400`, content moves | Record sha256 + fetch time in the manifest | P0 | HANDLED (spec sha on the run) |
| 25 | MDX qualifiers (`<Warning>`, `<Tabs>`) and quote check on raw markup | Normalise (strip tags, collapse whitespace) on both sides before the substring check | P0 | HANDLED (quotes checked after markup and whitespace normalisation) |
| 26 | Duplicates and contradictions across feature, getting-started and changelog | Dedupe by normalised-quote hash, keep `source_urls[]`; contradictions to needs-human | DOC | DOCUMENTED |
| 27 | Negative and non-functional statements | `polarity must_exist | must_not_exist`; NFRs `NOT_UI_TESTABLE`; a satisfied negative is a pass, not a gap | P0 |
| 28 | Requirement granularity drifts between runs | Stable id = hash(quote); check against 10 hand-split requirements | DEFER | DEFERRED |
| 73 | Sylius: `docs.sylius.com/llms-full.txt` behind OIDC; Gherkin lives in the same repo as the code | Public `/public/llms-full.txt` as PRD-like source, Gherkin as tags + cross-check, to keep layers independent | P1b | NOT_HANDLED (Gherkin parser is tested; no Sylius spec was ingested) |

## Code index

| # | Case | Handling | Tier | Status |
|---|---|---|---|---|
| 29 | **Deployed != main HEAD** (9/367 app files differ; 11/104 main-chunk SFCs; `graphql.ts` generated) | Index main + PR head overlay; stamp "deployed vs indexed: N of M differ" in the report header | P0 | HANDLED (main index plus PR head overlay; behind-main is on the report) |
| 30 | Workspace packages ship as `dist` (`@hoppscotch/data`, kernel, js-sandbox) | Map package `src` to importer edges with a named-import filter. Until then hub touch forces AMBIGUOUS | P1 (fallback P0) | HANDLED (named-import filter in `reason/grade.py`; it only downgrades) |
| 31 | Lazy chunks: main chunk has only the catch-all page + 104 SFCs | Fetch each chunk's map when its route is visited (oracle); the code layer is cloned so routes are present | P1 | HANDLED (oracle fetched 21 chunk maps on the visited routes) |
| 32 | Ambiguous copy: 213 of 1,903 `en.json` values shared by several keys (URL x7, Copy x5) | Reachability prior (page + layout closure) before text match; then LLM capped 0.60 | P0 | HANDLED (reachability prior, then confidence 0.90/k floored at 0.30) |
| 33 | Dynamic render: 33 `<component :is>` across 20 files | Known holes; effort goes to `:is`, not dynamic key prefixes (<1% of `t()`) | DOC | DOCUMENTED |
| 34 | External UI kit `@hoppscotch/ui` 0.2.6 | Kit text arrives via the caller; kit-internal change is `UNMAPPED_CODE` reason `external-package` | DOC | DOCUMENTED (`external_package`) |
| 35 | Generated/derived files (`components.d.ts`, graphql codegen, compiled locale JSON) | Class `GENERATED`; never evidence; re-read as resolver input | P0 | HANDLED (class `GENERATED`) |
| 36 | Global styles (`tailwind.config.cjs`, scss) change every screen | Class `STYLE_GLOBAL`, no per-element mapping, shown under "Global change" | P0 | HANDLED (class `STYLE_GLOBAL`, one global row) |
| 37 | CSR vs SSR: SSR targets (hydration, server components) break DOM-to-file | State as limitation; Hoppscotch is CSR-only | DOC | DOCUMENTED (Hoppscotch is client-rendered) |
| 38 | Runtime-generated text (API responses, backend errors) | Label `RUNTIME_TEXT` | DEFER | DEFERRED |

## PR shapes

| # | Case | Handling | Tier | Status |
|---|---|---|---|---|
| 39 | **Resolver-changing PRs**: #6641 and #6645 edit `components.d.ts` | Re-resolve changed `.vue` against head's table (base index + head overlay), or added components are orphaned | P1 | HANDLED (head overlay re-resolved; `code/overlay_*.json` on the run) |
| 40 | Classify files before lookup: tests (2 in #6645), generated, lockfile, docs, CI, build-config, data-schema | File-class table; unknown-in-graph for a test file is not needs-human | P0 | HANDLED (file class before lookup) |
| 41 | Stale base: #6645 base 2026-08-28; main 10 commits / 95 files ahead; overlap on `en.json`, `components.d.ts` | Intersect PR files with `base...main`; flag "diff may not apply as shown"; use merge-base for hunks | P0 | HANDLED (report says the branch is 10 commits behind main) |
| 42 | Fork heads (3/3 demo PRs) and force-push | Fetch via `pull/N/head`, pin `head_sha`, re-check at render time | P0 | HANDLED (fetched `pull/N/head`, head sha pinned) |
| 43 | Data-migration PRs (#6658: collection schema v13): impact is persisted localStorage, sync, import/export, CLI | Class `PERSISTED_DATA` + manual migration checklist; list unassessed packages | P0 | HANDLED (class `PERSISTED_DATA`; #6658 report has a saved-data row) |
| 44 | Near-duplicate components: #6641 edits both `components/gql/` and `components/graphql/` Response.vue | Show both; do not merge | DOC | DOCUMENTED |
| 45 | Huge/stacked PRs (>50 files), merge commits, rename-only, dependency bumps, i18n-only, reverts | Per-hunk LLM budget; rename-follow; locale-diff-only path | DEFER | DEFERRED |
| 75 | Sylius: all three candidate PRs already merged to 2.2 | Index PR base SHA; post-hoc check, labelled as such | P1b | NOT_HANDLED (no Sylius PR was indexed) |
| 76 | State-mediated coupling: additive setters write shared state that existing readers consume | Symbol narrowing keeps reader edges as `Possible`; **downgrade, never delete** | P0 | HANDLED (symbol narrowing downgrades, does not delete) |

## Graph

| # | Case | Handling | Tier | Status |
|---|---|---|---|---|
| 46 | Element duplication: layout and sidebar recur on every Screen | Canonical `UIElement` = (owner SFC, role, name) with `APPEARS_ON`; count distinct screens | P0 | HANDLED (443 canonical elements, 1,468 appearances) |
| 47 | Versioning: reruns overwrite | `run_id`, `bundle_hash`, `doc_sha` on nodes and edges; no MERGE-overwrite; reports pin a snapshot id | P0 | HANDLED (`run_id` on nodes; reports pin the snapshot) |
| 48 | Gap staleness: `NOT_FOUND_WITHIN_BUDGET` becomes false after a deploy | Gaps carry `bundle_hash`, never reused across bundles; ids are content hashes | P0 | HANDLED (gaps carry `bundle_hash`) |
| 49 | Store choice: Kuzu has no py3.14 wheel | Neo4j for the brief; networkx as in-process eval mirror | DOC | DOCUMENTED (Neo4j; no Kuzu wheel for this Python) |

## Reasoning

| # | Case | Handling | Tier | Status |
|---|---|---|---|---|
| 50 | **Hub explosion**: `layouts/default.vue`, `@hoppscotch/data` imported by 107/413 app files | Degree-normalised weights, depth cap 2, named-import filter, "Global change" category | P0 | HANDLED (hop cap 2; layout is a global row, not a walk) |
| 51 | Double counting through several paths or flows | Counts are `DISTINCT` screens/flows; noisy-OR for confidence only, never for counts | P0 | HANDLED (invariant `counts_distinct`; the three reports pass) |
| 52 | Popularity bias: the REST request screen dominates every PR | Rank by changed-symbol overlap and path distance, not centrality; always show the "why" path | DOC | DOCUMENTED |
| 53 | Judge circularity: extractor, linker and narrator share a model family | Silver labels from deterministic links, hand-label a sample, state the caveat (D21) | DOC | DOCUMENTED (same-vendor caveat; judge not run) |
| 54 | Verifier checks citation presence, not truth: a valid-but-irrelevant edge passes | Render the path per claim (P0); sampled entailment check is the offline judge | P1 | HANDLED (each claim's path is in the appendix); the entailment judge was not run |
| 61 | Alert fatigue: needs-review balloons from generated/test files | Aggregate by reason, rank, top 5 in the report, rest in appendix; Tier 2 capped at N | P0 | HANDLED (checklist is two actions on #6645; the rest is the table and the appendix) |
| 62 | Trust calibration: bands are unvalidated | Publish n and method; avoid false precision; keep "did not look" distinct from "looked, not found" | DOC | DOCUMENTED (bands are uncalibrated; n is on every number) |

## Security

| # | Case | Handling | Tier | Status |
|---|---|---|---|---|
| 55 | **PR text injection**: titles, bodies, commit messages, diff comments, `en.json` strings from fork authors; HTML comments hidden on GitHub, visible in body | Strip comments/images, delimit as quoted data, narrator has no tools, schema-constrained output | P0 | HANDLED (untrusted wrapper; narrator has no tools) |
| 56 | **Crawled/doc text injection**: app shows arbitrary API responses and user-named collections; docs publish agent-directed assets (`/.well-known/agent-skills`) | Policy sees element indices only; fetch only `llms-full.txt`; never follow in-page instructions | P0 | HANDLED (spec fetch is `llms-full.txt` only) |
| 57 | Output injection: DOM/PR strings in the HTML report; Cypher | Escape, CSP, no remote images; parameterised Cypher only; read-only role; no LLM-written Cypher | P0 | HANDLED (escaped HTML, parameterised Cypher) |
| 58 | Secrets and PII: screenshots/DOM can hold environment secret values (#6645 is about editing them); storage has Crisp/PostHog ids | Never persist cookies/localStorage; regex-scan DOM and diffs before any LLM call; prompts hash-only by default; `data/runs` gitignored | P0 | HANDLED (runs gitignored; secret scan before model calls) |
| 59 | Runaway cost | `--max-usd` breaker, `max_tokens` on every call, bounded 429/529 retries, content-hash cache, step cap, up-front estimate of judge K x elements | P0 | HANDLED (`--max-usd`, cache, step cap) |

## Legal, human, eval, ops

| # | Case | Handling | Tier | Status |
|---|---|---|---|---|
| 60 | robots `Allow` (hoppscotch.io), `Content-Signal: ai-input=yes` (docs), MIT repo and docs repo, all verified | Identify the bot; commit only sample screenshots and excerpts; do not redistribute `llms-full.txt`; attribute. No anti-automation clause found by keyword grep (not exhaustive) | DOC | DOCUMENTED |
| 63 | Circular eval: synthetic-PR recall reuses the links that built the graph | One independent signal: source-map oracle, else 25 hand labels | P1 | HANDLED (source-map oracle ran: 124 scored, top-1 64/117 of covered) |
| 64 | Variance: cache hits mask run-to-run variance | Stability runs bypass cache; manifest records model ids, bundle hash, commit shas | DOC | DOCUMENTED (stability N=5 was not run) |
| 71 | Oracle cannot label UI introduced by the open demo PRs (not in the deployed build) | State in the doc; PR-level scoring restricted to files present on main | DOC | DOCUMENTED (open-PR UI has no oracle label) |
| 72 | Judge cost is a lower bound (thinking always on); judge shares a vendor family with the generator; seeded corruptions overstate TNR | Report TNR as an upper bound with Wilson CI; state the caveat | DOC | DOCUMENTED (judge not run, so no TNR) |
| 65 | Observability | One `run_id`; JSONL spans per stage and LLM call; failure taxonomy; `trace tree <run_id>` | P0 | HANDLED (`trace.jsonl` and `blastradius trace`) |
| 66 | Environment: Docker daemon off, no `ANTHROPIC_API_KEY`; system `urllib` fails TLS verification | `doctor` surfaces both; use `httpx`/certifi | DOC | DOCUMENTED (`doctor` is the preflight) |
| 74 | Home Assistant: cold load of `#/lovelace/home` renders empty for 16s+ (3/3) | Seed `#/lovelace/0` and assert a minimum element count before capture (target not built; recorded) | DEFER | DEFERRED (Home Assistant was not built) |
| 77 | Authenticated crawl on a real account can mutate the owner's project (flags, destinations, invites, events) and captures real data (PostHog) | Not built. When added: a read-only action class, a dedicated test project, a human-made `storage_state`, redaction of captured DOM | DEFER | DEFERRED (no authenticated crawl) |
