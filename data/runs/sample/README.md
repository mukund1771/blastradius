# Sample run `crawl-gate-150`

A reviewer can read this directory without an API key, Docker, or a fresh crawl. It is a copy of the
Hoppscotch run from 2026-10-12, limited to files that do not contain secret-shaped strings.

Included:

- `trace.jsonl`, `ledger.jsonl`, `failures.jsonl`, `manifest.json`
- `crawl/summary.json`, `spec/stats.json`, `graph/load.json`
- `eval/results.json` and `eval/eval.md` (invariants, circular recall, source-map oracle)
- `links/gaps.jsonl`: every Gap (absence with recorded effort). 36 requirements have no UI link: 21 `NO_UI_EVIDENCE`
  (not found within budget, spec may be stale), 13 `ACCESS_RESTRICTED` (needs a login), 2 `ACCESS_RESTRICTED`
  (external link)
- `reason/6645`, `reason/6641`, `reason/6658` report markdown and JSON

The browsable write-up of PR #6645 is also at
[docs/sample-report-pr-6645.md](../../../docs/sample-report-pr-6645.md).

Left out on purpose: `evidence.jsonl` and the code index. They are large and contain the app's own
"Bearer " auth-scheme text. They stay in the local run directory, which is gitignored.
