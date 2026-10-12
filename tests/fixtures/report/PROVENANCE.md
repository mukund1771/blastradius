FIXTURE for the report rendering and lint tests (tests/test_report_render.py, tests/test_report_lint.py).
It is NOT a product output and nothing in it was measured.

- `sample_report_6645.json`: a `Report` for hoppscotch PR #6645, written by hand. Grounded in real inputs: the
  PR's 14 changed files, title, base/head/indexed shas and "10 commits behind main" come from
  tests/fixtures/reason (cached `gh` responses); screen and control names ("Environments", "Select environment",
  "New Collection", the Home, GraphQL and WebSocket pages) come from tests/fixtures/crawl/live_hoppscotch_45;
  the two requirement quotes come from tests/fixtures/link/requirements.json (both tagged `quote-unverified`
  there). Every score, band, evidence summary and could-not-determine row is authored, not computed. The
  report states this itself in its first "did not look at" line.
- `sample_evidence_6645.json`: ledger rows for every evidence id the report cites. The ids are content hashes
  made the way `EvidenceLedger.add` makes them (`stable_id("ev", kind, ref, summary)`), so `tests/report_helpers.py`
  can load them into a real in-memory ledger and the ids line up. Trace span ids are arbitrary.
