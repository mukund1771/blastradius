# Recon: evidence behind the decisions

Everything the design leans on, with how well it is known. Collected 2026-10-12 by 17 read-only agents
(8 researchers, 7 skeptic verifiers, 2 critics; 548 tool calls). Nothing here was run through our own code yet.

**Evidence labels**

| Label | Meaning |
|---|---|
| **V** | Verified by a second, independent agent or against primary docs/source |
| **M** | Measured once by a researcher (curl, `gh`, in-browser probe); not independently re-run |
| **R** | Recalled or secondary-sourced; do not present as fact |
| **U** | Unverified or untestable on this machine |

## 1. Primary target: Hoppscotch

| Fact | Value | Label |
|---|---|---|
| Live app | `https://hoppscotch.io`, Vercel, Vite SPA, 5,219 B JS shell, no Cloudflare challenge, login optional | V |
| Version | Bundle string `2026.9.0`; tag and `main` were both `63273f8e59` at first check, 0 ahead | V |
| Deployed vs main | Source map (45 MB, 2,577 sources, all with `sourcesContent`) vs main: 357 of 367 app files identical, 9 differ; in the main chunk 11 of 104 SFCs deviate (5 differ, 6 absent from public main, all `selfhost-web/src/components`) | V |
| Stack | Vue 3.5, vue-router 4, vue-i18n 11, Tailwind, pnpm monorepo. UI in `packages/hoppscotch-common/src`: 335 `.vue`, 522 `.ts`, about 195K LOC | V |
| Routes | File-based via `vite-plugin-pages` (`common/src/pages`, `selfhost-web/src/pages`); layout `layouts/default.vue` | V |
| Auth-free screens | `/` (REST), `/graphql`, `/realtime` (+ websocket/sse/socketio/mqtt), `/settings`, `/import`; about 25 in-page tabs and modals. Login-gated: `/profile*`, `/join-team`, `/enter` | M |
| i18n | `locales/en.json` 2,198 leaf keys; about 3,200 `t('key')` call sites over 1,401 keys; 213 of 1,903 distinct values shared by several keys | M |
| Auto-import table | `src/components.d.ts` committed (34 KB), `unplugin-vue-components` with `directoryAsNamespace: true`; `Hopp*` resolved from external `@hoppscotch/ui` | V |
| Selectors | `data-testid`: 16 occurrences in 6 source files (1 in live DOM); about 81 static `id=` | M |
| Scoped styles | 40 of 337 SFCs (11.9%) use `<style scoped>`; none under `environments/` (main-chunk map: 18 of 104) | V |
| Spec | `https://docs.hoppscotch.io/llms-full.txt`: 520,910 B, 10,326 lines, 104 pages incl. 20 self-host, privacy, terms, security; 5 fetches identical; changelog tops at 2026.5.0 | V |
| Spec gating | "only available in the Desktop App" (cookies), AI editing Cloud/Desktop only, "coming soon" x3 | M |
| robots | `hoppscotch.io: Allow: /`. `docs.hoppscotch.io` disallows `/cdn-cgi/`, `/_next/`; `Content-Signal: ai-train=yes, search=yes, ai-input=yes`; response `link: </llms-full.txt>; rel=llms-full-txt` | M |
| Crawl hazards | Crisp chat, PostHog, Firebase live; unknown paths return 200 `text/html`; sitemap has 44 URLs incl. 19 `/admin`, 7 `/orgs`; a service worker is served; 375px = 52 interactive elements vs 59 at 1024px; accessible names embed shortcut glyphs | M |
| Licence | MIT (app and docs repos) | V |

### Demo PRs (all open, all fork heads)

