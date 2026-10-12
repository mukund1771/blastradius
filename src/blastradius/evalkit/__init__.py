"""Evaluation kit (plan section 9; D21, D22, D23, D26; cases 53, 54, 63, 64, 71, 72).

Entry point (resolved lazily by cli.py): `evalkit.run:run_eval(run, cfg, store, llm, which)`.

Isolation rule (enforced by tests/test_eval_import_lint.py in both directions): nothing under
`blastradius.link`, `blastradius.reason` or `blastradius.graph` imports this package, and this package never
imports a link module: links are read from `links/links.jsonl` and from the graph store, so the evaluation
cannot share code with the thing it evaluates.
"""
