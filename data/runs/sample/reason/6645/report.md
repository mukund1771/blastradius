# Blast radius: PR #6645 "feat(common): edit environment values from quick peek and the sidebar"

**Risk: HIGH.** Touches Home page and Menu popup on the Home page. 10 screens, 5 user flows, 48 requirements.

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

### Collections > Save requests to collections

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
| **Socket.IO page** | This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Send”. [^8] | Possible |
| **WebSocket page** | This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Send”. [^9] | Possible |

### Collections > Organizing collections

Connected through: 2 pieces of shared code (current environment value, secret environment).

| Screen | What may be affected | How sure |
|---|---|---|
| **New Collection dialog on the GraphQL page** | This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). [^10] | Possible |

## Requirements at risk

| What the spec requires | Where in the spec | Status | How sure |
|---|---|---|---|
| User can review the generated pre-request script and click Accept Change to apply it to the pre-request script section. [^11] Spec says: “After reviewing the generated script, click Accept Change to apply it to the pre-request script section of your API request.” | AI Features > Generate Pre-request Scripts | At risk | Likely |
| User can click the Modify with AI icon within the Pre-request Script section to generate a pre-request script. [^12] Spec says: “Click the Modify with AI icon ( .⟡+ ) within the Pre-request Script section.” | AI Features > Generate Pre-request Scripts | At risk | Likely |
| User can click the Modify with AI icon in the Request Body section to generate a request body. [^13] Spec says: “Click the Modify with AI icon ( .⟡+ ) in the Request Body section.” | AI Features > Generate Request Body | At risk | Likely |
| User can click the Modify with AI icon in the Tests section to generate test scripts. [^14] Spec says: “Click the Modify with AI icon ( .⟡+ ) in the Tests section.” | AI Features > Generate Test Scripts | At risk | Likely |
| User can create mock servers in Hoppscotch that simulate API endpoints with custom responses, status codes, and latency. [^15] Spec says: “Create mock servers in Hoppscotch to simulate API endpoints with custom responses, status codes, and latency for prototyping and testing.” | API Mocking | At risk | Likely |
| User can create a mock server from scratch or from an existing Collection. [^16] Spec says: “You can create a mock server from scratch or from an existing Collection.” | API Mocking > Create a mock server | At risk | Likely |
| User can get a mock server URL after creation to call from an app or Hoppscotch requests. [^17] Spec says: “Once created, you'll get a mock server URL you can call from your app or your Hoppscotch requests.” | API Mocking > Create a mock server | At risk | Likely |
| User can add fixed or ranged latency as a delay to mock responses. [^18] Spec says: “Delay: Add fixed or ranged latency (for example, 250-1500 ms)” | API Mocking > Latency, errors, and headers | At risk | Likely |
| User can access collections by clicking the Collections icon on the side panel. [^19] Spec says: “You can access the collections by clicking the "Collections" icon on the side panel.” | Collections | At risk | Likely |
| User can specify authorization, headers, and variables for nested requests and folders in collection properties. [^20] Spec says: “Specify the authorization, headers, and variables that all nested requests or folders should adhere to.” | Collections > Collection Properties | At risk | Likely |
| User can use global, environment, and request-specific variable scopes. [^21] Spec says: “Hoppscotch supports various variable scopes, including global, environment, and request-specific variables.” | Context Menu > Set as Variable | At risk | Likely |
| User can assign a selected value to an environment variable using the Set as Variable option from the context menu. [^22] Spec says: “You can use the Set as Variable option from the context menu to assign a selected value to an environment variable.” | Context Menu > Set as Variable | At risk | Likely |
| User can create global, personal, and shared environments to manage reusable variables across requests, scripts, and workspaces. [^23] Spec says: “Create global, personal, and shared environments in Hoppscotch to manage reusable variables across API requests, scripts, and workspaces.” | Environments | At risk | Likely |
| User can open the Environments icon on the sidebar to create environments. [^24] Spec says: “Click on the "Environments" icon on the sidebar to create environments.” | Environments > Creating an environment | At risk | Likely |
| User can duplicate an environment from the More menu by selecting Duplicate, creating a new environment with the suffix - Duplicate. [^25] Spec says: “From the dropdown menu, select Duplicate.” | Environments > Duplicating an environment | At risk | Likely |
| User can choose whether to sync a personal or global environment. [^26] Spec says: “However, you have the option to choose whether to sync a personal or global environment.” | Environments > Types of Variables in an Environment | At risk | Likely |
| User can create secret environment variables whose values are masked with asterisks and not synced to the server. [^27] Spec says: “All secret variable values in Hoppscotch will be masked using asterisks (\\\\\\\*).” | Environments > Types of Variables in an Environment | At risk | Likely |
| User can open the history of their requests by clicking the History icon on the side panel. [^28] Spec says: “You can access the history of your requests by clicking on the "History" icon on the side panel.” | History | At risk | Likely |
| User can delete all requests from history by clicking the Clear history button in the history section. [^29] Spec says: “You can also delete all requests from your history by clicking on the "Clear history" button in the history section.” | History > Deleting requests from history | At risk | Likely |
| User can filter favorite requests using the Filter button and the Favorites filter in the history section. [^30] Spec says: “Favorite requests can be filtered by clicking on the "Filter" button in the history section and selecting the "Favorites" filter.” | History > Favorite requests | At risk | Likely |
| User can favorite a request and unfavorite it via the Favorite button in the history entry's options. [^31] Spec says: “You can favorite a request by clicking on the "Favorite" button on the history entry's options.” | History > Favorite requests | At risk | Likely |
| User can save a request to history by clicking Send on the request page, which saves it automatically. [^32] Spec says: “To save a request to your history, you can click on the "Send" button on the request page.” | History > Save requests to history | At risk | Likely |
| User can build, test, and share APIs directly in the browser using the Hoppscotch Web App. [^33] Spec says: “Access the Hoppscotch Web App to build, test, and share APIs directly in your browser.” | Hoppscotch Web App | At risk | Likely |
| User can run Tests on the responses received. [^34] Spec says: “Hoppscotch also provides the capability to run Tests on the responses you receive.” | RESTful API Testing > Request | At risk | Likely |
| User can add dynamic behaviors to requests by specifying Headers, Request Body, Authorization Headers, Parameters, and Pre-request scripts. [^35] Spec says: “You can also add dynamic behaviors to your requests by specifying Headers, Request Body, Authorization Headers, Parameters, and Pre-request scripts.” | RESTful API Testing > Request | At risk | Likely |
| User can write pre-request scripts and post-request tests in JavaScript to add dynamic behavior, set variables, and validate responses. [^36] Spec says: “Write pre-request scripts and post-request tests in Hoppscotch using JavaScript to add dynamic behavior, set variables, and validate responses.” | Scripts | At risk | Likely |
| User can see updates to request variables persisted and reflected in the UI, while other script updates are session-specific. [^37] Spec says: “Please note that only updates to request variables get persisted and reflected in the UI while the remaining are specific to the session.” | Scripts > hopp.request Namespace > hopp.request.variables.set(key: string, value: string) | At risk | Likely |
| User can use the "Tests" tab of an API request to add code that stores response data in environment variables for use in later requests. [^38] Spec says: “In the "Tests" tab of the first API request (that returns the tokens), add the following code:” | Scripts > pw.response > Setting Environment Variables from API Responses | At risk | Likely |
| User can use the AI Assistant, an experimental chat assistant, to act on the request, response, environment, and collections they are working with. [^39] Spec says: “The AI Assistant is an experimental chat assistant that can act on the request, response, environment, and collections you're working with.” | AI Features > AI Assistant | At risk | Possible |
| User can validate the generated body and select Accept Change to populate the request body field. [^40] Spec says: “Validate the generated body and select Accept Change to populate the request body field.” | AI Features > Generate Request Body | At risk | Possible |
| User can select Accept Change to integrate the generated test into the test script editor. [^41] Spec says: “Select Accept Change to integrate it into the test script editor for execution.” | AI Features > Generate Test Scripts | At risk | Possible |
| User can click the Modify with AI icon next to the request name field to have the system automatically name a request when saving it to a collection. [^42] Spec says: “When saving an API request to a collection, click the Modify with AI icon ( .⟡+ ) next to the request name field to have the system automatically assign a name based on the request's context and properties.” | AI Features > Rename API Requests | At risk | Possible |
| User can generate API documentation from their Collections. [^43] Spec says: “Generate API documentation from your Collections” | API Documentation > What you can do | At risk | Possible |
| User can save responses as examples to populate mock routes with realistic data. [^44] Spec says: “In Hoppscotch, you can save responses as examples to quickly populate mock routes with realistic data.” | API Mocking > Save a response as an Example | At risk | Possible |
| User can click the "Save as Example" icon in the response section to save a response as an example. [^45] Spec says: “In the response section, click the "Save as Example" icon” | API Mocking > Save a response as an Example | At risk | Possible |
| User can right-click a collection or subfolder to open its properties. [^46] Spec says: “Right-click on a collection or a subfolder within a collection to open its properties.” | Authorization > Managing Authorization at the Collection Level | At risk | Possible |
| User can create a sub-collection by clicking Add new on a collection and entering a name. [^47] Spec says: “You can also create a sub-collection by clicking on the "Add new" button on the desired collection and entering the name of the sub-collection.” | Collections > Creating a new collection | At risk | Possible |
| User can organize collections by dragging and dropping them to the desired location. [^48] Spec says: “You can organize your collections by dragging and dropping them to the desired location.” | Collections > Organizing collections | At risk | Possible |
| User can create sub-collections by dragging and dropping a collection into another collection. [^49] Spec says: “You can also create sub-collections by dragging and dropping a collection into another collection.” | Collections > Organizing collections | At risk | Possible |
| User can add the current request to a collection with the New request button in the collection's options. [^50] Spec says: “To add a request to a collection, click on the "New request" button on the collection's options to save the current request to the collection.” | Collections > Organizing collections > Adding requests to collections | At risk | Possible |
| User can save a request to a new location using Save as from the drop-down next to the Save button. [^51] Spec says: “Click on the drop-down menu next to the save button and click on "Save as" to save the request to a new location.” | Collections > Save requests to collections | At risk | Possible |
| User can add a request to a collection by clicking Save on the request page and selecting the collection. [^52] Spec says: “To add a new request to your collection, you can click on the "Save" button on the request page and select the collection you want to add it to.” | Collections > Save requests to collections | At risk | Possible |
| User can see the Add to Parameters option only when using query parameters. [^53] Spec says: “Info: The "Add to Parameters" option is available only when using query parameters.” | Context Menu | At risk | Possible |
| User can open a request from history by clicking it in the history section. [^54] Spec says: “To access a request from your history, click on the desired request in the history section.” | History > Accessing requests from history | At risk | Possible |
| User can delete a request from history using the Delete button in the history entry's options. [^55] Spec says: “To delete a request from your history, click on the "Delete" button on the history entry's options.” | History > Deleting requests from history | At risk | Possible |
| User can filter history by clicking the Filter button in the history section and selecting a filter. [^56] Spec says: “You can filter your history by clicking on the "Filter" button in the history section and selecting the desired filter.” | History > Filter history | At risk | Possible |
| User can right-click a collection and select the "Run Collection" option. [^57] Spec says: “Right-click on the collection and select the "Run Collection" option.” | Runner > Running a Collection | At risk | Possible |
| User can hover over a collection name and click the "Run Collection" icon. [^58] Spec says: “Alternatively, hover over the collection name and click on the "Run Collection" icon.” | Runner > Running a Collection | At risk | Possible |