| PR | Title | Files | Why chosen |
|---|---|---|---|
| **#6645** | edit environment values from quick peek and the sidebar | 14: `components/environments/{CurrentValueInput,Editor,Selector,index}.vue`, `my/*`, `teams/*`, 2 services + 2 specs, `en.json`, `components.d.ts` | Shared env UI + services; fully usable without login. Base 2026-08-28; main 10 commits / 95 files ahead; overlaps `en.json`, `components.d.ts` |
| **#6641** | add compare button | 13: `components/{http,graphql,gql}/Response*.vue`, `ResponseCompareModal.vue` x2, `helpers/diff.ts`, `newstore/responseHistory.ts`, `vite.config.ts`, `en.json`, `components.d.ts` | Fans out over REST and GraphQL; 4 of 10 code files have no baseline node |
| **#6658** | refresh collections imported from URL | 12 incl. `packages/hoppscotch-data/src/collection/v/13.ts`, `lib/sync/collections/*` | Hub package (`@hoppscotch/data` imported by 107 of 413 app files); schema v13 |

Fallback only: merged **#6447** (14 files, June 24): already in main, post-hoc.

## 2. The three initial candidates

| | Hoppscotch | Memos | Saleor storefront ("Paper") |
|---|---|---|---|
| Live | hoppscotch.io, whole product, no login | demo.usememos.com, guest: `/explore`, `/calendar`, `/map`, `/memos/:uid`, `/about`; editor/settings behind Clerk SSO; Cloudflare | storefront.saleor.io, SSR, browse/cart ok, checkout needs cart state |
| Stack | Vue 3, vue-i18n | React 19, react-router 7, i18next, 471 TS/TSX in `web/src` | Next.js 16 App Router, next-intl, 652 TS/TSX |
| Spec | `llms-full.txt` 520 KB | `llms-full.txt` 508 KB, but only ~65-100 KB (13-20%) UI-observable; 48% is changelog | **No consumer PRD**; developer docs, ~40-60 inferred requirements |
| Drift | live == main at first check | Manual deploy workflow; version 26.10, commit `a80fe19` | Deploys each merge; repo in flux: PRs #1252 (125 files), #1253 (49), #1255 (151) on Oct 9 |
| Verdict | Chosen | Fallback second target | Rejected: "requirements lose coverage" would be invented |

Memos PR chain verified end to end: live DOM `separator "Resize sidebar"` -> `common.resize-sidebar` -> `SidebarResizeHandle.tsx:183`
-> PR #6148. Memos is the cheapest generalisation proof (React, explicit imports) if the Sylius slice fails.

## 3. Access, WAF and bot management

### Measured headers (single `HEAD`, UA `Mozilla/5.0`, 2026-10-12 06:03Z) [M]

| Host | Result | Fronted by | Note |
|---|---|---|---|
| hoppscotch.io | 200, `content-length: 5219` | Vercel (`x-vercel-id bom1`) | SPA shell is tiny; **never classify on body size** |
| docs.hoppscotch.io | 200 | Cloudflare (`cf-ray`, `cf-cache-status: HIT`) | **Presence of `cf-ray` is not a block** |
| demo.usememos.com | 200 | Cloudflare, Render origin | same |
| storefront.saleor.io | 308 -> `/en/default` | Vercel | `browse-locale` cookie, 1 year |
| www.amazon.com / .in | 200 | CloudFront (`x-amz-cf-id`, `x-amz-cf-pop BLR50`) | **No WAF header seen. One HEAD proves nothing**; WAFs score GET and full browsers differently |
| demo.nopcommerce.com, docs, www; demo.opencart.com | **403 `cf-mitigated: challenge`** with a Mozilla UA | Cloudflare | Recorded as classifier fixtures; real-browser behaviour deliberately not tried |

### Signature table (what the classifier may key on)

