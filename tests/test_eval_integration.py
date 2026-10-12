"""The eval kit against the real reasoning pipeline on the demo PRs (cached `gh` responses, stored head
overlays, the real index and 45-action crawl in Neo4j, a fake model). The point is to meet real product
output: do the invariants stay quiet on a report the product itself wrote, does the judge read its real
evidence, does a cache-bypassed repetition leave the product's artifacts alone. Skips when Neo4j is
unreachable."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_reason_support import (
    FakeClient,
    FakeGh,
    World,
    link_plan,
    make_llm,
    overlay_fn_for,
)

from blastradius.evalkit.judge_offline import JudgeAnswer
from blastradius.evalkit.run import EvalOptions, run_eval
from blastradius.models import Link
from blastradius.reason import pipeline
from blastradius.reason.judge import JudgeOut
from blastradius.reason.narrate import ChecklistLine, Narration
from blastradius.report.model import Report

PR = 6645


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    w = World(tmp_path_factory.mktemp("eval-integration"))
    try:
        yield w
    finally:
        w.close()


def respond(kw: dict[str, Any]) -> Any:
    system = kw["system"]
    if "offline fact checker" in system:
        evidence_text = kw["messages"][0]["content"]
        return JudgeAnswer(
            f1_entities_in_evidence="Cited evidence" in evidence_text,
            f2_relation_not_stronger=True,
            f3_numbers_match=True,
            can_tell=True,
            quote=_first_phrase(evidence_text),
        )
    if "opening of a change-impact report" in system:
        return Narration(
            risk_line="Risk: HIGH. Touches Environments.",
            confidence_line="One item is confirmed.",
            checklist=[
                ChecklistLine(
                    text="On the Home page, click “Select environment”.",
                    why="the Home page is changed by this PR.",
                    item_ids=[],
                )
            ],
        )
    return JudgeOut(verdicts=[])


def _first_phrase(prompt: str) -> str:
    """A real substring of the first evidence entry the prompt shows."""
    body = prompt.split("E1:", 1)[1].split("\n", 2)[1] if "E1:" in prompt else ""
    return body.strip()[:40]


def produce_report(world: World, tmp_path: Path):
    client = FakeClient(respond)
    llm = make_llm(world.run, client, tmp_path)
    pipeline.run_pr(
        world.run, world.cfg, PR, world.store, llm, gh=FakeGh(), overlay_fn=overlay_fn_for(PR), render=False
    )
    return llm, client


def test_invariants_hold_on_a_report_the_product_wrote(world: World, tmp_path: Path):
    llm, _ = produce_report(world, tmp_path)
    res = run_eval(world.run, world.cfg, world.store, llm, ["invariants"])
    inv = res["evals"]["invariants"]
    assert inv["n_reports"] >= 1 and inv["all_ok"], json.dumps(inv["reports"], indent=1)[:3000]


def test_the_judge_reads_the_real_evidence_summaries_and_nothing_hidden(world: World, tmp_path: Path):
    llm, client = produce_report(world, tmp_path)
    before = len(client.calls)
    res = run_eval(
        world.run, world.cfg, world.store, llm, ["judge"], options=EvalOptions(judge_k=2, n_corruptions=8)
    )
    j = res["evals"]["judge"]
    assert j["status"] == "ok" and j["n_claims"] > 0 and j["seeded"]["produced"] > 0
    prompts = [
        c["messages"][0]["content"] for c in client.calls[before:] if "offline fact checker" in c["system"]
    ]
    assert prompts
    for p in prompts:
        assert "<untrusted_data" in p
        for hidden in ("confidence", "i18n_key", "link.", "ev_"):
            assert hidden not in p
    ledger = [json.loads(line) for line in (world.run.run_dir / "ledger.jsonl").read_text().splitlines()]
    assert {r["purpose"] for r in ledger} >= {"offline_judge", "narrate_report"}


def test_synthetic_recall_over_the_real_graph_and_links(world: World, tmp_path: Path):
    plan = link_plan(world.index)
    links = [
        Link(kind="IMPLEMENTED_BY", src=el, dst=f, confidence=c, method=m, evidence_id="ev_x")
        for el, (f, c, m) in sorted(plan.items())
    ]
    world.run.path("links", "links.jsonl").write_text(
        "".join(link.model_dump_json() + "\n" for link in links)
    )
    res = run_eval(world.run, world.cfg, world.store, None, ["recall"])
    r = res["evals"]["recall"]
    assert r["status"] == "ok" and r["circular"] is True and r["n_elements"] >= 4
    assert r["miss_count"] == 0, r["misses"]
    assert r["recall"]["k"] == r["recall"]["n"]


def test_a_cache_bypassed_repetition_recomputes_and_leaves_product_artifacts_alone(
    world: World, tmp_path: Path
):
    llm, client = produce_report(world, tmp_path)
    product = world.run.run_dir / "reason" / str(PR) / "report.json"
    before = hashlib.sha256(product.read_bytes()).hexdigest()
    narrates_before = sum(1 for c in client.calls if "opening of a change-impact report" in c["system"])

    def runner(scratch, cfg, store, bypass_llm, pr, i):
        pipeline.run_pr(
            scratch, cfg, pr, store, bypass_llm, gh=FakeGh(), overlay_fn=overlay_fn_for(pr), render=False
        )
        return json.loads((scratch.run_dir / "reason" / str(pr) / "report.json").read_text())

    res = run_eval(
        world.run,
        world.cfg,
        world.store,
        llm,
        ["stability"],
        options=EvalOptions(stability_n=3, pr_numbers=(PR,), runner=runner),
    )
    st = res["evals"]["stability"]
    v = st["per_pr"][str(PR)]
    assert st["status"] == "ok" and v["n_completed"] == 3 and v["invariants_clean"] == 3
    assert v["affected_set_jaccard"]["mean"] == 1.0 and v["unstable_items"] == []
    narrates_after = sum(1 for c in client.calls if "opening of a change-impact report" in c["system"])
    assert narrates_after - narrates_before == 3  # three live calls: the cache was bypassed
    assert hashlib.sha256(product.read_bytes()).hexdigest() == before
    assert st["cost_usd"] > 0 and "x 100 =" in st["n100"]["arithmetic"]
    assert Report.model_validate_json(product.read_text()).pr_number == PR
