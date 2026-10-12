Fixtures for the evalkit tests (tests/test_eval_*.py). None of them is product output of a model run.

- `oracle_labels_hoppscotch_2026-10-12.json`: the source-map oracle's labels from ONE live harvest of
  https://hoppscotch.io on 2026-10-12 (3 page visits: `/`, `/graphql`, `/settings`, plus one click on the
  Environments sidebar tab; user agent `blastradius/0.1.0`; robots.txt allows `/`). 208 labels over 4 UI
  states, 124 distinct elements. The `provenance` block pins the entry chunk by ETag and sha256, the drift
  against `tests/fixtures/link/hoppscotch_main.code_index.json.gz` (11 of 104 SFCs of the entry chunk deviate:
  5 differ, 6 absent from main, the RECON.md number), and the oracle's own self-checks. It is a snapshot: the
  deployed site redeploys, so these labels are not expected to be reproducible later. Tests use it as data.
  It is only valid for a target that publishes its source maps (D23) and labels only UI in the deployed build.
- `links_crawl45_index63273f8.jsonl.gz`: `links/links.jsonl` as written by the product's own link stage
  (deterministic cascade, no LLM: `llm=None`) for tests/fixtures/crawl/live_hoppscotch_45 and the code index
  above, 602 IMPLEMENTED_BY links. Frozen so that the scoring tests do not move when the linker is improved:
  they test the evaluation machinery on real data, not the linker.
- `stability/6645/rep_0..4.json`: five SYNTHETIC repetitions derived from the hand-authored
  tests/fixtures/report/sample_report_6645.json: rep_0 and rep_1 are the sample; rep_2 drops the indirect item
  `itm_569ebd490a4d` and moves `itm_0f38c031a1e5` from possible to unknown; rep_3 drops both; rep_4 drops the
  first. They exercise the stability analysis (fixture mode) and say nothing about the model's variance.
