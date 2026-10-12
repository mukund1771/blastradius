"""`blastradius link`: UI -> code and requirement -> UI links, written to links/links.jsonl and the graph.

    run_link(run, cfg, store, llm) -> dict

Reads crawl/screens.jsonl, code/index.json and spec/requirements.json (each stage runs when its inputs exist and
is reported `skipped` when they do not), runs `ui_to_code.link_ui_to_code` and `req_to_ui.link_req_to_ui`,
and writes:

  links/links.jsonl       every Link, sorted (kind, src, dst, method): identical across processes
  links/link_state.json   what link.gaps needs and the Link rows cannot say: the unresolved UI elements with
                          their reason, and per requirement whether the judge ran (so "the judge did not run"
                          is never reported as "the UI is absent"); and what it was derived from (target,
                          crawl bundle_hash, a fingerprint of the crawl's screens, the code snapshot), which
                          link.gaps compares with the current artifacts and refuses to trust when they differ
  store.write_links       idempotent MERGE keyed by (target, id); skipped when `store` is None

The returned dict is the stage's measurement: counts, per-method confidence histograms, LLM calls made and
avoided, unresolved reasons and wall-clock seconds. Nothing in it is estimated.
"""

from __future__ import annotations

import time
from typing import Any

from ..llm import LLM
from ..models import Link
from ..run import RunContext
from ..targets import TargetConfig
from .budget import ledger_delta
from .inputs import LinkInputs, crawl_fingerprint, load_inputs, write_json, write_jsonl
from .req_to_ui import ReqLinkResult, link_req_to_ui
from .ui_to_code import UiLinkResult, link_ui_to_code


def _state(cfg: TargetConfig, inputs: LinkInputs, ui: UiLinkResult | None,
           req: ReqLinkResult | None) -> dict[str, Any]:
    return {
        "target": cfg.name,
        "bundle_hash": inputs.summary.bundle_hash if inputs.summary else None,
        "crawl_sha": crawl_fingerprint(inputs.screens),
        "screens": len(inputs.screens),
        "code_snapshot": inputs.index.snapshot_id if inputs.index else None,
        "unresolved": [
            {"element_id": u.element_id, "obs_ids": u.obs_ids, "screen_ids": u.screen_ids, "name": u.name,
             "role": u.role, "reason": u.reason, "detail": u.detail}
            for u in (ui.unresolved if ui else [])
        ] if ui is not None else None,
        "requirements": {
            rid: {"status": r.status, "reason": r.reason, "candidates": r.candidates, "links": len(r.links)}
            for rid, r in sorted((req.results if req else {}).items())
        },
    }


def run_link(run: RunContext, cfg: TargetConfig, store: Any, llm: LLM | None) -> dict[str, Any]:
    t0 = time.monotonic()
    before = run.ledger.summary()
    inputs = load_inputs(run, cfg)
    out: dict[str, Any] = {"target": cfg.name, "run_id": run.run_id, "inputs": {
        "screens": len(inputs.screens), "requirements": len(inputs.requirements),
        "code_files": len(inputs.index.files) if inputs.index else 0, **inputs.notes}}
    ui: UiLinkResult | None = None
    req: ReqLinkResult | None = None
    if inputs.screens and inputs.index is not None:
        ui = link_ui_to_code(run, cfg, inputs.index, inputs.screens, llm)
        out["ui_to_code"] = ui.stats
    else:
        out["ui_to_code"] = {"status": "skipped", "reason": "needs crawl/screens.jsonl and code/index.json"}
    if inputs.screens and inputs.requirements:
        req = link_req_to_ui(run, llm, inputs.requirements, inputs.screens, cfg.name)
        out["req_to_ui"] = req.stats
    else:
        out["req_to_ui"] = {"status": "skipped", "reason": "needs crawl/screens.jsonl and spec/requirements.json"}

    links: list[Link] = sorted([*(ui.links if ui else []), *(req.links if req else [])],
                               key=lambda l: (l.kind, l.src, l.dst, l.method, -l.confidence, l.evidence_id))
    write_jsonl(run.path("links", "links.jsonl"), list(links))
    write_json(run.path("links", "link_state.json"), _state(cfg, inputs, ui, req))
    out["links"] = {"total": len(links), "implemented_by": sum(1 for l in links if l.kind == "IMPLEMENTED_BY"),
                    "realized_by": sum(1 for l in links if l.kind == "REALIZED_BY")}
    if store is not None:
        out["graph"] = store.write_links(links, run.run_id, target=cfg.name)
    out["llm"] = ledger_delta(run, before) | {
        "elements_resolved_without_llm": (ui.stats.get("elements_resolved_without_llm", 0) if ui else 0),
    }
    out["wall_clock_s"] = round(time.monotonic() - t0, 3)
    return out
