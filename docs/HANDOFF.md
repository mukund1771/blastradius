# Handoff: everything unfinished, exactly

Written 2026-10-12 ~20:20 IST for an engineer (Cursor) who will finish the work, followed by a code review.
It is the single source of truth for "what is left". Each item says **where**, **what is wrong or missing**,
**how to know it is done**, and **how to test it**. Nothing here is aspirational: every item was observed
(by running it, by an independent reviewer with a reproduction, or by diffing the plan against the tree).

## 0. Ground rules

1. **Test first.** Every fix gets a regression test that fails on the current code and passes after. A fix with
   no such test is not done. (Several defects in this project passed their own builders' suites; independent
   review found them. See [DESIGN.md](DESIGN.md) section 10.)
2. **Determinism.** Every artifact must be byte-identical across processes with different `PYTHONHASHSEED`
   (iterate `sorted(...)`, never set order; tie-break explicitly). `tests/test_determinism.py` shows the pattern.
3. **No secrets.** Never read, print or commit `.env`. The LLM is called only through `blastradius.llm.LLM`
   (`structured(...)`); text from pages, specs, PRs, locale strings is wrapped with `safety.untrusted` and the
   call's system prompt includes `UNTRUSTED_PREAMBLE`.
4. **Safety and egress.** Never weaken the egress allowlist, the Send gate, the destructive/auth classes or the
   WAF stance (detect, quarantine, degrade; never defeat). See DECISIONS.md D11-D14, D28.
5. **Honesty.** If a number is not measured, say so. If something is designed-not-built, it goes in DESIGN.md as
   such. Do not tune thresholds to make a metric look good (`eval/thresholds.json` is pre-registered).
6. **Commands.** On this Mac the editable install's `.pth` is flagged hidden by the OS, so scripts need
   `PYTHONPATH=src` (the `Makefile` and pytest config already set it). `make test-fast`, `make lint`, `make doctor`.
7. **Do not edit files another lane is editing** (section 2).

## 1. Snapshot (what is true right now)

| | |
|---|---|
| Code | `src/` 27.1K lines, `tests/` 22.9K lines; 2,219 tests in 58 files (18 slow-marked, need network/Chromium) |
| Last commit | `0d60476` "checkpoint before verification"; ~25 files uncommitted (wave 3, see lanes) |
| Lint | `ruff check src tests`: **43 errors, 34 auto-fixable** (`cli.py` 18, `run.py` 6, `tests/test_foundation.py` 5) |
| Verified by an independent reviewer and accepted | `access` (458 tests), `spec` (257) |
| Reviewed, fixes in flight | `link`, `graph`, `report`, `code`, `crawl` (open majors listed below) |
| **Never independently verified** | `reason`, `evalkit`, `sylius` slice (their verifiers failed to run; re-queued in wave 3) |
| Live runs done | crawl 150 actions (55 screens, 1,468 elements, 0 blocks); Sylius crawl 30 actions (25 screens); PR reports #6645, #6641, #6658 on the deterministic (no model) path |
| **Never run with a real model** | spec extraction, link LLM pick, requirement->UI judge, narration, residue judge, offline judge, LLM crawl policy. `doctor` fails: the API key is not workspace-scoped |
| Eval results | none persisted. `blastradius eval` has never been run to completion |

What works end to end today with no key: `crawl`, `index`, `load`, `link` (deterministic cascade), `gaps`,
`flows`, `pr N` (template narrative), `ingest -t sylius` (Gherkin, no LLM), `index`/`crawl -t sylius`.

## 2. Lanes (avoid edit conflicts)

Four fixers are editing right now (wave 3). **Do not touch these until they finish** (check `git status`; they
are done when their verifiers report):

| Lane | Paths | In flight |
|---|---|---|
| A1 link | `src/blastradius/link/`, `tests/test_link_*.py` | gap staleness, dialog-title disambiguation |
| A2 graph | `src/blastradius/graph/`, `tests/test_graph*.py` | shrink guard, dangling `screen_ids` |
| A3 report | `src/blastradius/report/`, `tests/test_report_*.py` | mobile overflow, vocabulary |
| A4 code | `src/blastradius/code/`, `tests/test_code_*.py`, `tests/test_determinism.py` | file-class regression, script-literal keys |

**Free now:** `reason/`, `evalkit/`, `crawl/` (minors only), `cli.py`, `doctor.py`, `Makefile`, `docs/`,
`targets/`, repo hygiene. Items below are tagged with their lane.

