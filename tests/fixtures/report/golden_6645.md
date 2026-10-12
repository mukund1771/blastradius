# Blast radius: PR #6645 "feat(common): edit environment values from quick peek and the sidebar"

**Risk: MEDIUM.** Touches Environments (sidebar, quick peek, selector). 5 screens, 2 user flows, 2 requirements.

Confidence: 5 items confirmed, 2 probable and 3 possible. 4 things could not be determined and are listed below with the same weight as the findings.

Deployed app compared with the indexed code: 11 of 104 files differ. The PR branch is 10 commits behind main, so its diff may not apply as shown.

## What to re-test first

- [ ] Open Environments in the sidebar, choose an environment, and edit a variable's key and current value. Why: the sidebar now has a full editor for the variables. [^1][^4]
- [ ] Open the Select environment menu, edit a current value in the quick peek, then send a request that uses it. Why: the selector now writes values straight to the saved variable. [^3][^6]
- [ ] Mark a variable as secret and check that its value stays masked in the quick peek and in the sidebar editor, and that the reveal toggle works. Why: secret values can now be edited in place and must stay hidden. [^5][^7]
- [ ] Rename a variable, then send a request that uses the old and the new name. Why: a rename is now written through the same value service. [^6]
- [ ] Press Back in the editor and confirm the environment list returns with your edits. Why: the Back button is new. [^2]
- [ ] Open New Collection from the sidebar and create a collection. Why: it opens from the sidebar that holds the changed tab. [^10]
- [ ] Sign in with a team account and edit a team variable's current value. Why: team environment rows use the same editor and could not be reached signed out.

## Directly changed

These screens and elements have their own code in this PR.

