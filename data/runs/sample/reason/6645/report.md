# Blast radius: PR #6645 "feat(common): edit environment values from quick peek and the sidebar"

**Risk: HIGH.** Touches Home page and Menu popup on the Home page. 10 screens, 5 user flows, 12 requirements.

Confidence: 2 items confirmed, 0 probable and 8 possible. 5 things could not be determined and are listed below with the same weight as the findings.

The PR branch is 10 commits behind main, so its diff may not apply as shown.

## What to re-test first

- [ ] On the Home page, click “Environment Quick Peek”. Why: the Home page is changed by this PR. [^1]
- [ ] On the Menu popup on the Home page, click “Edit”. Why: the Menu popup on the Home page is changed by this PR. [^2]

## Directly changed

These screens and elements have their own code in this PR.

How sure: Confirmed (the PR's own code changes it), Likely (reached through code it shares), Possible (a weaker connection, worth a look), Unknown (we cannot tell).

| Screen | What changed | How sure |
|---|---|---|
| **Home page** | This screen is changed by this PR. Controls involved: “Environment Quick Peek”, “Environments”, “Global” and 3 more. [^1] Flows: REST > Body > Environment Quick Peek; REST > Body > Select environment; REST > Environments; REST > New Collection. | Confirmed |
| **Menu popup on the Home page** | This screen is changed by this PR. Controls involved: “DELETE”, “Edit”, “No environment” and 3 more. [^2] | Confirmed |
| **Save as dialog on the Home page** | This screen might be affected by this PR; worth a quick look. Controls involved: “Search”. [^3] | Possible |

## Indirect, through shared code

Screens that reuse code this PR changes, grouped by the part of the spec they belong to.

### Collections > Organizing collections > Adding requests to collections

Connected through: 2 pieces of shared code (current environment value, secret environment).

| Screen | What may be affected | How sure |
|---|---|---|
| **Save as dialog on the GraphQL page** | This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Generate Request Name Using AI”, “Request name” and “Save”. [^4] | Possible |

### Other screens

Connected through: 2 pieces of shared code (current environment value, secret environment); 3 pieces of shared code (current environment value, secret environment, teams environment); the shared teams environment code.

| Screen | What may be affected | How sure |
|---|---|---|
| **GraphQL page** | This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (current environment value, secret environment, teams environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store; its behaviour changed). Controls built from code that uses it: “Authorization”, “Clear”, “Collections” and 9 more. [^5] Flows: GraphQL > Variables. | Possible |
| **Login to Hoppscotch dialog on the WebSocket page** | This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Continue with Email”, “Continue with GitHub”, “Continue with Google” and 1 more. [^6] | Possible |
| **SSE page** | This screen might be affected by this PR; worth a quick look. It uses the shared teams environment code, which this PR edits (its behaviour changed). Controls built from code that uses it: “Delete”. [^7] | Possible |
| **New Collection dialog on the GraphQL page** | This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). [^8] | Possible |
| **Socket.IO page** | This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Send”. [^9] | Possible |
| **WebSocket page** | This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Send”. [^10] | Possible |

## Requirements at risk

| What the spec requires | Where in the spec | Status | How sure |
|---|---|---|---|
| User can use global, environment, and request-specific variable scopes. [^11] Spec says: “Hoppscotch supports various variable scopes, including global, environment, and request-specific variables.” | Context Menu > Set as Variable | At risk | Likely |
| User can assign a selected value to an environment variable using the Set as Variable option from the context menu. [^12] Spec says: “You can use the Set as Variable option from the context menu to assign a selected value to an environment variable.” | Context Menu > Set as Variable | At risk | Likely |
| User can create global, personal, and shared environments to manage reusable variables across requests, scripts, and workspaces. [^13] Spec says: “Create global, personal, and shared environments in Hoppscotch to manage reusable variables across API requests, scripts, and workspaces.” | Environments | At risk | Likely |
| User can open the Environments icon on the sidebar to create environments. [^14] Spec says: “Click on the "Environments" icon on the sidebar to create environments.” | Environments > Creating an environment | At risk | Likely |
| User can duplicate an environment from the More menu by selecting Duplicate, creating a new environment with the suffix - Duplicate. [^15] Spec says: “From the dropdown menu, select Duplicate.” | Environments > Duplicating an environment | At risk | Likely |
| User can choose whether to sync a personal or global environment. [^16] Spec says: “However, you have the option to choose whether to sync a personal or global environment.” | Environments > Types of Variables in an Environment | At risk | Likely |
| User can create secret environment variables whose values are masked with asterisks and not synced to the server. [^17] Spec says: “All secret variable values in Hoppscotch will be masked using asterisks (\\\\\\\*).” | Environments > Types of Variables in an Environment | At risk | Likely |
| User can use the "Tests" tab of an API request to add code that stores response data in environment variables for use in later requests. [^18] Spec says: “In the "Tests" tab of the first API request (that returns the tokens), add the following code:” | Scripts > pw.response > Setting Environment Variables from API Responses | At risk | Likely |
| User can use the AI Assistant, an experimental chat assistant, to act on the request, response, environment, and collections they are working with. [^19] Spec says: “The AI Assistant is an experimental chat assistant that can act on the request, response, environment, and collections you're working with.” | AI Features > AI Assistant | At risk | Possible |
| User can select Accept Change to integrate the generated test into the test script editor. [^20] Spec says: “Select Accept Change to integrate it into the test script editor for execution.” | AI Features > Generate Test Scripts | At risk | Possible |
| User can create a sub-collection by clicking Add new on a collection and entering a name. [^21] Spec says: “You can also create a sub-collection by clicking on the "Add new" button on the desired collection and entering the name of the sub-collection.” | Collections > Creating a new collection | At risk | Possible |
| User can add the current request to a collection with the New request button in the collection's options. [^22] Spec says: “To add a request to a collection, click on the "New request" button on the collection's options to save the current request to the collection.” | Collections > Organizing collections > Adding requests to collections | At risk | Possible |

