Fixtures for the link stage tests (tests/test_link_*.py).

- `hoppscotch_main.code_index.json.gz`: `code/index.json` written by the product's own `blastradius index` for
  hoppscotch/hoppscotch@63273f8 (run gate-code-1, 2026-10-12), gzipped unchanged. 1,273 files, 6,190 edges,
  21 routes, 2,198 i18n keys, 2,671 key uses, 41 literals.
- `requirements.json`, `spec_stats.json`: 19 requirements written by hand, because no extraction has been run
  (there is no API key on this machine). Rows tagged only `hand-written` are verbatim sentences from
  tests/fixtures/spec/llms_excerpt.txt (checked by tests/test_link_req_to_ui.py). Rows also tagged
  `quote-unverified` paraphrase docs.hoppscotch.io feature pages and their quotes were not checked against a
  fetch. Rows also tagged `synthetic` are probes for the absence model and make no claim about hoppscotch.io.
  `spec_stats.json` says `stale: true` (changelog tops at 2026.5.0, app is 2026.9.0, docs/RECON.md section 1).
- The crawl input is not copied: tests read tests/fixtures/crawl/live_hoppscotch_45 in place.

The heuristic judge and picker in tests/link_helpers.py stand in for the model: they test the machinery
(prompts, wrapping, shuffles, agreement arithmetic, budgets), not model quality. No number in these tests is a
measurement of an LLM.