How sure: Confirmed (the PR's own code changes it), Likely (reached through code it shares), Possible (a weaker connection, worth a look), Unknown (we cannot tell).

| Screen | What changed | How sure |
|---|---|---|
| **Environments sidebar** (on Home page) | The Environments sidebar is changed by this PR: choosing an environment now opens an editor with an editable key and current value for each variable. Controls involved: “Environments”. [^1] Flows: Edit an environment variable. | Confirmed |
| **Back button** (on Home page) | The Back button label is changed by this PR: a new shared Back label is added so the editor can return to the environment list. Controls involved: “Back”. [^2] | Confirmed |
| **Environment selector** (on Select environment) | The environment selector is changed by this PR: the current value of a non-secret variable can now be edited right in the quick peek. Controls involved: “Select environment”. [^3] Flows: Edit an environment variable; Send a request that uses a variable. | Confirmed |
| **My and team environment lists** (on Home page) | Each environment row in the sidebar lists is changed by this PR: choosing one now opens the new editor instead of only selecting it. [^4] Flows: Edit an environment variable. | Confirmed |
| **Secret variable values** (on Home page) | Secret variable values will change in the new editor: they stay masked behind a reveal toggle and can now be edited in place. [^5] Flows: Edit an environment variable. | Confirmed |

## Indirect, through shared code

Screens that reuse code this PR changes, grouped by the part of the spec they belong to.

### Environments

Connected through: the shared current-value service; the shared secret-value service.

| Screen | What may be affected | How sure |
|---|---|---|
| **Sending a request** (on Home page) | Sending a request is probably affected: the value it uses for a variable is read from the current-value service this PR extends. [^6] Flows: Send a request that uses a variable. | Likely |
| **Secret variables when a request is sent** (on Home page) | Secret variables are probably affected when a request is sent: their values are now also written through the secret-value service this PR extends. [^7] Flows: Send a request that uses a variable. | Likely |
| **Variables in a GraphQL request** (on GraphQL page) | Variables in a GraphQL request might be affected: they are read from the same current-value service. [^8] | Possible |
| **Variables in the WebSocket address** (on WebSocket page) | Variables in the WebSocket address might be affected: they are read from the same current-value service; worth a quick look. [^9] | Possible |

### Collections

Connected through: the sidebar that hosts the Environments tab.

| Screen | What may be affected | How sure |
|---|---|---|
| **New Collection dialog** | The New Collection dialog might be affected: it opens from the same sidebar as the changed Environments tab; worth a quick look. [^10] | Possible |

## Requirements at risk

| What the spec requires | Where in the spec | Status | How sure |
|---|---|---|---|
| User can manage environment variables used in requests. [^11] Spec says: “Environments let you store variables and reuse them across requests.” | Environments | At risk | Likely |
| User can create a collection to organize requests. [^12] Spec says: “Collections allow you to organize your requests into folders.” | Collections | At risk | Possible |

## What we could not determine, and who should look

| Item | Why we could not tell | What a person should check |
|---|---|---|
| **the whole PR** | The PR is based on code 10 commits behind main, and two files it changes (the English text file and the component list) were also edited there, so its diff may not apply as shown. [^13] | Rebase the PR on main and run the report again before relying on the lists above; 2 file(s) were edited on both sides. |
| **Everything that reads or writes environment values** | Two shared services changed, and together they reach more than a quarter of the app's components. That is too wide to list reliably, so only the strongest connections are shown above. [^14] | Think about where a variable's value is used: sending a request, saving a request, the GraphQL and realtime screens, and import or export. |
| **The new sidebar editor and its reveal toggle** | These controls are not in the live app yet, so no screen we observed shows them and nothing could be matched to a screen title. [^15] | Open the PR's preview build, click an environment in the sidebar, and look at the editor, the reveal toggle and the Back button. |
| **Team environments** | They only appear after signing in, and this report looked at the signed-out app only. [^16] | Sign in with a team account, open Environments, and edit a team variable's current value. |

### What this report did not look at

- FIXTURE: this report was written by hand to test rendering and the lint. It is not a product output and nothing in it was measured.
- Only the signed-out surface of the web app was observed.
- One viewport size was used.
- Only English text (en) was compared.
- Frontend files only: backend, scripts and build tooling are outside this analysis.
- Cloud web edition only: desktop and self-hosted behaviour was not assessed.
- Set aside as not user-facing: 2 test file(s), 1 generated file(s).

## Appendix: evidence

Everything above cites the numbered entries below. File paths, ids and commit hashes appear only here.

### Snapshot

- Target: `hoppscotch`
- Repository: `hoppscotch/hoppscotch`
- Indexed commit: `63273f8e599e73f0fb38c0739314e8d46b6bc83d`
- PR base: `ac145e7f758151b41fd46d3e5f513886ce9068ba`
- PR head: `64ba4ff75eff7bef868b010947f50e9b6b326540`
- PR base behind main: 10 commits
- Deployed vs indexed: 11 of 104 files differ
- Crawl bundle: `c81a0323d1f5b4f5`
- Crawl size: 46 actions and 25 screens
- Spec sha: `f895cca9ad176109`
- Documentation version: 2026\.5.0
- App version: 2026\.9.0

### Evidence behind each statement

[^1]: **Directly changed.** Environments sidebar (on Home page): The Environments sidebar is changed by this PR: choosing an environment now opens an editor with an editable key and current value for each variable. Controls involved: “Environments”. Confirmed (0.93).

    - Claim `clm_63392103f1ac`: Environments sidebar: The Environments sidebar is changed by this PR: choosing an environment now opens an editor with an editable key and current value for each variable. Controls involved: “Environments”.
    - Evidence `ev_2ed0ea495242` (pr.file): added packages/hoppscotch-common/src/components/environments/Editor.vue (+387 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/Editor.vue`. Trace span `489e94042caa50e9`.
    - Evidence `ev_e4d6dc6004af` (reason.impact): direct impact on Environments sidebar: 2 control(s), confidence 0.93. Reference `hoppscotch/hoppscotch#6645:direct:sidebar`. Trace span `47148b0397ce2e76`.
    - Evidence `ev_ea0cca1009e9` (link.i18n_key): button "Environments" equals the English value of i18n key tab.environments; environments/index.vue uses it and is rendered by the Home page. Reference `el_12bb7a04cc57`. Trace span `5ffd2e90600ddcfe`.
    - Evidence `ev_eb418800dd4a` (pr.file): modified packages/hoppscotch-common/src/components/environments/index.vue (+53 -26) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/index.vue`. Trace span `033d433a8e100431`.
    - Files: `packages/hoppscotch-common/src/components/environments/Editor.vue`, `packages/hoppscotch-common/src/components/environments/index.vue`, `packages/hoppscotch-common/src/components/environments/my/index.vue`, `packages/hoppscotch-common/src/components/environments/teams/index.vue`.
    - Requirements: `req_a3a6dbfa8d98`.
    - Flows: `Edit an environment variable`.

[^2]: **Directly changed.** Back button (on Home page): The Back button label is changed by this PR: a new shared Back label is added so the editor can return to the environment list. Controls involved: “Back”. Confirmed (0.90).

    - Claim `clm_332ee09de0bb`: Back button: The Back button label is changed by this PR: a new shared Back label is added so the editor can return to the environment list. Controls involved: “Back”.
    - Evidence `ev_2589e7680ccb` (pr.locale_diff): packages/hoppscotch-common/locales/en.json: +2 \~0 -0 keys. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/locales/en.json@64ba4ff75eff7bef868b010947f50e9b6b326540`. Trace span `79e78fb7e6967a06`.
    - Evidence `ev_333ca8da0315` (reason.impact): direct impact on Back button: 1 control(s), confidence 0.90. Reference `hoppscotch/hoppscotch#6645:direct:back`. Trace span `a12c1e3c2d8060a3`.
    - Evidence `ev_82a09ba1fea1` (pr.file): modified packages/hoppscotch-common/locales/en.json (+2 -0) class locale kind COPY_ONLY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/locales/en.json`. Trace span `18d811a7ace8d5b7`.
    - Evidence `ev_a251650173ac` (link.i18n_key): button "Back" equals the English value of i18n key action.back, added by this PR; environments/Editor.vue uses it. Reference `el_853b6712ec2f`. Trace span `b4e108344b1a8ae7`.
    - Files: `packages/hoppscotch-common/locales/en.json`.

[^3]: **Directly changed.** Environment selector (on Select environment): The environment selector is changed by this PR: the current value of a non-secret variable can now be edited right in the quick peek. Controls involved: “Select environment”. Confirmed (0.90).

    - Claim `clm_6854fd6d15d7`: Environment selector: The environment selector is changed by this PR: the current value of a non-secret variable can now be edited right in the quick peek. Controls involved: “Select environment”.
    - Evidence `ev_02324575a2ba` (pr.file): modified packages/hoppscotch-common/src/components/environments/Selector.vue (+29 -4) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/Selector.vue`. Trace span `d4695cf2eaa1234a`.
    - Evidence `ev_0dcb0dfafec0` (link.i18n_key): button "Select environment" equals the English value of i18n key environment.select; environments/Selector.vue uses it and is rendered by the Home page. Reference `el_30f24fa5dad0`. Trace span `94eb38b00902cf6c`.
    - Evidence `ev_3835cc72df59` (reason.impact): direct impact on Environment selector: 2 control(s), confidence 0.90. Reference `hoppscotch/hoppscotch#6645:direct:selector`. Trace span `97d17fc7bef79783`.
    - Evidence `ev_e3ca6af3a9f8` (pr.file): added packages/hoppscotch-common/src/components/environments/CurrentValueInput.vue (+55 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/CurrentValueInput.vue`. Trace span `5b9d4024aab6d2d9`.
    - Files: `packages/hoppscotch-common/src/components/environments/CurrentValueInput.vue`, `packages/hoppscotch-common/src/components/environments/Selector.vue`.
    - Requirements: `req_a3a6dbfa8d98`.
    - Flows: `Edit an environment variable`, `Send a request that uses a variable`.

[^4]: **Directly changed.** My and team environment lists (on Home page): Each environment row in the sidebar lists is changed by this PR: choosing one now opens the new editor instead of only selecting it. Confirmed (0.88).

    - Claim `clm_33660c99a592`: My and team environment lists: Each environment row in the sidebar lists is changed by this PR: choosing one now opens the new editor instead of only selecting it.
    - Evidence `ev_19d2e2dd495c` (pr.file): modified packages/hoppscotch-common/src/components/environments/my/index.vue (+4 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/my/index.vue`. Trace span `f98953d593431d6f`.
    - Evidence `ev_53a2aee52577` (reason.impact): direct impact on My and team environment lists: 4 control(s), confidence 0.88. Reference `hoppscotch/hoppscotch#6645:direct:lists`. Trace span `922d0009cf1b3831`.
    - Evidence `ev_56902424d433` (pr.file): modified packages/hoppscotch-common/src/components/environments/teams/Environment.vue (+8 -4) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/teams/Environment.vue`. Trace span `be706dde2abd332d`.
    - Evidence `ev_57953b3ce1f3` (pr.file): modified packages/hoppscotch-common/src/components/environments/my/Environment.vue (+8 -4) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/my/Environment.vue`. Trace span `5279d2227206c56a`.
    - Evidence `ev_7f62f2dfe65e` (pr.file): modified packages/hoppscotch-common/src/components/environments/teams/index.vue (+5 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/teams/index.vue`. Trace span `d4448092364e178a`.
    - Files: `packages/hoppscotch-common/src/components/environments/my/Environment.vue`, `packages/hoppscotch-common/src/components/environments/my/index.vue`, `packages/hoppscotch-common/src/components/environments/teams/Environment.vue`, `packages/hoppscotch-common/src/components/environments/teams/index.vue`.
    - Requirements: `req_a3a6dbfa8d98`.
    - Flows: `Edit an environment variable`.

[^5]: **Directly changed.** Secret variable values (on Home page): Secret variable values will change in the new editor: they stay masked behind a reveal toggle and can now be edited in place. Confirmed (0.87).

    - Claim `clm_a6ba1f5e9299`: Secret variable values: Secret variable values will change in the new editor: they stay masked behind a reveal toggle and can now be edited in place.
    - Evidence `ev_2ed0ea495242` (pr.file): added packages/hoppscotch-common/src/components/environments/Editor.vue (+387 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/Editor.vue`. Trace span `489e94042caa50e9`.
    - Evidence `ev_87571466a3b3` (reason.impact): direct impact on Secret variable values: 2 control(s), confidence 0.87. Reference `hoppscotch/hoppscotch#6645:direct:secrets`. Trace span `38a0e80af5da7534`.
    - Evidence `ev_b78081607646` (pr.file): modified packages/hoppscotch-common/src/services/secret-environment.service.ts (+55 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/secret-environment.service.ts`. Trace span `499873840657b139`.
    - Files: `packages/hoppscotch-common/src/components/environments/Editor.vue`, `packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Requirements: `req_a3a6dbfa8d98`.
    - Flows: `Edit an environment variable`.

[^6]: **Indirect, through shared code.** Sending a request (on Home page): Sending a request is probably affected: the value it uses for a variable is read from the current-value service this PR extends. Likely (0.66).

    - Claim `clm_3109a10166d2`: Sending a request: Sending a request is probably affected: the value it uses for a variable is read from the current-value service this PR extends.
    - Evidence `ev_1c82524d7820` (reason.impact): indirect impact on Request sending: 3 control(s), confidence 0.66, via the shared current-value service. Reference `hoppscotch/hoppscotch#6645:indirect:rest-send`. Trace span `edfc880166ec0d4b`.
    - Evidence `ev_46547990b66b` (pr.file): modified packages/hoppscotch-common/src/services/current-environment-value.service.ts (+50 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/current-environment-value.service.ts`. Trace span `65e0b8791e71b2a9`.
    - Files: `packages/hoppscotch-common/src/services/current-environment-value.service.ts`.
    - Requirements: `req_a3a6dbfa8d98`.
    - Flows: `Send a request that uses a variable`.

[^7]: **Indirect, through shared code.** Secret variables when a request is sent (on Home page): Secret variables are probably affected when a request is sent: their values are now also written through the secret-value service this PR extends. Likely (0.55).

    - Claim `clm_4d9692f2ba70`: Secret variables when a request is sent: Secret variables are probably affected when a request is sent: their values are now also written through the secret-value service this PR extends.
    - Evidence `ev_71a1251f6ec1` (reason.impact): indirect impact on Secret values when sending: 2 control(s), confidence 0.55, via the shared secret-value service. Reference `hoppscotch/hoppscotch#6645:indirect:secret-send`. Trace span `0d0f9c4633cb0bdb`.
    - Evidence `ev_b78081607646` (pr.file): modified packages/hoppscotch-common/src/services/secret-environment.service.ts (+55 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/secret-environment.service.ts`. Trace span `499873840657b139`.
    - Files: `packages/hoppscotch-common/src/services/secret-environment.service.ts`.
    - Requirements: `req_a3a6dbfa8d98`.
    - Flows: `Send a request that uses a variable`.

[^8]: **Indirect, through shared code.** Variables in a GraphQL request (on GraphQL page): Variables in a GraphQL request might be affected: they are read from the same current-value service. Possible (0.42).

    - Claim `clm_988a6cc9709b`: Variables in a GraphQL request: Variables in a GraphQL request might be affected: they are read from the same current-value service.
    - Evidence `ev_46547990b66b` (pr.file): modified packages/hoppscotch-common/src/services/current-environment-value.service.ts (+50 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/current-environment-value.service.ts`. Trace span `65e0b8791e71b2a9`.
    - Evidence `ev_b925244cdcb5` (reason.impact): indirect impact on GraphQL request sending: 2 control(s), confidence 0.42, via the shared current-value service. Reference `hoppscotch/hoppscotch#6645:indirect:graphql-send`. Trace span `636dfe6ea8d1d0ca`.
    - Files: `packages/hoppscotch-common/src/services/current-environment-value.service.ts`.

[^9]: **Indirect, through shared code.** Variables in the WebSocket address (on WebSocket page): Variables in the WebSocket address might be affected: they are read from the same current-value service; worth a quick look. Possible (0.38).

    - Claim `clm_f51e23a7fc0d`: Variables in the WebSocket address: Variables in the WebSocket address might be affected: they are read from the same current-value service; worth a quick look.
    - Evidence `ev_1e045387db70` (reason.impact): indirect impact on WebSocket address: 1 control(s), confidence 0.38, via the shared current-value service. Reference `hoppscotch/hoppscotch#6645:indirect:ws-url`. Trace span `ae89a50e9cabd435`.
    - Evidence `ev_46547990b66b` (pr.file): modified packages/hoppscotch-common/src/services/current-environment-value.service.ts (+50 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/current-environment-value.service.ts`. Trace span `65e0b8791e71b2a9`.
    - Files: `packages/hoppscotch-common/src/services/current-environment-value.service.ts`.

[^10]: **Indirect, through shared code.** New Collection dialog: The New Collection dialog might be affected: it opens from the same sidebar as the changed Environments tab; worth a quick look. Possible (0.33).

    - Claim `clm_ea1eac3c3ac6`: New Collection dialog: The New Collection dialog might be affected: it opens from the same sidebar as the changed Environments tab; worth a quick look.
    - Evidence `ev_9403433671b9` (reason.impact): indirect impact on New Collection dialog: 1 control(s), confidence 0.33, via the sidebar that hosts the Environments tab. Reference `hoppscotch/hoppscotch#6645:indirect:new-collection`. Trace span `6c9f7ca446106cfb`.
    - Evidence `ev_eb418800dd4a` (pr.file): modified packages/hoppscotch-common/src/components/environments/index.vue (+53 -26) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/index.vue`. Trace span `033d433a8e100431`.
    - Files: `packages/hoppscotch-common/src/components/environments/index.vue`.
    - Requirements: `req_22a2e8c41679`.

[^11]: **Requirements at risk.** Environments: User can manage environment variables used in requests. Likely.

    - Claim `clm_e1bc2e2d3932`: “User can manage environment variables used in requests.”: This requirement rests on a screen that is probably affected by this PR.
    - Evidence `ev_0dcb0dfafec0` (link.i18n_key): button "Select environment" equals the English value of i18n key environment.select; environments/Selector.vue uses it and is rendered by the Home page. Reference `el_30f24fa5dad0`. Trace span `94eb38b00902cf6c`.
    - Evidence `ev_19ab71e046d6` (reason.requirement): requirement req_a3a6dbfa8d98 is at_risk at likely. Reference `hoppscotch/hoppscotch#6645:req_a3a6dbfa8d98:at_risk`. Trace span `14ded46fc8fc0169`.
    - Evidence `ev_78b39389a408` (spec.quote): Environments let you store variables and reuse them across requests. Reference `req_a3a6dbfa8d98`. source_url `hxxps://docs.hoppscotch.io/documentation/features/environments`. Trace span `b56c102898812898`.
    - Evidence `ev_ea0cca1009e9` (link.i18n_key): button "Environments" equals the English value of i18n key tab.environments; environments/index.vue uses it and is rendered by the Home page. Reference `el_12bb7a04cc57`. Trace span `5ffd2e90600ddcfe`.
    - Requirements: `req_a3a6dbfa8d98`.

[^12]: **Requirements at risk.** Collections: User can create a collection to organize requests. Possible.

    - Claim `clm_b7b4ac013438`: “User can create a collection to organize requests.”: This requirement rests on a screen that might be affected by this PR; worth a quick look.
    - Evidence `ev_0354a072cec3` (reason.requirement): requirement req_22a2e8c41679 is at_risk at possible. Reference `hoppscotch/hoppscotch#6645:req_22a2e8c41679:at_risk`. Trace span `7a31f19d7afe9f15`.
    - Evidence `ev_e934eaf645b3` (spec.quote): Collections allow you to organize your requests into folders. Reference `req_22a2e8c41679`. source_url `hxxps://docs.hoppscotch.io/documentation/features/collections`. Trace span `e954d7d54e93578c`.
    - Requirements: `req_22a2e8c41679`.

[^13]: **What we could not determine, and who should look.** the whole PR: The PR is based on code 10 commits behind main, and two files it changes (the English text file and the component list) were also edited there, so its diff may not apply as shown.

    - Reason code `STALE_BASE`.
    - Evidence `ev_cceeb14d61fd` (pr.meta): PR #6645 head 64ba4ff75eff base ac145e7f7581 (fork safwanyp/hoppscotch): 14 of 14 files. Reference `hoppscotch/hoppscotch#6645@64ba4ff75eff7bef868b010947f50e9b6b326540`. Trace span `0b9aa1b95fe04d74`.
    - Evidence `ev_d2cfae393db5` (pr.compare): base is 10 commit(s) behind; 2 overlapping file(s). Reference `hoppscotch/hoppscotch#6645:ac145e7f7581...63273f8e599e`. Trace span `8c404759d4b04a80`.

[^14]: **What we could not determine, and who should look.** Everything that reads or writes environment values: Two shared services changed, and together they reach more than a quarter of the app's components. That is too wide to list reliably, so only the strongest connections are shown above.

    - Reason code `NEEDS_HUMAN`.
    - Evidence `ev_1c5a556668d4` (gap.unmapped_code): two changed services reach more than a quarter of the app's components within two shared steps; too broad to list. Reference `shared-services`. Trace span `532fe8c45b12519f`.
    - Evidence `ev_46547990b66b` (pr.file): modified packages/hoppscotch-common/src/services/current-environment-value.service.ts (+50 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/current-environment-value.service.ts`. Trace span `65e0b8791e71b2a9`.
    - Evidence `ev_b78081607646` (pr.file): modified packages/hoppscotch-common/src/services/secret-environment.service.ts (+55 -0) class service kind ADDITIVE_API. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/services/secret-environment.service.ts`. Trace span `499873840657b139`.

[^15]: **What we could not determine, and who should look.** The new sidebar editor and its reveal toggle: These controls are not in the live app yet, so no screen we observed shows them and nothing could be matched to a screen title.

    - Reason code `UNMAPPED_CHANGE`.
    - Evidence `ev_2ed0ea495242` (pr.file): added packages/hoppscotch-common/src/components/environments/Editor.vue (+387 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/Editor.vue`. Trace span `489e94042caa50e9`.
    - Evidence `ev_e191b3a89b13` (gap.unmapped_change): added file environments/Editor.vue renders no control that the crawl observed. Reference `packages/hoppscotch-common/src/components/environments/Editor.vue`. Trace span `c6ccf1875dacb05d`.

[^16]: **What we could not determine, and who should look.** Team environments: They only appear after signing in, and this report looked at the signed-out app only.

    - Reason code `ACCESS_RESTRICTED`.
    - Evidence `ev_56902424d433` (pr.file): modified packages/hoppscotch-common/src/components/environments/teams/Environment.vue (+8 -4) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/teams/Environment.vue`. Trace span `be706dde2abd332d`.
    - Evidence `ev_7f62f2dfe65e` (pr.file): modified packages/hoppscotch-common/src/components/environments/teams/index.vue (+5 -0) class component kind BODY. Reference `hoppscotch/hoppscotch#6645:packages/hoppscotch-common/src/components/environments/teams/index.vue`. Trace span `d4448092364e178a`.
    - Evidence `ev_80473c722b52` (gap.access_restricted): team environments render only for a signed-in user; the crawl was unauthenticated. Reference `packages/hoppscotch-common/src/components/environments/teams/index.vue`. Trace span `3110a05ee75f153f`.

### How the bands are set

- Confirmed: confidence >= 0.8.
- Likely: confidence 0.5 to 0.8.
- Possible: confidence 0.25 to 0.5.
- Unknown: confidence < 0.25.

The bands are rules of thumb applied to how each link was found. They have not been validated against outcomes, so read them as an ordering, not as probabilities.

Generated by blastradius <package version>.