## 3. Blocked on the owner (cannot be done in code)

| # | What | Why |
|---|---|---|
| U1 | Put `ANTHROPIC_WORKSPACE_ID=wrkspc_...` in `.env` (Console > Settings > Workspaces), or create a workspace-scoped key | `blastradius doctor` fails with "key not scoped to a workspace"; every model call 400s until fixed. `llm.make_client` already sends the header |
| U2 | Approve creating the public GitHub repo and pushing | outward-facing; not done without approval |
| U3 | Record the Loom (5-10 min), send the email | submission |

## 4. P0: required for a credible submission

### T1. Run the model-dependent phases and record real numbers (after U1)

```bash
make doctor                       # must be all PASS (key, workspace, models, Neo4j, Chromium, target reachable)
make demo                         # crawl -> ingest -> index -> load -> link -> gaps -> flows -> pr 6645 -> eval
PYTHONPATH=src .venv/bin/python -m blastradius pr 6641 -t hoppscotch
PYTHONPATH=src .venv/bin/python -m blastradius pr 6658 -t hoppscotch
```

Acceptance: `ingest` writes `spec/requirements.json` with quote-verified items (record counts,
`dropped_by_reason`, cost from `ledger.jsonl`); `link` reports LLM calls and the confidence histogram; the PR
reports say "N requirements" > 0 and cite spec sections. **Record**: requirement counts, link coverage, LLM
spend, report sizes. These fill the 11 `⟦...⟧` placeholders in DESIGN.md. Hand-check 20 extracted requirements
(quote supports the text? edition/polarity right?) and report precision honestly (small n).

### T2. Reasoning report defects (lane: `reason/`; found on the real #6645 report)

Run `PYTHONPATH=src .venv/bin/python -m blastradius pr 6645 -t hoppscotch --run crawl-gate-150 --offline`
and read `data/runs/crawl-gate-150/reason/6645/report.md` as a QA lead. Defects:

| # | Defect | Fix | Test |
|---|---|---|---|
| a | **6,300 words**; appendix cites 71 evidence ids for 10 claims, many duplicates (the same "Wiki" element repeated per file) | Group evidence per claim: one line per (element, key) with "in N files"; cap per claim; full detail behind a flag. Main body stays within the lint word budget | report word count < ~2,000; every claim still cites >= 1 evidence id that resolves |
| b | **Weak indirect screens leak into the checklist** ("Take a quick look at the Login to Hoppscotch dialog on the WebSocket page": a login dialog does not use environment values) | Checklist = direct Confirmed/Likely + at most 2 indirect Likely; "Possible" items never become checklist actions | checklist contains no item whose band is possible/unknown |
| c | **"Controls involved" lists weak matches** ("Add new", "one of 18 files") at the same weight as unique ones | List only controls with link confidence >= 0.5 (or the changed file is their only candidate); "and N more" counts the rest | none of the listed controls has `one of k files` with k > 3 |
| d | **New keys attached to every direct screen** ("New in this PR here: 'Edit current value'" on Home page, Menu popup and Save-as dialog) though they do not exist in the live app | Emit one `Undetermined` "New in this PR; not observable in the live app yet" with the keys and what a person should open; never attach new keys to a screen | a PR that adds keys produces exactly one such item |
| e | **"direct impact on Home page: 93 control(s)"**: confirm "direct" means elements implemented by *changed* files or changed keys (hop 0), not importers at hop 1-2 | audit `reason/retrieve.py`/`assemble.py` tier assignment; the 93 may come from `pages/index.vue` being an importer | direct items' evidence all reference a changed file or key |
| f | **Stale requirements leak into reports**: this run has no spec, yet the report cites a requirement loaded by an earlier run | filter requirements by the current run's spec sha / run id; or have `load` replace the spec layer even when empty-by-design (status ok, 0 items) | a run with no spec produces "0 requirements" and no spec quotes |
| g | Offline checklist wording is mechanical ("click X and click Y and check it still behaves as before") | one action per direct item, using the nearest screen/dialog title and the control's label; keep "why" in product words | read by a human: each line is an action a person can take |
| h | Never use canonical `UIElement.owner_file` as evidence (arbitrary on tied floors: 36 files for "Cancel") | grep for `owner_file` in `reason/`, assert unused | AST/grep test |

