# Blast radius: PR #6645 "Edit "environment" values from the sidebar"

**Risk: HIGH.** Touches Environments (sidebar, quick peek). 4 screens, 1 user flow, 4 requirements. It also changes code shared across the app.

Confidence: 3 items confirmed, 2 probable and 1 possible. 7 things could not be determined and are listed below with the same weight as the findings.

Deployed app compared with the indexed code: 11 of 104 files differ. The PR branch is 1 commit behind main, so its diff may not apply as shown.

## What to re-test first

- [ ] Open Environments in the sidebar and edit a variable's current value. Why: the Environments sidebar is changed by this PR. [^1][^2]
- [ ] Open the quick peek and look at it. [^2]
- [ ] Export your saved data and import it again. Why: the saved collection data format is changed by this PR. [^14]
- [ ] Open Screen 11 and look at it. Why: it might be affected. [^22]
- [ ] Check the page frame. Why: the page layout is changed by this PR. [^13]

## Directly changed

These screens and elements have their own code in this PR.

How sure: Confirmed (the PR's own code changes it), Likely (reached through code it shares), Possible (a weaker connection, worth a look), Unknown (we cannot tell).

| Screen | What changed | How sure |
|---|---|---|
| **Environments sidebar** (on Home page) | The Environments sidebar is changed by this PR. Controls involved: “Environments”. [^1] Flows: Edit a variable. | Confirmed |
| **Quick peek** | The quick peek is changed by this PR. [^2] | Confirmed |
| **Variable selector** (on Home page) | The variable selector is changed by this PR. (unsourced) | Confirmed |

## Indirect, through shared code

Screens that reuse code this PR changes, grouped by the part of the spec they belong to.

Showing the 10 strongest of 12 (at most 10); the rest are listed in the appendix.

### Environments

Connected through: the shared current-value service; the shared secret-value service.

| Screen | What may be affected | How sure |
|---|---|---|
| **Screen 00** | Screen 00 is probably affected. [^3] Flows: Flow 00. | Likely |
| **Screen 03** | Screen 03 is probably affected. [^4] | Likely |
| **Screen 07** (on Page 07) | Screen 07 is probably affected. [^5] | Likely |

### Collections

Connected through: the shared current-value service.

| Screen | What may be affected | How sure |
|---|---|---|
| **Screen 01** (on Page 01) | Screen 01 is probably affected. [^6] | Likely |
| **Screen 05** (on Page 05) | Screen 05 is probably affected. [^7] Flows: Flow 05. | Likely |

### Other screens

Connected through: the shared current-value service.

| Screen | What may be affected | How sure |
|---|---|---|
| **Screen 02** (on Page 02) | Screen 02 is probably affected. [^8] | Likely |
| **Screen 06** | Screen 06 is probably affected. [^9] | Likely |
| **Screen 09** | Screen 09 might be affected. [^10] | Possible |

### Requests

Connected through: the shared secret-value service.

| Screen | What may be affected | How sure |
|---|---|---|
| **Screen 04** (on Page 04) | Screen 04 is probably affected. [^11] | Likely |
| **Screen 08** (on Page 08) | Screen 08 might be affected. [^12] | Possible |

## Global change

This PR changes something every screen shares, so every screen may be affected and the lists above are not exhaustive.

| Shared piece | What changed | How sure |
|---|---|---|
| **The shared page frame** | This PR edits the page layout. Every screen is changed by this PR to some degree. [^13] | Confirmed |

## Stored data

This PR changes how something is saved. Check it by hand.

| Saved data | What changed | How sure |
|---|---|---|
| **Saved collection data** | The saved collection data format is changed by this PR. [^14] | Confirmed |

- [ ] Export your saved data and import it again.

## Requirements at risk

| What the spec requires | Where in the spec | Status | How sure |
|---|---|---|---|
| User can see the current value. [^15] Spec says: “The current value is shown.” | Environments | Loses its only realization | Confirmed |
| User can edit an environment variable. [^16] Spec says: “You can edit "the value" of a variable.” | Environments | At risk | Likely |
| User can sort collections. [^17] Spec says: “Collections can be sorted.” | Collections | Cannot verify | Unknown |
| User can import a collection. (unsourced) |  | This PR may close a gap | Possible |

## What we could not determine, and who should look

| Item | Why we could not tell | What a person should check |
|---|---|---|
| **the whole PR** | The PR base is behind main. [^18] | Rebase and run again. |
| **File 0** | File 0 is outside the snapshot. | Look at it by hand. |
| **File 1** | File 1 is outside the snapshot. [^19] | Look at it by hand. |
| **File 2** | File 2 is outside the snapshot. | Look at it by hand. |
| **File 3** | File 3 is outside the snapshot. [^20] | Look at it by hand. |

2 more (top 5 shown) are listed in the appendix.

### What this report did not look at

- Only the signed-out surface of the web app was observed.
- One viewport size was used.
- Only English text (\`en\`) was compared.

## Appendix: evidence

Everything above cites the numbered entries below. File paths, ids and commit hashes appear only here.

### Snapshot

- Target: `hoppscotch`
- Repository: `hoppscotch/hoppscotch`
- Indexed commit: `63273f8e599e73f0fb38c0739314e8d46b6bc83d`
- PR base: `ac145e7f758151b41fd46d3e5f513886ce9068ba`
- PR head: `64ba4ff75eff7bef868b010947f50e9b6b326540`
- PR base behind main: 1 commit
- Deployed vs indexed: 11 of 104 files differ
- Crawl bundle: `c81a0323d1f5b4f5`
- Crawl size: 1 action and 25 screens
- Spec sha: `f895cca9ad176109`
- Documentation version: 2026\.5.0
- App version: 2026\.9.0

### Evidence behind each statement

[^1]: **Directly changed.** Environments sidebar (on Home page): The Environments sidebar is changed by this PR. Controls involved: “Environments”. Confirmed (0.93).

    - Claim `clm_892c2c2c2214`: Environments sidebar: The Environments sidebar is changed by this PR. Controls involved: “Environments”.
    - Evidence `ev_000000000001` (pr.file): modified packages/app/src/File1.vue (+1 -1). Reference `a/b#6645:packages/app/src/File1.vue`. Trace span `span0001`.
    - Evidence `ev_000000000002` (link.edge): imports the changed editor. Reference `packages/app/src/index.vue`. method `static-import`. confidence `0.75`. path `packages/app/src/index.vue`. file `index.vue`. lines `3-9`. line_start `3`. line_end `9`. start_line `3`. end_line `9`. source_url `hxxps://docs.example/x`. sha `abc1234`. commit `def5678`.
    - Files: `packages/app/src/Editor.vue`, `packages/app/src/index.vue`.
    - Requirements: `req_000000000001`, `req_000000000002`.
    - Flows: `Edit a variable`, `flow_0123456789ab`, `src/flows/edit.ts`.

[^2]: **Directly changed.** Quick peek: The quick peek is changed by this PR. Confirmed (0.85).

    - Claim `clm_fec613bbd6bc`: Quick peek has a new field.
    - Evidence `ev_000000000003` (pr.file): modified packages/app/src/File3.vue (+3 -1). Reference `a/b#6645:packages/app/src/File3.vue`. Trace span `span0003`.

[^3]: **Indirect, through shared code.** Screen 00: Screen 00 is probably affected. Likely (0.79).

    - Evidence `ev_00000000000a` (pr.file): modified packages/app/src/File10.vue (+10 -1). Reference `a/b#6645:packages/app/src/File10.vue`. Trace span `span0010`.
    - Flows: `Flow 00`.

[^4]: **Indirect, through shared code.** Screen 03: Screen 03 is probably affected. Likely (0.70).

    - Evidence `ev_00000000000d`: not found in this run's evidence ledger.

[^5]: **Indirect, through shared code.** Screen 07 (on Page 07): Screen 07 is probably affected. Likely (0.54).

    - Evidence `ev_000000000011`: not found in this run's evidence ledger.

[^6]: **Indirect, through shared code.** Screen 01 (on Page 01): Screen 01 is probably affected. Likely (0.76).

    - Evidence `ev_00000000000b` (pr.file): modified packages/app/src/File11.vue (+11 -1). Reference `a/b#6645:packages/app/src/File11.vue`. Trace span `span0011`.

[^7]: **Indirect, through shared code.** Screen 05 (on Page 05): Screen 05 is probably affected. Likely (0.62).

    - Evidence `ev_00000000000f`: not found in this run's evidence ledger.
    - Flows: `Flow 05`.

[^8]: **Indirect, through shared code.** Screen 02 (on Page 02): Screen 02 is probably affected. Likely (0.73).

    - Evidence `ev_00000000000c`: not found in this run's evidence ledger.

[^9]: **Indirect, through shared code.** Screen 06: Screen 06 is probably affected. Likely (0.58).

    - Evidence `ev_000000000010`: not found in this run's evidence ledger.

[^10]: **Indirect, through shared code.** Screen 09: Screen 09 might be affected. Possible (0.30).

    - Evidence `ev_000000000013`: not found in this run's evidence ledger.

[^11]: **Indirect, through shared code.** Screen 04 (on Page 04): Screen 04 is probably affected. Likely (0.66).

    - Evidence `ev_00000000000e`: not found in this run's evidence ledger.

[^12]: **Indirect, through shared code.** Screen 08 (on Page 08): Screen 08 might be affected. Possible (0.45).

    - Evidence `ev_000000000012`: not found in this run's evidence ledger.

[^13]: **Global change.** The shared page frame: This PR edits the page layout. Every screen is changed by this PR to some degree. Confirmed (0.85).

    - Evidence `ev_00000000001e` (pr.file): modified packages/app/src/File30.vue (+30 -1). Reference `a/b#6645:packages/app/src/File30.vue`. Trace span `span0030`.

[^14]: **Stored data.** Saved collection data: The saved collection data format is changed by this PR. Confirmed (0.82).

    - Evidence `ev_00000000001f` (pr.file): modified packages/app/src/File31.vue (+31 -1). Reference `a/b#6645:packages/app/src/File31.vue`. Trace span `span0031`.

[^15]: **Requirements at risk.** Environments: User can see the current value. Confirmed.

    - Evidence `ev_000000000028` (pr.file): modified packages/app/src/File40.vue (+40 -1). Reference `a/b#6645:packages/app/src/File40.vue`. Trace span `span0040`.
    - Evidence `ev_000000000029` (spec.quote). Trace span `s41`.
    - Requirements: `req_000000000002`.

[^16]: **Requirements at risk.** Environments: User can edit an environment variable. Likely.

    - Evidence `ev_000000000028` (pr.file): modified packages/app/src/File40.vue (+40 -1). Reference `a/b#6645:packages/app/src/File40.vue`. Trace span `span0040`.
    - Requirements: `req_000000000001`.

[^17]: **Requirements at risk.** Collections: User can sort collections. Unknown.

    - Evidence `ev_00000000002a`: not found in this run's evidence ledger.
    - Requirements: `req_000000000003`.

[^18]: **What we could not determine, and who should look.** the whole PR: The PR base is behind main.

    - Reason code `STALE_BASE`.
    - Evidence `ev_000000000032` (pr.file): modified packages/app/src/File50.vue (+50 -1). Reference `a/b#6645:packages/app/src/File50.vue`. Trace span `span0050`.

[^19]: **What we could not determine, and who should look.** File 1: File 1 is outside the snapshot.

    - Reason code `NEEDS_HUMAN`.
    - Evidence `ev_000000000034` (pr.file): modified packages/app/src/File52.vue (+52 -1). Reference `a/b#6645:packages/app/src/File52.vue`. Trace span `span0052`.

[^20]: **What we could not determine, and who should look.** File 3: File 3 is outside the snapshot.

    - Reason code `NEEDS_HUMAN`.
    - Evidence `ev_000000000036` (pr.file): modified packages/app/src/File54.vue (+54 -1). Reference `a/b#6645:packages/app/src/File54.vue`. Trace span `span0054`.

[^21]: **Indirect, through shared code.** Screen 10 (on Page 10): Screen 10 might be affected. Possible (0.28). Cut from the main report by its display cap.

    - Evidence `ev_000000000014`: not found in this run's evidence ledger.
    - Flows: `Flow 10`.

[^22]: **Indirect, through shared code.** Screen 11 (on Page 11): Screen 11 might be affected. Possible (0.26). Cut from the main report by its display cap.

    - Evidence `ev_000000000015`: not found in this run's evidence ledger.

[^23]: **What we could not determine, and who should look.** File 4: File 4 is outside the snapshot. Cut from the main report by its display cap.

    - Reason code `NEEDS_HUMAN`.
    - No evidence was cited for this statement.

[^24]: **What we could not determine, and who should look.** File 5: File 5 is outside the snapshot. Cut from the main report by its display cap.

    - Reason code `NEEDS_HUMAN`.
    - Evidence `ev_000000000038`: not found in this run's evidence ledger.

[^25]: **Claim not shown as a sentence above.** Claim: A claim that no sentence shows. Likely.

    - Claim `clm_90e08ac4491c`.
    - Evidence `ev_00000000003c`: not found in this run's evidence ledger.

### How the bands are set

- Confirmed: confidence >= 0.8.
- Likely: confidence 0.5 to 0.8.
- Possible: confidence 0.25 to 0.5.
- Unknown: confidence < 0.25.

The bands are rules of thumb applied to how each link was found. They have not been validated against outcomes, so read them as an ordering, not as probabilities.

Generated by blastradius <package version>.
