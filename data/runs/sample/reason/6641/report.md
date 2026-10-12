# Blast radius: PR #6641 "Feature/add compare button"

**Risk: MEDIUM.** Touches GraphQL page. 11 screens, 0 user flows, 8 requirements.

Confidence: 1 item confirmed, 0 probable and 10 possible. 5 things could not be determined and are listed below with the same weight as the findings.

The PR branch is 10 commits behind main, so its diff may not apply as shown.

## What to re-test first

- [ ] On the GraphQL page, click “Copy”. Why: the GraphQL page is changed by this PR. [^1]

## Directly changed

These screens and elements have their own code in this PR.

How sure: Confirmed (the PR's own code changes it), Likely (reached through code it shares), Possible (a weaker connection, worth a look), Unknown (we cannot tell).

| Screen | What changed | How sure |
|---|---|---|
| **GraphQL page** | This screen is changed by this PR. Controls involved: “Copy”, “Documentation”, “Download file” and 3 more. [^1] | Confirmed |
| **Home page** | This screen might be affected by this PR; worth a quick look. Controls involved: “Documentation”, “Download file”, “More” and 1 more. [^2] | Possible |
| **Menu popup on the Home page** | This screen might be affected by this PR; worth a quick look. Controls involved: “Documentation” and “Status”. [^3] | Possible |
| **Save as dialog on the Home page** | This screen might be affected by this PR; worth a quick look. Controls involved: “More”. [^4] | Possible |
| **GraphQL Collections dialog on the GraphQL page** | This screen might be affected by this PR; worth a quick look. Controls involved: “Export as JSON”. [^5] | Possible |

## Indirect, through shared code

Screens that reuse code this PR changes, grouped by the part of the spec they belong to.

### Other screens

Connected through: 2 pieces of shared code (gql response meta, http response meta); 3 pieces of shared code (gql response, gql response meta, http response meta).

| Screen | What may be affected | How sure |
|---|---|---|
| **MQTT page** | This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Documentation” and “Interceptor”. [^6] | Possible |
| **Socket.IO page** | This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (gql response, gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Documentation”, “Interceptor” and “Wrap lines”. [^7] | Possible |
| **SSE page** | This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Documentation” and “Interceptor”. [^8] | Possible |
| **WebSocket page** | This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (gql response, gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Documentation”, “Interceptor” and “Wrap lines”. [^9] | Possible |
| **Not-found page** | This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Interceptor”. [^10] | Possible |
| **Settings page** | This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Interceptor”. [^11] | Possible |

## Requirements at risk

| What the spec requires | Where in the spec | Status | How sure |
|---|---|---|---|
| User can download and copy returned GraphQL responses. [^12] Spec says: “You can download and copy the returned responses for further use.” | GraphQL API Testing > Response | At risk | Likely |
| User can view responses and logs in the response section once the connection is established. [^13] Spec says: “Once the connection is established, you can view the responses and logs in the response section.” | Realtime API Testing > Response | At risk | Likely |
| User can view headers and bodies of persisted responses by selecting each request when enabled. [^14] Spec says: “Persisted Responses: If enabled, view headers and bodies by selecting each request; otherwise, this section remains hidden.” | Runner > Runner Results | At risk | Likely |
| User can run Tests on the responses received. [^15] Spec says: “Hoppscotch also provides the capability to run Tests on the responses you receive.” | RESTful API Testing > Request | At risk | Possible |
| User can write pre-request scripts and post-request tests in JavaScript to add dynamic behavior, set variables, and validate responses. [^16] Spec says: “Write pre-request scripts and post-request tests in Hoppscotch using JavaScript to add dynamic behavior, set variables, and validate responses.” | Scripts | At risk | Possible |
| User can click the "Install +" button at the top right of the address bar to install the PWA. [^17] Spec says: “At the top right of the address bar, click "Install +".” | Hoppscotch Progressive Web App > Install Hoppscotch PWA | Cannot verify | Unknown |
| User can use the context menu on selected text to choose Set as Variable, Add to Parameters, or Open Request in New Tab. [^18] Spec says: “The context menu offers three options: Set as Variable, Add to Parameters, and Open Request in New Tab.” | Context Menu | This PR may close a gap | Possible |
| User can have the collection run stop if a script error or request sending problem occurs. [^19] Spec says: “Stop Run if Error Occurs: The collection run stops if an error is encountered within a script or if there's a problem sending a request.” | Runner > Run Settings | This PR may close a gap | Possible |

## What we could not determine, and who should look

| Item | Why we could not tell | What a person should check |
|---|---|---|
| **New in this PR; not observable in the live app yet** | This PR adds “Compare API Responses”, “diff”, “gql response compare modal” and “response history”. The live app does not show that yet. | After this PR is deployed, open the screen that should contain “Compare API Responses”, “diff”, “gql response compare modal” and “response history”. |
| **the whole PR** | The PR base is 10 commit(s) behind the indexed main and 2 changed file(s) were also edited there: the diff may not apply as shown. [^20] | Rebase the PR on main and run the report again before relying on the file lists; 2 file(s) were edited on both sides. |
| **the http response compare modal component** | This PR adds the file and other code in the PR uses it, but none of that code reaches a screen we observed. [^21] | Find where http response compare modal appears in the product and try it by hand; this change is not tied to any screen we observed. |
| **4 items: the shared gql response code, the shared gql response meta code, the shared http response code and 1 more** | The only links we found to screens are weak. [^22] | Decide which screens that rely on gql response need a re-test; the list shown is not exhaustive. |
| **3 items: controls linked to the gql response code, controls linked to the http response code and controls linked to the http response meta code** | Two independent checks disagreed about 1 control(s); they were left as found. | Look at these controls by hand: More. |

### What this report did not look at

- Only the signed-out surface of the web app was observed.
- One viewport size was used.
- Only English text (\`en\`) was compared.
- Frontend files only: backend, scripts and build tooling are outside this analysis.
- Cloud web edition only: desktop and self-hosted behaviour was not assessed.
- Set aside as not user-facing: 1 build config file(s), 1 generated file(s).
- 2 more lower-priority item(s) could not be determined (see the appendix data).
- 18 requirement(s) with no matching screen sit on pages this PR touches but do not mention anything it changes; they are not listed.
- 2 requirement(s) reach this PR only through a whole screen, a flow, or code it shares, and do not mention anything it changes; they are not listed.
- The crawl stopped at its action budget, so some screens were not observed.
- The product documentation may be older than the app, so some requirements may be out of date.
- Some source files could not be read completely.

## Appendix: evidence

Everything above cites the numbered entries below. File paths, ids and commit hashes appear only here.

### Snapshot

- Target: `hoppscotch`
- Repository: `hoppscotch/hoppscotch`
- Indexed commit: `63273f8e599e73f0fb38c0739314e8d46b6bc83d`
- PR base: `ac145e7f758151b41fd46d3e5f513886ce9068ba`
- PR head: `5eea64fd209addde0d770831b7234817eb339699`
- PR base behind main: 10 commits
- Crawl bundle: `c81a0323d1f5b4f5`
- Crawl size: 150 actions and 55 screens
- Documentation version: 2026\.5.0
- App version: 2026\.9.0

### Evidence behind each statement

[^1]: **Directly changed.** GraphQL page: This screen is changed by this PR. Controls involved: “Copy”, “Documentation”, “Download file” and 3 more. Confirmed (0.99).

    - Claim `clm_786645b5e44a`: GraphQL page: This screen is changed by this PR. Controls involved: “Copy”, “Documentation”, “Download file” and 3 more.
    - Evidence `ev_099ad834a7df` (reason.control): button "Wrap lines" in 9 file(s). Reference `hoppscotch/hoppscotch#6641:button:Wrap lines:9`.
    - Evidence `ev_2a5e03ae6bbe` (reason.control): button "Response" in 1 file(s). Reference `hoppscotch/hoppscotch#6641:button:Response:1`.
    - Evidence `ev_2ee01925e42c` (reason.control): button "Download file" in 5 file(s). Reference `hoppscotch/hoppscotch#6641:button:Download file:5`.
    - Evidence `ev_598871ed5ac4` (reason.control): button "More" in 9 file(s). Reference `hoppscotch/hoppscotch#6641:button:More:9`.
    - Evidence `ev_6913f294e976` (reason.control): button "Documentation" in 5 file(s). Reference `hoppscotch/hoppscotch#6641:button:Documentation:5`.
    - Evidence `ev_758c4f2581b2` (reason.impact): direct impact on GraphQL page: 34 control(s), confidence 0.99. Reference `hoppscotch/hoppscotch#6641:direct:/graphql|`.
    - Evidence `ev_9e9ee29d7914` (pr.file): modified packages/hoppscotch-common/src/components/graphql/ResponseMeta.vue (+13 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/graphql/ResponseMeta.vue`.
    - Evidence `ev_d434df7a758b` (reason.control): button "Copy" in 12 file(s). Reference `hoppscotch/hoppscotch#6641:button:Copy:12`.
    - Evidence `ev_e63e6d766983` (pr.file): modified packages/hoppscotch-common/src/components/graphql/Response.vue (+14 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/graphql/Response.vue`.
    - Files: `packages/hoppscotch-common/src/components/graphql/Response.vue`, `packages/hoppscotch-common/src/components/graphql/ResponseMeta.vue`.
    - Requirements: `req_0ee81ee1b9a2`, `req_2b2a27e7080e`, `req_9046a70ced66`.

[^2]: **Directly changed.** Home page: This screen might be affected by this PR; worth a quick look. Controls involved: “Documentation”, “Download file”, “More” and 1 more. Possible (0.50).

    - Claim `clm_b442f3c37f46`: Home page: This screen might be affected by this PR; worth a quick look. Controls involved: “Documentation”, “Download file”, “More” and 1 more.
    - Evidence `ev_12495e5d4267` (pr.file): modified packages/hoppscotch-common/src/components/http/ResponseMeta.vue (+13 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.
    - Evidence `ev_3c8f5ac5501d` (pr.file): modified packages/hoppscotch-common/src/components/gql/Response.vue (+10 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/Response.vue`.
    - Evidence `ev_6fdfc69ee9fe` (reason.control): button "Download file" in 7 file(s). Reference `hoppscotch/hoppscotch#6641:button:Download file:7`.
    - Evidence `ev_87ac88ee6fc0` (reason.control): button "More" in 15 file(s). Reference `hoppscotch/hoppscotch#6641:button:More:15`.
    - Evidence `ev_9057bd560b3e` (reason.impact): direct impact on Home page: 23 control(s), confidence 0.50. Reference `hoppscotch/hoppscotch#6641:direct:/|`.
    - Evidence `ev_a973fa00ae99` (reason.control): button "Wrap lines" in 19 file(s). Reference `hoppscotch/hoppscotch#6641:button:Wrap lines:19`.
    - Evidence `ev_bd3f3ca0b8c4` (pr.file): modified packages/hoppscotch-common/src/components/gql/ResponseMeta.vue (+19 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`.
    - Evidence `ev_c755b8c1f9f7` (reason.control): button "Documentation" in 7 file(s). Reference `hoppscotch/hoppscotch#6641:button:Documentation:7`.
    - Files: `packages/hoppscotch-common/src/components/gql/Response.vue`, `packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`, `packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.
    - Requirements: `req_21b7a82f89ef`, `req_741ef7e034a6`.

[^3]: **Directly changed.** Menu popup on the Home page: This screen might be affected by this PR; worth a quick look. Controls involved: “Documentation” and “Status”. Possible (0.50).

    - Claim `clm_020e12769493`: Menu popup on the Home page: This screen might be affected by this PR; worth a quick look. Controls involved: “Documentation” and “Status”.
    - Evidence `ev_12495e5d4267` (pr.file): modified packages/hoppscotch-common/src/components/http/ResponseMeta.vue (+13 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.
    - Evidence `ev_553722392d39` (reason.control): menuitem "Documentation" in 7 file(s). Reference `hoppscotch/hoppscotch#6641:menuitem:Documentation:7`.
    - Evidence `ev_9c703c5fbe08` (reason.control): menuitem "Status" in 5 file(s). Reference `hoppscotch/hoppscotch#6641:menuitem:Status:5`.
    - Evidence `ev_bd3f3ca0b8c4` (pr.file): modified packages/hoppscotch-common/src/components/gql/ResponseMeta.vue (+19 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`.
    - Evidence `ev_ee4520e8df5b` (reason.impact): direct impact on Menu popup on the Home page: 2 control(s), confidence 0.50. Reference `hoppscotch/hoppscotch#6641:direct:/|menu`.
    - Files: `packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`, `packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.

[^4]: **Directly changed.** Save as dialog on the Home page: This screen might be affected by this PR; worth a quick look. Controls involved: “More”. Possible (0.50).

    - Claim `clm_cdac68e90fb1`: Save as dialog on the Home page: This screen might be affected by this PR; worth a quick look. Controls involved: “More”.
    - Evidence `ev_2d143644ffb9` (reason.impact): direct impact on Save as dialog on the Home page: 2 control(s), confidence 0.50. Reference `hoppscotch/hoppscotch#6641:direct:/|Save as`.
    - Evidence `ev_3c8f5ac5501d` (pr.file): modified packages/hoppscotch-common/src/components/gql/Response.vue (+10 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/Response.vue`.
    - Evidence `ev_87ac88ee6fc0` (reason.control): button "More" in 15 file(s). Reference `hoppscotch/hoppscotch#6641:button:More:15`.
    - Files: `packages/hoppscotch-common/src/components/gql/Response.vue`.

[^5]: **Directly changed.** GraphQL Collections dialog on the GraphQL page: This screen might be affected by this PR; worth a quick look. Controls involved: “Export as JSON”. Possible (0.30).

    - Claim `clm_ef5adfa9c2de`: GraphQL Collections dialog on the GraphQL page: This screen might be affected by this PR; worth a quick look. Controls involved: “Export as JSON”.
    - Evidence `ev_53ba8344faa0` (reason.impact): direct impact on GraphQL Collections dialog on the GraphQL page: 1 control(s), confidence 0.30. Reference `hoppscotch/hoppscotch#6641:direct:/graphql|GraphQL Collections`.
    - Evidence `ev_8d41774243c4` (reason.control): menuitem "Export as JSON" in 1 file(s). Reference `hoppscotch/hoppscotch#6641:menuitem:Export as JSON:1`.
    - Evidence `ev_e63e6d766983` (pr.file): modified packages/hoppscotch-common/src/components/graphql/Response.vue (+14 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/graphql/Response.vue`.
    - Files: `packages/hoppscotch-common/src/components/graphql/Response.vue`.

[^6]: **Indirect, through shared code.** MQTT page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Documentation” and “Interceptor”. Possible (0.50).

    - Claim `clm_5a501e0440b1`: MQTT page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Documentation” and “Interceptor”.
    - Evidence `ev_12495e5d4267` (pr.file): modified packages/hoppscotch-common/src/components/http/ResponseMeta.vue (+13 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.
    - Evidence `ev_595f7a65fe61` (reason.control): button "Interceptor" in 1 file(s). Reference `hoppscotch/hoppscotch#6641:button:Interceptor:1`.
    - Evidence `ev_5a80eb0b6159` (reason.impact): indirect impact on MQTT page: 2 control(s), confidence 0.50, via 2 pieces of shared code (gql response meta, http response meta). Reference `hoppscotch/hoppscotch#6641:indirect:/realtime/mqtt|`.
    - Evidence `ev_690b7f70c0cf` (reason.control): button "Documentation" in 2 file(s). Reference `hoppscotch/hoppscotch#6641:button:Documentation:2`.
    - Evidence `ev_bd3f3ca0b8c4` (pr.file): modified packages/hoppscotch-common/src/components/gql/ResponseMeta.vue (+19 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`.
    - Files: `packages/hoppscotch-common/src/components/app/Footer.vue`, `packages/hoppscotch-common/src/components/app/KernelInterceptor.vue`, `packages/hoppscotch-common/src/components/app/ShortcutsPrompt.vue`, `packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`, `packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.

[^7]: **Indirect, through shared code.** Socket.IO page: This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (gql response, gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Documentation”, “Interceptor” and “Wrap lines”. Possible (0.50).

    - Claim `clm_47124d65d79c`: Socket.IO page: This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (gql response, gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Documentation”, “Interceptor” and “Wrap lines”.
    - Evidence `ev_0e670c50ab2d` (reason.control): button "Wrap lines" in 4 file(s). Reference `hoppscotch/hoppscotch#6641:button:Wrap lines:4`.
    - Evidence `ev_12495e5d4267` (pr.file): modified packages/hoppscotch-common/src/components/http/ResponseMeta.vue (+13 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.
    - Evidence `ev_1cfb31e3a1dd` (reason.control): button "Documentation" in 3 file(s). Reference `hoppscotch/hoppscotch#6641:button:Documentation:3`.
    - Evidence `ev_3c8f5ac5501d` (pr.file): modified packages/hoppscotch-common/src/components/gql/Response.vue (+10 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/Response.vue`.
    - Evidence `ev_595f7a65fe61` (reason.control): button "Interceptor" in 1 file(s). Reference `hoppscotch/hoppscotch#6641:button:Interceptor:1`.
    - Evidence `ev_bd3f3ca0b8c4` (pr.file): modified packages/hoppscotch-common/src/components/gql/ResponseMeta.vue (+19 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`.
    - Evidence `ev_e58a312cad27` (reason.impact): indirect impact on Socket.IO page: 3 control(s), confidence 0.50, via 3 pieces of shared code (gql response, gql response meta, http response meta). Reference `hoppscotch/hoppscotch#6641:indirect:/realtime/socketio|`.
    - Files: `packages/hoppscotch-common/src/components/app/Footer.vue`, `packages/hoppscotch-common/src/components/app/KernelInterceptor.vue`, `packages/hoppscotch-common/src/components/app/ShortcutsPrompt.vue`, `packages/hoppscotch-common/src/components/cookies/EditCookie.vue`, `packages/hoppscotch-common/src/components/gql/Response.vue`, `packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`, `packages/hoppscotch-common/src/components/http/ResponseInterface.vue`, `packages/hoppscotch-common/src/components/http/ResponseMeta.vue`, `packages/hoppscotch-common/src/components/realtime/Communication.vue`, `packages/hoppscotch-common/src/components/realtime/LogEntry.vue`, `packages/hoppscotch-common/src/pages/realtime/socketio.vue`.

[^8]: **Indirect, through shared code.** SSE page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Documentation” and “Interceptor”. Possible (0.50).

    - Claim `clm_fdeca6ff4f66`: SSE page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Documentation” and “Interceptor”.
    - Evidence `ev_12495e5d4267` (pr.file): modified packages/hoppscotch-common/src/components/http/ResponseMeta.vue (+13 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.
    - Evidence `ev_26c1b14b0cfa` (reason.impact): indirect impact on SSE page: 3 control(s), confidence 0.50, via 2 pieces of shared code (gql response meta, http response meta). Reference `hoppscotch/hoppscotch#6641:indirect:/realtime/sse|`.
    - Evidence `ev_595f7a65fe61` (reason.control): button "Interceptor" in 1 file(s). Reference `hoppscotch/hoppscotch#6641:button:Interceptor:1`.
    - Evidence `ev_690b7f70c0cf` (reason.control): button "Documentation" in 2 file(s). Reference `hoppscotch/hoppscotch#6641:button:Documentation:2`.
    - Evidence `ev_bd3f3ca0b8c4` (pr.file): modified packages/hoppscotch-common/src/components/gql/ResponseMeta.vue (+19 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`.
    - Files: `packages/hoppscotch-common/src/components/app/Footer.vue`, `packages/hoppscotch-common/src/components/app/KernelInterceptor.vue`, `packages/hoppscotch-common/src/components/app/ShortcutsPrompt.vue`, `packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`, `packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.

[^9]: **Indirect, through shared code.** WebSocket page: This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (gql response, gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Documentation”, “Interceptor” and “Wrap lines”. Possible (0.50).

    - Claim `clm_fc5f7182a0e3`: WebSocket page: This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (gql response, gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Documentation”, “Interceptor” and “Wrap lines”.
    - Evidence `ev_0e670c50ab2d` (reason.control): button "Wrap lines" in 4 file(s). Reference `hoppscotch/hoppscotch#6641:button:Wrap lines:4`.
    - Evidence `ev_12495e5d4267` (pr.file): modified packages/hoppscotch-common/src/components/http/ResponseMeta.vue (+13 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.
    - Evidence `ev_333bc7fef11a` (reason.impact): indirect impact on WebSocket page: 5 control(s), confidence 0.50, via 3 pieces of shared code (gql response, gql response meta, http response meta). Reference `hoppscotch/hoppscotch#6641:indirect:/realtime/websocket|`.
    - Evidence `ev_3c8f5ac5501d` (pr.file): modified packages/hoppscotch-common/src/components/gql/Response.vue (+10 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/Response.vue`.
    - Evidence `ev_595f7a65fe61` (reason.control): button "Interceptor" in 1 file(s). Reference `hoppscotch/hoppscotch#6641:button:Interceptor:1`.
    - Evidence `ev_690b7f70c0cf` (reason.control): button "Documentation" in 2 file(s). Reference `hoppscotch/hoppscotch#6641:button:Documentation:2`.
    - Evidence `ev_bd3f3ca0b8c4` (pr.file): modified packages/hoppscotch-common/src/components/gql/ResponseMeta.vue (+19 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`.
    - Files: `packages/hoppscotch-common/src/components/app/Footer.vue`, `packages/hoppscotch-common/src/components/app/KernelInterceptor.vue`, `packages/hoppscotch-common/src/components/app/ShortcutsPrompt.vue`, `packages/hoppscotch-common/src/components/cookies/EditCookie.vue`, `packages/hoppscotch-common/src/components/gql/Response.vue`, `packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`, `packages/hoppscotch-common/src/components/http/ResponseInterface.vue`, `packages/hoppscotch-common/src/components/http/ResponseMeta.vue`, `packages/hoppscotch-common/src/components/realtime/Communication.vue`, `packages/hoppscotch-common/src/components/realtime/LogEntry.vue`.

[^10]: **Indirect, through shared code.** Not-found page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Interceptor”. Possible (0.36).

    - Claim `clm_343ecb4dca5c`: Not-found page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Interceptor”.
    - Evidence `ev_12495e5d4267` (pr.file): modified packages/hoppscotch-common/src/components/http/ResponseMeta.vue (+13 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.
    - Evidence `ev_595f7a65fe61` (reason.control): button "Interceptor" in 1 file(s). Reference `hoppscotch/hoppscotch#6641:button:Interceptor:1`.
    - Evidence `ev_bd3f3ca0b8c4` (pr.file): modified packages/hoppscotch-common/src/components/gql/ResponseMeta.vue (+19 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`.
    - Evidence `ev_ccbc3a813ae6` (reason.impact): indirect impact on Not-found page: 1 control(s), confidence 0.36, via 2 pieces of shared code (gql response meta, http response meta). Reference `hoppscotch/hoppscotch#6641:indirect:/*|`.
    - Files: `packages/hoppscotch-common/src/components/app/KernelInterceptor.vue`, `packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`, `packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.

[^11]: **Indirect, through shared code.** Settings page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Interceptor”. Possible (0.36).

    - Claim `clm_59ab0340b95e`: Settings page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (gql response meta, http response meta), which this PR edits (its behaviour changed). Controls built from code that uses it: “Interceptor”.
    - Evidence `ev_12495e5d4267` (pr.file): modified packages/hoppscotch-common/src/components/http/ResponseMeta.vue (+13 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.
    - Evidence `ev_595f7a65fe61` (reason.control): button "Interceptor" in 1 file(s). Reference `hoppscotch/hoppscotch#6641:button:Interceptor:1`.
    - Evidence `ev_bd3f3ca0b8c4` (pr.file): modified packages/hoppscotch-common/src/components/gql/ResponseMeta.vue (+19 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`.
    - Evidence `ev_bede94d519f7` (reason.impact): indirect impact on Settings page: 1 control(s), confidence 0.36, via 2 pieces of shared code (gql response meta, http response meta). Reference `hoppscotch/hoppscotch#6641:indirect:/settings|`.
    - Files: `packages/hoppscotch-common/src/components/app/KernelInterceptor.vue`, `packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`, `packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.

[^12]: **Requirements at risk.** GraphQL API Testing > Response: User can download and copy returned GraphQL responses. Likely.

    - Claim `clm_d2e6fcddae82`: “User can download and copy returned GraphQL responses.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0eb78877f38a` (link.i18n_key): button "Copy" equals the English value of i18n key action.copy; packages/hoppscotch-common/src/components/graphql/Sidebar.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 14 files with this text on the page. Reference `el_228d946474ff`.
    - Evidence `ev_176f5e49ec8d` (link.i18n_key): button "Copy" equals the English value of i18n key action.copy; packages/hoppscotch-common/src/components/realtime/LogEntry.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 14 files with this text on the page. Reference `el_d6117356f0ed`.
    - Evidence `ev_1daada532550` (link.i18n_key): button "Copy" equals the English value of i18n key action.copy; packages/hoppscotch-common/src/components/mockServer/MockServerCreatedInfo.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 14 files with this text on the page. Reference `el_d6117356f0ed`.
    - Evidence `ev_1f71e9f758c1` (link.i18n_key): button "Copy" equals the English value of i18n key action.copy; packages/hoppscotch-common/src/components/graphql/Variable.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 14 files with this text on the page. Reference `el_d6117356f0ed`.
    - Evidence `ev_27f57ce4b885` (reason.requirement): requirement req_0ee81ee1b9a2 is at_risk at likely. Reference `hoppscotch/hoppscotch#6641:req_0ee81ee1b9a2:at_risk`.
    - Requirements: `req_0ee81ee1b9a2`.

[^13]: **Requirements at risk.** Realtime API Testing > Response: User can view responses and logs in the response section once the connection is established. Likely.

    - Claim `clm_915a0f3e3219`: “User can view responses and logs in the response section once the connection is established.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0ffa21f909fc` (link.bm25_judge): requirement "User can view responses and logs in the response section once the connection is established." (Realtime API Testing > Response) -> element button 'Response' on screen "GraphQL • Hoppscotch", tabs "Untitled", "Query", "Documentation"; judged partial/partial over 2 samples, bm25 rank 1. Reference `req_2b2a27e7080e`.
    - Evidence `ev_512387621a15` (code.import): packages/hoppscotch-common/src/components/gql/ResponseCompareModal.vue imports packages/hoppscotch-common/src/helpers/diff.ts via \~/helpers/diff symbols=computeLineDiff,getUtf8ByteSize. Reference `packages/hoppscotch-common/src/components/gql/ResponseCompareModal.vue->packages/hoppscotch-common/src/helpers/diff.ts`.
    - Evidence `ev_576016dcc506` (link.i18n_key): button "Response" equals the English value of i18n key response.title; packages/hoppscotch-common/src/components/graphql/Response.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql). Reference `el_e08df81a2a31`.
    - Evidence `ev_7171ab88f81d` (reason.requirement): requirement req_2b2a27e7080e is at_risk at likely. Reference `hoppscotch/hoppscotch#6641:req_2b2a27e7080e:at_risk`.
    - Evidence `ev_7bbceaeba3fb` (code.import): packages/hoppscotch-common/src/components/graphql/Response.vue imports packages/hoppscotch-common/src/components/gql/ResponseCompareModal.vue via ../gql/ResponseCompareModal.vue symbols=default. Reference `packages/hoppscotch-common/src/components/graphql/Response.vue->packages/hoppscotch-common/src/components/gql/ResponseCompareModal.vue`.
    - Requirements: `req_2b2a27e7080e`.

[^14]: **Requirements at risk.** Runner > Runner Results: User can view headers and bodies of persisted responses by selecting each request when enabled. Likely.

    - Claim `clm_299f21fa89b9`: “User can view headers and bodies of persisted responses by selecting each request when enabled.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_053f446bbd18` (link.bm25_judge): requirement "User can view headers and bodies of persisted responses by selecting each request when enabled." (Runner > Runner Results) -> element button 'Response' on screen "GraphQL • Hoppscotch", tabs "Untitled", "Query", "Documentation"; judged partial/partial over 2 samples, bm25 rank 3. Reference `req_9046a70ced66`.
    - Evidence `ev_512387621a15` (code.import): packages/hoppscotch-common/src/components/gql/ResponseCompareModal.vue imports packages/hoppscotch-common/src/helpers/diff.ts via \~/helpers/diff symbols=computeLineDiff,getUtf8ByteSize. Reference `packages/hoppscotch-common/src/components/gql/ResponseCompareModal.vue->packages/hoppscotch-common/src/helpers/diff.ts`.
    - Evidence `ev_576016dcc506` (link.i18n_key): button "Response" equals the English value of i18n key response.title; packages/hoppscotch-common/src/components/graphql/Response.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql). Reference `el_e08df81a2a31`.
    - Evidence `ev_7bbceaeba3fb` (code.import): packages/hoppscotch-common/src/components/graphql/Response.vue imports packages/hoppscotch-common/src/components/gql/ResponseCompareModal.vue via ../gql/ResponseCompareModal.vue symbols=default. Reference `packages/hoppscotch-common/src/components/graphql/Response.vue->packages/hoppscotch-common/src/components/gql/ResponseCompareModal.vue`.
    - Evidence `ev_d5cb1ab3d941` (reason.requirement): requirement req_9046a70ced66 is at_risk at likely. Reference `hoppscotch/hoppscotch#6641:req_9046a70ced66:at_risk`.
    - Requirements: `req_9046a70ced66`.

[^15]: **Requirements at risk.** RESTful API Testing > Request: User can run Tests on the responses received. Possible.

    - Claim `clm_f26df0892570`: “User can run Tests on the responses received.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_0a7c5f8ddc35` (link.i18n_key): button "Wrap lines" equals the English value of i18n key state.linewrap; packages/hoppscotch-common/src/components/http/RequestVariables.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 19 files with this text on the page. Reference `el_4362b59162d0`.
    - Evidence `ev_0f7876561c54` (link.i18n_key): button "Wrap lines" equals the English value of i18n key state.linewrap; packages/hoppscotch-common/src/components/http/ResponseInterface.vue uses it and is rendered under packages/hoppscotch-common/src/layouts/default.vue (route /); one of 19 files with this text on the page. Reference `el_4362b59162d0`.
    - Evidence `ev_43f23ff9d7f3` (reason.requirement): requirement req_741ef7e034a6 is at_risk at possible. Reference `hoppscotch/hoppscotch#6641:req_741ef7e034a6:at_risk`.
    - Requirements: `req_741ef7e034a6`.

[^16]: **Requirements at risk.** Scripts: User can write pre-request scripts and post-request tests in JavaScript to add dynamic behavior, set variables, and validate responses. Possible.

    - Claim `clm_75a6959f86a1`: “User can write pre-request scripts and post-request tests in JavaScript to add dynamic behavior, set variables, and validate responses.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_1736c73e10cd` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/ExampleResponse.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_1bb1ac1e41de` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_fc9e1d578995` (reason.requirement): requirement req_21b7a82f89ef is at_risk at possible. Reference `hoppscotch/hoppscotch#6641:req_21b7a82f89ef:at_risk`.
    - Requirements: `req_21b7a82f89ef`.

[^17]: **Requirements at risk.** Hoppscotch Progressive Web App > Install Hoppscotch PWA: User can click the "Install +" button at the top right of the address bar to install the PWA. Unknown.

    - Claim `clm_1d71a934519f`: “User can click the "Install +" button at the top right of the address bar to install the PWA.”: We cannot tell whether the screens that should show this requirement still do; not determined.
    - Evidence `ev_27a751974e1a` (reason.absence): requirement has no realising control; its searched screens sit under 3 changed file(s); words it shares with the change: button, right. Reference `hoppscotch/hoppscotch#6641:req_7cebff10bcf5`.
    - Evidence `ev_dad21dace3ff` (reason.requirement): requirement req_7cebff10bcf5 is cannot_verify at unknown. Reference `hoppscotch/hoppscotch#6641:req_7cebff10bcf5:cannot_verify`.
    - Requirements: `req_7cebff10bcf5`.

[^18]: **Requirements at risk.** Context Menu: User can use the context menu on selected text to choose Set as Variable, Add to Parameters, or Open Request in New Tab. Possible.

    - Claim `clm_2f6906ed349b`: “User can use the context menu on selected text to choose Set as Variable, Add to Parameters, or Open Request in New Tab.”: This PR adds wording that might close this gap; worth a quick look.
    - Evidence `ev_cdcc912dbe61` (reason.requirement): requirement req_2039a51bc971 is may_close_gap at possible. Reference `hoppscotch/hoppscotch#6641:req_2039a51bc971:may_close_gap`.
    - Evidence `ev_d074cbb06843` (reason.absence): requirement has no realising control; its searched screens sit under 3 changed file(s); words it shares with the change: add, open, request, select, set, tab, text. Reference `hoppscotch/hoppscotch#6641:req_2039a51bc971`.
    - Requirements: `req_2039a51bc971`.

[^19]: **Requirements at risk.** Runner > Run Settings: User can have the collection run stop if a script error or request sending problem occurs. Possible.

    - Claim `clm_c7f3e24ea0bd`: “User can have the collection run stop if a script error or request sending problem occurs.”: This PR adds wording that might close this gap; worth a quick look.
    - Evidence `ev_1e4dd34da393` (reason.absence): requirement has no realising control; its searched screens sit under 3 changed file(s); words it shares with the change: request, run, send. Reference `hoppscotch/hoppscotch#6641:req_f7cb70d6f5a1`.
    - Evidence `ev_5ef4aa132911` (reason.requirement): requirement req_f7cb70d6f5a1 is may_close_gap at possible. Reference `hoppscotch/hoppscotch#6641:req_f7cb70d6f5a1:may_close_gap`.
    - Requirements: `req_f7cb70d6f5a1`.

[^20]: **What we could not determine, and who should look.** the whole PR: The PR base is 10 commit(s) behind the indexed main and 2 changed file(s) were also edited there: the diff may not apply as shown.

    - Reason code `STALE_BASE`.
    - Evidence `ev_c39f73cfc7eb` (pr.meta): PR #6641 head 5eea64fd209a base ac145e7f7581 (fork NavuluriBalaji/hoppscotch): 13 of 13 files. Reference `hoppscotch/hoppscotch#6641@5eea64fd209addde0d770831b7234817eb339699`.

[^21]: **What we could not determine, and who should look.** the http response compare modal component: This PR adds the file and other code in the PR uses it, but none of that code reaches a screen we observed.

    - Reason code `UNMAPPED_CHANGE`.
    - Evidence `ev_aacdd73d2b8d` (pr.file): added packages/hoppscotch-common/src/components/http/ResponseCompareModal.vue (+407 -0) class component kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/http/ResponseCompareModal.vue`.
    - Evidence `ev_fdf104a1044e` (reason.probe): packages/hoppscotch-common/src/components/http/ResponseCompareModal.vue is imported by 1 file(s) (1 in the PR); 0 of them draw a screen. Reference `packages/hoppscotch-common/src/components/http/ResponseCompareModal.vue`.

[^22]: **What we could not determine, and who should look.** 4 items: the shared gql response code, the shared gql response meta code, the shared http response code and 1 more: The only links we found to screens are weak.

    - Reason code `NEEDS_HUMAN`.
    - Evidence `ev_12495e5d4267` (pr.file): modified packages/hoppscotch-common/src/components/http/ResponseMeta.vue (+13 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.
    - Evidence `ev_2b8262036f73` (reason.probe): packages/hoppscotch-common/src/components/gql/Response.vue is imported by 2 file(s) (2 component); 2 of them draw a screen. Reference `packages/hoppscotch-common/src/components/gql/Response.vue`.
    - Evidence `ev_3c8f5ac5501d` (pr.file): modified packages/hoppscotch-common/src/components/gql/Response.vue (+10 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/Response.vue`.
    - Evidence `ev_8660859c1214` (pr.file): modified packages/hoppscotch-common/src/components/http/Response.vue (+9 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/http/Response.vue`.
    - Evidence `ev_8c0f59866fb0` (reason.probe): packages/hoppscotch-common/src/components/http/Response.vue is imported by 2 file(s) (2 component); 2 of them draw a screen. Reference `packages/hoppscotch-common/src/components/http/Response.vue`.
    - Evidence `ev_9658ca2b1e95` (reason.probe): packages/hoppscotch-common/src/components/gql/ResponseMeta.vue is imported by 1 file(s) (1 component); 1 of them draw a screen. Reference `packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`.
    - Evidence `ev_bca133f7f4de` (reason.probe): packages/hoppscotch-common/src/components/http/ResponseMeta.vue is imported by 2 file(s) (2 component); 2 of them draw a screen. Reference `packages/hoppscotch-common/src/components/http/ResponseMeta.vue`.
    - Evidence `ev_bd3f3ca0b8c4` (pr.file): modified packages/hoppscotch-common/src/components/gql/ResponseMeta.vue (+19 -1) class component kind BODY. Reference `hoppscotch/hoppscotch#6641:packages/hoppscotch-common/src/components/gql/ResponseMeta.vue`.

### How the bands are set

- Confirmed: confidence >= 0.8.
- Likely: confidence 0.5 to 0.8.
- Possible: confidence 0.25 to 0.5.
- Unknown: confidence < 0.25.

The bands are rules of thumb applied to how each link was found. They have not been validated against outcomes, so read them as an ordering, not as probabilities.

Generated by blastradius 0.1.0.
