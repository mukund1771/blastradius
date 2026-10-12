# blastradius

**An intent-aware change-impact agent.** It crawls a live web app, ingests its product spec, connects both to the
source code that built it in a Neo4j graph (Requirements, UI, Code), and, given a real pull request, tells a QA lead
which screens, flows and requirements are at risk, what it could not determine, and why.

 The design document is [docs/DESIGN.md](docs/DESIGN.md);
the decisions, evidence and edge cases behind it are in [docs/DECISIONS.md](docs/DECISIONS.md),
[docs/RECON.md](docs/RECON.md) and [docs/CASES.md](docs/CASES.md).

| | |
|---|---|
| Target | [Hoppscotch](https://hoppscotch.io) (open source API client, Vue 3), repo `hoppscotch/hoppscotch` |
| Spec | `docs.hoppscotch.io/llms-full.txt` (49 of 104 pages selected) |
| Demo PRs | #6645 (environments, primary), #6641 (compare button), #6658 (hub package, ambiguity case) |
| Second target (adapter proof) | Sylius v2 demo, Twig, Gherkin requirements |
| Sample output | [docs/sample-report-pr-6645.md](docs/sample-report-pr-6645.md) (HTML beside it). A keyless copy of the run is in [data/runs/sample](data/runs/sample) |

## Quickstart

Prerequisites: Python 3.12+, Docker (for Neo4j), `gh` CLI authenticated to GitHub (used for PR metadata), an
Anthropic API key. About 2 GB of disk (a sparse clone of the target repo, Chromium, Neo4j).

```bash
git clone <this repo> && cd blastradius
make install                  # venv, dependencies, Playwright Chromium
cp .env.example .env          # then set ANTHROPIC_API_KEY (see "Keys" below)
make up                       # Neo4j 5.26 in Docker, waits for health
make doctor                   # preflight: key, models, Docker, Neo4j, browser, target reachability. No spend.
make demo                     # crawl -> ingest -> index -> load -> link -> gaps -> flows -> pr 6645 -> eval
```

`make demo` writes everything into one run directory, `data/runs/<run_id>/`, and prints the paths. The PR report
is `data/runs/<run_id>/reason/6645/report.md` (and `.html`). Open Neo4j Browser at <http://localhost:7474>
(user `neo4j`, password from `.env`) to explore the graph.

### Keys

- `ANTHROPIC_API_KEY` in `.env`. Never commit `.env` (it is gitignored); `doctor` never prints the key.
- If the API answers *"This API key is not scoped to a workspace"*, set `ANTHROPIC_WORKSPACE_ID=wrkspc_...`
  (Console, Settings, Workspaces), or create the key inside a workspace.
- `BLASTRADIUS_MAX_USD` is a per-run spend circuit breaker (estimated from response usage, not billing).

### Without a key

Everything deterministic still runs: `crawl`, `index`, `load`, the UI-to-code cascade, `flows`, and a PR report
built from a template (no model-written prose). Add `--offline` to any stage to forbid API calls and use the
content-addressed cache only. Requirement extraction, requirement-to-UI judging and the narrative need a key.

## The stages

Each stage reads and writes artifacts in the run directory and cites evidence ids; run them one at a time with
`blastradius <stage> -t <target>` (or `make <stage>`). Stages of one pipeline share the latest run; pass
`--run <id>` to choose, and `crawl --new` to start fresh.

| Command | What it does | Needs |
|---|---|---|
| `crawl` | Playwright explorer: screens, elements, transitions, screenshots. Safety classes, egress allowlist, robots, breaker | network |
| `ingest` | Fetch the spec, select pages, extract atomic requirements, verify every quote verbatim | key |
| `index` | Sparse-clone the repo at a ref; Vue SFC adapter: files, import/render edges, routes, i18n keys | network |
| `load` | Write the three layers into Neo4j (idempotent; prunes only complete layers) | Neo4j |
| `link` | UI to code (route, reachability prior, i18n key, literal, LLM pick capped 0.60); requirement to UI (BM25 + judge) | key for the judge |
| `gaps` | Absence as evidence: `NO_UI_EVIDENCE` with effort, `ACCESS_RESTRICTED`, `NOT_UI_TESTABLE`, `UNMAPPED_CODE` | |
| `flows` | Flows from observed transitions | |
| `pr N` | Blast radius for PR N: retrieve, grade, narrate, verify, render | key for prose |
| `eval` | Invariants, synthetic recall, source-map oracle, offline judge with seeded corruptions, stability | key |
| `doctor` | Preflight | |
| `trace tree` | Reconstruct a run from its trace (spans, LLM calls, failures, cost) | |

### Run a different PR or target

```bash
PYTHONPATH=src .venv/bin/python -m blastradius pr 6641 -t hoppscotch
make demo TARGET=sylius PR=19187       # second target: Twig adapter, Gherkin requirements
```

Open PRs can be merged or closed over time; if a PR number no longer resolves, pick another open one with
`gh pr list --repo hoppscotch/hoppscotch --state open`.

## What the crawler will and will not do

- Stays inside `targets/<name>.yaml: crawl.egress_allow` (enforced at the network layer, including redirect hops
  and WebSockets). Third-party widgets are never touched.
- Never logs in, signs up, submits forms to third parties, or clicks anything destructive or authenticating.
  *Send* is allowed only to the allowlisted echo endpoint, re-checked at click time.
- Fetches `robots.txt` first and honours it. Identifies itself (`blastradius/... (+contact)`; set
  `BLASTRADIUS_CONTACT` in `.env`).
- **Never attempts to evade WAFs, bot management or CAPTCHAs.** A blocked capture becomes an `ACCESS_RESTRICTED`
  gap and the run says what the app owner must allow (an allowlist, a service token, a staging environment, or a
  recorded session via `--source recorded --recorded-dir`).

## Tests

```bash
make test           # full suite (needs Neo4j for the graph tests; live-network tests are marked slow)
make test-fast      # no slow tests
make lint
```

Determinism is tested across processes with different `PYTHONHASHSEED` values, and every security fix ships a
regression test that fails on the pre-fix code.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ModuleNotFoundError: blastradius` outside pytest on macOS | The editable-install `.pth` can get a hidden flag that Python 3.13+ skips. Use `make` targets or `PYTHONPATH=src` (pytest is configured), or `chflags nohidden .venv/lib/python*/site-packages/__editable__*.pth` |
| `doctor`: docker FAIL | Start Docker Desktop, then `make up` |
| `doctor`: workspace FAIL | Set `ANTHROPIC_WORKSPACE_ID` (see Keys) |
| A stage reports `ACCESS_BLOCKED` | The target's WAF blocked the crawler. Read the failure's `user_action`; ask the owner to allowlist it or supply a recorded session |
| Neo4j: `Neo.ClientError.Procedure` / `CALL (vars)` syntax error | Neo4j older than 5.23; use the pinned image in `docker-compose.yml` |
| Graph looks stale after a new crawl | A partial layer is never pruned (the guard names the reason in `graph/load.json` notices); re-run a complete crawl |

## Repository layout

```
src/blastradius/
  access/   WAF/bot-management classifier, robots, breaker, recorded sessions
  crawl/    explorer, perception, fingerprints, action classes, policy
  spec/     spec fetch, normalisation, quote-verified extraction, Gherkin
  code/     repo checkout, Vue/Twig adapters, file classes, PR head overlay
  graph/    Neo4j schema, idempotent writes, named read templates
  link/     UI to code cascade, requirement to UI judge, gaps, flows
  reason/   PR fetch, retrieval, CRAG-inspired grading, narration, verification
  report/   report model, markdown/HTML rendering, lint
  evalkit/  invariants, oracle, offline judge, stability
  run.py llm.py trace.py ledger.py evidence.py failures.py safety.py redact.py   shared services
targets/    hoppscotch.yaml, sylius.yaml (adding a target must not touch the core)
docs/       DESIGN, DECISIONS, CASES, RECON, ARCHITECTURE, REPORT_SPEC
tests/      unit, integration (live Neo4j), real-data (slow-marked), determinism
```