| Vendor | Hard (block) signal | Presence only (not a block) | Label |
|---|---|---|---|
| Cloudflare | `cf-mitigated: challenge` + `text/html`; separate rule for Block pages (403/1020, no such header); body `Just a moment...`, `/cdn-cgi/challenge-platform/` | `server: cloudflare`, `cf-ray`, `__cf_bm` | header V; body R |
| AWS WAF | `x-amzn-waf-action: challenge` (**HTTP 202**) or `captcha` (**405**); interstitial body only if `Accept` includes `text/html`; no CORS headers, read at network layer | `aws-waf-token` cookie, CloudFront headers | V (vendor docs) |
| Akamai | `server: AkamaiGHost` + 403 + `Access Denied ... Reference #` | `_abck`, `bm_sz`, `ak_bmsc` | R / S |
| DataDome | cookie presence + body referencing `captcha-delivery.com` + 403 (`x-datadome-*` are internal API headers, **do not use**) | `datadome` cookie | S |
| HUMAN / PerimeterX | body `Press & Hold`, `collector-*.perimeterx.net`, 403 | `_px3`, `_pxhd`, `_pxvid` | S |
| Imperva | body `Request unsuccessful. Incapsula incident ID`, `_Incapsula_Resource` | `X-Iinfo`, `visid_incap_*`, `incap_ses_*` | S |
| Vercel | header `x-vercel-mitigated: challenge\|deny` on any status; title `Vercel Security Checkpoint` (soft) | `server: Vercel`, `x-vercel-id` | R (synthesized from documented markers) |
| Amazon robot check | 200 page, `Type the characters you see in this image`, form to `/errors/validateCaptcha` | none | R |
| Vendor-agnostic | status in `{202,403,405,429,503}` with HTML where JSON/app expected; titles `Just a moment` / `Access Denied` / `Robot Check`; zero interactive elements after hydration; **fingerprint collapse** (many URLs, one fingerprint) | none | design |

### Cooperative access (owner-side) [V unless noted]

Ranked by robustness: (1) staging without WAF; (2) WAF skip/allow rule on a pre-shared secret header (survives IP
churn); (3) dedicated UA token; (4) IP allowlist of the tool's egress; (5) Cloudflare Access service token
(`CF-Access-Client-Id` / `CF-Access-Client-Secret`, action `Service Auth`); (6) AWS WAF scope-down or label rules;
(7) Web Bot Auth, RFC 9421 signed requests (Ed25519, `Signature-Agent`; IETF draft, R). Cloudflare WAF *Skip*
can skip rate limiting, managed rules and Super Bot Fight Mode, **not** Bot Fight Mode on the Free plan. Testsigma
documents IP lists (Server and Lab) and a tunnel; Checkly (IP ranges, header, UA with account UUID), Datadog
(`sec-datadog`, `x-datadog-origin: synthetics`, UA suffix; identification only) and QA.tech (`cf.client.bot`,
UA `QATechBot`, shared header, Turnstile test sitekeys) document the same patterns. QA.tech notes a WAF rule
cannot remove an *embedded* Turnstile widget (so `EMBEDDED_CHALLENGE` is a per-element class, P2).

### Offline ingestion

| Artifact | Keeps | Loses | Label |
|---|---|---|---|
| `trace.zip` | actions, before/after DOM snapshots, network, screenshots; replays offline | unvisited states; transitions only for recorded steps; snapshot deltas are versioned (V3..V10), parser is a 1.5-2h job | V (contents), U (hours) |
| HAR + `page.content()` | static DOM and bodies | replay needs strict URL/method/POST match | V |
| MHTML / SingleFile | one frozen DOM | transitions | R |
| `connectOverCDP` | live user-driven session | documented as "significantly lower fidelity"; Chromium only | V |

HAR and traces hold live tokens: redact `Cookie`, `Set-Cookie`, `Authorization` on import.

### Amazon-style patterns that stress a DOM crawler and a graph [R]

Hover mega-menu (hundreds of links, state explosion); lazy/IntersectionObserver carousels; infinite scroll;
A/B variants (unstable fingerprints, duplicate Screens); delivery-location modal (precondition state);
cross-origin ad iframes (outside main-frame DOM); personalization; locale by ccTLD/path/cookie. Graph consequences:
variant-tolerant fingerprints + `observed_variant`; a `precondition` concept (location set, locale); mask ad
iframes; cap scroll and menu expansion with recorded effort. Closed source means the Code layer is absent: the
legitimate deployment is a customer's own app, where allowlisting is available.