### T3. Verify reasoning, eval and the Sylius slice (never reviewed independently)

Wave-3 verifiers `verify-reason`, `verify-eval`, `verify-sylius` are queued. Whatever they report is P0. Also check by hand:

- `reason`: prompt-injection in PR title/body/diff and `en.json` strings reaches neither the report body nor the
  narrator as instructions; the residue judge can only *demote*; every added item has a deterministic edge;
  a claim never cites an evidence id that does not exist (`reason/verify.py`); `--offline` never calls the API.
- `evalkit`: its tests are `tests/test_eval_*.py` (14 files, incl. `test_eval_import_lint.py`); check that the
  oracle never imports from `link/`, `reason/`, `graph/` and that the import-lint enforces it in both
  directions; no scratch files are left in the repo root (`index-*.js`, `chunks/`, `vnode_*.json` were created
  by the oracle work; they are now deleted and gitignored, make sure the code writes them under `data/cache/`).
- `sylius`: `git diff` shows **zero** edits to core files for the target (the "core lines changed" metric).

### T4. Eval: run it and persist results

`blastradius eval` has never completed. Run each and save under `data/runs/<id>/eval/results.json` + `eval.md`:

| Eval | Needs key | Acceptance |
|---|---|---|
| invariants on the real report | no | all pass, or offenders listed |
| synthetic-PR recall (labelled CIRCULAR) | no | recall + misses reported |
| source-map oracle (live, polite, <= 6 visits) | no | coverage, top-1 precision, hit@3 with Wilson CI **by method**; "deployed vs indexed: N of M files differ" |
| offline judge: 20 seeded corruptions + natural claims | yes | TNR as an *upper bound* with Wilson CI; k=3 majority; same-vendor caveat printed |
| stability N=5 (cache bypassed) | yes | Jaccard + per-item agreement; items < 60% demoted in a `stable_view` |
| grader ablation: A vs A+grader on the 3 PRs | no | pre-registered thresholds from `eval/thresholds.json`, unchanged |

Never print a metric without its n and interval.

### T5. Documentation (lane: `docs/`)

| # | Item | Detail |
|---|---|---|
| a | DESIGN.md placeholders | 11 `⟦...⟧` markers: crawl numbers, link coverage, oracle precision, requirement counts, PR #6645 result, tests, eval numbers, sample report link |
| b | Sample report | copy the final #6645 `report.md` to `docs/sample-report-pr-6645.md` (the brief requires it); add the HTML |
| c | CASES.md final statuses | all 77 rows still carry a *tier* (P0/P1/DOC/DEFER). Convert each to `HANDLED (path:line / test name)`, `DOCUMENTED (DESIGN section)`, `DEFERRED`, or `NOT_HANDLED` |
| d | DECISIONS.md additions (D36+) | prune guard: budget-bounded crawls prune, shrink guard; redactor key:value needs a secret-shaped value; `Capture.id` includes verdict; workspace-header client; `dialog prose vs label vocabulary`; obs-id/canonical-key policy; per-screen attribution + dangling fallback |
| e | ARCHITECTURE.md | sync with final signatures (link/reason/report/evalkit sections are thin); remove "wave 2" wording |
| f | README dry run | fresh clone, clean venv, no `.env` -> `make install && make up && make doctor && make demo` (no-key path) and the keyed path; fix every step that fails; keep the troubleshooting table accurate |
| g | Loom outline | `DESIGN.md` has it in the plan; produce a one-page script: QA-lead problem; access verdict + recorded WAF fixtures; graph + absence with effort; one PR -> CORRECT and AMBIGUOUS seeds -> two-tier report; explain one claim; honest numbers; what is designed-not-run |

### T6. Hygiene (lane: free)

- `ruff check src tests --fix` then hand-fix the rest (UP045 `Optional[X]`, UP037 quoted annotations, F401 unused
  imports, PLE2515, B008 typer `Option` defaults need `# noqa` or `Annotated`). `cli.py` is the worst (18).
- Full suite once, including slow: `make test` (the crawl explorer tests drive real Chromium, ~10 min). Record
  pass/fail counts; investigate any flake (time-bounded tests use SIGALRM at 1 s).
- Delete stale run dirs (`data/runs/crawl-fixed-45`, `det-*`, `gate-*`, `sylius-slice-1`, keep the final ones);
  `data/` is gitignored except `data/runs/sample/`.
