"""Stage 7-8: from a pull request to a blast-radius report a QA lead can act on (D07, D17, D18, D21, D25, D29).

  pr          the PR through `gh`: pinned head, classified files, stale-base check, locale diff
  change      what a diff says about a file (change kind, touched symbols) and about a locale file
  retrieve    templated Cypher: changed files -> screens and controls, tiers, routes with path confidence
  grade       the CRAG-inspired grader: verdict, corrective actions, caps (never raises a confidence)
  judge       the demote-only residue judge for ambiguous subgraphs
  assemble    graded facts -> Report items, requirements, could-not-determine, risk level
  narrate     one structured model call for the prose, with a deterministic template fallback
  verify      the deterministic verifier (citations, verbs per band, completeness, hygiene, risk)
  pipeline    `run_pr(run, cfg, pr_number, store, llm)`; writes reason/<pr>/*
  risk, words, facts, thresholds   the shared rule, vocabulary, data shapes and registered numbers
"""