## 4. Second-target recon

### Sylius v2 demo (chosen, P1b)

| Fact | Value | Label |
|---|---|---|
| Live | `https://v2.demo.sylius.com/en_US/`, "Community Edition, public access, no login"; Cloudflare-fronted, answered 302/200, **no challenge**; robots has no Disallow | V |
| UI | Server-rendered Twig + Symfony Live Components: taxon grid, sort, page size 9/18/27, search, variant select, add to cart, cart, reviews, promo labels, mini-cart. **No faceted filters**: Amazon-likeness partial | M |
| Spec | 855 `.feature` files; 352 under `features/shop` = 1,006 scenarios; 906 `@ui`, 99 `@no-ui` (API-only = free `NOT_UI_TESTABLE` ground truth); guest slice (product, cart, homepage, locale, currency, contact) about 265 scenarios. Public docs: `https://docs.sylius.com/public/llms-full.txt` (413 KB part 1 of 2, 108 pages); `llms-full.txt` proper is behind OIDC | V |
| Code | 608 ShopBundle templates; hook wiring in 42-44 YAML files (declarative, no import resolution); strings `'sylius.ui.x'|trans`; Twig components name their PHP class in a header comment; prod DOM strips most `data-test` (2 kinds seen live); 26 of 73 live strings matched a catalog key, the rest is catalog data | M |
| **Drift** | Deployed version **unresolved**: live footer markup predates #19170 (v2.2.8 or earlier or a local override) and lacks the Stripe logo that 2.2 HEAD and v2.2.10 render. Bundle `shop-entry.2545a8fa.js` last-modified 2026-09-02, which does not equal template version | V |
| PRs | All merged to 2.2: **#19187** (2 files: `offcanvas/cart.html.twig`, `_offcanvas.scss`; base `64360308fa6b`), **#19170** (29 files, 22 locale, footer payment methods; base `e541c8c5ce25`), #19146 (feature + page object + template; needs a placed order, unreachable: needs-human path). 193 merged in 6 months, about 25 of 125 mid-size merges touch shop UI | V |
| Bonus | For #19170 the live UI is the genuine *before* state; the added Stripe logo is absent from the crawl: a clean absence-model example | V |

### Others