- No `print` of secrets; grep the repo and a run dir for `sk-ant-`, `wrkspc_`, `Bearer ` (the redaction canary test
  covers the tracer, not every artifact).
- Consider `LICENSE` (MIT) and a minimal `.github/workflows/ci.yml` (fast tests with a Neo4j service container).

## 5. P1: planned and not built

| # | Item | Where | Notes |
|---|---|---|---|
| P1-1 | **Owner-granted access header** (rung L1) from env, redacted in logs, recorded on each capture | `crawl/explorer.py` context headers; `access/capture.py` rung; `targets.py` | only `models.AccessResult.rung` exists; nothing sets L1. Never log the value |
| P1-2 | **`blastradius bundle <run>`** (zip of redacted trace, ledger, failures, doctor JSON, report, sha256 list) and **`blastradius explain <claim_id>`** (claim -> evidence -> node -> span) | new `bundle.py`, `explain.py`, `cli.py` | promised in plan section 8; `trace tree` exists, these do not |
| P1-3 | **Committed sample run dir** | `data/runs/sample/` (gitignore already allows it) | a small real run (trace.jsonl, ledger, failures, evidence, report) so a reviewer can browse a trace with no key/Docker; run the canary scan over it first |
| P1-4 | **Recorded-session demo** | `crawl --source recorded --recorded-dir` | `RecordedSession` + tests exist; no end-to-end demo. Use the nopCommerce 403 fixture to show `ACCESS_RESTRICTED` in the Loom |
| P1-5 | **Bounded goal-directed re-crawl, live** | `link/gaps.py` (`recrawl=True`), `crawl` `artifact_subdir` | implemented against a fake `run_crawl`; never run live. Needs graph loader to merge `crawl/<subdir>/` explicitly (screen ids can collide across subdirs) |
| P1-6 | **Flows are 1 step** (30 flows = entry -> tab/modal) | `link/flows.py` | compose open-modal -> type -> confirm from `type` transitions; target >= 2 steps for modal flows |
| P1-7 | **testid/data-attr rung** (D35) | `CodeIndex.testid_files`, `link/ui_to_code.py`, `targets.CrawlConfig.testid_attributes` | empty on Hoppscotch (1 testid); needed for the "next target" story (PostHog: `data-attr` in 2,576 files) |
| P1-8 | **Placeholder-aware text matching** ("Show: 9", "Sort: by position") | `link/ui_to_code.py` | Sylius link rate is 20.8% mostly because of this and DB content |
| P1-9 | **Sylius PR reports** (#19187 base `64360308fa6b`, #19170 base `e541c8c5ce25`) | `code/overlay.py` (A4 lane), `reason/change.py` | needs overlay `context_paths` (adapter hook edges) and YAML-catalogue locale diff (`diff_locale` is `json.loads` only -> `head_unreadable`); use `LocaleDiff.added` values because `gh`'s `baseRefOid` for a merged PR is the base ref tip, not the merge-base |
| P1-10 | Crawl obs-id collisions | `crawl/perceive.py` | 20 of 363 observation ids (nameless buttons) belong to 2-4 canonical elements; include dialog scope + per-(owner,role,name) ordinal |
| P1-11 | `UIElementObs.ordinal` is per (role,name) per screen, not per owner | `crawl/perceive.py` | a control loses identity when a sibling from another component appears |

## 6. Open review findings (by module; severity from the independent verifiers)

Items marked (A1-A4) are already assigned to the in-flight lanes; list kept so nothing is lost if they fail.

**link** (A1): MAJOR gap staleness (`run_gaps` trusts `link_state.json` without comparing bundle hash);
minors: gaps without prior link emits 133 bogus UNMAPPED_CODE; page-title key use treated as rendering the
control (`usePageHead`); evidence text uses `texts[0]`; DOM `role`/`url_pattern` unredacted in prompts;
CONFIRMED_ABSENT 0.40 floor not effort-derived; per-screen confidence = best across screens; LLM pick limited
to R(screen); contradicted negatives only counted; corrupt inputs crash instead of recording.
**graph** (A2): MAJOR shrink guard after budget-bounded prune; MAJOR dangling `screen_ids` after re-crawl
(Screen ids churn); minors: `absence_near`/`unmapped_ui_elements` ordering; URI scrub with literal `@` in password;
`GRAPH_PRUNE_SKIPPED` never recorded; degraded-index guard; `hub_degree`/`absence_near` path enumeration at 60K edges.
**report** (A3): MAJOR long unbroken token overflows on phones (CSS `min-width:0`); minors: bidi isolates,
GFM autolinks, `RAW_ID` missing `itm/rev/und`, footnote order, 12 of 28 mutations survive, unknown-band rule is heuristic.
**code** (A4): MAJOR `fileclass` regression (stem `NOTICE/SECURITY/LICENSE` -> DOCS inside a root: a PR touching
`security.ts` is "set aside"); minors: quadratic template merge, lone-surrogate locale crash.
**crawl**: dialog-prose destructive judgement was fixed (commit pending); minors: WebRTC not covered by the
network guard; `sanitise_recorded_html` single pass; icon-only "Trash" -> "Move to trash?" confirmed.
**spec** (accepted): 7 minors: edition/polarity cue heuristics misfire on "only in X, not in Y"; callout fallback
re-licenses a negative from a sibling sentence; negative cue uses the quote not the sentence; **none of the new
tags (`polarity:unverified`, `edition:default`, `edition:multi`, `conflict:*`, `also:*`) is read downstream**
(wire into gaps/report or drop); `final_url` unscrubbed in `stats.json`.
**access** (accepted): DataDome 200-delivered challenge is OK (decision); Vercel/Akamai/DataDome/PerimeterX/Imperva
markers are synthesized from recall (labelled). Optional second marker for DataDome.

## 7. P2: designed, deliberately not built (state in DESIGN.md; do not build unless time remains)

TraceImport (trace.zip), Web Bot Auth, personalised PageRank ablation, Home Assistant slice, full-depth Sylius,
area cards, Phoenix/OTLP export, HTML trace viewer, authenticated crawl (needs a human-made `storage_state`),
held-out co-change replay over ~20 merged PRs, mobile viewport and non-`en` locale passes, function-level code
graph and backend route mapping.

## 8. Known limitations to state, not fix

Frontend files only, file granularity; CSR only; one viewport; `en` locale; cloud-web edition; unauthenticated
surface; Sylius is post-hoc (deployed version unresolved, PRs already merged, indexed at base SHA);
three PRs are case studies, not validated accuracy; link confidences are uncalibrated priors; oracle exists only
because this target publishes source maps; the cart was never populated on Sylius (state-mutating POST refused).

## 9. Code review plan (after the above)

Review in this order; each has a concrete question.

| Area | Files (size) | Question |
|---|---|---|
| Safety of the crawler | `crawl/explorer.py` (1,956 lines), `crawl/safety.py`, `crawl/perceive.py` | Can any path leave the allowlist, click destructive/auth, Send to a non-allowed URL, or touch a widget? Split `explorer.py` if it hides this |
| Reasoning correctness | `reason/assemble.py` (849), `grade.py` (553), `retrieve.py`, `verify.py` | Is a claim ever stronger than its weakest edge? Can the LLM add radius? |
| Graph writes | `graph/store.py` (1,015), `writes.py`, `queries*.py` | Idempotent? Parameterised only? Prune guards right? |
| Determinism | everything that writes artifacts | cross-process hash-seed test exists per stage? |
| Untrusted text | `spec/*`, `reason/pr.py`, `link/req_to_ui.py`, `narrate.py` | every prompt wraps with `untrusted` + preamble? |
| Evidence integrity | `evidence.py`, `reason/verify.py`, `report/lint.py` | can a claim cite a non-existent id? |
| Tests that cannot fail | `tests/test_report_*` (12/28 mutants survive), `tests/test_eval_*` | add mutants; delete vacuous tests |
| Duplication | `reason/words.py` vs `report/vocab.py` (verb tables); `access` vs `crawl` URL scrubbing | one source of truth |
| Over-engineering | `code/adapters/twig.py` (1,293), `evalkit/*` (3.2K src) | cut dead branches |

## 10. Definition of done for submission

1. `make doctor` all PASS; `make demo` completes on a clean clone with a key.
2. Three PR reports + the Sylius adapter proof exist; `docs/sample-report-pr-6645.md` committed.
3. `blastradius eval` results persisted and quoted in DESIGN.md with n and intervals.
4. DESIGN.md has no `⟦...⟧`; CASES.md has no bare tier; README steps verified from a clean clone.
5. `ruff` clean; full suite green (slow included); no secrets in the tree or in `data/runs/sample`.
6. Repo pushed (owner approval), Loom recorded, email sent.
