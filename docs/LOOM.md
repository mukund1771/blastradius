# Loom script (8 minutes)

Record the prototype running and the design document. Speak to a QA lead, then to the reviewer.

## 0:00 — The problem (45s)

A QA lead gets a pull request and needs to know what to re-test. Not every file the change imports: the screens a person can open, the flows those screens sit in, and the requirements that lose coverage. Say what you went deep on: evidence on every link, absence as a recorded search, and a report whose sentences are checked against that evidence.

## 1:00 — Access, then the crawl (90s)

Run `make doctor` (or show the last all-PASS output). Point at one recorded fixture that is a real block, and say the system detects, records a gap, and stops. It does not try to get past a WAF.

Show the Hoppscotch crawl: 55 screens, 1,468 elements, 321 transitions, 150 actions, 0 blocks. 280 requests were refused by the egress allowlist because Hoppscotch is an API client and Send can target localhost.

## 2:30 — The graph and absence (90s)

Open Neo4j or the load summary. Three layers: 160 requirements, 55 screens, 1,273 code files. A requirement with no UI is a Gap that records how many screens were searched and how many actions were spent. Blocked and "desktop only" are not "missing". The docs stop at 2026.5.0 and the app is 2026.9.0, so a missing control is not automatically a product gap.

## 4:00 — One pull request (2 min)

Open `docs/sample-report-pr-6645.md` (PR #6645, environment sidebar). The risk line is: **Risk: HIGH. Touches Home page and Menu popup on the Home page. 10 screens, 5 user flows, 12 requirements.** The checklist is two lines: click “Environment Quick Peek” on the Home page, and click “Edit” on the menu popup. Walk one "could not determine" row: the PR adds “Edit current value” and “environments editor”, and the live app does not show them yet, so that is a single item. An indirect screen marked Possible is not on the checklist. Say the 12 requirements are all about environments and variables (the report listed 48 before the requirement filter, D41: 36 are now counted in the not-checked section instead), and the two checklist lines are what to re-test. If a second PR is on screen, use #6658 as the saved-data case (risk HIGH, and it also changes how data is saved).

## 6:00 — One claim, then the numbers (90s)

Open footnote [^1]. The sentence is “This screen is changed by this PR” for the Home page, band Confirmed. The appendix cites `ev_e32e9b25cc57`: button "Environment Quick Peek" in 1 file. The verifier rejects a sentence that is stronger than that evidence.

Say what was measured. Invariants: the three reports pass. Synthetic recall is circular: 142/142 screens, Wilson 95% 0.9737–1.0. UI to code: 333 of 434 named elements linked, median confidence 0.30. The source-map oracle, on 124 controls from `/`, `/graphql` and `/settings`: the linker covers 117/124, top-1 of those covered is 64/117 (Wilson 95% 0.457–0.634), hit@3 is 78/117 (0.577–0.746). Say top-1 is conservative because the oracle names the template file and the linker names the file that shows the text. The offline judge and the N=5 stability run were not run. Three PRs are case studies, not a validated accuracy.

## 7:30 — What is designed and not built (30s)

Another week: function-level code and backend routes, an authenticated crawl, CI that posts the report on the PR. Do not claim a second live target, community GraphRAG, or a 100-run accuracy study.
