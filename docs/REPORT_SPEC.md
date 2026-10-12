# Report specification (what a non-engineer QA lead reads)

The brief: "readable by a non-engineer who understands the product but not your code." The report is judged on
whether a QA lead could act on it in five minutes without opening a file path. File paths, ids, and graph terms
live only in the appendix.

## Structure (fixed order)

1. **Headline** (2-3 lines)
   `Blast radius: PR #6645 "edit environment values from quick peek and the sidebar"`
   `Risk: MEDIUM. Touches Environments (sidebar, quick peek, selector). 4 screens, 2 user flows, 3 requirements.`
   One sentence on confidence: how many items are confirmed vs probable, and how many we could not determine.
2. **What to re-test first** (checklist, max 8): each line is an action a person can take, names the screen
   in the product's own words, and ends with why in plain language.
   `[ ] Open Environments in the sidebar and edit a variable's current value. Why: the value editor changed.`
3. **Directly changed** (Tier 1): screens and elements whose own code is in the PR (changed UI files, changed
   copy via the locale diff). High precision. Table: Screen | What changed | How sure.
4. **Indirect, through shared code** (Tier 2): capped at N=10, grouped by spec section; each group says which
   shared piece connects it ("the variable input is used by 6 other screens").
5. **Global change** (only if present): layout, shared data package, global styles. States that every screen
   may be affected and that the list below is therefore not exhaustive.
6. **Stored data** (only if present): the PR changes how something is saved (for example collections schema).
   Includes a short manual migration checklist: export/import, sync, old data opening in the new build.
7. **Requirements at risk**: spec statements (quoted, with the doc section) that rest on affected screens,
   and any that lose their only realization. Statuses: *at risk*, *loses its only realization*, *cannot verify*,
   *this PR may close a gap*.
8. **What we could not determine, and who should look**: given the same visual weight as findings. Each item
   states why (not mapped to any screen, backend-only change, file not in the indexed snapshot, blocked by
   access policy, stale base) and what a human should check. Generated from `failures.jsonl` + needs-review.
9. **Appendix: evidence**: per claim, the evidence ids, file paths and line ranges, edge methods and confidences,
   the trace span, and the exact snapshot (`indexed commit`, `PR head`, `deployed vs indexed: N of M files differ`).

## Language rules (enforced by the verifier and the lint)

| Band | Allowed verbs | Forbidden |
|---|---|---|
| confirmed (>= 0.8) | "will change", "is changed by this PR" | "might", "could" |
| likely (0.5-0.8) | "probably affected", "likely changes" | "will", "is certain" |
| possible (0.25-0.5) | "might be affected", "worth a quick look" | "will", "probably" |
| unknown (< 0.25) | "we cannot tell", "not determined" | any claim of effect |

- No file paths, ids, "node", "edge", "graph", "closure", "hop" in sections 1-8. Use screen and dialog titles
  captured by the crawl, and the spec's own section names.
- Every sentence in sections 3-7 cites one or more ledger ids (rendered as footnote markers); the verifier
  checks that the cited ids exist and that the verb matches the lowest-confidence cited edge.
- "Could not determine" never uses softer visual weight than a finding.
- The report states what it did not look at: unauthenticated surface only, one viewport, `en` locale,
  frontend files only, cloud-web edition.

## Required lint checks (report/lint.py, deterministic)

risk level appears in the first 3 lines; the checklist has <= 8 items; every Tier-1/Tier-2 item has a band
word from the table; a "could not determine" section exists (even when empty it says "nothing outstanding" and
why); no raw ids or paths outside the appendix; no HTML tags in text fields (escaped at render time); no
remote images; total length in the main body <= 1.5 pages equivalent (~600 words) before the tables.