## What we could not determine, and who should look

| Item | Why we could not tell | What a person should check |
|---|---|---|
| **New in this PR; not observable in the live app yet** | This PR adds “Edit current value” and “environments editor”. The live app does not show that yet. | After this PR is deployed, open the screen that should contain “Edit current value” and “environments editor”. |
| **the whole PR** | The PR base is 10 commit(s) behind the indexed main and 2 changed file(s) were also edited there: the diff may not apply as shown. [^23] | Rebase the PR on main and run the report again before relying on the file lists; 2 file(s) were edited on both sides. |
| **the shared teams environment code** | The only links we found to screens are weak. [^24] | Decide which screens that rely on teams environment need a re-test; the list shown is not exhaustive. |
| **controls linked to the teams environment code** | Two independent checks disagreed about 3 control(s); they were left as found. | Look at these controls by hand: DELETE, More, More. |
| **6 screen(s) linked only by very weak evidence: “Collections dialog on the Home page”, “New Collection dialog on the Home page”, “New Folder dialog on the Home page” and 3 more** | The connection from the change to these screens is too weak to call an effect. | Look at these screens if time allows; nothing says they are affected. |

### What this report did not look at

- Only the signed-out surface of the web app was observed.
- One viewport size was used.
- Only English text (\`en\`) was compared.
- Frontend files only: backend, scripts and build tooling are outside this analysis.
- Cloud web edition only: desktop and self-hosted behaviour was not assessed.
- Set aside as not user-facing: 1 generated file(s), 2 test file(s).
- 36 requirement(s) reach this PR only through a whole screen or through code it shares, and do not mention anything it changes; they are not listed.
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
- PR head: `64ba4ff75eff7bef868b010947f50e9b6b326540`
- PR base behind main: 10 commits
- Crawl bundle: `c81a0323d1f5b4f5`
- Crawl size: 150 actions and 55 screens
- Documentation version: 2026\.5.0
- App version: 2026\.9.0

### Evidence behind each statement

[^1]: **Directly changed.** Home page: This screen is changed by this PR. Controls involved: “Environment Quick Peek”, “Environments”, “Global” and 3 more. Confirmed (0.99).

    - Claim `clm_64a8b44c3b54`: Home page: This screen is changed by this PR. Controls involved: “Environment Quick Peek”, “Environments”, “Global” and 3 more.
    - Evidence `ev_02324575a2ba` (pr.file): modified packages/hoppscotch-common/src/components/environments/Selector.vue (+29 -4) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/Selector.vue`.
    - Evidence `ev_03a21058305c` (reason.control): generic "Global" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:generic:Global:1`.
    - Evidence `ev_12228301bc6c` (reason.control): button "Environments" in 2 file(s). Reference `hoppscotch/hoppscotch#6645:button:Environments:2`.
    - Evidence `ev_19d2e2dd495c` (pr.file): modified packages/hoppscotch-common/src/components/environments/my/index.vue (+4 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_213d5ca619d8` (reason.control): button "New" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:New:1`.
    - Evidence `ev_2f106db5f760` (reason.impact): direct impact on Home page: 42 control(s), confidence 0.99. Reference `hoppscotch/hoppscotch#6645:direct:/|`.
    - Evidence `ev_7759c78c507b` (reason.control): button "Select environment" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Select environment:1`.
    - Evidence `ev_7f62f2dfe65e` (pr.file): modified packages/hoppscotch-common/src/components/environments/teams/index.vue (+5 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/teams/index.vue`.
    - Evidence `ev_a6cb6eca35ad` (reason.control): searchbox "Search" in 4 file(s). Reference `hoppscotch/hoppscotch#6645:searchbox:Search:4`.
    - Evidence `ev_e32e9b25cc57` (reason.control): button "Environment Quick Peek" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Environment Quick Peek:1`.
    - Evidence `ev_eb418800dd4a` (pr.file): modified packages/hoppscotch-common/src/components/environments/index.vue (+53 -26) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/index.vue`.
    - Files: `packages/hoppscotch-common/src/components/environments/Selector.vue`, `packages/hoppscotch-common/src/components/environments/index.vue`, `packages/hoppscotch-common/src/components/environments/my/index.vue`, `packages/hoppscotch-common/src/components/environments/teams/index.vue`.
    - Requirements: `req_0560dca1573e`, `req_18444664dc0d`, `req_1f5961c9f955`, `req_258609b57bf2`, `req_56b132947294`, `req_606faa4c4e13`, `req_ef494b31c799`.
    - Flows: `REST > Body > Environment Quick Peek`, `REST > Body > Select environment`, `REST > Environments`, `REST > New Collection`.

[^2]: **Directly changed.** Menu popup on the Home page: This screen is changed by this PR. Controls involved: “DELETE”, “Edit”, “No environment” and 3 more. Confirmed (0.99).

    - Claim `clm_95e1069536ef`: Menu popup on the Home page: This screen is changed by this PR. Controls involved: “DELETE”, “Edit”, “No environment” and 3 more.
    - Evidence `ev_02324575a2ba` (pr.file): modified packages/hoppscotch-common/src/components/environments/Selector.vue (+29 -4) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/Selector.vue`.
    - Evidence `ev_1177a6fc4493` (reason.control): textbox "Search" in 3 file(s). Reference `hoppscotch/hoppscotch#6645:textbox:Search:3`.
    - Evidence `ev_1ba79a70c5ee` (reason.control): button "Workspace Environments" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Workspace Environments:1`.
    - Evidence `ev_56902424d433` (pr.file): modified packages/hoppscotch-common/src/components/environments/teams/Environment.vue (+8 -4) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/teams/Environment.vue`.
    - Evidence `ev_57953b3ce1f3` (pr.file): modified packages/hoppscotch-common/src/components/environments/my/Environment.vue (+8 -4) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/my/Environment.vue`.
    - Evidence `ev_706e0b241d30` (reason.control): menuitem "DELETE" in 15 file(s). Reference `hoppscotch/hoppscotch#6645:menuitem:DELETE:15`.
    - Evidence `ev_9f5782109e5d` (reason.control): menuitem "No environment" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:menuitem:No environment:1`.
    - Evidence `ev_b46048180abc` (reason.impact): direct impact on Menu popup on the Home page: 7 control(s), confidence 0.99. Reference `hoppscotch/hoppscotch#6645:direct:/|menu`.
    - Evidence `ev_ba5fb0a0f30f` (reason.control): button "Personal Environments" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Personal Environments:1`.
    - Evidence `ev_c54e1e017611` (reason.control): button "Edit" in 12 file(s). Reference `hoppscotch/hoppscotch#6645:button:Edit:12`.
    - Files: `packages/hoppscotch-common/src/components/environments/Selector.vue`, `packages/hoppscotch-common/src/components/environments/my/Environment.vue`, `packages/hoppscotch-common/src/components/environments/teams/Environment.vue`.
    - Requirements: `req_0560dca1573e`, `req_56b132947294`, `req_6063e76c7c37`, `req_654c209d1b55`.

[^3]: **Directly changed.** Save as dialog on the Home page: This screen might be affected by this PR; worth a quick look. Controls involved: “Search”. Possible (0.30).

    - Claim `clm_d10911ce220b`: Save as dialog on the Home page: This screen might be affected by this PR; worth a quick look. Controls involved: “Search”.
    - Evidence `ev_02324575a2ba` (pr.file): modified packages/hoppscotch-common/src/components/environments/Selector.vue (+29 -4) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/Selector.vue`.
    - Evidence `ev_0ac4dd8396a7` (reason.control): searchbox "Search" in 5 file(s). Reference `hoppscotch/hoppscotch#6645:searchbox:Search:5`.
    - Evidence `ev_19d2e2dd495c` (pr.file): modified packages/hoppscotch-common/src/components/environments/my/index.vue (+4 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_7f62f2dfe65e` (pr.file): modified packages/hoppscotch-common/src/components/environments/teams/index.vue (+5 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/teams/index.vue`.
    - Evidence `ev_b570f3ee1099` (reason.impact): direct impact on Save as dialog on the Home page: 1 control(s), confidence 0.30. Reference `hoppscotch/hoppscotch#6645:direct:/|Save as`.
    - Files: `packages/hoppscotch-common/src/components/environments/Selector.vue`, `packages/hoppscotch-common/src/components/environments/my/index.vue`, `packages/hoppscotch-common/src/components/environments/teams/index.vue`.

[^4]: **Indirect, through shared code.** Save as dialog on the GraphQL page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Generate Request Name Using AI”, “Request name” and “Save”. Possible (0.50).

    - Claim `clm_16868bbefec6`: Save as dialog on the GraphQL page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Generate Request Name Using AI”, “Request name” and “Save”.
    - Evidence `ev_46547990b66b` (pr.file): modified packages/hoppscotch-common/src/services/current-environment-value.service.ts (+50 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/current-environment-value.service.ts`.
    - Evidence `ev_83ca75d0d747` (reason.control): button "Save" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Save:1`.
    - Evidence `ev_b78081607646` (pr.file): modified packages/hoppscotch-common/src/services/secret-environment.service.ts (+55 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_f06f0222750d` (reason.control): button "Generate Request Name Using AI" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Generate Request Name Using AI:1`.
    - Evidence `ev_f23a930a2d56` (reason.impact): indirect impact on Save as dialog on the GraphQL page: 3 control(s), confidence 0.50, via 2 pieces of shared code (current environment value, secret environment). Reference `hoppscotch/hoppscotch#6645:indirect:/graphql|Save as`.
    - Evidence `ev_f78143829f88` (reason.control): textbox "Request name" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:textbox:Request name:1`.
    - Files: `packages/hoppscotch-common/src/components/collections/SaveRequest.vue`, `packages/hoppscotch-common/src/services/current-environment-value.service.ts`, `packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Requirements: `req_0b11b11f956f`, `req_8f8a53fde411`, `req_eab4e3bd569b`.

[^5]: **Indirect, through shared code.** GraphQL page: This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (current environment value, secret environment, teams environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store; its behaviour changed). Controls built from code that uses it: “Authorization”, “Clear”, “Collections” and 9 more. Possible (0.50).

    - Claim `clm_87134c8e49e3`: GraphQL page: This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (current environment value, secret environment, teams environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store; its behaviour changed). Controls built from code that uses it: “Authorization”, “Clear”, “Collections” and 9 more.
    - Evidence `ev_0026fcece361` (reason.control): button "Turn off" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Turn off:1`.
    - Evidence `ev_2821d6efba8e` (reason.control): button "Clear" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Clear:1`.
    - Evidence `ev_2a0998024081` (reason.control): button "Variables" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Variables:1`.
    - Evidence `ev_2bf98275e4b0` (reason.control): checkbox "Enabled" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:checkbox:Enabled:1`.
    - Evidence `ev_429bea3c2839` (reason.control): button "Query" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Query:1`.
    - Evidence `ev_46547990b66b` (pr.file): modified packages/hoppscotch-common/src/services/current-environment-value.service.ts (+50 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/current-environment-value.service.ts`.
    - Evidence `ev_56902424d433` (pr.file): modified packages/hoppscotch-common/src/components/environments/teams/Environment.vue (+8 -4) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/teams/Environment.vue`.
    - Evidence `ev_8a0261c3c61d` (reason.control): button "Collections" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Collections:1`.
    - Evidence `ev_aa54516892a4` (reason.control): button "Authorization" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Authorization:1`.
    - Evidence `ev_b78081607646` (pr.file): modified packages/hoppscotch-common/src/services/secret-environment.service.ts (+55 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_e274eb1a862a` (reason.impact): indirect impact on GraphQL page: 61 control(s), confidence 0.50, via 3 pieces of shared code (current environment value, secret environment, teams environment). Reference `hoppscotch/hoppscotch#6645:indirect:/graphql|`.
    - Evidence `ev_e6c5bb5ddd82` (reason.control): button "Headers" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Headers:1`.
    - Files: `packages/hoppscotch-common/src/components/collections/Collection.vue`, `packages/hoppscotch-common/src/components/collections/ExampleResponse.vue`, `packages/hoppscotch-common/src/components/collections/Request.vue`, `packages/hoppscotch-common/src/components/collections/Variables.vue`, `packages/hoppscotch-common/src/components/collections/graphql/Collection.vue`, `packages/hoppscotch-common/src/components/collections/graphql/Folder.vue`, `packages/hoppscotch-common/src/components/collections/graphql/Request.vue`, `packages/hoppscotch-common/src/components/collections/index.vue`, `packages/hoppscotch-common/src/components/environments/teams/Environment.vue`, `packages/hoppscotch-common/src/components/graphql/RequestOptions.vue`, `packages/hoppscotch-common/src/components/graphql/Response.vue`, `packages/hoppscotch-common/src/components/http/Authorization.vue`, `packages/hoppscotch-common/src/components/http/KeyValue.vue`, `packages/hoppscotch-common/src/components/http/authorization/ApiKey.vue`, `packages/hoppscotch-common/src/components/teams/Team.vue`, `packages/hoppscotch-common/src/services/current-environment-value.service.ts`, `packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Flows: `GraphQL > Variables`.

[^6]: **Indirect, through shared code.** Login to Hoppscotch dialog on the WebSocket page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Continue with Email”, “Continue with GitHub”, “Continue with Google” and 1 more. Possible (0.50).

    - Claim `clm_8508103e2e8d`: Login to Hoppscotch dialog on the WebSocket page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Continue with Email”, “Continue with GitHub”, “Continue with Google” and 1 more.
    - Evidence `ev_1b0e11a18f35` (reason.control): menuitem "Continue with GitHub" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:menuitem:Continue with GitHub:1`.
    - Evidence `ev_26704d5f0d16` (reason.impact): indirect impact on Login to Hoppscotch dialog on the WebSocket page: 4 control(s), confidence 0.50, via 2 pieces of shared code (current environment value, secret environment). Reference `hoppscotch/hoppscotch#6645:indirect:/realtime/websocket|Login to Hoppscotch`.
    - Evidence `ev_46547990b66b` (pr.file): modified packages/hoppscotch-common/src/services/current-environment-value.service.ts (+50 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/current-environment-value.service.ts`.
    - Evidence `ev_92bf322f807c` (reason.control): menuitem "Continue with Email" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:menuitem:Continue with Email:1`.
    - Evidence `ev_b474e9692428` (reason.control): menuitem "Continue with Microsoft" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:menuitem:Continue with Microsoft:1`.
    - Evidence `ev_b70518c5808b` (reason.control): menuitem "Continue with Google" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:menuitem:Continue with Google:1`.
    - Evidence `ev_b78081607646` (pr.file): modified packages/hoppscotch-common/src/services/secret-environment.service.ts (+55 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Files: `packages/hoppscotch-common/src/components/firebase/Login.vue`, `packages/hoppscotch-common/src/services/current-environment-value.service.ts`, `packages/hoppscotch-common/src/services/secret-environment.service.ts`.

[^7]: **Indirect, through shared code.** SSE page: This screen might be affected by this PR; worth a quick look. It uses the shared teams environment code, which this PR edits (its behaviour changed). Controls built from code that uses it: “Delete”. Possible (0.30).

    - Claim `clm_a4abc4627729`: SSE page: This screen might be affected by this PR; worth a quick look. It uses the shared teams environment code, which this PR edits (its behaviour changed). Controls built from code that uses it: “Delete”.
    - Evidence `ev_037258078133` (reason.impact): indirect impact on SSE page: 1 control(s), confidence 0.30, via the shared teams environment code. Reference `hoppscotch/hoppscotch#6645:indirect:/realtime/sse|`.
    - Evidence `ev_56902424d433` (pr.file): modified packages/hoppscotch-common/src/components/environments/teams/Environment.vue (+8 -4) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/teams/Environment.vue`.
    - Evidence `ev_94116de0e43d` (reason.control): button "Delete" in 3 file(s). Reference `hoppscotch/hoppscotch#6645:button:Delete:3`.
    - Files: `packages/hoppscotch-common/src/components/cookies/AllModal.vue`, `packages/hoppscotch-common/src/components/environments/teams/Environment.vue`, `packages/hoppscotch-common/src/components/realtime/Log.vue`, `packages/hoppscotch-common/src/components/teams/Team.vue`.

[^8]: **Indirect, through shared code.** New Collection dialog on the GraphQL page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Possible (0.29).

    - Claim `clm_fd96e3bbe346`: New Collection dialog on the GraphQL page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store).
    - Evidence `ev_27428043bb66` (reason.impact): indirect impact on New Collection dialog on the GraphQL page: 1 control(s), confidence 0.29, via 2 pieces of shared code (current environment value, secret environment). Reference `hoppscotch/hoppscotch#6645:indirect:/graphql|New Collection`.
    - Evidence `ev_46547990b66b` (pr.file): modified packages/hoppscotch-common/src/services/current-environment-value.service.ts (+50 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/current-environment-value.service.ts`.
    - Evidence `ev_b78081607646` (pr.file): modified packages/hoppscotch-common/src/services/secret-environment.service.ts (+55 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Files: `packages/hoppscotch-common/src/components/collections/graphql/Add.vue`, `packages/hoppscotch-common/src/services/current-environment-value.service.ts`, `packages/hoppscotch-common/src/services/secret-environment.service.ts`.

[^9]: **Indirect, through shared code.** Socket.IO page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Send”. Possible (0.29).

    - Claim `clm_e8bc7bcf7634`: Socket.IO page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Send”.
    - Evidence `ev_4227466c1dac` (reason.impact): indirect impact on Socket.IO page: 1 control(s), confidence 0.29, via 2 pieces of shared code (current environment value, secret environment). Reference `hoppscotch/hoppscotch#6645:indirect:/realtime/socketio|`.
    - Evidence `ev_46547990b66b` (pr.file): modified packages/hoppscotch-common/src/services/current-environment-value.service.ts (+50 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/current-environment-value.service.ts`.
    - Evidence `ev_4661408efd8d` (reason.control): button "Send" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Send:1`.
    - Evidence `ev_b78081607646` (pr.file): modified packages/hoppscotch-common/src/services/secret-environment.service.ts (+55 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Files: `packages/hoppscotch-common/src/components/aichat/Assistant.vue`, `packages/hoppscotch-common/src/services/current-environment-value.service.ts`, `packages/hoppscotch-common/src/services/secret-environment.service.ts`.

[^10]: **Indirect, through shared code.** WebSocket page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Send”. Possible (0.29).

    - Claim `clm_610680aa0133`: WebSocket page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Send”.
    - Evidence `ev_46547990b66b` (pr.file): modified packages/hoppscotch-common/src/services/current-environment-value.service.ts (+50 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/current-environment-value.service.ts`.
    - Evidence `ev_4661408efd8d` (reason.control): button "Send" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Send:1`.
    - Evidence `ev_b78081607646` (pr.file): modified packages/hoppscotch-common/src/services/secret-environment.service.ts (+55 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_c0fa31208f38` (reason.impact): indirect impact on WebSocket page: 1 control(s), confidence 0.29, via 2 pieces of shared code (current environment value, secret environment). Reference `hoppscotch/hoppscotch#6645:indirect:/realtime/websocket|`.
    - Files: `packages/hoppscotch-common/src/components/aichat/Assistant.vue`, `packages/hoppscotch-common/src/services/current-environment-value.service.ts`, `packages/hoppscotch-common/src/services/secret-environment.service.ts`.

[^11]: **Requirements at risk.** Context Menu > Set as Variable: User can use global, environment, and request-specific variable scopes. Likely.

    - Claim `clm_6c2ca9c20d4d`: “User can use global, environment, and request-specific variable scopes.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_4ab2d2b0860b` (code.import): packages/hoppscotch-common/src/components/environments/Selector.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/environments/Selector.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4bd3482b1703` (link.bm25_judge): requirement "User can use global, environment, and request-specific variable scopes." (Context Menu > Set as Variable) -> element generic 'Global' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Environments"; judged partial/partial over 2 samples, bm25 rank 1. Reference `req_258609b57bf2`.
    - Evidence `ev_6465f83b06fa` (code.import): packages/hoppscotch-common/src/components/environments/Selector.vue imports packages/hoppscotch-common/src/components/environments/CurrentValueInput.vue via \~/components/environments/CurrentValueInput.vue symbols=default. Reference `packages/hoppscotch-common/src/components/environments/Selector.vue->packages/hoppscotch-common/src/components/environments/CurrentValueInput.vue`.
    - Evidence `ev_7fc136d777e0` (reason.requirement): requirement req_258609b57bf2 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_258609b57bf2:at_risk`.
    - Evidence `ev_c1b5b87a5552` (link.i18n_key): generic "Global" equals the English value of i18n key environment.global; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_1d8b1db7fec9`.
    - Requirements: `req_258609b57bf2`.

[^12]: **Requirements at risk.** Context Menu > Set as Variable: User can assign a selected value to an environment variable using the Set as Variable option from the context menu. Likely.

    - Claim `clm_a59b09447f7c`: “User can assign a selected value to an environment variable using the Set as Variable option from the context menu.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_845e99baeb7f` (reason.requirement): requirement req_ef494b31c799 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_ef494b31c799:at_risk`.
    - Requirements: `req_ef494b31c799`.

[^13]: **Requirements at risk.** Environments: User can create global, personal, and shared environments to manage reusable variables across requests, scripts, and workspaces. Likely.

    - Claim `clm_8030fc6d7145`: “User can create global, personal, and shared environments to manage reusable variables across requests, scripts, and workspaces.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0af7dceeacf9` (link.i18n_key): textbox "Search" equals the English value of i18n key action.search; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 12 files with this text on the page; also uses the active tab "personal environments" (i18n key environment.my_environments). Reference `el_933ac1587ed1`.
    - Evidence `ev_15002bfa747d` (link.i18n_key): button "Workspace Environments" equals the English value of i18n key environment.team_environments; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_52c0e5ae20ec`.
    - Evidence `ev_2f88a85fa86a` (link.i18n_key): menuitem "No environment" equals the English value of i18n key environment.no_environment; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page; also uses the active tab "personal environments" (i18n key environment.my_environments). Reference `el_506913382c5a`.
    - Evidence `ev_4ab2d2b0860b` (code.import): packages/hoppscotch-common/src/components/environments/Selector.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/environments/Selector.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_b10060aa1241` (reason.requirement): requirement req_654c209d1b55 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_654c209d1b55:at_risk`.
    - Requirements: `req_654c209d1b55`.

[^14]: **Requirements at risk.** Environments > Creating an environment: User can open the Environments icon on the sidebar to create environments. Likely.

    - Claim `clm_cd9d550a658d`: “User can open the Environments icon on the sidebar to create environments.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0665d2950baf` (link.bm25_judge): requirement "User can open the Environments icon on the sidebar to create environments." (Environments > Creating an environment) -> element button 'Environments' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged full/full over 2 samples, bm25 rank 7. Reference `req_56b132947294`.
    - Evidence `ev_15002bfa747d` (link.i18n_key): button "Workspace Environments" equals the English value of i18n key environment.team_environments; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_52c0e5ae20ec`.
    - Evidence `ev_204876695976` (link.bm25_judge): requirement "User can open the Environments icon on the sidebar to create environments." (Environments > Creating an environment) -> element button 'Workspace Environments' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", dialog "menu", tabs "Personal Environments"; judged partial/partial over 2 samples, bm25 rank 4. Reference `req_56b132947294`.
    - Evidence `ev_4ab2d2b0860b` (code.import): packages/hoppscotch-common/src/components/environments/Selector.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/environments/Selector.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_7a6930876b4f` (reason.requirement): requirement req_56b132947294 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_56b132947294:at_risk`.
    - Requirements: `req_56b132947294`.

[^15]: **Requirements at risk.** Environments > Duplicating an environment: User can duplicate an environment from the More menu by selecting Duplicate, creating a new environment with the suffix - Duplicate. Likely.

    - Claim `clm_ac678a56ee18`: “User can duplicate an environment from the More menu by selecting Duplicate, creating a new environment with the suffix - Duplicate.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0af7dceeacf9` (link.i18n_key): textbox "Search" equals the English value of i18n key action.search; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 12 files with this text on the page; also uses the active tab "personal environments" (i18n key environment.my_environments). Reference `el_933ac1587ed1`.
    - Evidence `ev_15002bfa747d` (link.i18n_key): button "Workspace Environments" equals the English value of i18n key environment.team_environments; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_52c0e5ae20ec`.
    - Evidence `ev_2f88a85fa86a` (link.i18n_key): menuitem "No environment" equals the English value of i18n key environment.no_environment; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page; also uses the active tab "personal environments" (i18n key environment.my_environments). Reference `el_506913382c5a`.
    - Evidence `ev_34e916161aa4` (link.bm25_judge): requirement "User can duplicate an environment from the More menu by selecting Duplicate, creating a new environment with the suffix - Duplicate." (Environments > Duplicating an environment) -> element button 'Workspace Environments' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", dialog "menu", tabs "Personal Environments"; judged partial/partial over 2 samples, bm25 rank 3. Reference `req_6063e76c7c37`.
    - Evidence `ev_ed5cfa5d4987` (reason.requirement): requirement req_6063e76c7c37 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_6063e76c7c37:at_risk`.
    - Requirements: `req_6063e76c7c37`.

[^16]: **Requirements at risk.** Environments > Types of Variables in an Environment: User can choose whether to sync a personal or global environment. Likely.

    - Claim `clm_44076a0196f5`: “User can choose whether to sync a personal or global environment.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0af7dceeacf9` (link.i18n_key): textbox "Search" equals the English value of i18n key action.search; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 12 files with this text on the page; also uses the active tab "personal environments" (i18n key environment.my_environments). Reference `el_933ac1587ed1`.
    - Evidence `ev_15002bfa747d` (link.i18n_key): button "Workspace Environments" equals the English value of i18n key environment.team_environments; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_52c0e5ae20ec`.
    - Evidence `ev_2f88a85fa86a` (link.i18n_key): menuitem "No environment" equals the English value of i18n key environment.no_environment; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page; also uses the active tab "personal environments" (i18n key environment.my_environments). Reference `el_506913382c5a`.
    - Evidence `ev_4ab2d2b0860b` (code.import): packages/hoppscotch-common/src/components/environments/Selector.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/environments/Selector.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_7b69b2e2b89b` (reason.requirement): requirement req_0560dca1573e is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_0560dca1573e:at_risk`.
    - Requirements: `req_0560dca1573e`.

[^17]: **Requirements at risk.** Environments > Types of Variables in an Environment: User can create secret environment variables whose values are masked with asterisks and not synced to the server. Likely.

    - Claim `clm_ddb6576a1669`: “User can create secret environment variables whose values are masked with asterisks and not synced to the server.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_aff704b49dcd` (reason.requirement): requirement req_18444664dc0d is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_18444664dc0d:at_risk`.
    - Requirements: `req_18444664dc0d`.

[^18]: **Requirements at risk.** Scripts > pw.response > Setting Environment Variables from API Responses: User can use the "Tests" tab of an API request to add code that stores response data in environment variables for use in later requests. Likely.

    - Claim `clm_650a8530d89c`: “User can use the "Tests" tab of an API request to add code that stores response data in environment variables for use in later requests.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_78358bcc4e9d` (reason.requirement): requirement req_1f5961c9f955 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_1f5961c9f955:at_risk`.
    - Requirements: `req_1f5961c9f955`.

[^19]: **Requirements at risk.** AI Features > AI Assistant: User can use the AI Assistant, an experimental chat assistant, to act on the request, response, environment, and collections they are working with. Possible.

    - Claim `clm_d4b5e54f53e1`: “User can use the AI Assistant, an experimental chat assistant, to act on the request, response, environment, and collections they are working with.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_59b56cd87114` (link.bm25_judge): requirement "User can use the AI Assistant, an experimental chat assistant, to act on the request, response, environment, and collections they are working with." (AI Features > AI Assistant) -> element button 'Generate Request Name Using AI' on screen "GraphQL • Hoppscotch", dialog "Save as"; judged partial/partial over 2 samples, bm25 rank 2. Reference `req_eab4e3bd569b`.
    - Evidence `ev_b9519bfdca9d` (reason.requirement): requirement req_eab4e3bd569b is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_eab4e3bd569b:at_risk`.
    - Evidence `ev_d6c4401fd5c4` (link.i18n_key): button "Generate Request Name Using AI" equals the English value of i18n key ai_experiments.generate_request_name; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 3 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_d1efc39f4400`.
    - Requirements: `req_eab4e3bd569b`.

[^20]: **Requirements at risk.** AI Features > Generate Test Scripts: User can select Accept Change to integrate the generated test into the test script editor. Possible.

    - Claim `clm_f6243b000aed`: “User can select Accept Change to integrate the generated test into the test script editor.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_45cd4e24ff06` (reason.requirement): requirement req_606faa4c4e13 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_606faa4c4e13:at_risk`.
    - Requirements: `req_606faa4c4e13`.

[^21]: **Requirements at risk.** Collections > Creating a new collection: User can create a sub-collection by clicking Add new on a collection and entering a name. Possible.

    - Claim `clm_6e82c1b08b39`: “User can create a sub-collection by clicking Add new on a collection and entering a name.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_08c735f98f69` (link.bm25_judge): requirement "User can create a sub-collection by clicking Add new on a collection and entering a name." (Collections > Creating a new collection) -> element button 'Add new' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Headers", "Collections"; judged full/partial over 2 samples, bm25 rank 3. Reference `req_8f8a53fde411`.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_0eec05c72539` (link.bm25_judge): requirement "User can create a sub-collection by clicking Add new on a collection and entering a name." (Collections > Creating a new collection) -> screen modal screen "GraphQL • Hoppscotch", dialog "Save as" with controls: 'Close', 'New', 'Search', 'Save', 'Add new', 'Wiki', 'Import', 'Request name'; judged partial/partial over 2 samples, bm25 rank 2. Reference `req_8f8a53fde411`.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_320bd0943de5` (reason.requirement): requirement req_8f8a53fde411 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_8f8a53fde411:at_risk`.
    - Requirements: `req_8f8a53fde411`.

[^22]: **Requirements at risk.** Collections > Organizing collections > Adding requests to collections: User can add the current request to a collection with the New request button in the collection's options. Possible.

    - Claim `clm_189067864ae8`: “User can add the current request to a collection with the New request button in the collection's options.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_03112b92412d` (link.bm25_judge): requirement "User can add the current request to a collection with the New request button in the collection's options." (Collections > Organizing collections > Adding requests to collections) -> element button 'Add new' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Headers", "Collections"; judged partial/partial over 2 samples, bm25 rank 5. Reference `req_0b11b11f956f`.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_3945b8531487` (link.bm25_judge): requirement "User can add the current request to a collection with the New request button in the collection's options." (Collections > Organizing collections > Adding requests to collections) -> screen modal screen "GraphQL • Hoppscotch", dialog "Save as" with controls: 'Close', 'New', 'Search', 'Save', 'Add new', 'Wiki', 'Import', 'Request name'; judged partial/partial over 2 samples, bm25 rank 2. Reference `req_0b11b11f956f`.
    - Evidence `ev_cc1ef1b5c02a` (reason.requirement): requirement req_0b11b11f956f is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_0b11b11f956f:at_risk`.
    - Requirements: `req_0b11b11f956f`.

[^23]: **What we could not determine, and who should look.** the whole PR: The PR base is 10 commit(s) behind the indexed main and 2 changed file(s) were also edited there: the diff may not apply as shown.

    - Reason code `STALE_BASE`.
    - Evidence `ev_cceeb14d61fd` (pr.meta): PR #6645 head 64ba4ff75eff base ac145e7f7581 (fork safwanyp/hoppscotch): 14 of 14 files. Reference `hoppscotch/hoppscotch#6645@64ba4ff75eff7bef868b010947f50e9b6b326540`.

[^24]: **What we could not determine, and who should look.** the shared teams environment code: The only links we found to screens are weak.

    - Reason code `NEEDS_HUMAN`.
    - Evidence `ev_56902424d433` (pr.file): modified packages/hoppscotch-common/src/components/environments/teams/Environment.vue (+8 -4) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/teams/Environment.vue`.
    - Evidence `ev_6989e5c9c227` (reason.probe): packages/hoppscotch-common/src/components/environments/teams/Environment.vue is imported by 1 file(s) (1 component); 1 of them draw a screen. Reference `packages/hoppscotch-common/src/components/environments/teams/Environment.vue`.

### How the bands are set

- Confirmed: confidence >= 0.8.
- Likely: confidence 0.5 to 0.8.
- Possible: confidence 0.25 to 0.5.
- Unknown: confidence < 0.25.

The bands are rules of thumb applied to how each link was found. They have not been validated against outcomes, so read them as an ordering, not as probabilities.

Generated by blastradius 0.1.0.