## What we could not determine, and who should look

| Item | Why we could not tell | What a person should check |
|---|---|---|
| **New in this PR; not observable in the live app yet** | This PR adds “Edit current value” and “environments editor”. The live app does not show that yet. | After this PR is deployed, open the screen that should contain “Edit current value” and “environments editor”. |
| **the whole PR** | The PR base is 10 commit(s) behind the indexed main and 2 changed file(s) were also edited there: the diff may not apply as shown. [^59] | Rebase the PR on main and run the report again before relying on the file lists; 2 file(s) were edited on both sides. |
| **the shared teams environment code** | The only links we found to screens are weak. [^60] | Decide which screens that rely on teams environment need a re-test; the list shown is not exhaustive. |
| **controls linked to the teams environment code** | Two independent checks disagreed about 3 control(s); they were left as found. | Look at these controls by hand: DELETE, More, More. |
| **6 screen(s) linked only by very weak evidence: “Collections dialog on the Home page”, “New Collection dialog on the Home page”, “New Folder dialog on the Home page” and 3 more** | The connection from the change to these screens is too weak to call an effect. | Look at these screens if time allows; nothing says they are affected. |

### What this report did not look at

- Only the signed-out surface of the web app was observed.
- One viewport size was used.
- Only English text (\`en\`) was compared.
- Frontend files only: backend, scripts and build tooling are outside this analysis.
- Cloud web edition only: desktop and self-hosted behaviour was not assessed.
- Set aside as not user-facing: 1 generated file(s), 2 test file(s).
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
    - Requirements: `req_0560dca1573e`, `req_0bbd5f0e70f2`, `req_10122ac6ce1a`, `req_18383c56dec8`, `req_18444664dc0d`, `req_1f5961c9f955`, `req_21b7a82f89ef`, `req_258609b57bf2`, `req_56b132947294`, `req_5f040c91914f`, `req_606faa4c4e13`, `req_62faf923c343`, `req_69263667dd08`, `req_704bac30b8d4`, `req_741ef7e034a6`, `req_7c0b317fd334`, `req_a352512e6ba8`, `req_b074b70d7b29`, `req_bd9364e190b4`, `req_ca957a0958ba`, `req_cedfd3417308`, `req_d0244e570cf2`, `req_de6af3bbc4f4`, `req_e0e1717f7fff`, `req_ef494b31c799`, `req_fc55fe49f1fe`.
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
    - Requirements: `req_0b11b11f956f`, `req_3d628eebc38b`, `req_4056f688470e`, `req_8f8a53fde411`, `req_9a0d7f2f4686`, `req_c956eff2f8dd`, `req_eab4e3bd569b`.

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

[^8]: **Indirect, through shared code.** Socket.IO page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Send”. Possible (0.29).

    - Claim `clm_e8bc7bcf7634`: Socket.IO page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Send”.
    - Evidence `ev_4227466c1dac` (reason.impact): indirect impact on Socket.IO page: 1 control(s), confidence 0.29, via 2 pieces of shared code (current environment value, secret environment). Reference `hoppscotch/hoppscotch#6645:indirect:/realtime/socketio|`.
    - Evidence `ev_46547990b66b` (pr.file): modified packages/hoppscotch-common/src/services/current-environment-value.service.ts (+50 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/current-environment-value.service.ts`.
    - Evidence `ev_4661408efd8d` (reason.control): button "Send" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Send:1`.
    - Evidence `ev_b78081607646` (pr.file): modified packages/hoppscotch-common/src/services/secret-environment.service.ts (+55 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Files: `packages/hoppscotch-common/src/components/aichat/Assistant.vue`, `packages/hoppscotch-common/src/services/current-environment-value.service.ts`, `packages/hoppscotch-common/src/services/secret-environment.service.ts`.

[^9]: **Indirect, through shared code.** WebSocket page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Send”. Possible (0.29).

    - Claim `clm_610680aa0133`: WebSocket page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Controls built from code that uses it: “Send”.
    - Evidence `ev_46547990b66b` (pr.file): modified packages/hoppscotch-common/src/services/current-environment-value.service.ts (+50 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/current-environment-value.service.ts`.
    - Evidence `ev_4661408efd8d` (reason.control): button "Send" in 1 file(s). Reference `hoppscotch/hoppscotch#6645:button:Send:1`.
    - Evidence `ev_b78081607646` (pr.file): modified packages/hoppscotch-common/src/services/secret-environment.service.ts (+55 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_c0fa31208f38` (reason.impact): indirect impact on WebSocket page: 1 control(s), confidence 0.29, via 2 pieces of shared code (current environment value, secret environment). Reference `hoppscotch/hoppscotch#6645:indirect:/realtime/websocket|`.
    - Files: `packages/hoppscotch-common/src/components/aichat/Assistant.vue`, `packages/hoppscotch-common/src/services/current-environment-value.service.ts`, `packages/hoppscotch-common/src/services/secret-environment.service.ts`.

[^10]: **Indirect, through shared code.** New Collection dialog on the GraphQL page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store). Possible (0.29).

    - Claim `clm_fd96e3bbe346`: New Collection dialog on the GraphQL page: This screen might be affected by this PR; worth a quick look. It uses 2 pieces of shared code (current environment value, secret environment), which this PR edits (it gained new functions, so existing users only see a difference through state the new functions store).
    - Evidence `ev_27428043bb66` (reason.impact): indirect impact on New Collection dialog on the GraphQL page: 1 control(s), confidence 0.29, via 2 pieces of shared code (current environment value, secret environment). Reference `hoppscotch/hoppscotch#6645:indirect:/graphql|New Collection`.
    - Evidence `ev_46547990b66b` (pr.file): modified packages/hoppscotch-common/src/services/current-environment-value.service.ts (+50 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/current-environment-value.service.ts`.
    - Evidence `ev_b78081607646` (pr.file): modified packages/hoppscotch-common/src/services/secret-environment.service.ts (+55 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Files: `packages/hoppscotch-common/src/components/collections/graphql/Add.vue`, `packages/hoppscotch-common/src/services/current-environment-value.service.ts`, `packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Requirements: `req_cd7435eb4b61`.

[^11]: **Requirements at risk.** AI Features > Generate Pre-request Scripts: User can review the generated pre-request script and click Accept Change to apply it to the pre-request script section. Likely.

    - Claim `clm_5c597630369b`: “User can review the generated pre-request script and click Accept Change to apply it to the pre-request script section.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_af2438fc3b6b` (reason.requirement): requirement req_18383c56dec8 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_18383c56dec8:at_risk`.
    - Requirements: `req_18383c56dec8`.

[^12]: **Requirements at risk.** AI Features > Generate Pre-request Scripts: User can click the Modify with AI icon within the Pre-request Script section to generate a pre-request script. Likely.

    - Claim `clm_bc75a497be19`: “User can click the Modify with AI icon within the Pre-request Script section to generate a pre-request script.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_d0631b7711a8` (reason.requirement): requirement req_704bac30b8d4 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_704bac30b8d4:at_risk`.
    - Requirements: `req_704bac30b8d4`.

[^13]: **Requirements at risk.** AI Features > Generate Request Body: User can click the Modify with AI icon in the Request Body section to generate a request body. Likely.

    - Claim `clm_5c6daa47f037`: “User can click the Modify with AI icon in the Request Body section to generate a request body.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0096cda55836` (link.bm25_judge): requirement "User can click the Modify with AI icon in the Request Body section to generate a request body." (AI Features > Generate Request Body) -> screen tab screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Body", "Collections" with controls: 'Post-request Script', 'Generate Code', 'Environments', 'Environment Quick Peek', 'Save', 'History', 'Headers', 'Select environment'; judged partial/partial over 2 samples, bm25 rank 7. Reference `req_bd9364e190b4`.
    - Evidence `ev_00aa58701d06` (link.bm25_judge): requirement "User can click the Modify with AI icon in the Request Body section to generate a request body." (AI Features > Generate Request Body) -> element button 'Body' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 5. Reference `req_bd9364e190b4`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_d4f8cc734e43` (reason.requirement): requirement req_bd9364e190b4 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_bd9364e190b4:at_risk`.
    - Requirements: `req_bd9364e190b4`.

[^14]: **Requirements at risk.** AI Features > Generate Test Scripts: User can click the Modify with AI icon in the Tests section to generate test scripts. Likely.

    - Claim `clm_939318ee7094`: “User can click the Modify with AI icon in the Tests section to generate test scripts.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_1473885bda21` (reason.requirement): requirement req_a352512e6ba8 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_a352512e6ba8:at_risk`.
    - Requirements: `req_a352512e6ba8`.

[^15]: **Requirements at risk.** API Mocking: User can create mock servers in Hoppscotch that simulate API endpoints with custom responses, status codes, and latency. Likely.

    - Claim `clm_fd4e4350e785`: “User can create mock servers in Hoppscotch that simulate API endpoints with custom responses, status codes, and latency.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4622fcfb1c71` (code.render): packages/hoppscotch-common/src/components/environments/index.vue renders packages/hoppscotch-common/src/components/environments/my/index.vue via EnvironmentsMy. Reference `packages/hoppscotch-common/src/components/environments/index.vue->packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_5d1ba9b64cc2` (link.bm25_judge): requirement "User can create mock servers in Hoppscotch that simulate API endpoints with custom responses, status codes, and latency." (API Mocking) -> element button 'Mock Servers' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged full/full over 2 samples, bm25 rank 3. Reference `req_adef6d2c4695`.
    - Evidence `ev_8388711ac7ae` (code.render): packages/hoppscotch-common/src/components/http/Sidebar.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/http/Sidebar.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_c7333bf70ab7` (reason.requirement): requirement req_adef6d2c4695 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_adef6d2c4695:at_risk`.
    - Requirements: `req_adef6d2c4695`.

[^16]: **Requirements at risk.** API Mocking > Create a mock server: User can create a mock server from scratch or from an existing Collection. Likely.

    - Claim `clm_358648f294a1`: “User can create a mock server from scratch or from an existing Collection.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_071c2dab7f2a` (link.bm25_judge): requirement "User can create a mock server from scratch or from an existing Collection." (API Mocking > Create a mock server) -> screen tab screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Mock Servers" with controls: 'Post-request Script', 'Description', 'Generate Code', 'Clear all', 'Environments', 'Environment Quick Peek', 'Value', 'Save'; judged partial/partial over 2 samples, bm25 rank 6. Reference `req_62faf923c343`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_ce4beb2f7e04` (reason.requirement): requirement req_62faf923c343 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_62faf923c343:at_risk`.
    - Requirements: `req_62faf923c343`.

[^17]: **Requirements at risk.** API Mocking > Create a mock server: User can get a mock server URL after creation to call from an app or Hoppscotch requests. Likely.

    - Claim `clm_eb1e2b5cf93a`: “User can get a mock server URL after creation to call from an app or Hoppscotch requests.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_12220097ee13` (code.import): packages/hoppscotch-common/src/helpers/RequestRunner.ts imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService,SecretVariable. Reference `packages/hoppscotch-common/src/helpers/RequestRunner.ts->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_d0b814726283` (reason.requirement): requirement req_69263667dd08 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_69263667dd08:at_risk`.
    - Requirements: `req_69263667dd08`.

[^18]: **Requirements at risk.** API Mocking > Latency, errors, and headers: User can add fixed or ranged latency as a delay to mock responses. Likely.

    - Claim `clm_b13271a58325`: “User can add fixed or ranged latency as a delay to mock responses.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_12220097ee13` (code.import): packages/hoppscotch-common/src/helpers/RequestRunner.ts imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService,SecretVariable. Reference `packages/hoppscotch-common/src/helpers/RequestRunner.ts->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_586093ddf015` (reason.requirement): requirement req_b074b70d7b29 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_b074b70d7b29:at_risk`.
    - Requirements: `req_b074b70d7b29`.

[^19]: **Requirements at risk.** Collections: User can access collections by clicking the Collections icon on the side panel. Likely.

    - Claim `clm_e30c966ddb0d`: “User can access collections by clicking the Collections icon on the side panel.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0b69161e348f` (link.bm25_judge): requirement "User can access collections by clicking the Collections icon on the side panel." (Collections) -> element button 'Collections' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged full/full over 2 samples, bm25 rank 1. Reference `req_eaba14acfe8b`.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4622fcfb1c71` (code.render): packages/hoppscotch-common/src/components/environments/index.vue renders packages/hoppscotch-common/src/components/environments/my/index.vue via EnvironmentsMy. Reference `packages/hoppscotch-common/src/components/environments/index.vue->packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_6c8c40c08fbb` (link.i18n_key): button "Collections" equals the English value of i18n key tab.collections; packages/hoppscotch-common/src/components/collections/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page. Reference `el_6c08185436a7`.
    - Evidence `ev_727b452be25f` (reason.requirement): requirement req_eaba14acfe8b is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_eaba14acfe8b:at_risk`.
    - Requirements: `req_eaba14acfe8b`.

[^20]: **Requirements at risk.** Collections > Collection Properties: User can specify authorization, headers, and variables for nested requests and folders in collection properties. Likely.

    - Claim `clm_dc598ff0f970`: “User can specify authorization, headers, and variables for nested requests and folders in collection properties.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_caf873b0f35f` (reason.requirement): requirement req_cedfd3417308 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_cedfd3417308:at_risk`.
    - Requirements: `req_cedfd3417308`.

[^21]: **Requirements at risk.** Context Menu > Set as Variable: User can use global, environment, and request-specific variable scopes. Likely.

    - Claim `clm_6c2ca9c20d4d`: “User can use global, environment, and request-specific variable scopes.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_4ab2d2b0860b` (code.import): packages/hoppscotch-common/src/components/environments/Selector.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/environments/Selector.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4bd3482b1703` (link.bm25_judge): requirement "User can use global, environment, and request-specific variable scopes." (Context Menu > Set as Variable) -> element generic 'Global' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Environments"; judged partial/partial over 2 samples, bm25 rank 1. Reference `req_258609b57bf2`.
    - Evidence `ev_6465f83b06fa` (code.import): packages/hoppscotch-common/src/components/environments/Selector.vue imports packages/hoppscotch-common/src/components/environments/CurrentValueInput.vue via \~/components/environments/CurrentValueInput.vue symbols=default. Reference `packages/hoppscotch-common/src/components/environments/Selector.vue->packages/hoppscotch-common/src/components/environments/CurrentValueInput.vue`.
    - Evidence `ev_7fc136d777e0` (reason.requirement): requirement req_258609b57bf2 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_258609b57bf2:at_risk`.
    - Evidence `ev_c1b5b87a5552` (link.i18n_key): generic "Global" equals the English value of i18n key environment.global; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_1d8b1db7fec9`.
    - Requirements: `req_258609b57bf2`.

[^22]: **Requirements at risk.** Context Menu > Set as Variable: User can assign a selected value to an environment variable using the Set as Variable option from the context menu. Likely.

    - Claim `clm_a59b09447f7c`: “User can assign a selected value to an environment variable using the Set as Variable option from the context menu.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_845e99baeb7f` (reason.requirement): requirement req_ef494b31c799 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_ef494b31c799:at_risk`.
    - Requirements: `req_ef494b31c799`.

[^23]: **Requirements at risk.** Environments: User can create global, personal, and shared environments to manage reusable variables across requests, scripts, and workspaces. Likely.

    - Claim `clm_8030fc6d7145`: “User can create global, personal, and shared environments to manage reusable variables across requests, scripts, and workspaces.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0af7dceeacf9` (link.i18n_key): textbox "Search" equals the English value of i18n key action.search; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 12 files with this text on the page; also uses the active tab "personal environments" (i18n key environment.my_environments). Reference `el_933ac1587ed1`.
    - Evidence `ev_15002bfa747d` (link.i18n_key): button "Workspace Environments" equals the English value of i18n key environment.team_environments; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_52c0e5ae20ec`.
    - Evidence `ev_2f88a85fa86a` (link.i18n_key): menuitem "No environment" equals the English value of i18n key environment.no_environment; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page; also uses the active tab "personal environments" (i18n key environment.my_environments). Reference `el_506913382c5a`.
    - Evidence `ev_4ab2d2b0860b` (code.import): packages/hoppscotch-common/src/components/environments/Selector.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/environments/Selector.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_b10060aa1241` (reason.requirement): requirement req_654c209d1b55 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_654c209d1b55:at_risk`.
    - Requirements: `req_654c209d1b55`.

[^24]: **Requirements at risk.** Environments > Creating an environment: User can open the Environments icon on the sidebar to create environments. Likely.

    - Claim `clm_cd9d550a658d`: “User can open the Environments icon on the sidebar to create environments.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0665d2950baf` (link.bm25_judge): requirement "User can open the Environments icon on the sidebar to create environments." (Environments > Creating an environment) -> element button 'Environments' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged full/full over 2 samples, bm25 rank 7. Reference `req_56b132947294`.
    - Evidence `ev_15002bfa747d` (link.i18n_key): button "Workspace Environments" equals the English value of i18n key environment.team_environments; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_52c0e5ae20ec`.
    - Evidence `ev_204876695976` (link.bm25_judge): requirement "User can open the Environments icon on the sidebar to create environments." (Environments > Creating an environment) -> element button 'Workspace Environments' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", dialog "menu", tabs "Personal Environments"; judged partial/partial over 2 samples, bm25 rank 4. Reference `req_56b132947294`.
    - Evidence `ev_4ab2d2b0860b` (code.import): packages/hoppscotch-common/src/components/environments/Selector.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/environments/Selector.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_7a6930876b4f` (reason.requirement): requirement req_56b132947294 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_56b132947294:at_risk`.
    - Requirements: `req_56b132947294`.

[^25]: **Requirements at risk.** Environments > Duplicating an environment: User can duplicate an environment from the More menu by selecting Duplicate, creating a new environment with the suffix - Duplicate. Likely.

    - Claim `clm_ac678a56ee18`: “User can duplicate an environment from the More menu by selecting Duplicate, creating a new environment with the suffix - Duplicate.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0af7dceeacf9` (link.i18n_key): textbox "Search" equals the English value of i18n key action.search; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 12 files with this text on the page; also uses the active tab "personal environments" (i18n key environment.my_environments). Reference `el_933ac1587ed1`.
    - Evidence `ev_15002bfa747d` (link.i18n_key): button "Workspace Environments" equals the English value of i18n key environment.team_environments; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_52c0e5ae20ec`.
    - Evidence `ev_2f88a85fa86a` (link.i18n_key): menuitem "No environment" equals the English value of i18n key environment.no_environment; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page; also uses the active tab "personal environments" (i18n key environment.my_environments). Reference `el_506913382c5a`.
    - Evidence `ev_34e916161aa4` (link.bm25_judge): requirement "User can duplicate an environment from the More menu by selecting Duplicate, creating a new environment with the suffix - Duplicate." (Environments > Duplicating an environment) -> element button 'Workspace Environments' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", dialog "menu", tabs "Personal Environments"; judged partial/partial over 2 samples, bm25 rank 3. Reference `req_6063e76c7c37`.
    - Evidence `ev_ed5cfa5d4987` (reason.requirement): requirement req_6063e76c7c37 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_6063e76c7c37:at_risk`.
    - Requirements: `req_6063e76c7c37`.

[^26]: **Requirements at risk.** Environments > Types of Variables in an Environment: User can choose whether to sync a personal or global environment. Likely.

    - Claim `clm_44076a0196f5`: “User can choose whether to sync a personal or global environment.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0af7dceeacf9` (link.i18n_key): textbox "Search" equals the English value of i18n key action.search; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 12 files with this text on the page; also uses the active tab "personal environments" (i18n key environment.my_environments). Reference `el_933ac1587ed1`.
    - Evidence `ev_15002bfa747d` (link.i18n_key): button "Workspace Environments" equals the English value of i18n key environment.team_environments; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_52c0e5ae20ec`.
    - Evidence `ev_2f88a85fa86a` (link.i18n_key): menuitem "No environment" equals the English value of i18n key environment.no_environment; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page; also uses the active tab "personal environments" (i18n key environment.my_environments). Reference `el_506913382c5a`.
    - Evidence `ev_4ab2d2b0860b` (code.import): packages/hoppscotch-common/src/components/environments/Selector.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/environments/Selector.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_7b69b2e2b89b` (reason.requirement): requirement req_0560dca1573e is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_0560dca1573e:at_risk`.
    - Requirements: `req_0560dca1573e`.

[^27]: **Requirements at risk.** Environments > Types of Variables in an Environment: User can create secret environment variables whose values are masked with asterisks and not synced to the server. Likely.

    - Claim `clm_ddb6576a1669`: “User can create secret environment variables whose values are masked with asterisks and not synced to the server.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_aff704b49dcd` (reason.requirement): requirement req_18444664dc0d is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_18444664dc0d:at_risk`.
    - Requirements: `req_18444664dc0d`.

[^28]: **Requirements at risk.** History: User can open the history of their requests by clicking the History icon on the side panel. Likely.

    - Claim `clm_a766e14f7abe`: “User can open the history of their requests by clicking the History icon on the side panel.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4622fcfb1c71` (code.render): packages/hoppscotch-common/src/components/environments/index.vue renders packages/hoppscotch-common/src/components/environments/my/index.vue via EnvironmentsMy. Reference `packages/hoppscotch-common/src/components/environments/index.vue->packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_5d0ba140861d` (link.i18n_key): button "History" equals the English value of i18n key tab.history; packages/hoppscotch-common/src/components/http/Sidebar.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page; also uses the active tab "environments" (i18n key tab.environments). Reference `el_29c4aca1b30a`.
    - Evidence `ev_7892a779b14d` (reason.requirement): requirement req_03da4ecbda2d is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_03da4ecbda2d:at_risk`.
    - Evidence `ev_8388711ac7ae` (code.render): packages/hoppscotch-common/src/components/http/Sidebar.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/http/Sidebar.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Requirements: `req_03da4ecbda2d`.

[^29]: **Requirements at risk.** History > Deleting requests from history: User can delete all requests from history by clicking the Clear history button in the history section. Likely.

    - Claim `clm_84465a4f3727`: “User can delete all requests from history by clicking the Clear history button in the history section.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_12220097ee13` (code.import): packages/hoppscotch-common/src/helpers/RequestRunner.ts imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService,SecretVariable. Reference `packages/hoppscotch-common/src/helpers/RequestRunner.ts->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_97eb43cd0a10` (reason.requirement): requirement req_de6af3bbc4f4 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_de6af3bbc4f4:at_risk`.
    - Requirements: `req_de6af3bbc4f4`.

[^30]: **Requirements at risk.** History > Favorite requests: User can filter favorite requests using the Filter button and the Favorites filter in the history section. Likely.

    - Claim `clm_057d203f3785`: “User can filter favorite requests using the Filter button and the Favorites filter in the history section.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_12220097ee13` (code.import): packages/hoppscotch-common/src/helpers/RequestRunner.ts imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService,SecretVariable. Reference `packages/hoppscotch-common/src/helpers/RequestRunner.ts->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_776992b34123` (reason.requirement): requirement req_10122ac6ce1a is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_10122ac6ce1a:at_risk`.
    - Requirements: `req_10122ac6ce1a`.

[^31]: **Requirements at risk.** History > Favorite requests: User can favorite a request and unfavorite it via the Favorite button in the history entry's options. Likely.

    - Claim `clm_1a571bc15689`: “User can favorite a request and unfavorite it via the Favorite button in the history entry's options.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_12220097ee13` (code.import): packages/hoppscotch-common/src/helpers/RequestRunner.ts imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService,SecretVariable. Reference `packages/hoppscotch-common/src/helpers/RequestRunner.ts->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_31c3083874c6` (reason.requirement): requirement req_ca957a0958ba is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_ca957a0958ba:at_risk`.
    - Requirements: `req_ca957a0958ba`.

[^32]: **Requirements at risk.** History > Save requests to history: User can save a request to history by clicking Send on the request page, which saves it automatically. Likely.

    - Claim `clm_6eb8ef70ac12`: “User can save a request to history by clicking Send on the request page, which saves it automatically.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_d9668a3b640f` (reason.requirement): requirement req_0bbd5f0e70f2 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_0bbd5f0e70f2:at_risk`.
    - Requirements: `req_0bbd5f0e70f2`.

[^33]: **Requirements at risk.** Hoppscotch Web App: User can build, test, and share APIs directly in the browser using the Hoppscotch Web App. Likely.

    - Claim `clm_75b6649b1a4a`: “User can build, test, and share APIs directly in the browser using the Hoppscotch Web App.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_12220097ee13` (code.import): packages/hoppscotch-common/src/helpers/RequestRunner.ts imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService,SecretVariable. Reference `packages/hoppscotch-common/src/helpers/RequestRunner.ts->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_7006325709f4` (reason.requirement): requirement req_fc55fe49f1fe is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_fc55fe49f1fe:at_risk`.
    - Requirements: `req_fc55fe49f1fe`.

[^34]: **Requirements at risk.** RESTful API Testing > Request: User can run Tests on the responses received. Likely.

    - Claim `clm_e40361a38648`: “User can run Tests on the responses received.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_c3d3f42bc7df` (reason.requirement): requirement req_741ef7e034a6 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_741ef7e034a6:at_risk`.
    - Requirements: `req_741ef7e034a6`.

[^35]: **Requirements at risk.** RESTful API Testing > Request: User can add dynamic behaviors to requests by specifying Headers, Request Body, Authorization Headers, Parameters, and Pre-request scripts. Likely.

    - Claim `clm_507af075bea0`: “User can add dynamic behaviors to requests by specifying Headers, Request Body, Authorization Headers, Parameters, and Pre-request scripts.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_3b8c6250c7a6` (reason.requirement): requirement req_e0e1717f7fff is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_e0e1717f7fff:at_risk`.
    - Requirements: `req_e0e1717f7fff`.

[^36]: **Requirements at risk.** Scripts: User can write pre-request scripts and post-request tests in JavaScript to add dynamic behavior, set variables, and validate responses. Likely.

    - Claim `clm_542b060124ed`: “User can write pre-request scripts and post-request tests in JavaScript to add dynamic behavior, set variables, and validate responses.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_711ebfe43873` (reason.requirement): requirement req_21b7a82f89ef is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_21b7a82f89ef:at_risk`.
    - Requirements: `req_21b7a82f89ef`.

[^37]: **Requirements at risk.** Scripts > hopp.request Namespace > hopp.request.variables.set(key: string, value: string): User can see updates to request variables persisted and reflected in the UI, while other script updates are session-specific. Likely.

    - Claim `clm_caf315f9dec6`: “User can see updates to request variables persisted and reflected in the UI, while other script updates are session-specific.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_d22ae1522f29` (reason.requirement): requirement req_7c0b317fd334 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_7c0b317fd334:at_risk`.
    - Requirements: `req_7c0b317fd334`.

[^38]: **Requirements at risk.** Scripts > pw.response > Setting Environment Variables from API Responses: User can use the "Tests" tab of an API request to add code that stores response data in environment variables for use in later requests. Likely.

    - Claim `clm_650a8530d89c`: “User can use the "Tests" tab of an API request to add code that stores response data in environment variables for use in later requests.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_78358bcc4e9d` (reason.requirement): requirement req_1f5961c9f955 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_1f5961c9f955:at_risk`.
    - Requirements: `req_1f5961c9f955`.

[^39]: **Requirements at risk.** AI Features > AI Assistant: User can use the AI Assistant, an experimental chat assistant, to act on the request, response, environment, and collections they are working with. Possible.

    - Claim `clm_d4b5e54f53e1`: “User can use the AI Assistant, an experimental chat assistant, to act on the request, response, environment, and collections they are working with.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_59b56cd87114` (link.bm25_judge): requirement "User can use the AI Assistant, an experimental chat assistant, to act on the request, response, environment, and collections they are working with." (AI Features > AI Assistant) -> element button 'Generate Request Name Using AI' on screen "GraphQL • Hoppscotch", dialog "Save as"; judged partial/partial over 2 samples, bm25 rank 2. Reference `req_eab4e3bd569b`.
    - Evidence `ev_b9519bfdca9d` (reason.requirement): requirement req_eab4e3bd569b is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_eab4e3bd569b:at_risk`.
    - Evidence `ev_d6c4401fd5c4` (link.i18n_key): button "Generate Request Name Using AI" equals the English value of i18n key ai_experiments.generate_request_name; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 3 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_d1efc39f4400`.
    - Requirements: `req_eab4e3bd569b`.

[^40]: **Requirements at risk.** AI Features > Generate Request Body: User can validate the generated body and select Accept Change to populate the request body field. Possible.

    - Claim `clm_b9fd44adf018`: “User can validate the generated body and select Accept Change to populate the request body field.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_bdf2a671d359` (reason.requirement): requirement req_5f040c91914f is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_5f040c91914f:at_risk`.
    - Requirements: `req_5f040c91914f`.

[^41]: **Requirements at risk.** AI Features > Generate Test Scripts: User can select Accept Change to integrate the generated test into the test script editor. Possible.

    - Claim `clm_f6243b000aed`: “User can select Accept Change to integrate the generated test into the test script editor.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_45cd4e24ff06` (reason.requirement): requirement req_606faa4c4e13 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_606faa4c4e13:at_risk`.
    - Requirements: `req_606faa4c4e13`.

[^42]: **Requirements at risk.** AI Features > Rename API Requests: User can click the Modify with AI icon next to the request name field to have the system automatically name a request when saving it to a collection. Possible.

    - Claim `clm_db67b5c42862`: “User can click the Modify with AI icon next to the request name field to have the system automatically name a request when saving it to a collection.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_299abfc4d334` (link.bm25_judge): requirement "User can click the Modify with AI icon next to the request name field to have the system automatically name a request when saving it to a collection." (AI Features > Rename API Requests) -> element textbox 'Request name' on screen "GraphQL • Hoppscotch", dialog "Save as"; judged partial/partial over 2 samples, bm25 rank 2. Reference `req_3d628eebc38b`.
    - Evidence `ev_2ad0ea3eb6b4` (reason.requirement): requirement req_3d628eebc38b is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_3d628eebc38b:at_risk`.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Requirements: `req_3d628eebc38b`.

[^43]: **Requirements at risk.** API Documentation > What you can do: User can generate API documentation from their Collections. Possible.

    - Claim `clm_cc997111341b`: “User can generate API documentation from their Collections”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_1ac85f66443c` (link.bm25_judge): requirement "User can generate API documentation from their Collections" (API Documentation > What you can do) -> element button 'Documentation' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Body", "Collections"; judged partial/partial over 2 samples, bm25 rank 3. Reference `req_1b544c59a825`.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4622fcfb1c71` (code.render): packages/hoppscotch-common/src/components/environments/index.vue renders packages/hoppscotch-common/src/components/environments/my/index.vue via EnvironmentsMy. Reference `packages/hoppscotch-common/src/components/environments/index.vue->packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_6c10b297f389` (link.bm25_judge): requirement "User can generate API documentation from their Collections" (API Documentation > What you can do) -> element button 'Documentation' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 4. Reference `req_1b544c59a825`.
    - Evidence `ev_d920280baf94` (reason.requirement): requirement req_1b544c59a825 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_1b544c59a825:at_risk`.
    - Requirements: `req_1b544c59a825`.

[^44]: **Requirements at risk.** API Mocking > Save a response as an Example: User can save responses as examples to populate mock routes with realistic data. Possible.

    - Claim `clm_569bd7b511a3`: “User can save responses as examples to populate mock routes with realistic data.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_13c1916d7a92` (link.bm25_judge): requirement "User can save responses as examples to populate mock routes with realistic data." (API Mocking > Save a response as an Example) -> element button 'Save' on screen "GraphQL • Hoppscotch", dialog "Save as"; judged none/partial over 2 samples, bm25 rank 9. Reference `req_9a0d7f2f4686`.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_7be7cc35d395` (reason.requirement): requirement req_9a0d7f2f4686 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_9a0d7f2f4686:at_risk`.
    - Requirements: `req_9a0d7f2f4686`.

[^45]: **Requirements at risk.** API Mocking > Save a response as an Example: User can click the "Save as Example" icon in the response section to save a response as an example. Possible.

    - Claim `clm_8e7871c87931`: “User can click the "Save as Example" icon in the response section to save a response as an example.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_015a304f02dc` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/environments/my/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 29 files with this text on the page. Reference `el_fc83f72c9069`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_032a0b42d885` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/share/Request.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_fd78f3c071c7`.
    - Evidence `ev_05a76622d917` (link.i18n_key): button "More" equals the English value of i18n key action.more; packages/hoppscotch-common/src/components/collections/graphql/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 15 files with this text on the page. Reference `el_f012d9b6c588`.
    - Evidence `ev_89e44e4bbf2b` (reason.requirement): requirement req_d0244e570cf2 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_d0244e570cf2:at_risk`.
    - Requirements: `req_d0244e570cf2`.

[^46]: **Requirements at risk.** Authorization > Managing Authorization at the Collection Level: User can right-click a collection or subfolder to open its properties. Possible.

    - Claim `clm_65fab26ea830`: “User can right-click a collection or subfolder to open its properties.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_13efb1b857bc` (reason.requirement): requirement req_e98358ca45a9 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_e98358ca45a9:at_risk`.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4622fcfb1c71` (code.render): packages/hoppscotch-common/src/components/environments/index.vue renders packages/hoppscotch-common/src/components/environments/my/index.vue via EnvironmentsMy. Reference `packages/hoppscotch-common/src/components/environments/index.vue->packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_6489faef584d` (link.bm25_judge): requirement "User can right-click a collection or subfolder to open its properties." (Authorization > Managing Authorization at the Collection Level) -> element button 'Collections' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 2. Reference `req_e98358ca45a9`.
    - Evidence `ev_6c8c40c08fbb` (link.i18n_key): button "Collections" equals the English value of i18n key tab.collections; packages/hoppscotch-common/src/components/collections/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page. Reference `el_6c08185436a7`.
    - Requirements: `req_e98358ca45a9`.

[^47]: **Requirements at risk.** Collections > Creating a new collection: User can create a sub-collection by clicking Add new on a collection and entering a name. Possible.

    - Claim `clm_6e82c1b08b39`: “User can create a sub-collection by clicking Add new on a collection and entering a name.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_08c735f98f69` (link.bm25_judge): requirement "User can create a sub-collection by clicking Add new on a collection and entering a name." (Collections > Creating a new collection) -> element button 'Add new' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Headers", "Collections"; judged full/partial over 2 samples, bm25 rank 3. Reference `req_8f8a53fde411`.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_0eec05c72539` (link.bm25_judge): requirement "User can create a sub-collection by clicking Add new on a collection and entering a name." (Collections > Creating a new collection) -> screen modal screen "GraphQL • Hoppscotch", dialog "Save as" with controls: 'Close', 'New', 'Search', 'Save', 'Add new', 'Wiki', 'Import', 'Request name'; judged partial/partial over 2 samples, bm25 rank 2. Reference `req_8f8a53fde411`.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_320bd0943de5` (reason.requirement): requirement req_8f8a53fde411 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_8f8a53fde411:at_risk`.
    - Requirements: `req_8f8a53fde411`.

[^48]: **Requirements at risk.** Collections > Organizing collections: User can organize collections by dragging and dropping them to the desired location. Possible.

    - Claim `clm_be6d3f09ba6c`: “User can organize collections by dragging and dropping them to the desired location.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4622fcfb1c71` (code.render): packages/hoppscotch-common/src/components/environments/index.vue renders packages/hoppscotch-common/src/components/environments/my/index.vue via EnvironmentsMy. Reference `packages/hoppscotch-common/src/components/environments/index.vue->packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_64a03cc42186` (link.bm25_judge): requirement "User can organize collections by dragging and dropping them to the desired location." (Collections > Organizing collections) -> element button 'Collections' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 1. Reference `req_a11de9dc8a10`.
    - Evidence `ev_6c8c40c08fbb` (link.i18n_key): button "Collections" equals the English value of i18n key tab.collections; packages/hoppscotch-common/src/components/collections/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page. Reference `el_6c08185436a7`.
    - Evidence `ev_8ea64102e24b` (reason.requirement): requirement req_a11de9dc8a10 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_a11de9dc8a10:at_risk`.
    - Requirements: `req_a11de9dc8a10`.

[^49]: **Requirements at risk.** Collections > Organizing collections: User can create sub-collections by dragging and dropping a collection into another collection. Possible.

    - Claim `clm_e4e0e4a92e44`: “User can create sub-collections by dragging and dropping a collection into another collection.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_08fa63fa260b` (link.bm25_judge): requirement "User can create sub-collections by dragging and dropping a collection into another collection." (Collections > Organizing collections) -> element button 'Collections' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 7. Reference `req_cd7435eb4b61`.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4622fcfb1c71` (code.render): packages/hoppscotch-common/src/components/environments/index.vue renders packages/hoppscotch-common/src/components/environments/my/index.vue via EnvironmentsMy. Reference `packages/hoppscotch-common/src/components/environments/index.vue->packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_483de74e5bcf` (reason.requirement): requirement req_cd7435eb4b61 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_cd7435eb4b61:at_risk`.
    - Evidence `ev_6c8c40c08fbb` (link.i18n_key): button "Collections" equals the English value of i18n key tab.collections; packages/hoppscotch-common/src/components/collections/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page. Reference `el_6c08185436a7`.
    - Requirements: `req_cd7435eb4b61`.

[^50]: **Requirements at risk.** Collections > Organizing collections > Adding requests to collections: User can add the current request to a collection with the New request button in the collection's options. Possible.

    - Claim `clm_189067864ae8`: “User can add the current request to a collection with the New request button in the collection's options.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_03112b92412d` (link.bm25_judge): requirement "User can add the current request to a collection with the New request button in the collection's options." (Collections > Organizing collections > Adding requests to collections) -> element button 'Add new' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Headers", "Collections"; judged partial/partial over 2 samples, bm25 rank 5. Reference `req_0b11b11f956f`.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_3945b8531487` (link.bm25_judge): requirement "User can add the current request to a collection with the New request button in the collection's options." (Collections > Organizing collections > Adding requests to collections) -> screen modal screen "GraphQL • Hoppscotch", dialog "Save as" with controls: 'Close', 'New', 'Search', 'Save', 'Add new', 'Wiki', 'Import', 'Request name'; judged partial/partial over 2 samples, bm25 rank 2. Reference `req_0b11b11f956f`.
    - Evidence `ev_cc1ef1b5c02a` (reason.requirement): requirement req_0b11b11f956f is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_0b11b11f956f:at_risk`.
    - Requirements: `req_0b11b11f956f`.

[^51]: **Requirements at risk.** Collections > Save requests to collections: User can save a request to a new location using Save as from the drop-down next to the Save button. Possible.

    - Claim `clm_b211a9b52abd`: “User can save a request to a new location using Save as from the drop-down next to the Save button.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_591af05efae0` (reason.requirement): requirement req_4056f688470e is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_4056f688470e:at_risk`.
    - Evidence `ev_942c520842c6` (link.i18n_key): textbox "Request name" equals the English value of i18n key request.name; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page. Reference `el_1c417015600e`.
    - Requirements: `req_4056f688470e`.

[^52]: **Requirements at risk.** Collections > Save requests to collections: User can add a request to a collection by clicking Save on the request page and selecting the collection. Possible.

    - Claim `clm_eda00fb5fba2`: “User can add a request to a collection by clicking Save on the request page and selecting the collection.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_0866ba3c44f9` (reason.requirement): requirement req_c956eff2f8dd is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_c956eff2f8dd:at_risk`.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_5c921d81c57b` (link.bm25_judge): requirement "User can add a request to a collection by clicking Save on the request page and selecting the collection." (Collections > Save requests to collections) -> element textbox 'Request name' on screen "GraphQL • Hoppscotch", dialog "Save as"; judged partial/partial over 2 samples, bm25 rank 2. Reference `req_c956eff2f8dd`.
    - Requirements: `req_c956eff2f8dd`.

[^53]: **Requirements at risk.** Context Menu: User can see the Add to Parameters option only when using query parameters. Possible.

    - Claim `clm_a6f470f4b851`: “User can see the Add to Parameters option only when using query parameters.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_186bd4b7815a` (code.import): packages/hoppscotch-common/src/newstore/environments.ts imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/newstore/environments.ts->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_1d6e2cfec24c` (link.bm25_judge): requirement "User can see the Add to Parameters option only when using query parameters." (Context Menu) -> element button 'Parameters' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 8. Reference `req_b318bc935734`.
    - Evidence `ev_38f60d369c99` (code.import): packages/hoppscotch-common/src/components/http/Headers.vue imports packages/hoppscotch-common/src/services/current-environment-value.service.ts via \~/services/current-environment-value.service symbols=CurrentValueService. Reference `packages/hoppscotch-common/src/components/http/Headers.vue->packages/hoppscotch-common/src/services/current-environment-value.service.ts`.
    - Evidence `ev_72b8d06a964e` (reason.requirement): requirement req_b318bc935734 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_b318bc935734:at_risk`.
    - Evidence `ev_95f7e2bf82c6` (code.import): packages/hoppscotch-common/src/components/http/RequestOptions.vue imports packages/hoppscotch-common/src/newstore/environments.ts via \~/newstore/environments symbols=AggregateEnvironment. Reference `packages/hoppscotch-common/src/components/http/RequestOptions.vue->packages/hoppscotch-common/src/newstore/environments.ts`.
    - Requirements: `req_b318bc935734`.

[^54]: **Requirements at risk.** History > Accessing requests from history: User can open a request from history by clicking it in the history section. Possible.

    - Claim `clm_ece221deaf55`: “User can open a request from history by clicking it in the history section.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4622fcfb1c71` (code.render): packages/hoppscotch-common/src/components/environments/index.vue renders packages/hoppscotch-common/src/components/environments/my/index.vue via EnvironmentsMy. Reference `packages/hoppscotch-common/src/components/environments/index.vue->packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_5d0ba140861d` (link.i18n_key): button "History" equals the English value of i18n key tab.history; packages/hoppscotch-common/src/components/http/Sidebar.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page; also uses the active tab "environments" (i18n key tab.environments). Reference `el_29c4aca1b30a`.
    - Evidence `ev_8388711ac7ae` (code.render): packages/hoppscotch-common/src/components/http/Sidebar.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/http/Sidebar.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_e11ddfe910da` (reason.requirement): requirement req_da8ae4b3d067 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_da8ae4b3d067:at_risk`.
    - Requirements: `req_da8ae4b3d067`.

[^55]: **Requirements at risk.** History > Deleting requests from history: User can delete a request from history using the Delete button in the history entry's options. Possible.

    - Claim `clm_9ad9a228fdfa`: “User can delete a request from history using the Delete button in the history entry's options.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_176e45432ae1` (link.bm25_judge): requirement "User can delete a request from history using the Delete button in the history entry's options." (History > Deleting requests from history) -> element button 'History' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 4. Reference `req_65597e37ad65`.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4622fcfb1c71` (code.render): packages/hoppscotch-common/src/components/environments/index.vue renders packages/hoppscotch-common/src/components/environments/my/index.vue via EnvironmentsMy. Reference `packages/hoppscotch-common/src/components/environments/index.vue->packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_5c49388a098c` (reason.requirement): requirement req_65597e37ad65 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_65597e37ad65:at_risk`.
    - Evidence `ev_5d0ba140861d` (link.i18n_key): button "History" equals the English value of i18n key tab.history; packages/hoppscotch-common/src/components/http/Sidebar.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page; also uses the active tab "environments" (i18n key tab.environments). Reference `el_29c4aca1b30a`.
    - Requirements: `req_65597e37ad65`.

[^56]: **Requirements at risk.** History > Filter history: User can filter history by clicking the Filter button in the history section and selecting a filter. Possible.

    - Claim `clm_5984e4c1ea9e`: “User can filter history by clicking the Filter button in the history section and selecting a filter.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4622fcfb1c71` (code.render): packages/hoppscotch-common/src/components/environments/index.vue renders packages/hoppscotch-common/src/components/environments/my/index.vue via EnvironmentsMy. Reference `packages/hoppscotch-common/src/components/environments/index.vue->packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_5d0ba140861d` (link.i18n_key): button "History" equals the English value of i18n key tab.history; packages/hoppscotch-common/src/components/http/Sidebar.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page; also uses the active tab "environments" (i18n key tab.environments). Reference `el_29c4aca1b30a`.
    - Evidence `ev_8388711ac7ae` (code.render): packages/hoppscotch-common/src/components/http/Sidebar.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/http/Sidebar.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_ed71087cd5ac` (reason.requirement): requirement req_c8565d9dd9a3 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_c8565d9dd9a3:at_risk`.
    - Requirements: `req_c8565d9dd9a3`.

[^57]: **Requirements at risk.** Runner > Running a Collection: User can right-click a collection and select the "Run Collection" option. Possible.

    - Claim `clm_4aa9df0fcf5b`: “User can right-click a collection and select the "Run Collection" option.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_1b6ee0829e7d` (reason.requirement): requirement req_04e46d9ed399 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_04e46d9ed399:at_risk`.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4622fcfb1c71` (code.render): packages/hoppscotch-common/src/components/environments/index.vue renders packages/hoppscotch-common/src/components/environments/my/index.vue via EnvironmentsMy. Reference `packages/hoppscotch-common/src/components/environments/index.vue->packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_6c8c40c08fbb` (link.i18n_key): button "Collections" equals the English value of i18n key tab.collections; packages/hoppscotch-common/src/components/collections/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page. Reference `el_6c08185436a7`.
    - Evidence `ev_9b73929b8a60` (link.bm25_judge): requirement "User can right-click a collection and select the "Run Collection" option." (Runner > Running a Collection) -> element button 'Collections' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 9. Reference `req_04e46d9ed399`.
    - Requirements: `req_04e46d9ed399`.

[^58]: **Requirements at risk.** Runner > Running a Collection: User can hover over a collection name and click the "Run Collection" icon. Possible.

    - Claim `clm_62d0b4814319`: “User can hover over a collection name and click the "Run Collection" icon.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_4186af1d533e` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/services/secret-environment.service.ts via \~/services/secret-environment.service symbols=SecretEnvironmentService. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Evidence `ev_4622fcfb1c71` (code.render): packages/hoppscotch-common/src/components/environments/index.vue renders packages/hoppscotch-common/src/components/environments/my/index.vue via EnvironmentsMy. Reference `packages/hoppscotch-common/src/components/environments/index.vue->packages/hoppscotch-common/src/components/environments/my/index.vue`.
    - Evidence `ev_6c8c40c08fbb` (link.i18n_key): button "Collections" equals the English value of i18n key tab.collections; packages/hoppscotch-common/src/components/collections/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page. Reference `el_6c08185436a7`.
    - Evidence `ev_9c2c1024ca45` (code.render): packages/hoppscotch-common/src/components/environments/index.vue renders packages/hoppscotch-common/src/components/environments/teams/index.vue via EnvironmentsTeams. Reference `packages/hoppscotch-common/src/components/environments/index.vue->packages/hoppscotch-common/src/components/environments/teams/index.vue`.
    - Evidence `ev_b505d314470a` (reason.requirement): requirement req_a2deaac054d3 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_a2deaac054d3:at_risk`.
    - Requirements: `req_a2deaac054d3`.

[^59]: **What we could not determine, and who should look.** the whole PR: The PR base is 10 commit(s) behind the indexed main and 2 changed file(s) were also edited there: the diff may not apply as shown.

    - Reason code `STALE_BASE`.
    - Evidence `ev_cceeb14d61fd` (pr.meta): PR #6645 head 64ba4ff75eff base ac145e7f7581 (fork safwanyp/hoppscotch): 14 of 14 files. Reference `hoppscotch/hoppscotch#6645@64ba4ff75eff7bef868b010947f50e9b6b326540`.

[^60]: **What we could not determine, and who should look.** the shared teams environment code: The only links we found to screens are weak.

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
