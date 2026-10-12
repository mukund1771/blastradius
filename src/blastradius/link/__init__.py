"""Linking: UI -> code, requirement -> UI, absence gaps and flows (D07, D08, D09, D20, D25).

Entry points (resolved lazily by cli.py):
  link.link:run_link(run, cfg, store, llm)               links/links.jsonl, links/link_state.json
  link.gaps:run_gaps(run, cfg, store, llm, recrawl=...)  links/gaps.jsonl
  link.flows:run_flows(run, cfg, store)                  links/flows.jsonl

link/ must never import evalkit/ (tests/test_link_run.py enforces it).
"""
