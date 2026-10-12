# Blast radius: PR #6658 "feat: refresh collections imported from URL"

**Risk: HIGH.** Touches Home page and GraphQL page. 9 screens, 0 user flows, 51 requirements. It also changes code shared across the app. It also changes how data is saved.

Confidence: 3 items confirmed, 1 probable and 7 possible. 4 things could not be determined and are listed below with the same weight as the findings.

The PR branch is 9 commits behind main, so its diff may not apply as shown.

## What to re-test first

- [ ] On the GraphQL page, click “Collections”. Why: the GraphQL page is changed by this PR. [^1]
- [ ] On the Home page, click “Collections”. Why: the Home page is changed by this PR. [^2]
- [ ] Export your saved data, import the export again, and check nothing is lost. Why: Saved collection data is changed by this PR. [^11]
- [ ] Open data saved by the previous version in the new build and check it still loads. Why: Saved collection data is changed by this PR. [^11]
- [ ] If you use sync or teams, sync once and check the same data appears on a second device. Why: Saved collection data is changed by this PR. [^11]
- [ ] Click through the main screens of the app as a quick smoke test, starting with “Home page” and “GraphQL page”. Why: the shared code is probably affected by this PR. [^10]

## Directly changed

These screens and elements have their own code in this PR.

How sure: Confirmed (the PR's own code changes it), Likely (reached through code it shares), Possible (a weaker connection, worth a look), Unknown (we cannot tell).

| Screen | What changed | How sure |
|---|---|---|
| **GraphQL page** | This screen is changed by this PR. Controls involved: “Collections”, “Documentation”, “More” and 1 more. [^1] | Confirmed |
| **Home page** | This screen is changed by this PR. Controls involved: “Collections”, “Documentation”, “More” and 1 more. [^2] | Confirmed |
| **Menu popup on the Home page** | This screen might be affected by this PR; worth a quick look. Controls involved: “DELETE”, “Documentation” and “Edit”. [^3] | Possible |
| **Save as dialog on the Home page** | This screen might be affected by this PR; worth a quick look. Controls involved: “More” and “Search”. [^4] | Possible |
| **Save as dialog on the GraphQL page** | This screen might be affected by this PR; worth a quick look. Controls involved: “Search”. [^5] | Possible |

## Indirect, through shared code

Screens that reuse code this PR changes, grouped by the part of the spec they belong to.

### Collections > Organizing collections

Connected through: the shared collection code.

| Screen | What may be affected | How sure |
|---|---|---|
| **New Collection dialog on the GraphQL page** | This screen might be affected by this PR; worth a quick look. It uses the shared collection code, which this PR edits (the way it is called changed). Controls built from code that uses it: “Cancel”, “Label” and “Save”. [^6] | Possible |

### Other screens

Connected through: 3 pieces of shared code (backend helpers, collection, import export steps url import); the shared collection code.

| Screen | What may be affected | How sure |
|---|---|---|
| **New Collection dialog on the Home page** | This screen might be affected by this PR; worth a quick look. It uses the shared collection code, which this PR edits (the way it is called changed). Controls built from code that uses it: “Cancel”, “Label” and “Save”. [^7] | Possible |
| **Socket.IO page** | This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (backend helpers, collection, import export steps url import), which this PR edits (its behaviour changed; only type definitions or comments changed). Controls built from code that uses it: “Import” and “Send”. [^8] | Possible |
| **WebSocket page** | This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (backend helpers, collection, import export steps url import), which this PR edits (its behaviour changed; only type definitions or comments changed). Controls built from code that uses it: “Import” and “Send”. [^9] | Possible |

## Global change

This PR changes something every screen shares, so every screen may be affected and the lists above are not exhaustive.

| Shared piece | What changed | How sure |
|---|---|---|
| **Code shared across much of the app** | This PR edits the shared package (“hoppscotch data”). Many screens are probably affected by this PR. The lists in this report are therefore not exhaustive. [^10] | Likely |

## Stored data

This PR changes how something is saved. Check it by hand.

| Saved data | What changed | How sure |
|---|---|---|
| **Saved collection data** | The saved collection data format is changed by this PR. Data saved by older versions has to keep opening in the new build. [^11] | Confirmed |

- [ ] Export your saved data, import the export again, and check nothing is lost.
- [ ] Open data saved by the previous version in the new build and check it still loads.
- [ ] If you use sync or teams, sync once and check the same data appears on a second device.

## Requirements at risk

| What the spec requires | Where in the spec | Status | How sure |
|---|---|---|---|
| User can review the generated pre-request script and click Accept Change to apply it to the pre-request script section. [^12] Spec says: “After reviewing the generated script, click Accept Change to apply it to the pre-request script section of your API request.” | AI Features > Generate Pre-request Scripts | At risk | Likely |
| User can click the Modify with AI icon within the Pre-request Script section to generate a pre-request script. [^13] Spec says: “Click the Modify with AI icon ( .⟡+ ) within the Pre-request Script section.” | AI Features > Generate Pre-request Scripts | At risk | Likely |
| User can click the Modify with AI icon in the Request Body section to generate a request body. [^14] Spec says: “Click the Modify with AI icon ( .⟡+ ) in the Request Body section.” | AI Features > Generate Request Body | At risk | Likely |
| User can click the Modify with AI icon in the Tests section to generate test scripts. [^15] Spec says: “Click the Modify with AI icon ( .⟡+ ) in the Tests section.” | AI Features > Generate Test Scripts | At risk | Likely |
| User can click the Modify with AI icon next to the request name field to have the system automatically name a request when saving it to a collection. [^16] Spec says: “When saving an API request to a collection, click the Modify with AI icon ( .⟡+ ) next to the request name field to have the system automatically assign a name based on the request's context and properties.” | AI Features > Rename API Requests | At risk | Likely |
| User can create mock servers in Hoppscotch that simulate API endpoints with custom responses, status codes, and latency. [^17] Spec says: “Create mock servers in Hoppscotch to simulate API endpoints with custom responses, status codes, and latency for prototyping and testing.” | API Mocking | At risk | Likely |
| User can specify authorization, headers, and variables for nested requests and folders in collection properties. [^18] Spec says: “Specify the authorization, headers, and variables that all nested requests or folders should adhere to.” | Collections > Collection Properties | At risk | Likely |
| User can add a request to a collection by clicking Save on the request page and selecting the collection. [^19] Spec says: “To add a new request to your collection, you can click on the "Save" button on the request page and select the collection you want to add it to.” | Collections > Save requests to collections | At risk | Likely |
| User can assign a selected value to an environment variable using the Set as Variable option from the context menu. [^20] Spec says: “You can use the Set as Variable option from the context menu to assign a selected value to an environment variable.” | Context Menu > Set as Variable | At risk | Likely |
| User can open the Environments icon on the sidebar to create environments. [^21] Spec says: “Click on the "Environments" icon on the sidebar to create environments.” | Environments > Creating an environment | At risk | Likely |
| User can create secret environment variables whose values are masked with asterisks and not synced to the server. [^22] Spec says: “All secret variable values in Hoppscotch will be masked using asterisks (\\\\\\\*).” | Environments > Types of Variables in an Environment | At risk | Likely |
| User can open the history of their requests by clicking the History icon on the side panel. [^23] Spec says: “You can access the history of your requests by clicking on the "History" icon on the side panel.” | History | At risk | Likely |
| User can delete all requests from history by clicking the Clear history button in the history section. [^24] Spec says: “You can also delete all requests from your history by clicking on the "Clear history" button in the history section.” | History > Deleting requests from history | At risk | Likely |
| User can filter favorite requests using the Filter button and the Favorites filter in the history section. [^25] Spec says: “Favorite requests can be filtered by clicking on the "Filter" button in the history section and selecting the "Favorites" filter.” | History > Favorite requests | At risk | Likely |
| User can favorite a request and unfavorite it via the Favorite button in the history entry's options. [^26] Spec says: “You can favorite a request by clicking on the "Favorite" button on the history entry's options.” | History > Favorite requests | At risk | Likely |
| User can save a request to history by clicking Send on the request page, which saves it automatically. [^27] Spec says: “To save a request to your history, you can click on the "Send" button on the request page.” | History > Save requests to history | At risk | Likely |
| User can build, test, and share APIs directly in the browser using the Hoppscotch Web App. [^28] Spec says: “Access the Hoppscotch Web App to build, test, and share APIs directly in your browser.” | Hoppscotch Web App | At risk | Likely |
| User can run Tests on the responses received. [^29] Spec says: “Hoppscotch also provides the capability to run Tests on the responses you receive.” | RESTful API Testing > Request | At risk | Likely |
| User can add dynamic behaviors to requests by specifying Headers, Request Body, Authorization Headers, Parameters, and Pre-request scripts. [^30] Spec says: “You can also add dynamic behaviors to your requests by specifying Headers, Request Body, Authorization Headers, Parameters, and Pre-request scripts.” | RESTful API Testing > Request | At risk | Likely |
| User can write pre-request scripts and post-request tests in JavaScript to add dynamic behavior, set variables, and validate responses. [^31] Spec says: “Write pre-request scripts and post-request tests in Hoppscotch using JavaScript to add dynamic behavior, set variables, and validate responses.” | Scripts | At risk | Likely |
| User can see updates to request variables persisted and reflected in the UI, while other script updates are session-specific. [^32] Spec says: “Please note that only updates to request variables get persisted and reflected in the UI while the remaining are specific to the session.” | Scripts > hopp.request Namespace > hopp.request.variables.set(key: string, value: string) | At risk | Likely |
| User can use the "Tests" tab of an API request to add code that stores response data in environment variables for use in later requests. [^33] Spec says: “In the "Tests" tab of the first API request (that returns the tokens), add the following code:” | Scripts > pw.response > Setting Environment Variables from API Responses | At risk | Likely |
| User can use the AI Assistant, an experimental chat assistant, to act on the request, response, environment, and collections they are working with. [^34] Spec says: “The AI Assistant is an experimental chat assistant that can act on the request, response, environment, and collections you're working with.” | AI Features > AI Assistant | At risk | Possible |
| User can validate the generated body and select Accept Change to populate the request body field. [^35] Spec says: “Validate the generated body and select Accept Change to populate the request body field.” | AI Features > Generate Request Body | At risk | Possible |
| User can select Accept Change to integrate the generated test into the test script editor. [^36] Spec says: “Select Accept Change to integrate it into the test script editor for execution.” | AI Features > Generate Test Scripts | At risk | Possible |
| User can generate API documentation from their Collections. [^37] Spec says: “Generate API documentation from your Collections” | API Documentation > What you can do | At risk | Possible |
| User can create a mock server from scratch or from an existing Collection. [^38] Spec says: “You can create a mock server from scratch or from an existing Collection.” | API Mocking > Create a mock server | At risk | Possible |
| User can get a mock server URL after creation to call from an app or Hoppscotch requests. [^39] Spec says: “Once created, you'll get a mock server URL you can call from your app or your Hoppscotch requests.” | API Mocking > Create a mock server | At risk | Possible |
| User can add fixed or ranged latency as a delay to mock responses. [^40] Spec says: “Delay: Add fixed or ranged latency (for example, 250-1500 ms)” | API Mocking > Latency, errors, and headers | At risk | Possible |
| User can save responses as examples to populate mock routes with realistic data. [^41] Spec says: “In Hoppscotch, you can save responses as examples to quickly populate mock routes with realistic data.” | API Mocking > Save a response as an Example | At risk | Possible |
| User can click the "Save as Example" icon in the response section to save a response as an example. [^42] Spec says: “In the response section, click the "Save as Example" icon” | API Mocking > Save a response as an Example | At risk | Possible |
| User can right-click a collection or subfolder to open its properties. [^43] Spec says: “Right-click on a collection or a subfolder within a collection to open its properties.” | Authorization > Managing Authorization at the Collection Level | At risk | Possible |
| User can specify Scope and pass the token as a Header or Query Parameter in Authorization Code grant. [^44] Spec says: “You can also specify the desired permissions in the Scope field and pass the token as a Header or as a Query Parameter.” | Authorization > OAuth 2.0 > Grant types > 1. Authorization Code | At risk | Possible |
| User can create collections and add requests to them to share with the team or use later. [^45] Spec says: “You can create collections and add requests to them to share with your team or to use later.” | Collections | At risk | Possible |
| User can access collections by clicking the Collections icon on the side panel. [^46] Spec says: “You can access the collections by clicking the "Collections" icon on the side panel.” | Collections | At risk | Possible |
| User can create a new collection by clicking Add new in the collections section and entering a name. [^47] Spec says: “To create a new collection, click on the "Add new" button on the collections section and enter the name of the collection.” | Collections > Creating a new collection | At risk | Possible |
| User can create a sub-collection by clicking Add new on a collection and entering a name. [^48] Spec says: “You can also create a sub-collection by clicking on the "Add new" button on the desired collection and entering the name of the sub-collection.” | Collections > Creating a new collection | At risk | Possible |
| User can organize collections by dragging and dropping them to the desired location. [^49] Spec says: “You can organize your collections by dragging and dropping them to the desired location.” | Collections > Organizing collections | At risk | Possible |
| User can create sub-collections by dragging and dropping a collection into another collection. [^50] Spec says: “You can also create sub-collections by dragging and dropping a collection into another collection.” | Collections > Organizing collections | At risk | Possible |
| User can add the current request to a collection with the New request button in the collection's options. [^51] Spec says: “To add a request to a collection, click on the "New request" button on the collection's options to save the current request to the collection.” | Collections > Organizing collections > Adding requests to collections | At risk | Possible |
| User can save a request to a new location using Save as from the drop-down next to the Save button. [^52] Spec says: “Click on the drop-down menu next to the save button and click on "Save as" to save the request to a new location.” | Collections > Save requests to collections | At risk | Possible |
| User can use the schema explorer to understand how data is structured. [^53] Spec says: “GraphQL uses a schema to define the structure of the data, the schema explorer helps you to understand how your data is structured.” | GraphQL API Testing > Other features > Schema | At risk | Possible |
| User can enter a server endpoint and initiate a connection in the GraphQL request section. [^54] Spec says: “The request section houses the feature to enter your server endpoint and initiate a connection.” | GraphQL API Testing > Request | At risk | Possible |
| User can add dynamic behavior to GraphQL queries by defining variables, headers, and authorization. [^55] Spec says: “You can also add dynamic behavior to your queries by defining variables, headers, and authorization.” | GraphQL API Testing > Request | At risk | Possible |
| User can use the query builder to design and run queries to fetch data once connected. [^56] Spec says: “Once the connection is made, the query builder assists you in designing queries to fetch the data that you require and run it.” | GraphQL API Testing > Request | At risk | Possible |
| User can open a request from history by clicking it in the history section. [^57] Spec says: “To access a request from your history, click on the desired request in the history section.” | History > Accessing requests from history | At risk | Possible |
| User can delete a request from history using the Delete button in the history entry's options. [^58] Spec says: “To delete a request from your history, click on the "Delete" button on the history entry's options.” | History > Deleting requests from history | At risk | Possible |
| User can filter history by clicking the Filter button in the history section and selecting a filter. [^59] Spec says: “You can filter your history by clicking on the "Filter" button in the history section and selecting the desired filter.” | History > Filter history | At risk | Possible |
| User can select from a range of HTTP methods such as GET, POST and PUT for a request. [^60] Spec says: “You can select from a range of HTTP methods such as GET, POST, PUT etc.” | RESTful API Testing > Request | At risk | Possible |
| User can right-click a collection and select the "Run Collection" option. [^61] Spec says: “Right-click on the collection and select the "Run Collection" option.” | Runner > Running a Collection | At risk | Possible |
| User can hover over a collection name and click the "Run Collection" icon. [^62] Spec says: “Alternatively, hover over the collection name and click on the "Run Collection" icon.” | Runner > Running a Collection | At risk | Possible |

## What we could not determine, and who should look

| Item | Why we could not tell | What a person should check |
|---|---|---|
| **New in this PR; not observable in the live app yet** | This PR adds “v 13”. The live app does not show that yet. | After this PR is deployed, open the screen that should contain “v 13”. |
| **the shared import export steps url import code** | The only links we found to screens are weak. [^63] | Decide which screens that rely on import export steps url import need a re-test; the list shown is not exhaustive. |
| **the collections import code** | No control we observed is built from this file or from code that uses it within two steps (it may be backend-only, internal, or on a screen the crawl did not reach). [^64] | Find where collections import appears in the product and try it by hand; this change is not tied to any screen we observed. |
| **8 screen(s) linked only by very weak evidence: “Collections dialog on the Home page”, “New Folder dialog on the Home page”, “Support dialog on the Home page” and 5 more** | The connection from the change to these screens is too weak to call an effect. | Look at these screens if time allows; nothing says they are affected. |

### What this report did not look at

- Only the signed-out surface of the web app was observed.
- One viewport size was used.
- Only English text (\`en\`) was compared.
- Frontend files only: backend, scripts and build tooling are outside this analysis.
- Cloud web edition only: desktop and self-hosted behaviour was not assessed.
- Other programs that read this saved data (a command-line tool, the backend, the desktop app) were not assessed.
- The crawl stopped at its action budget, so some screens were not observed.
- The product documentation may be older than the app, so some requirements may be out of date.
- Some source files could not be read completely.

## Appendix: evidence

Everything above cites the numbered entries below. File paths, ids and commit hashes appear only here.

### Snapshot

- Target: `hoppscotch`
- Repository: `hoppscotch/hoppscotch`
- Indexed commit: `63273f8e599e73f0fb38c0739314e8d46b6bc83d`
- PR base: `d86e59f6e9574c69f01b300691b9f4396eeb38d2`
- PR head: `735a6a0806f90e013339ea76d08d0b6cb2d9c62c`
- PR base behind main: 9 commits
- Crawl bundle: `c81a0323d1f5b4f5`
- Crawl size: 150 actions and 55 screens
- Documentation version: 2026\.5.0
- App version: 2026\.9.0

### Evidence behind each statement

[^1]: **Directly changed.** GraphQL page: This screen is changed by this PR. Controls involved: “Collections”, “Documentation”, “More” and 1 more. Confirmed (0.99).

    - Claim `clm_e76b3e2cf83b`: GraphQL page: This screen is changed by this PR. Controls involved: “Collections”, “Documentation”, “More” and 1 more.
    - Evidence `ev_0e50cd0b9e70` (reason.impact): direct impact on GraphQL page: 28 control(s), confidence 0.99. Reference `hoppscotch/hoppscotch#6658:direct:/graphql|`.
    - Evidence `ev_18326c530d81` (pr.file): modified packages/hoppscotch-common/src/components/collections/Collection.vue (+8 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/components/collections/Collection.vue`.
    - Evidence `ev_50b2d504420b` (reason.control): button "Collections" in 1 file(s). Reference `hoppscotch/hoppscotch#6658:button:Collections:1`.
    - Evidence `ev_98d4fcdbb5cd` (reason.control): searchbox "Search" in 4 file(s). Reference `hoppscotch/hoppscotch#6658:searchbox:Search:4`.
    - Evidence `ev_9a7ea37bb08c` (reason.control): button "More" in 1 file(s). Reference `hoppscotch/hoppscotch#6658:button:More:1`.
    - Evidence `ev_a24d5f263cf4` (pr.file): modified packages/hoppscotch-common/src/components/collections/index.vue (+66 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_c265b360c673` (reason.control): button "Documentation" in 3 file(s). Reference `hoppscotch/hoppscotch#6658:button:Documentation:3`.
    - Files: `packages/hoppscotch-common/src/components/collections/Collection.vue`, `packages/hoppscotch-common/src/components/collections/index.vue`.
    - Requirements: `req_21df567193b7`, `req_262a77629ec0`, `req_68820fea04b5`, `req_748eb8f9dbf9`.

[^2]: **Directly changed.** Home page: This screen is changed by this PR. Controls involved: “Collections”, “Documentation”, “More” and 1 more. Confirmed (0.99).

    - Claim `clm_f18d2546a2a8`: Home page: This screen is changed by this PR. Controls involved: “Collections”, “Documentation”, “More” and 1 more.
    - Evidence `ev_1227401b5e7c` (reason.control): searchbox "Search" in 2 file(s). Reference `hoppscotch/hoppscotch#6658:searchbox:Search:2`.
    - Evidence `ev_18326c530d81` (pr.file): modified packages/hoppscotch-common/src/components/collections/Collection.vue (+8 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/components/collections/Collection.vue`.
    - Evidence `ev_4aee47b0094b` (reason.impact): direct impact on Home page: 39 control(s), confidence 0.99. Reference `hoppscotch/hoppscotch#6658:direct:/|`.
    - Evidence `ev_50b2d504420b` (reason.control): button "Collections" in 1 file(s). Reference `hoppscotch/hoppscotch#6658:button:Collections:1`.
    - Evidence `ev_9a7ea37bb08c` (reason.control): button "More" in 1 file(s). Reference `hoppscotch/hoppscotch#6658:button:More:1`.
    - Evidence `ev_a24d5f263cf4` (pr.file): modified packages/hoppscotch-common/src/components/collections/index.vue (+66 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_c265b360c673` (reason.control): button "Documentation" in 3 file(s). Reference `hoppscotch/hoppscotch#6658:button:Documentation:3`.
    - Files: `packages/hoppscotch-common/src/components/collections/Collection.vue`, `packages/hoppscotch-common/src/components/collections/index.vue`.
    - Requirements: `req_04e46d9ed399`, `req_0bbd5f0e70f2`, `req_10122ac6ce1a`, `req_18383c56dec8`, `req_18444664dc0d`, `req_1b544c59a825`, `req_1f5961c9f955`, `req_21b7a82f89ef`, `req_5f040c91914f`, `req_606faa4c4e13`, `req_62faf923c343`, `req_69263667dd08`, `req_704bac30b8d4`, `req_741ef7e034a6`, `req_7c0b317fd334`, `req_a11de9dc8a10`, `req_a2deaac054d3`, `req_a352512e6ba8`, `req_b074b70d7b29`, `req_bd9364e190b4`, `req_ca957a0958ba`, `req_cd7435eb4b61`, `req_cedfd3417308`, `req_d0244e570cf2`, `req_de6af3bbc4f4`, `req_e0e1717f7fff`, `req_e98358ca45a9`, `req_eaba14acfe8b`, `req_ef494b31c799`, `req_fc55fe49f1fe`.

[^3]: **Directly changed.** Menu popup on the Home page: This screen might be affected by this PR; worth a quick look. Controls involved: “DELETE”, “Documentation” and “Edit”. Possible (0.50).

    - Claim `clm_401fbfe0aa43`: Menu popup on the Home page: This screen might be affected by this PR; worth a quick look. Controls involved: “DELETE”, “Documentation” and “Edit”.
    - Evidence `ev_00c64f0c9d03` (reason.control): menuitem "Documentation" in 3 file(s). Reference `hoppscotch/hoppscotch#6658:menuitem:Documentation:3`.
    - Evidence `ev_0f358e0dc7e7` (reason.impact): direct impact on Menu popup on the Home page: 4 control(s), confidence 0.50. Reference `hoppscotch/hoppscotch#6658:direct:/|menu`.
    - Evidence `ev_18326c530d81` (pr.file): modified packages/hoppscotch-common/src/components/collections/Collection.vue (+8 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/components/collections/Collection.vue`.
    - Evidence `ev_37d10d358b20` (reason.control): menuitem "DELETE" in 1 file(s). Reference `hoppscotch/hoppscotch#6658:menuitem:DELETE:1`.
    - Evidence `ev_46ca8f5590a1` (reason.control): button "Edit" in 1 file(s). Reference `hoppscotch/hoppscotch#6658:button:Edit:1`.
    - Files: `packages/hoppscotch-common/src/components/collections/Collection.vue`.
    - Requirements: `req_1b544c59a825`, `req_bcfa3ab48380`.

[^4]: **Directly changed.** Save as dialog on the Home page: This screen might be affected by this PR; worth a quick look. Controls involved: “More” and “Search”. Possible (0.50).

    - Claim `clm_fcd6ac19108b`: Save as dialog on the Home page: This screen might be affected by this PR; worth a quick look. Controls involved: “More” and “Search”.
    - Evidence `ev_18326c530d81` (pr.file): modified packages/hoppscotch-common/src/components/collections/Collection.vue (+8 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/components/collections/Collection.vue`.
    - Evidence `ev_4c3c3d27ef27` (reason.impact): direct impact on Save as dialog on the Home page: 3 control(s), confidence 0.50. Reference `hoppscotch/hoppscotch#6658:direct:/|Save as`.
    - Evidence `ev_98d4fcdbb5cd` (reason.control): searchbox "Search" in 4 file(s). Reference `hoppscotch/hoppscotch#6658:searchbox:Search:4`.
    - Evidence `ev_9a7ea37bb08c` (reason.control): button "More" in 1 file(s). Reference `hoppscotch/hoppscotch#6658:button:More:1`.
    - Evidence `ev_a24d5f263cf4` (pr.file): modified packages/hoppscotch-common/src/components/collections/index.vue (+66 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/components/collections/index.vue`.
    - Files: `packages/hoppscotch-common/src/components/collections/Collection.vue`, `packages/hoppscotch-common/src/components/collections/index.vue`.

[^5]: **Directly changed.** Save as dialog on the GraphQL page: This screen might be affected by this PR; worth a quick look. Controls involved: “Search”. Possible (0.30).

    - Claim `clm_29f4de249aa3`: Save as dialog on the GraphQL page: This screen might be affected by this PR; worth a quick look. Controls involved: “Search”.
    - Evidence `ev_52603663e867` (reason.impact): direct impact on Save as dialog on the GraphQL page: 1 control(s), confidence 0.30. Reference `hoppscotch/hoppscotch#6658:direct:/graphql|Save as`.
    - Evidence `ev_98d4fcdbb5cd` (reason.control): searchbox "Search" in 4 file(s). Reference `hoppscotch/hoppscotch#6658:searchbox:Search:4`.
    - Evidence `ev_a24d5f263cf4` (pr.file): modified packages/hoppscotch-common/src/components/collections/index.vue (+66 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/components/collections/index.vue`.
    - Files: `packages/hoppscotch-common/src/components/collections/index.vue`.
    - Requirements: `req_0b11b11f956f`, `req_3d628eebc38b`, `req_4056f688470e`, `req_8f8a53fde411`, `req_c956eff2f8dd`.

[^6]: **Indirect, through shared code.** New Collection dialog on the GraphQL page: This screen might be affected by this PR; worth a quick look. It uses the shared collection code, which this PR edits (the way it is called changed). Controls built from code that uses it: “Cancel”, “Label” and “Save”. Possible (0.50).

    - Claim `clm_359ec13dd5db`: New Collection dialog on the GraphQL page: This screen might be affected by this PR; worth a quick look. It uses the shared collection code, which this PR edits (the way it is called changed). Controls built from code that uses it: “Cancel”, “Label” and “Save”.
    - Evidence `ev_5032144afd85` (reason.control): button "Cancel" in 2 file(s). Reference `hoppscotch/hoppscotch#6658:button:Cancel:2`.
    - Evidence `ev_6f63e6277e57` (reason.control): button "Save" in 4 file(s). Reference `hoppscotch/hoppscotch#6658:button:Save:4`.
    - Evidence `ev_7a37d43a39f6` (reason.impact): indirect impact on New Collection dialog on the GraphQL page: 3 control(s), confidence 0.50, via the shared collection code. Reference `hoppscotch/hoppscotch#6658:indirect:/graphql|New Collection`.
    - Evidence `ev_a41849476c42` (reason.control): textbox "Label" in 2 file(s). Reference `hoppscotch/hoppscotch#6658:textbox:Label:2`.
    - Evidence `ev_e55fdf0376ea` (pr.file): modified packages/hoppscotch-data/src/collection/index.ts (+4 -2) class data_schema kind SIGNATURE. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-data/src/collection/index.ts`.
    - Files: `packages/hoppscotch-common/src/components/collections/graphql/Add.vue`, `packages/hoppscotch-data/src/collection/index.ts`.
    - Requirements: `req_cd7435eb4b61`.

[^7]: **Indirect, through shared code.** New Collection dialog on the Home page: This screen might be affected by this PR; worth a quick look. It uses the shared collection code, which this PR edits (the way it is called changed). Controls built from code that uses it: “Cancel”, “Label” and “Save”. Possible (0.50).

    - Claim `clm_96c99deb2fe2`: New Collection dialog on the Home page: This screen might be affected by this PR; worth a quick look. It uses the shared collection code, which this PR edits (the way it is called changed). Controls built from code that uses it: “Cancel”, “Label” and “Save”.
    - Evidence `ev_5032144afd85` (reason.control): button "Cancel" in 2 file(s). Reference `hoppscotch/hoppscotch#6658:button:Cancel:2`.
    - Evidence `ev_6f63e6277e57` (reason.control): button "Save" in 4 file(s). Reference `hoppscotch/hoppscotch#6658:button:Save:4`.
    - Evidence `ev_a41849476c42` (reason.control): textbox "Label" in 2 file(s). Reference `hoppscotch/hoppscotch#6658:textbox:Label:2`.
    - Evidence `ev_e55fdf0376ea` (pr.file): modified packages/hoppscotch-data/src/collection/index.ts (+4 -2) class data_schema kind SIGNATURE. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-data/src/collection/index.ts`.
    - Evidence `ev_f1c58675259e` (reason.impact): indirect impact on New Collection dialog on the Home page: 3 control(s), confidence 0.50, via the shared collection code. Reference `hoppscotch/hoppscotch#6658:indirect:/|New Collection`.
    - Files: `packages/hoppscotch-common/src/components/collections/graphql/Add.vue`, `packages/hoppscotch-data/src/collection/index.ts`.

[^8]: **Indirect, through shared code.** Socket.IO page: This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (backend helpers, collection, import export steps url import), which this PR edits (its behaviour changed; only type definitions or comments changed). Controls built from code that uses it: “Import” and “Send”. Possible (0.50).

    - Claim `clm_c1cc693301f8`: Socket.IO page: This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (backend helpers, collection, import export steps url import), which this PR edits (its behaviour changed; only type definitions or comments changed). Controls built from code that uses it: “Import” and “Send”.
    - Evidence `ev_3aea58ea3012` (reason.control): button "Import" in 1 file(s). Reference `hoppscotch/hoppscotch#6658:button:Import:1`.
    - Evidence `ev_48ee70ad4e31` (pr.file): modified packages/hoppscotch-common/src/helpers/collection/collection.ts (+1 -0) class util kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/helpers/collection/collection.ts`.
    - Evidence `ev_50b6f972e034` (reason.control): button "Send" in 1 file(s). Reference `hoppscotch/hoppscotch#6658:button:Send:1`.
    - Evidence `ev_c2313f3347d7` (pr.file): modified packages/hoppscotch-common/src/components/importExport/ImportExportSteps/UrlImport.vue (+3 -3) class component kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/components/importExport/ImportExportSteps/UrlImport.vue`.
    - Evidence `ev_d3796c844db6` (reason.impact): indirect impact on Socket.IO page: 2 control(s), confidence 0.50, via 3 pieces of shared code (backend helpers, collection, import export steps url import). Reference `hoppscotch/hoppscotch#6658:indirect:/realtime/socketio|`.
    - Evidence `ev_e9ddbf1363ef` (pr.file): modified packages/hoppscotch-common/src/helpers/backend/helpers.ts (+1 -0) class util kind TYPE_ONLY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Files: `packages/hoppscotch-common/src/components/aichat/Assistant.vue`, `packages/hoppscotch-common/src/components/importExport/ImportExportSteps/UrlImport.vue`, `packages/hoppscotch-common/src/components/realtime/Communication.vue`, `packages/hoppscotch-common/src/helpers/backend/helpers.ts`, `packages/hoppscotch-common/src/helpers/collection/collection.ts`.

[^9]: **Indirect, through shared code.** WebSocket page: This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (backend helpers, collection, import export steps url import), which this PR edits (its behaviour changed; only type definitions or comments changed). Controls built from code that uses it: “Import” and “Send”. Possible (0.50).

    - Claim `clm_06dbd7be5f7a`: WebSocket page: This screen might be affected by this PR; worth a quick look. It uses 3 pieces of shared code (backend helpers, collection, import export steps url import), which this PR edits (its behaviour changed; only type definitions or comments changed). Controls built from code that uses it: “Import” and “Send”.
    - Evidence `ev_3aea58ea3012` (reason.control): button "Import" in 1 file(s). Reference `hoppscotch/hoppscotch#6658:button:Import:1`.
    - Evidence `ev_48ee70ad4e31` (pr.file): modified packages/hoppscotch-common/src/helpers/collection/collection.ts (+1 -0) class util kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/helpers/collection/collection.ts`.
    - Evidence `ev_50b6f972e034` (reason.control): button "Send" in 1 file(s). Reference `hoppscotch/hoppscotch#6658:button:Send:1`.
    - Evidence `ev_9027495af54f` (reason.impact): indirect impact on WebSocket page: 2 control(s), confidence 0.50, via 3 pieces of shared code (backend helpers, collection, import export steps url import). Reference `hoppscotch/hoppscotch#6658:indirect:/realtime/websocket|`.
    - Evidence `ev_c2313f3347d7` (pr.file): modified packages/hoppscotch-common/src/components/importExport/ImportExportSteps/UrlImport.vue (+3 -3) class component kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/components/importExport/ImportExportSteps/UrlImport.vue`.
    - Evidence `ev_e9ddbf1363ef` (pr.file): modified packages/hoppscotch-common/src/helpers/backend/helpers.ts (+1 -0) class util kind TYPE_ONLY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Files: `packages/hoppscotch-common/src/components/aichat/Assistant.vue`, `packages/hoppscotch-common/src/components/importExport/ImportExportSteps/UrlImport.vue`, `packages/hoppscotch-common/src/components/realtime/Communication.vue`, `packages/hoppscotch-common/src/helpers/backend/helpers.ts`, `packages/hoppscotch-common/src/helpers/collection/collection.ts`.

[^10]: **Global change.** Code shared across much of the app: This PR edits the shared package (“hoppscotch data”). Many screens are probably affected by this PR. The lists in this report are therefore not exhaustive. Likely (0.58).

    - Claim `clm_b511d9a81bca`: Code shared across much of the app: This PR edits the shared package (“hoppscotch data”). Many screens are probably affected by this PR. The lists in this report are therefore not exhaustive.
    - Evidence `ev_964178b58f37` (reason.impact): global impact on Code shared across much of the app: 2 control(s), confidence 0.58, via shared package. Reference `hoppscotch/hoppscotch#6658:global:hub`.
    - Evidence `ev_984107d9bc63` (pr.file): added packages/hoppscotch-data/src/collection/v/13.ts (+51 -0) class data_schema kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-data/src/collection/v/13.ts`.
    - Evidence `ev_e55fdf0376ea` (pr.file): modified packages/hoppscotch-data/src/collection/index.ts (+4 -2) class data_schema kind SIGNATURE. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-data/src/collection/index.ts`.
    - Files: `packages/hoppscotch-data/src/collection/index.ts`, `packages/hoppscotch-data/src/collection/v/13.ts`.

[^11]: **Stored data.** Saved collection data: The saved collection data format is changed by this PR. Data saved by older versions has to keep opening in the new build. Confirmed (0.85).

    - Claim `clm_dcc56a87d560`: Saved collection data: The saved collection data format is changed by this PR. Data saved by older versions has to keep opening in the new build.
    - Evidence `ev_984107d9bc63` (pr.file): added packages/hoppscotch-data/src/collection/v/13.ts (+51 -0) class data_schema kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-data/src/collection/v/13.ts`.
    - Evidence `ev_cb0b1906da39` (reason.impact): stored_data impact on Saved collection data: 2 control(s), confidence 0.85. Reference `hoppscotch/hoppscotch#6658:stored_data:collection`.
    - Evidence `ev_e55fdf0376ea` (pr.file): modified packages/hoppscotch-data/src/collection/index.ts (+4 -2) class data_schema kind SIGNATURE. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-data/src/collection/index.ts`.
    - Files: `packages/hoppscotch-data/src/collection/index.ts`, `packages/hoppscotch-data/src/collection/v/13.ts`.

[^12]: **Requirements at risk.** AI Features > Generate Pre-request Scripts: User can review the generated pre-request script and click Accept Change to apply it to the pre-request script section. Likely.

    - Claim `clm_03bc32d3c684`: “User can review the generated pre-request script and click Accept Change to apply it to the pre-request script section.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_9f9fbae395d7` (reason.requirement): requirement req_18383c56dec8 is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_18383c56dec8:at_risk`.
    - Requirements: `req_18383c56dec8`.

[^13]: **Requirements at risk.** AI Features > Generate Pre-request Scripts: User can click the Modify with AI icon within the Pre-request Script section to generate a pre-request script. Likely.

    - Claim `clm_249079111188`: “User can click the Modify with AI icon within the Pre-request Script section to generate a pre-request script.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_fd545ce4091a` (reason.requirement): requirement req_704bac30b8d4 is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_704bac30b8d4:at_risk`.
    - Requirements: `req_704bac30b8d4`.

[^14]: **Requirements at risk.** AI Features > Generate Request Body: User can click the Modify with AI icon in the Request Body section to generate a request body. Likely.

    - Claim `clm_8ab840eb563e`: “User can click the Modify with AI icon in the Request Body section to generate a request body.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0096cda55836` (link.bm25_judge): requirement "User can click the Modify with AI icon in the Request Body section to generate a request body." (AI Features > Generate Request Body) -> screen tab screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Body", "Collections" with controls: 'Post-request Script', 'Generate Code', 'Environments', 'Environment Quick Peek', 'Save', 'History', 'Headers', 'Select environment'; judged partial/partial over 2 samples, bm25 rank 7. Reference `req_bd9364e190b4`.
    - Evidence `ev_00aa58701d06` (link.bm25_judge): requirement "User can click the Modify with AI icon in the Request Body section to generate a request body." (AI Features > Generate Request Body) -> element button 'Body' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 5. Reference `req_bd9364e190b4`.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_e52eac6237a7` (reason.requirement): requirement req_bd9364e190b4 is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_bd9364e190b4:at_risk`.
    - Requirements: `req_bd9364e190b4`.

[^15]: **Requirements at risk.** AI Features > Generate Test Scripts: User can click the Modify with AI icon in the Tests section to generate test scripts. Likely.

    - Claim `clm_152772a31f08`: “User can click the Modify with AI icon in the Tests section to generate test scripts.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_cd5d409dc1ec` (reason.requirement): requirement req_a352512e6ba8 is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_a352512e6ba8:at_risk`.
    - Requirements: `req_a352512e6ba8`.

[^16]: **Requirements at risk.** AI Features > Rename API Requests: User can click the Modify with AI icon next to the request name field to have the system automatically name a request when saving it to a collection. Likely.

    - Claim `clm_541a0bac341f`: “User can click the Modify with AI icon next to the request name field to have the system automatically name a request when saving it to a collection.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_01551b8ceb64` (link.i18n_key): searchbox "Search" equals the English value of i18n key action.search; packages/hoppscotch-common/src/components/collections/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 8 files with this text on the page. Reference `el_3ae1882436cb`.
    - Evidence `ev_0bebfe905396` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/collections/MyCollections.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 15 files with this text on the page. Reference `el_8b0bc6ca7913`.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_0ce524ab2e4d` (link.i18n_key): button "New" equals the English value of i18n key action.new; packages/hoppscotch-common/src/components/collections/MyCollections.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 7 files with this text on the page. Reference `el_caf41bed8b1c`.
    - Evidence `ev_e991e83068e0` (reason.requirement): requirement req_3d628eebc38b is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_3d628eebc38b:at_risk`.
    - Requirements: `req_3d628eebc38b`.

[^17]: **Requirements at risk.** API Mocking: User can create mock servers in Hoppscotch that simulate API endpoints with custom responses, status codes, and latency. Likely.

    - Claim `clm_5bc7fe5d73d8`: “User can create mock servers in Hoppscotch that simulate API endpoints with custom responses, status codes, and latency.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_4230353c36d0` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via \~/helpers/backend/helpers symbols=CollectionDataProps,getTeamCollectionObject. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Evidence `ev_5d1ba9b64cc2` (link.bm25_judge): requirement "User can create mock servers in Hoppscotch that simulate API endpoints with custom responses, status codes, and latency." (API Mocking) -> element button 'Mock Servers' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged full/full over 2 samples, bm25 rank 3. Reference `req_adef6d2c4695`.
    - Evidence `ev_8388711ac7ae` (code.render): packages/hoppscotch-common/src/components/http/Sidebar.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/http/Sidebar.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_c4c0f896920d` (reason.requirement): requirement req_adef6d2c4695 is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_adef6d2c4695:at_risk`.
    - Requirements: `req_adef6d2c4695`.

[^18]: **Requirements at risk.** Collections > Collection Properties: User can specify authorization, headers, and variables for nested requests and folders in collection properties. Likely.

    - Claim `clm_3d655bc48875`: “User can specify authorization, headers, and variables for nested requests and folders in collection properties.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_28b609d4920b` (reason.requirement): requirement req_cedfd3417308 is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_cedfd3417308:at_risk`.
    - Requirements: `req_cedfd3417308`.

[^19]: **Requirements at risk.** Collections > Save requests to collections: User can add a request to a collection by clicking Save on the request page and selecting the collection. Likely.

    - Claim `clm_b2ef02b4efe5`: “User can add a request to a collection by clicking Save on the request page and selecting the collection.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_01551b8ceb64` (link.i18n_key): searchbox "Search" equals the English value of i18n key action.search; packages/hoppscotch-common/src/components/collections/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 8 files with this text on the page. Reference `el_3ae1882436cb`.
    - Evidence `ev_0bebfe905396` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/collections/MyCollections.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 15 files with this text on the page. Reference `el_8b0bc6ca7913`.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_0ce524ab2e4d` (link.i18n_key): button "New" equals the English value of i18n key action.new; packages/hoppscotch-common/src/components/collections/MyCollections.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 7 files with this text on the page. Reference `el_caf41bed8b1c`.
    - Evidence `ev_5f305d53b8ca` (reason.requirement): requirement req_c956eff2f8dd is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_c956eff2f8dd:at_risk`.
    - Requirements: `req_c956eff2f8dd`.

[^20]: **Requirements at risk.** Context Menu > Set as Variable: User can assign a selected value to an environment variable using the Set as Variable option from the context menu. Likely.

    - Claim `clm_069475a7c0a1`: “User can assign a selected value to an environment variable using the Set as Variable option from the context menu.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_73cd28f9c73f` (reason.requirement): requirement req_ef494b31c799 is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_ef494b31c799:at_risk`.
    - Requirements: `req_ef494b31c799`.

[^21]: **Requirements at risk.** Environments > Creating an environment: User can open the Environments icon on the sidebar to create environments. Likely.

    - Claim `clm_ca315b13f722`: “User can open the Environments icon on the sidebar to create environments.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0665d2950baf` (link.bm25_judge): requirement "User can open the Environments icon on the sidebar to create environments." (Environments > Creating an environment) -> element button 'Environments' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged full/full over 2 samples, bm25 rank 7. Reference `req_56b132947294`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_15002bfa747d` (link.i18n_key): button "Workspace Environments" equals the English value of i18n key environment.team_environments; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_52c0e5ae20ec`.
    - Evidence `ev_204876695976` (link.bm25_judge): requirement "User can open the Environments icon on the sidebar to create environments." (Environments > Creating an environment) -> element button 'Workspace Environments' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", dialog "menu", tabs "Personal Environments"; judged partial/partial over 2 samples, bm25 rank 4. Reference `req_56b132947294`.
    - Evidence `ev_9cd03b802145` (reason.requirement): requirement req_56b132947294 is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_56b132947294:at_risk`.
    - Requirements: `req_56b132947294`.

[^22]: **Requirements at risk.** Environments > Types of Variables in an Environment: User can create secret environment variables whose values are masked with asterisks and not synced to the server. Likely.

    - Claim `clm_d856bc64cf84`: “User can create secret environment variables whose values are masked with asterisks and not synced to the server.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0b1a25f94c56` (link.bm25_judge): requirement "User can create secret environment variables whose values are masked with asterisks and not synced to the server." (Environments > Types of Variables in an Environment) -> screen tab screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Pre-request Script", "Collections" with controls: 'Post-request Script', 'Generate Code', 'Environments', 'Environment Quick Peek', 'Environment: Set random number variable', 'Save', 'History', 'Headers'; judged. Reference `req_18444664dc0d`.
    - Evidence `ev_611711237105` (reason.requirement): requirement req_18444664dc0d is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_18444664dc0d:at_risk`.
    - Requirements: `req_18444664dc0d`.

[^23]: **Requirements at risk.** History: User can open the history of their requests by clicking the History icon on the side panel. Likely.

    - Claim `clm_a4f649c663a5`: “User can open the history of their requests by clicking the History icon on the side panel.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_2a8ad396e9d0` (reason.requirement): requirement req_03da4ecbda2d is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_03da4ecbda2d:at_risk`.
    - Evidence `ev_33728ae0be73` (code.import): packages/hoppscotch-common/src/components/history/Personal.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppGQLRequest,HoppRESTRequest,makeGQLRequest. Reference `packages/hoppscotch-common/src/components/history/Personal.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_3c046c31691e` (link.i18n_key): button "Filter" equals the English value of i18n key action.filter; packages/hoppscotch-common/src/components/history/Personal.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_fae25a745b95`.
    - Evidence `ev_4230353c36d0` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via \~/helpers/backend/helpers symbols=CollectionDataProps,getTeamCollectionObject. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Requirements: `req_03da4ecbda2d`.

[^24]: **Requirements at risk.** History > Deleting requests from history: User can delete all requests from history by clicking the Clear history button in the history section. Likely.

    - Claim `clm_363a5c794a78`: “User can delete all requests from history by clicking the Clear history button in the history section.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_8bbf9c99d6ec` (reason.requirement): requirement req_de6af3bbc4f4 is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_de6af3bbc4f4:at_risk`.
    - Requirements: `req_de6af3bbc4f4`.

[^25]: **Requirements at risk.** History > Favorite requests: User can filter favorite requests using the Filter button and the Favorites filter in the history section. Likely.

    - Claim `clm_51d833fd733c`: “User can filter favorite requests using the Filter button and the Favorites filter in the history section.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_3785ab5aa609` (reason.requirement): requirement req_10122ac6ce1a is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_10122ac6ce1a:at_risk`.
    - Requirements: `req_10122ac6ce1a`.

[^26]: **Requirements at risk.** History > Favorite requests: User can favorite a request and unfavorite it via the Favorite button in the history entry's options. Likely.

    - Claim `clm_f70c56c9ee26`: “User can favorite a request and unfavorite it via the Favorite button in the history entry's options.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_ac1fca482214` (reason.requirement): requirement req_ca957a0958ba is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_ca957a0958ba:at_risk`.
    - Requirements: `req_ca957a0958ba`.

[^27]: **Requirements at risk.** History > Save requests to history: User can save a request to history by clicking Send on the request page, which saves it automatically. Likely.

    - Claim `clm_a0cbca14fa94`: “User can save a request to history by clicking Send on the request page, which saves it automatically.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_3b2fb32e6ee0` (reason.requirement): requirement req_0bbd5f0e70f2 is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_0bbd5f0e70f2:at_risk`.
    - Requirements: `req_0bbd5f0e70f2`.

[^28]: **Requirements at risk.** Hoppscotch Web App: User can build, test, and share APIs directly in the browser using the Hoppscotch Web App. Likely.

    - Claim `clm_e34935bd7572`: “User can build, test, and share APIs directly in the browser using the Hoppscotch Web App.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_9a5fe18ab8e6` (reason.requirement): requirement req_fc55fe49f1fe is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_fc55fe49f1fe:at_risk`.
    - Requirements: `req_fc55fe49f1fe`.

[^29]: **Requirements at risk.** RESTful API Testing > Request: User can run Tests on the responses received. Likely.

    - Claim `clm_73bd9c4183b0`: “User can run Tests on the responses received.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_fa26b3288d8c` (reason.requirement): requirement req_741ef7e034a6 is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_741ef7e034a6:at_risk`.
    - Requirements: `req_741ef7e034a6`.

[^30]: **Requirements at risk.** RESTful API Testing > Request: User can add dynamic behaviors to requests by specifying Headers, Request Body, Authorization Headers, Parameters, and Pre-request scripts. Likely.

    - Claim `clm_a75c6f612a22`: “User can add dynamic behaviors to requests by specifying Headers, Request Body, Authorization Headers, Parameters, and Pre-request scripts.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0719f7fa50a1` (link.bm25_judge): requirement "User can add dynamic behaviors to requests by specifying Headers, Request Body, Authorization Headers, Parameters, and Pre-request scripts." (RESTful API Testing > Request) -> screen tab screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Headers", "Collections" with controls: 'Post-request Script', 'Description', 'Generate Code', 'Clear all', 'Environments', 'Environment Quick Peek', 'Value', 'Save'; judged partial/partial over 2 samples, bm2. Reference `req_e0e1717f7fff`.
    - Evidence `ev_45d333dc756a` (reason.requirement): requirement req_e0e1717f7fff is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_e0e1717f7fff:at_risk`.
    - Requirements: `req_e0e1717f7fff`.

[^31]: **Requirements at risk.** Scripts: User can write pre-request scripts and post-request tests in JavaScript to add dynamic behavior, set variables, and validate responses. Likely.

    - Claim `clm_4752d51a0f49`: “User can write pre-request scripts and post-request tests in JavaScript to add dynamic behavior, set variables, and validate responses.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_ed9648fc56e5` (reason.requirement): requirement req_21b7a82f89ef is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_21b7a82f89ef:at_risk`.
    - Requirements: `req_21b7a82f89ef`.

[^32]: **Requirements at risk.** Scripts > hopp.request Namespace > hopp.request.variables.set(key: string, value: string): User can see updates to request variables persisted and reflected in the UI, while other script updates are session-specific. Likely.

    - Claim `clm_4fc05fe5e7c6`: “User can see updates to request variables persisted and reflected in the UI, while other script updates are session-specific.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_28e6bc95cba8` (reason.requirement): requirement req_7c0b317fd334 is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_7c0b317fd334:at_risk`.
    - Requirements: `req_7c0b317fd334`.

[^33]: **Requirements at risk.** Scripts > pw.response > Setting Environment Variables from API Responses: User can use the "Tests" tab of an API request to add code that stores response data in environment variables for use in later requests. Likely.

    - Claim `clm_439498bd219c`: “User can use the "Tests" tab of an API request to add code that stores response data in environment variables for use in later requests.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_4a0943443de5` (reason.requirement): requirement req_1f5961c9f955 is at_risk at likely. Reference `hoppscotch/hoppscotch#6658:req_1f5961c9f955:at_risk`.
    - Requirements: `req_1f5961c9f955`.

[^34]: **Requirements at risk.** AI Features > AI Assistant: User can use the AI Assistant, an experimental chat assistant, to act on the request, response, environment, and collections they are working with. Possible.

    - Claim `clm_684e1559007c`: “User can use the AI Assistant, an experimental chat assistant, to act on the request, response, environment, and collections they are working with.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_2f0300865468` (code.import): packages/hoppscotch-common/src/components/collections/SaveRequest.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppGQLRequest,HoppRESTRequest,generateUniqueRefId,isGQLRequest. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_4230353c36d0` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via \~/helpers/backend/helpers symbols=CollectionDataProps,getTeamCollectionObject. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Evidence `ev_aee1a91faca3` (reason.requirement): requirement req_eab4e3bd569b is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_eab4e3bd569b:at_risk`.
    - Requirements: `req_eab4e3bd569b`.

[^35]: **Requirements at risk.** AI Features > Generate Request Body: User can validate the generated body and select Accept Change to populate the request body field. Possible.

    - Claim `clm_b0f4672edd78`: “User can validate the generated body and select Accept Change to populate the request body field.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_8192f6de965f` (reason.requirement): requirement req_5f040c91914f is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_5f040c91914f:at_risk`.
    - Requirements: `req_5f040c91914f`.

[^36]: **Requirements at risk.** AI Features > Generate Test Scripts: User can select Accept Change to integrate the generated test into the test script editor. Possible.

    - Claim `clm_774969562d6f`: “User can select Accept Change to integrate the generated test into the test script editor.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_06a33d5bf82a` (reason.requirement): requirement req_606faa4c4e13 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_606faa4c4e13:at_risk`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Requirements: `req_606faa4c4e13`.

[^37]: **Requirements at risk.** API Documentation > What you can do: User can generate API documentation from their Collections. Possible.

    - Claim `clm_1d9da967abdc`: “User can generate API documentation from their Collections”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_1425c928a675` (link.i18n_key): button "Documentation" equals the English value of i18n key documentation.title; packages/hoppscotch-common/src/components/collections/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 11 files with this text on the page. Reference `el_e900729ba1f7`.
    - Evidence `ev_1ac85f66443c` (link.bm25_judge): requirement "User can generate API documentation from their Collections" (API Documentation > What you can do) -> element button 'Documentation' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Body", "Collections"; judged partial/partial over 2 samples, bm25 rank 3. Reference `req_1b544c59a825`.
    - Evidence `ev_6c10b297f389` (link.bm25_judge): requirement "User can generate API documentation from their Collections" (API Documentation > What you can do) -> element button 'Documentation' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 4. Reference `req_1b544c59a825`.
    - Evidence `ev_6ffffe2ff715` (link.i18n_key): button "Documentation" equals the English value of i18n key tab.documentation; packages/hoppscotch-common/src/components/http/Sidebar.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 11 files with this text on the page; also uses the active tab "environments" (i18n key tab.environments). Reference `el_e900729ba1f7`.
    - Evidence `ev_77d73f07dee8` (reason.requirement): requirement req_1b544c59a825 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_1b544c59a825:at_risk`.
    - Requirements: `req_1b544c59a825`.

[^38]: **Requirements at risk.** API Mocking > Create a mock server: User can create a mock server from scratch or from an existing Collection. Possible.

    - Claim `clm_ae1812627f56`: “User can create a mock server from scratch or from an existing Collection.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_071c2dab7f2a` (link.bm25_judge): requirement "User can create a mock server from scratch or from an existing Collection." (API Mocking > Create a mock server) -> screen tab screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Mock Servers" with controls: 'Post-request Script', 'Description', 'Generate Code', 'Clear all', 'Environments', 'Environment Quick Peek', 'Value', 'Save'; judged partial/partial over 2 samples, bm25 rank 6. Reference `req_62faf923c343`.
    - Evidence `ev_a1d466c60688` (reason.requirement): requirement req_62faf923c343 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_62faf923c343:at_risk`.
    - Requirements: `req_62faf923c343`.

[^39]: **Requirements at risk.** API Mocking > Create a mock server: User can get a mock server URL after creation to call from an app or Hoppscotch requests. Possible.

    - Claim `clm_c0280c47bbcd`: “User can get a mock server URL after creation to call from an app or Hoppscotch requests.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_cb2c9ff2612c` (reason.requirement): requirement req_69263667dd08 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_69263667dd08:at_risk`.
    - Requirements: `req_69263667dd08`.

[^40]: **Requirements at risk.** API Mocking > Latency, errors, and headers: User can add fixed or ranged latency as a delay to mock responses. Possible.

    - Claim `clm_f78f752585cf`: “User can add fixed or ranged latency as a delay to mock responses.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_99246e498c01` (reason.requirement): requirement req_b074b70d7b29 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_b074b70d7b29:at_risk`.
    - Requirements: `req_b074b70d7b29`.

[^41]: **Requirements at risk.** API Mocking > Save a response as an Example: User can save responses as examples to populate mock routes with realistic data. Possible.

    - Claim `clm_440a9f85c7a3`: “User can save responses as examples to populate mock routes with realistic data.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_13c1916d7a92` (link.bm25_judge): requirement "User can save responses as examples to populate mock routes with realistic data." (API Mocking > Save a response as an Example) -> element button 'Save' on screen "GraphQL • Hoppscotch", dialog "Save as"; judged none/partial over 2 samples, bm25 rank 9. Reference `req_9a0d7f2f4686`.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_625724e75976` (reason.requirement): requirement req_9a0d7f2f4686 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_9a0d7f2f4686:at_risk`.
    - Requirements: `req_9a0d7f2f4686`.

[^42]: **Requirements at risk.** API Mocking > Save a response as an Example: User can click the "Save as Example" icon in the response section to save a response as an example. Possible.

    - Claim `clm_c36c708511ef`: “User can click the "Save as Example" icon in the response section to save a response as an example.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_113b6e2447a5` (link.i18n_key): button "Environment Quick Peek" equals the English value of i18n key environment.quick_peek; packages/hoppscotch-common/src/components/environments/Selector.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_191068a8147a`.
    - Evidence `ev_a16aec22ab13` (reason.requirement): requirement req_d0244e570cf2 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_d0244e570cf2:at_risk`.
    - Requirements: `req_d0244e570cf2`.

[^43]: **Requirements at risk.** Authorization > Managing Authorization at the Collection Level: User can right-click a collection or subfolder to open its properties. Possible.

    - Claim `clm_51165b91b674`: “User can right-click a collection or subfolder to open its properties.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_38e96c82266e` (reason.requirement): requirement req_e98358ca45a9 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_e98358ca45a9:at_risk`.
    - Evidence `ev_4230353c36d0` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via \~/helpers/backend/helpers symbols=CollectionDataProps,getTeamCollectionObject. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Evidence `ev_6489faef584d` (link.bm25_judge): requirement "User can right-click a collection or subfolder to open its properties." (Authorization > Managing Authorization at the Collection Level) -> element button 'Collections' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 2. Reference `req_e98358ca45a9`.
    - Requirements: `req_e98358ca45a9`.

[^44]: **Requirements at risk.** Authorization > OAuth 2.0 > Grant types > 1. Authorization Code: User can specify Scope and pass the token as a Header or Query Parameter in Authorization Code grant. Possible.

    - Claim `clm_6f4c2c9a5c02`: “User can specify Scope and pass the token as a Header or Query Parameter in Authorization Code grant.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_026ea2883963` (link.i18n_key): button "Authorization" equals the English value of i18n key tab.authorization; packages/hoppscotch-common/src/components/collections/Properties.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 2 files with this text on the page. Reference `el_75c63359e18c`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_0f60fd6530a8` (link.bm25_judge): requirement "User can specify Scope and pass the token as a Header or Query Parameter in Authorization Code grant." (Authorization > OAuth 2.0 > Grant types > 1. Authorization Code) -> screen tab screen "GraphQL • Hoppscotch", tabs "Untitled", "Authorization", "Documentation" with controls: 'Schema', 'Settings', 'Enter a GraphQL endpoint URL', 'Interceptor', 'Collapse sidebar', 'Horizontal layout', 'Search and commands', 'Login'; judged partial/partial over 2 samples, bm25 rank 13. Reference `req_68820fea04b5`.
    - Evidence `ev_734411129d41` (reason.requirement): requirement req_68820fea04b5 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_68820fea04b5:at_risk`.
    - Requirements: `req_68820fea04b5`.

[^45]: **Requirements at risk.** Collections: User can create collections and add requests to them to share with the team or use later. Possible.

    - Claim `clm_164f2bfcbc4e`: “User can create collections and add requests to them to share with the team or use later.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_1ece28fed3e1` (code.import): packages/hoppscotch-common/src/helpers/shortcode/ShortcodeListAdapter.ts imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via ../backend/helpers symbols=BACKEND_PAGE_SIZE. Reference `packages/hoppscotch-common/src/helpers/shortcode/ShortcodeListAdapter.ts->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Evidence `ev_354d887d7efa` (link.bm25_judge): requirement "User can create collections and add requests to them to share with the team or use later." (Collections) -> element button 'Add new' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged full/full over 2 samples, bm25 rank 9. Reference `req_791a74af2d16`.
    - Evidence `ev_420607c6a31a` (link.i18n_key): button "Add new" equals the English value of i18n key add.new; packages/hoppscotch-common/src/components/share/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 18 files with this text on the page; also uses the active tab "parameters" (i18n key tab.parameters). Reference `el_5a873aa6da97`.
    - Evidence `ev_4a9d1a34148b` (link.i18n_key): button "Add new" equals the English value of i18n key add.new; packages/hoppscotch-common/src/components/graphql/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 9 files with this text on the page; also uses the active tab "headers" (i18n key tab.headers). Reference `el_c94809d4234e`.
    - Evidence `ev_d0b1b1a0761b` (reason.requirement): requirement req_791a74af2d16 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_791a74af2d16:at_risk`.
    - Requirements: `req_791a74af2d16`.

[^46]: **Requirements at risk.** Collections: User can access collections by clicking the Collections icon on the side panel. Possible.

    - Claim `clm_abf4a7b00c71`: “User can access collections by clicking the Collections icon on the side panel.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0b69161e348f` (link.bm25_judge): requirement "User can access collections by clicking the Collections icon on the side panel." (Collections) -> element button 'Collections' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged full/full over 2 samples, bm25 rank 1. Reference `req_eaba14acfe8b`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_4230353c36d0` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via \~/helpers/backend/helpers symbols=CollectionDataProps,getTeamCollectionObject. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Evidence `ev_f47a7360d0d5` (reason.requirement): requirement req_eaba14acfe8b is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_eaba14acfe8b:at_risk`.
    - Requirements: `req_eaba14acfe8b`.

[^47]: **Requirements at risk.** Collections > Creating a new collection: User can create a new collection by clicking Add new in the collections section and entering a name. Possible.

    - Claim `clm_02866d0fed12`: “User can create a new collection by clicking Add new in the collections section and entering a name.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_124e6f4b053a` (link.bm25_judge): requirement "User can create a new collection by clicking Add new in the collections section and entering a name." (Collections > Creating a new collection) -> element button 'Add new' on screen "GraphQL • Hoppscotch", tabs "Untitled", "Query", "Collections"; judged full/full over 2 samples, bm25 rank 1. Reference `req_1a9637e480a3`.
    - Evidence `ev_1ece28fed3e1` (code.import): packages/hoppscotch-common/src/helpers/shortcode/ShortcodeListAdapter.ts imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via ../backend/helpers symbols=BACKEND_PAGE_SIZE. Reference `packages/hoppscotch-common/src/helpers/shortcode/ShortcodeListAdapter.ts->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Evidence `ev_4a9d1a34148b` (link.i18n_key): button "Add new" equals the English value of i18n key add.new; packages/hoppscotch-common/src/components/graphql/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 9 files with this text on the page; also uses the active tab "headers" (i18n key tab.headers). Reference `el_c94809d4234e`.
    - Evidence `ev_4bd182703e2c` (code.import): packages/hoppscotch-common/src/components/share/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppGQLRequest,HoppRESTRequest. Reference `packages/hoppscotch-common/src/components/share/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_63908c6cbc2a` (reason.requirement): requirement req_1a9637e480a3 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_1a9637e480a3:at_risk`.
    - Requirements: `req_1a9637e480a3`.

[^48]: **Requirements at risk.** Collections > Creating a new collection: User can create a sub-collection by clicking Add new on a collection and entering a name. Possible.

    - Claim `clm_5c3f3c1e3910`: “User can create a sub-collection by clicking Add new on a collection and entering a name.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_01551b8ceb64` (link.i18n_key): searchbox "Search" equals the English value of i18n key action.search; packages/hoppscotch-common/src/components/collections/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 8 files with this text on the page. Reference `el_3ae1882436cb`.
    - Evidence `ev_08c735f98f69` (link.bm25_judge): requirement "User can create a sub-collection by clicking Add new on a collection and entering a name." (Collections > Creating a new collection) -> element button 'Add new' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Headers", "Collections"; judged full/partial over 2 samples, bm25 rank 3. Reference `req_8f8a53fde411`.
    - Evidence `ev_0bebfe905396` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/collections/MyCollections.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 15 files with this text on the page. Reference `el_8b0bc6ca7913`.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_4ad859fef92b` (reason.requirement): requirement req_8f8a53fde411 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_8f8a53fde411:at_risk`.
    - Requirements: `req_8f8a53fde411`.

[^49]: **Requirements at risk.** Collections > Organizing collections: User can organize collections by dragging and dropping them to the desired location. Possible.

    - Claim `clm_7989b2b0dc20`: “User can organize collections by dragging and dropping them to the desired location.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_4230353c36d0` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via \~/helpers/backend/helpers symbols=CollectionDataProps,getTeamCollectionObject. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Evidence `ev_64a03cc42186` (link.bm25_judge): requirement "User can organize collections by dragging and dropping them to the desired location." (Collections > Organizing collections) -> element button 'Collections' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 1. Reference `req_a11de9dc8a10`.
    - Evidence `ev_e31a9aee39bc` (reason.requirement): requirement req_a11de9dc8a10 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_a11de9dc8a10:at_risk`.
    - Requirements: `req_a11de9dc8a10`.

[^50]: **Requirements at risk.** Collections > Organizing collections: User can create sub-collections by dragging and dropping a collection into another collection. Possible.

    - Claim `clm_b4f0f0a50c06`: “User can create sub-collections by dragging and dropping a collection into another collection.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_08fa63fa260b` (link.bm25_judge): requirement "User can create sub-collections by dragging and dropping a collection into another collection." (Collections > Organizing collections) -> element button 'Collections' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 7. Reference `req_cd7435eb4b61`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_4230353c36d0` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via \~/helpers/backend/helpers symbols=CollectionDataProps,getTeamCollectionObject. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Evidence `ev_d8055ae8bfd4` (reason.requirement): requirement req_cd7435eb4b61 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_cd7435eb4b61:at_risk`.
    - Requirements: `req_cd7435eb4b61`.

[^51]: **Requirements at risk.** Collections > Organizing collections > Adding requests to collections: User can add the current request to a collection with the New request button in the collection's options. Possible.

    - Claim `clm_49ac2eaeaf1c`: “User can add the current request to a collection with the New request button in the collection's options.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_01551b8ceb64` (link.i18n_key): searchbox "Search" equals the English value of i18n key action.search; packages/hoppscotch-common/src/components/collections/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 8 files with this text on the page. Reference `el_3ae1882436cb`.
    - Evidence `ev_03112b92412d` (link.bm25_judge): requirement "User can add the current request to a collection with the New request button in the collection's options." (Collections > Organizing collections > Adding requests to collections) -> element button 'Add new' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Headers", "Collections"; judged partial/partial over 2 samples, bm25 rank 5. Reference `req_0b11b11f956f`.
    - Evidence `ev_0bebfe905396` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/collections/MyCollections.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 15 files with this text on the page. Reference `el_8b0bc6ca7913`.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_34b41033dcc5` (reason.requirement): requirement req_0b11b11f956f is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_0b11b11f956f:at_risk`.
    - Requirements: `req_0b11b11f956f`.

[^52]: **Requirements at risk.** Collections > Save requests to collections: User can save a request to a new location using Save as from the drop-down next to the Save button. Possible.

    - Claim `clm_aa8f2f2962f5`: “User can save a request to a new location using Save as from the drop-down next to the Save button.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_01551b8ceb64` (link.i18n_key): searchbox "Search" equals the English value of i18n key action.search; packages/hoppscotch-common/src/components/collections/index.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 8 files with this text on the page. Reference `el_3ae1882436cb`.
    - Evidence `ev_0bebfe905396` (link.i18n_key): button "Wiki" equals the English value of i18n key app.wiki; packages/hoppscotch-common/src/components/collections/MyCollections.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 15 files with this text on the page. Reference `el_8b0bc6ca7913`.
    - Evidence `ev_0c9f421fa2e1` (link.i18n_key): button "Save" equals the English value of i18n key action.save; packages/hoppscotch-common/src/components/collections/SaveRequest.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql); one of 22 files with this text on the page; also uses the dialog title "save as" (i18n key collection.save_as). Reference `el_3e05813c4d46`.
    - Evidence `ev_0cddb1a82900` (reason.requirement): requirement req_4056f688470e is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_4056f688470e:at_risk`.
    - Evidence `ev_0ce524ab2e4d` (link.i18n_key): button "New" equals the English value of i18n key action.new; packages/hoppscotch-common/src/components/collections/MyCollections.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 7 files with this text on the page. Reference `el_caf41bed8b1c`.
    - Requirements: `req_4056f688470e`.

[^53]: **Requirements at risk.** GraphQL API Testing > Other features > Schema: User can use the schema explorer to understand how data is structured. Possible.

    - Claim `clm_d345d37bc323`: “User can use the schema explorer to understand how data is structured.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_1425c928a675` (link.i18n_key): button "Documentation" equals the English value of i18n key documentation.title; packages/hoppscotch-common/src/components/collections/Collection.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 11 files with this text on the page. Reference `el_e900729ba1f7`.
    - Evidence `ev_1c96ba752e94` (link.bm25_judge): requirement "User can use the schema explorer to understand how data is structured." (GraphQL API Testing > Other features > Schema) -> element button 'Schema' on screen "GraphQL • Hoppscotch", tabs "Untitled", "Query", "Documentation"; judged full/full over 2 samples, bm25 rank 2. Reference `req_21df567193b7`.
    - Evidence `ev_83e9c9535fd4` (reason.requirement): requirement req_21df567193b7 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_21df567193b7:at_risk`.
    - Requirements: `req_21df567193b7`.

[^54]: **Requirements at risk.** GraphQL API Testing > Request: User can enter a server endpoint and initiate a connection in the GraphQL request section. Possible.

    - Claim `clm_8787093906d1`: “User can enter a server endpoint and initiate a connection in the GraphQL request section.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_009f677c7350` (link.bm25_judge): requirement "User can enter a server endpoint and initiate a connection in the GraphQL request section." (GraphQL API Testing > Request) -> screen page screen "GraphQL • Hoppscotch", tabs "Untitled", "Query", "Documentation" with controls: 'Prettify', 'Schema', 'Settings', 'Enter a GraphQL endpoint URL', 'Clear all', 'Interceptor', 'Wrap lines', 'Collapse sidebar'; judged partial/partial over 2 samples, bm25 rank 5. Reference `req_262a77629ec0`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_105ba2a80c68` (link.bm25_judge): requirement "User can enter a server endpoint and initiate a connection in the GraphQL request section." (GraphQL API Testing > Request) -> screen page screen "GraphQL • Hoppscotch", tabs "Untitled", "Query", "Documentation" with controls: 'Prettify', 'Schema', 'Settings', 'Enter a GraphQL endpoint URL', 'Clear all', 'Interceptor', 'Wrap lines', 'Collapse sidebar'; judged partial/partial over 2 samples, bm25 rank 4. Reference `req_262a77629ec0`.
    - Evidence `ev_d02e32f70558` (reason.requirement): requirement req_262a77629ec0 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_262a77629ec0:at_risk`.
    - Requirements: `req_262a77629ec0`.

[^55]: **Requirements at risk.** GraphQL API Testing > Request: User can add dynamic behavior to GraphQL queries by defining variables, headers, and authorization. Possible.

    - Claim `clm_00786591d2ee`: “User can add dynamic behavior to GraphQL queries by defining variables, headers, and authorization.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_0113caa87e2b` (link.i18n_key): textbox "Description" equals the English value of i18n key documentation.description; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/graphql.vue (route /graphql). Reference `el_24996fa1219a`.
    - Evidence `ev_03dc9b198240` (link.i18n_key): textbox "Key" equals the English value of i18n key documentation.key; packages/hoppscotch-common/src/components/collections/documentation/sections/Headers.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 5 files with this text on the page; also uses the active tab "headers" (i18n key documentation.headers.title). Reference `el_54bb71f26730`.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_1bdfa91c3448` (reason.requirement): requirement req_748eb8f9dbf9 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_748eb8f9dbf9:at_risk`.
    - Requirements: `req_748eb8f9dbf9`.

[^56]: **Requirements at risk.** GraphQL API Testing > Request: User can use the query builder to design and run queries to fetch data once connected. Possible.

    - Claim `clm_85dfb02b5a81`: “User can use the query builder to design and run queries to fetch data once connected.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_23a8f4937c00` (link.bm25_judge): requirement "User can use the query builder to design and run queries to fetch data once connected." (GraphQL API Testing > Request) -> element button 'Query' on screen "GraphQL • Hoppscotch", tabs "Untitled", "Query", "Documentation"; judged partial/partial over 2 samples, bm25 rank 4. Reference `req_d7294f811b8f`.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_5f25d4c2ac7d` (reason.requirement): requirement req_d7294f811b8f is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_d7294f811b8f:at_risk`.
    - Evidence `ev_6334aa661c0a` (code.import): packages/hoppscotch-common/src/components/graphql/RequestOptions.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppGQLAuth,HoppGQLRequest. Reference `packages/hoppscotch-common/src/components/graphql/RequestOptions.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_651fe7a795ea` (code.render): packages/hoppscotch-common/src/components/graphql/RequestOptions.vue renders packages/hoppscotch-common/src/components/collections/SaveRequest.vue via CollectionsSaveRequest. Reference `packages/hoppscotch-common/src/components/graphql/RequestOptions.vue->packages/hoppscotch-common/src/components/collections/SaveRequest.vue`.
    - Requirements: `req_d7294f811b8f`.

[^57]: **Requirements at risk.** History > Accessing requests from history: User can open a request from history by clicking it in the history section. Possible.

    - Claim `clm_301468d587b1`: “User can open a request from history by clicking it in the history section.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_2d2c88b34aab` (reason.requirement): requirement req_da8ae4b3d067 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_da8ae4b3d067:at_risk`.
    - Evidence `ev_33728ae0be73` (code.import): packages/hoppscotch-common/src/components/history/Personal.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppGQLRequest,HoppRESTRequest,makeGQLRequest. Reference `packages/hoppscotch-common/src/components/history/Personal.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_3c046c31691e` (link.i18n_key): button "Filter" equals the English value of i18n key action.filter; packages/hoppscotch-common/src/components/history/Personal.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_fae25a745b95`.
    - Evidence `ev_4230353c36d0` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via \~/helpers/backend/helpers symbols=CollectionDataProps,getTeamCollectionObject. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Requirements: `req_da8ae4b3d067`.

[^58]: **Requirements at risk.** History > Deleting requests from history: User can delete a request from history using the Delete button in the history entry's options. Possible.

    - Claim `clm_63c9b90ea34f`: “User can delete a request from history using the Delete button in the history entry's options.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_176e45432ae1` (link.bm25_judge): requirement "User can delete a request from history using the Delete button in the history entry's options." (History > Deleting requests from history) -> element button 'History' on screen "Hoppscotch • Open source API development ecosystem • Hoppscotch", tabs "Untitled", "Parameters", "Collections"; judged partial/partial over 2 samples, bm25 rank 4. Reference `req_65597e37ad65`.
    - Evidence `ev_4230353c36d0` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via \~/helpers/backend/helpers symbols=CollectionDataProps,getTeamCollectionObject. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Evidence `ev_5d0ba140861d` (link.i18n_key): button "History" equals the English value of i18n key tab.history; packages/hoppscotch-common/src/components/http/Sidebar.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /); one of 2 files with this text on the page; also uses the active tab "environments" (i18n key tab.environments). Reference `el_29c4aca1b30a`.
    - Evidence `ev_8cb88f5029f2` (reason.requirement): requirement req_65597e37ad65 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_65597e37ad65:at_risk`.
    - Requirements: `req_65597e37ad65`.

[^59]: **Requirements at risk.** History > Filter history: User can filter history by clicking the Filter button in the history section and selecting a filter. Possible.

    - Claim `clm_9fb2dd9dbae7`: “User can filter history by clicking the Filter button in the history section and selecting a filter.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_33728ae0be73` (code.import): packages/hoppscotch-common/src/components/history/Personal.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppGQLRequest,HoppRESTRequest,makeGQLRequest. Reference `packages/hoppscotch-common/src/components/history/Personal.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_3c046c31691e` (link.i18n_key): button "Filter" equals the English value of i18n key action.filter; packages/hoppscotch-common/src/components/history/Personal.vue uses it and is rendered under packages/hoppscotch-common/src/pages/index.vue (route /). Reference `el_fae25a745b95`.
    - Evidence `ev_4230353c36d0` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via \~/helpers/backend/helpers symbols=CollectionDataProps,getTeamCollectionObject. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Evidence `ev_e687ff0d03d9` (reason.requirement): requirement req_c8565d9dd9a3 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_c8565d9dd9a3:at_risk`.
    - Requirements: `req_c8565d9dd9a3`.

[^60]: **Requirements at risk.** RESTful API Testing > Request: User can select from a range of HTTP methods such as GET, POST and PUT for a request. Possible.

    - Claim `clm_ff6f08d63d1c`: “User can select from a range of HTTP methods such as GET, POST and PUT for a request.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_25ff0629e7a3` (code.render): packages/hoppscotch-common/src/components/gql/Request.vue renders packages/hoppscotch-common/src/components/collections/SaveRequest.vue via CollectionsSaveRequest. Reference `packages/hoppscotch-common/src/components/gql/Request.vue->packages/hoppscotch-common/src/components/collections/SaveRequest.vue`.
    - Evidence `ev_2cf17f738c8c` (code.render): packages/hoppscotch-common/src/components/collections/SaveRequest.vue renders packages/hoppscotch-common/src/components/collections/index.vue via Collections. Reference `packages/hoppscotch-common/src/components/collections/SaveRequest.vue->packages/hoppscotch-common/src/components/collections/index.vue`.
    - Evidence `ev_33a66bccb6b9` (code.import): packages/hoppscotch-common/src/components/gql/Request.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppGQLAuth,HoppGQLRequest. Reference `packages/hoppscotch-common/src/components/gql/Request.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_3ef6a1a3d484` (code.import): packages/hoppscotch-common/src/components/aichat/Assistant.vue imports packages/hoppscotch-common/src/services/ai-chat.service.ts via \~/services/ai-chat.service symbols=AIChatService,ChatMessage,PendingConfirmation. Reference `packages/hoppscotch-common/src/components/aichat/Assistant.vue->packages/hoppscotch-common/src/services/ai-chat.service.ts`.
    - Evidence `ev_caddf21ff533` (reason.requirement): requirement req_bcfa3ab48380 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_bcfa3ab48380:at_risk`.
    - Requirements: `req_bcfa3ab48380`.

[^61]: **Requirements at risk.** Runner > Running a Collection: User can right-click a collection and select the "Run Collection" option. Possible.

    - Claim `clm_4f8ebe17fdf5`: “User can right-click a collection and select the "Run Collection" option.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_2c83419cb406` (reason.requirement): requirement req_04e46d9ed399 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_04e46d9ed399:at_risk`.
    - Evidence `ev_4230353c36d0` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via \~/helpers/backend/helpers symbols=CollectionDataProps,getTeamCollectionObject. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Evidence `ev_6705bf060010` (code.import): packages/hoppscotch-common/src/components/collections/ImportExport.vue imports packages/hoppscotch-common/src/helpers/import-export/import/import-sources/UrlSource.ts via \~/helpers/import-export/import/import-sources/UrlSource symbols=UrlSource. Reference `packages/hoppscotch-common/src/components/collections/ImportExport.vue->packages/hoppscotch-common/src/helpers/import-export/import/import-sources/UrlSource.ts`.
    - Requirements: `req_04e46d9ed399`.

[^62]: **Requirements at risk.** Runner > Running a Collection: User can hover over a collection name and click the "Run Collection" icon. Possible.

    - Claim `clm_86463281babf`: “User can hover over a collection name and click the "Run Collection" icon.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_063397a0b6f2` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-data/src/index.ts via @hoppscotch/data symbols=HoppCollection,HoppCollectionVariable,HoppGQLRequest,HoppGQLRequestResponse,HoppRESTAuth,HoppRESTHeaders,HoppRESTRequest,HoppRESTRequestResponse,generateUniqueRefId,getDefaultRESTRequest,isGQLRequest,makeCollection,makeHoppGQLResponseOriginalRequest,makeHoppRESTResponseOriginalRequest. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-data/src/index.ts`.
    - Evidence `ev_0f3555cafca3` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/components/collections/MyCollections.vue via ./MyCollections.vue symbols=Collection. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/components/collections/MyCollections.vue`.
    - Evidence `ev_3f6bf3976413` (reason.requirement): requirement req_a2deaac054d3 is at_risk at possible. Reference `hoppscotch/hoppscotch#6658:req_a2deaac054d3:at_risk`.
    - Evidence `ev_4230353c36d0` (code.import): packages/hoppscotch-common/src/components/collections/index.vue imports packages/hoppscotch-common/src/helpers/backend/helpers.ts via \~/helpers/backend/helpers symbols=CollectionDataProps,getTeamCollectionObject. Reference `packages/hoppscotch-common/src/components/collections/index.vue->packages/hoppscotch-common/src/helpers/backend/helpers.ts`.
    - Evidence `ev_6705bf060010` (code.import): packages/hoppscotch-common/src/components/collections/ImportExport.vue imports packages/hoppscotch-common/src/helpers/import-export/import/import-sources/UrlSource.ts via \~/helpers/import-export/import/import-sources/UrlSource symbols=UrlSource. Reference `packages/hoppscotch-common/src/components/collections/ImportExport.vue->packages/hoppscotch-common/src/helpers/import-export/import/import-sources/UrlSource.ts`.
    - Requirements: `req_a2deaac054d3`.

[^63]: **What we could not determine, and who should look.** the shared import export steps url import code: The only links we found to screens are weak.

    - Reason code `NEEDS_HUMAN`.
    - Evidence `ev_1786871934d1` (reason.probe): packages/hoppscotch-common/src/components/importExport/ImportExportSteps/UrlImport.vue is imported by 2 file(s) (2 util); 0 of them draw a screen. Reference `packages/hoppscotch-common/src/components/importExport/ImportExportSteps/UrlImport.vue`.
    - Evidence `ev_c2313f3347d7` (pr.file): modified packages/hoppscotch-common/src/components/importExport/ImportExportSteps/UrlImport.vue (+3 -3) class component kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/components/importExport/ImportExportSteps/UrlImport.vue`.

[^64]: **What we could not determine, and who should look.** the collections import code: No control we observed is built from this file or from code that uses it within two steps (it may be backend-only, internal, or on a screen the crawl did not reach).

    - Reason code `NEEDS_HUMAN`.
    - Evidence `ev_24c5b710b5c4` (pr.file): modified packages/hoppscotch-common/src/lib/sync/collections/import.ts (+1 -0) class source_other kind BODY. Reference `hoppscotch/hoppscotch#6658:packages/hoppscotch-common/src/lib/sync/collections/import.ts`.
    - Evidence `ev_7f6d7f751b84` (reason.probe): packages/hoppscotch-common/src/lib/sync/collections/import.ts is imported by 1 file(s) (1 source_other); 0 of them draw a screen. Reference `packages/hoppscotch-common/src/lib/sync/collections/import.ts`.

### How the bands are set

- Confirmed: confidence >= 0.8.
- Likely: confidence 0.5 to 0.8.
- Possible: confidence 0.25 to 0.5.
- Unknown: confidence < 0.25.

The bands are rules of thumb applied to how each link was found. They have not been validated against outcomes, so read them as an ordering, not as probabilities.

Generated by blastradius 0.1.0.
