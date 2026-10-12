# Evaluation: hoppscotch, run crawl-gate-150

Every rate below is k/n with its Wilson 95% interval; a number without its n is not printed.
Thresholds were pre-registered (eval/thresholds.json, registered 2026-10-12, sha256 227f6687a5e5): {'hop_cap': 2, 'breadth_fraction_max': 0.25, 'min_confidence_correct': 0.5}; eval-only: {'stability_min_agreement': 0.6, 'stability_n': 5, 'judge_k': 3, 'seeded_corruptions': 20, 'high_confidence': 0.8, 'demoted_confidence': 0.49}.

| evaluation | status |
|---|---|
| invariants | ok |
| recall | ok |
| oracle | ok |

## 1. Invariants

3 of 3 reports pass every invariant (evidence rows available: 10734). A report that fails any is wrong regardless of its content.
- pr 6641: ok
- pr 6645: ok
- pr 6658: ok

Caveats:
- Invariants check the form of a report, not its truth: a report can pass all of them and still be wrong (a valid-but-irrelevant citation passes, case 54).

## 2. Synthetic-PR recall (circular)

**CIRCULAR: the expected screens and flows come from the same links that built the graph. This checks that retrieval returns what the graph holds, not that the links are correct.**

- elements checked: 142 over 41 files (of 41 candidates)
- (element, screen) recall: 142/142 = 100.0% (Wilson 95% 97.4% to 100.0%)
- recall excluding trivially broad files: 80/80 = 100.0% (Wilson 95% 95.4% to 100.0%) (21 files reach >= 25% of 55 screens)
- flow recall: 458/458 = 100.0% (Wilson 95% 99.2% to 100.0%)
- misses: 0 (retrieval at hop cap 2)

## 3. Independent link signal: source-map oracle

**This oracle exists only because this target publishes its source maps. It labels only UI present in the deployed build (not code added by an open PR), only files that also exist on main are scored, and it is test-only: it is not a product rung and nothing in link/, reason/ or graph/ may import it.**

- label source: source-map oracle (harvest: 3 page visits, 21 chunks pinned by ETag and sha256)
- oracle coverage: 208/208 = 100.0% (Wilson 95% 98.2% to 100.0%) of perceived elements got a repo file
- component attribution: 41/41 = 100.0% (Wilson 95% 91.4% to 100.0%) basenames equal the component's own `__name`; vote share unanimous 53/53 = 100.0% (Wilson 95% 93.2% to 100.0%)
- deployed vs indexed main: 11 of 289 .vue sources in the loaded maps differ from or are absent on the indexed main; the largest chunk index-BVV_7b8U.js: 11 of 104
- label plausibility (not a score): 199/207 = 96.1% (Wilson 95% 92.6% to 98.0%) of named elements have their name in the file the oracle names
- scored: 124 distinct elements (208 labels; excluded: 0 not in the crawl, 0 file not on main)
- linker coverage: 117/124 = 94.4% (Wilson 95% 88.8% to 97.2%)
- top-1 precision (of covered): 64/117 = 54.7% (Wilson 95% 45.7% to 63.4%)
- top-1 accuracy (of all scored): 64/124 = 51.6% (Wilson 95% 42.9% to 60.2%)
- hit@3 (of covered): 78/117 = 66.7% (Wilson 95% 57.7% to 74.6%)
- hit@3 (of all scored): 78/124 = 62.9% (Wilson 95% 54.1% to 70.9%)
- by evidence method of the top-1 link:
  - i18n_key: top-1 42/90 = 46.7% (Wilson 95% 36.7% to 56.9%); hit@3 56/90 = 62.2% (Wilson 95% 51.9% to 71.5%)
  - llm_pick: top-1 22/27 = 81.5% (Wilson 95% 63.3% to 91.8%); hit@3 22/27 = 81.5% (Wilson 95% 63.3% to 91.8%)
  - none: top-1 0/7 = 0.0% (Wilson 95% 0.0% to 35.4%); hit@3 0/7 = 0.0% (Wilson 95% 0.0% to 35.4%)

Caveats:
- Test-only oracle: it exists because this target publishes source maps; no other target has it.
- It labels only UI present in the deployed build: UI introduced by an open PR has no label (case 71).
- Deployed is not main: scoring is restricted to files that exist on the indexed main; drift is stated.
- Elements with no component instance and unattributed chains are left unlabelled and counted.
- The unit is a distinct (file, role, name, ordinal): a layout control seen on four pages counts once.
- A 'pattern' join matches by url_pattern only and can conflate two states' ordinals; 'state' is exact.
- The oracle names the file whose template contains the element; the linker names the file that displays its text. They differ when text arrives as a prop, so top-1 is a conservative measure and hit@3 less so.
- Small n: read the Wilson interval, not the point estimate.

LLM in this stage: {'calls': 0, 'cache_hits': 0, 'spent_usd': 0.0, 'avoided_usd': 0.0}