| Candidate | Finding | Verdict |
|---|---|---|
| **Home Assistant demo** | Real frontend on in-browser stubs; no login. Lit + shadow DOM: Overview has 485 open shadow hosts, 1,906 deep elements vs 44 in light DOM; `innerText` empty. Playwright *locators* pierce open shadow roots (not XPath, not closed). **Cold load of `#/lovelace/home` stays empty (3/3)**: seed `#/lovelace/0`. Prod tag `20260930.3` (sha `3f7f4613`), public sourcemap (829 KB, 752 sources). 1,736 `@customElement` files, tag-to-file ~1:1; ~800-1,300 files use literal `localize()` (count is query-dependent); 0 `data-testid`. No `llms.txt`: 84 `_docs` + 43 `_dashboards` + 46 more-info pages in raw GitHub, ~0.5h fetcher. PR #54266 (merged, 4 files, ancestor of the tag) reproducible; open #54821 (13 files) moves with a dev demo that redeploys several times a day | P2 (3-4h) |
| **Memos** | Section 2. React/TSX adapter would need: import bindings + relative/alias resolution + barrels; JSX tags; `t('key')` calls | Fallback |
| **Plausible** (public dashboard) | 896 elements, 0 shadow hosts, 146 `data-testid`; no i18n, no sourcemap, no version string (v3.2.1, 2026-05-15), live data churns fingerprints; clean frontend PRs (#6716 two files). Weakest spec | Dominated by Memos |
| **nopCommerce / OpenCart** | 403 `cf-mitigated: challenge` on demo, docs, www and robots; PRs tiny (14 merged in 6 months). Drive the degrade path from `nopCommerce-Docs` + 201 Razor views offline | **WAF fixtures only** |
| **Spree** | Vercel, Next.js 16, next-intl; 3.7 MB `llms-full.txt` mostly developer/admin docs (the Saleor problem again); robots disallows cart, checkout, `?sort/?filter/?page` | Runner-up |
| Rejected | Bagisto (launch + OTP sandbox), Vendure (redirects to `/admin`, storefront archived), Medusa starter (archived), Shopware (no response, 2,635 merges/6 mo), PrestaShop (launcher), Magento (no public demo), Immich (login page only), Cal.com (`/auth/login`, 1.1 GB), Grafana (4,613 TS files), Excalidraw (canvas), Penpot (ClojureScript), OSM (Rails) | |

### PostHog (evaluated at the user's request after the target was chosen; 1 researcher + 1 verifier)

| Fact | Value | Label |
|---|---|---|
| Guest surface | `us`/`eu`/`app.posthog.com` 302 to `/login`; `/login` and `/signup` 200 (~240 KB JS shell + JSON app context); `/demo` 302s to login. More scenes are `allowUnauthenticated` (Authorize, VerifyEmail, TwoFactorReset, ...) but all are token/link-driven. **No free-roam guest product** | V |
| Build pinning | `commit_sha` is embedded in the unauthenticated HTML (`34a467e986`, 2 commits behind master); continuous deploy, sub-hour lag | V |
| Source maps | Production maps public with `sourcesContent`, byte-match git; map-vs-JS mtime gap means pairing is unverified; 2,330 lazy chunks | V |
| DOM -> code | `data-attr` in 2,576 `.tsx` files vs `data-testid` in 61; survives minification into the prod DOM: a **strong deterministic rung** Hoppscotch lacks | V |
| Stack | React 18.3, Kea logics, TS, Tailwind 4; `frontend/src` 3,551 `.tsx` + 2,362 `.ts`; `products/*/frontend` ~5,532 `.tsx` (79 of 98 products; the root tree call timed out, so this total is not re-verified); ~1,300 `*Logic.ts`; no i18n | M |
| Monorepo | Python `posthog/` 5,216 files, `ee/` 854 (separate licence), Rust 1,485, plugin server 1,791 TS; repo 7.8 GB | M |
| Spec | `posthog.com/llms.txt` 440 KB, index only; `llms-full.txt` 404; ~1,100 product docs, 2,355 API links; guest-observable requirement yield ~15-25 (estimate) | M |
| PRs | ~3,500 created/week, 1,877 open; heads are in-repo branches (7 of 150 newest open are forks); staleness 11-67 commits; **open guest-observable PRs: 0 in the sample**; merged login-surface PRs ~2/week | V (open), M (merged) |
| Terms | No automated-access clause found; closest is "circumvent authentication or security mechanisms"; applies to contracted customers. Signup uses Cloudflare Turnstile (risk-triggered, via WorkOS Radar); **account creation is off-limits for the agent** | M |
| Mutation risk | An authenticated crawl on a real project can toggle flags, edit destinations, send invites, pollute events; needs a human-made Playwright `storage_state` | read |
| Self-host | Hobby deploy: 4 vCPU, 16 GB RAM, 30 GB+ disk | read |
| Not re-verified | The 7-10h cost estimate and the 3/10 score (judgement) | U |

Net: strong code-side evidence, but the live-crawl premise fails without a human-supplied authenticated session.
See D34 and D35 in DECISIONS.md.

## 5. Reasoning-phase measurements (Hoppscotch `main`, in-memory tarball parse) [V unless noted]

| Measure | Value |
|---|---|
| Graph | 839 code files, 3,265 edges (2,810 imports + 455 renders); 41 of 1,308 (file, tag) pairs unresolved (3%); 812 external `Hopp*`/icon tags; 83 dynamic `<component>` |
| Barrels | 248 barrel importers |
| #6645 services closure | **2 hops: 93/90 `.vue` (28%)**; 3 hops: 158/156 (47%); unbounded: 252/335 (75%). Both service diffs add four public methods and delete nothing |
| #6658 reverse BFS | 2 of 5 co-changed UI files at any depth <=3 (57 `.vue`); misses are a forward dependency (`UrlSource.ts` -> `UrlImport.vue`) and emit/prop plumbing. Undirected 1-hop BFS (8 `.vue`) already gives 3/5 |
| PPR | #6645: all six co-changed UI files in top 14-15 (BFS needs 93). **n=1**, only vs hop-2 BFS |
| Louvain | 33 communities, modularity 0.51, purity 0.47 vs directory labels; directory modularity 0.208. Largest community 146 nodes mixes helpers/http/gql/data |
| Leiden | networkx 3.7 `leiden_communities` returned 839 singletons: **default-resolution artifact, do not cite**; true Leiden (`leidenalg` 0.12.0 + `igraph` 1.0.0) resolves on 3.14 |
| Hub | 107 of 413 main-chunk app files import `@hoppscotch/data` |
| #6641 `en.json` | +27/-1: 24 leaf strings + 2 braces; the -1/+1 is a trailing-newline removal |

CRAG itself (arXiv 2401.15884): fine-tuned T5-large evaluator 84.3% vs prompted ChatGPT 58.0% (CoT 62.4, few-shot
64.7); Ambiguous action exists to reduce dependence on evaluator accuracy; Incorrect discards and falls back to
web search; thresholds set empirically per dataset. Ours is therefore **CRAG-inspired**.

Embeddings: Anthropic offers no embedding model (points to Voyage, separate key). BM25 via SQLite FTS5 works
(3.50.4); `rank-bm25` 0.2.2 resolves but pulls numpy. Neo4j vector index exists in Community since 5.13.

## 6. Oracle technique (test-only) [V on main chunk; cross-chunk totals not re-run]

| Fact | Value |
|---|---|
| PROD hooks | Vue 3.5.41 sets `el.__vueParentComponent` and `type.__file` only under `__DEV__ \|\| __FEATURE_PROD_DEVTOOLS__`; Hoppscotch never enables it. Live: 0/1,153 elements carry either |
| What survives | `container.__vue_app__` and `container._vnode` are unconditional, so the tree is walkable |
| Technique | `String(type.setup)` is a verbatim substring of exactly one chunk; **majority-vote** source-map segments over that span to a `.vue` (first-segment lookup mis-attributes to the previous module). Needed because 145 of 337 SFCs share 54 basenames |
| Main chunk | After 3s idle: 91 unique setup texts; 62 found exactly once, 0 ambiguous, 29 in other chunks; of the 62, 40 resolved to `.vue` at 100% vote, 22 to library files; 40/40 basename == `type.__name` |
| Element owner | Walking `vnode.ctx` to the first repo-owned instance: 195/195 interactive elements got an owner across 38 files, but 82% of element vnodes needed hops through `@hoppscotch/ui` wrappers. CodeMirror/Monaco DOM has no vnode |
| data-v | `sha256(relpath-from-vite-root + (isProduction ? source : '')).hex.slice(0,8)`, root `packages/hoppscotch-selfhost-web`; reproduced 18/18 against the map; 7 of 11 live ids matched (4 on library components) |
| `vite-plugin-vue-inspector` | **Not a dependency** (only `vite-plugin-inspect`); dev-only anyway |
| Local dev | Needs pnpm 10.34.5 (not installed), full-monorepo install because `gql-gen/` is produced by backend postinstall (prisma + nest); 2,704 lockfile entries; Node 22 is a CI pin only. Skipped |
| Limit | Cannot label UI introduced by the open demo PRs (code not in the deployed build). Pin each map by ETag + hash at harvest time |

## 7. Observability

| Option | Result |
|---|---|
| OTel SDK 1.45.1 (3 pure-Python wheels) + custom JSONL SpanProcessor | Resolves on 3.14. Cross-process traces need `TraceFlags.SAMPLED` on a synthetic parent or children vanish silently. **We hand-roll JSONL with OTel field names instead** |
| `openinference-instrumentation-anthropic` 3.0.3 | Resolves; wraps `messages.create/stream/parse` and records prompts by default; skipped (we own cache, cost, retry) |
| Phoenix / Jaeger | Optional OTLP sinks (P2) |
| Langfuse self-host, Logfire, Weave | Rejected: server or account breaks zero-infra reviewer |
| 28-package set | sdk + instrumentor + anthropic 1.13.0 + neo4j 6.4.0 + playwright 1.63.0 + pydantic 2.14.1 resolves |

Span taxonomy `run > stage > {llm_call, browser_action, cypher_query, graph_write, verifier_check}` + `http_fetch`.
Attributes per kind and the closed failure enum are in the plan (section 8); redaction by allowlist with a canary test.
`doctor` exits non-zero on FAIL and would fail today on exactly Docker and the API key.

## 8. Judge and eval facts [V from papers; cost U]

| Fact | Source |
|---|---|
| Position, verbosity, self-enhancement bias; GPT-4 consistent in only 65% of order swaps; reference-guided judging cut math failures 14/20 -> 3/20 | Zheng et al. 2306.05685 |
| Self-preference tracks self-recognition | 2404.13076 |
| Self-correction without external feedback degrades | Huang et al. 2310.01798 |
| Atomic-fact precision within <2% of humans | FActScore 2305.14251 |
| Binary decomposed scoring; Prometheus Pearson 0.897 vs GPT-4 0.882 over 45 rubrics | InFoBench 2401.03601; 2310.08491 |
| Percent agreement hides score gaps | 2406.12624 |
| Small-n arithmetic: 27/30 = 90%, Wilson 95% CI [74%, 96.5%]; always-supported judge at 90% prevalence has kappa 0.00 | computed locally |
| Opus 5.5 and Sonnet 5.5 reject non-default sampling params | claude-api skill table cached 2026-09-25 (docs, not a live call) |
| Judge cost | Lower bound only: thinking always on and billed as output; expect 1.5-3x |

## 9. This machine (observed 2026-10-12)

| Item | State |
|---|---|
| Python 3.14.7; Node 24.20.0 | present; no `uv`, no `pnpm`, no Java |
| Docker | `Docker.app` installed, **daemon not running** |
| `ANTHROPIC_API_KEY` | **not set** in the shell |
| Playwright | chromium 1243/1244 cached; Python `playwright 1.63.0` will need `playwright install chromium` (no sudo) |
| `gh` | authenticated as `mukund1771` |
| Python TLS | system `urllib` fails certificate verification: use `httpx`/certifi |
| Wheels | playwright, neo4j 6.4.0, anthropic 1.13.0, tree-sitter 0.26.0, tree-sitter-typescript 0.23.2, pydantic 2.14.1, httpx, rich, typer, rank-bm25 all resolve on 3.14 (pip `--dry-run`) |

## 10. Model and pricing claims (verifier-reported from live docs; not confirmed on this machine)

| Claim | Status |
|---|---|
| `claude-haiku-5-5` is current Haiku, $0.10/$0.50 per MTok up to 100k | U: `doctor` retrieves it |
| `claude-haiku-4-5-20251001` retirement "not sooner than 2026-10-15" | U |
| Sonnet 5.5 $2/$10, cache read $0.10/MTok (not $0.20) | U |
| 5.5 models return 400 on non-default `temperature`/`top_p`/`top_k`; `stop_reason=refusal` carries `stop_details` | U (docs) |
| Models API `retrieve` spends no tokens | U (GET metadata endpoint per docs) |

## 11. Process notes

- One researcher wrote and removed `/tmp/.llm_probe_*` scratch files; one verifier left `/tmp/x.html` (a `curl -o` of a
  public docs page). Both disclosed by the agents; read-only was the instruction.
- Another agent navigated the shared browser pane mid-run; the affected researcher used a dedicated tab.
- Hour estimates in the digests are unverifiable estimates; the critics' repricing is what the timeline uses.
