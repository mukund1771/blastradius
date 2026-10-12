"""The evaluation machinery on frozen real inputs (no network, no browser, no LLM): the oracle labels of one
live harvest, the real 45-action crawl, the linker's frozen links and the real code index. The numbers
asserted are properties of these fixtures (see tests/fixtures/eval/PROVENANCE.md), not of the live site or
of today's linker."""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

from test_eval_support import make_run

from blastradius.evalkit.artifacts import indexed_paths, read_jsonl
from blastradius.evalkit.oracle_score import Label, metrics, score_units
from blastradius.evalkit.run import EvalOptions, run_eval
from blastradius.evalkit.stability import run_stability
from blastradius.models import CodeIndex, Link, ScreenState
from blastradius.targets import TargetConfig

FIX = Path(__file__).parent / "fixtures"
EVAL = FIX / "eval"
CRAWL = FIX / "crawl" / "live_hoppscotch_45"
INDEX_GZ = FIX / "link" / "hoppscotch_main.code_index.json.gz"
LABELS = EVAL / "oracle_labels_hoppscotch_2026-10-12.json"


def _labels() -> list[Label]:
    doc = json.loads(LABELS.read_text(encoding="utf-8"))
    return [
        Label(
            url_pattern=r["url_pattern"],
            active_tabs=tuple(r["active_tabs"]),
            dialog_title=r["dialog_title"],
            role=r["role"],
            name=r["name"],
            ordinal=r["ordinal"],
            oracle_file=r["oracle_file"],
            owner_hops=r["owner_hops"],
            owner_chain=tuple(r["owner_chain"]),
        )
        for r in doc["labels"]
    ]


def _links() -> list[Link]:
    with gzip.open(EVAL / "links_crawl45_index63273f8.jsonl.gz", "rt", encoding="utf-8") as fh:
        return [Link.model_validate_json(line) for line in fh if line.strip()]


def _index() -> CodeIndex:
    with gzip.open(INDEX_GZ, "rt", encoding="utf-8") as fh:
        return CodeIndex.model_validate_json(fh.read())


def _screens() -> list[ScreenState]:
    return read_jsonl(CRAWL / "screens.jsonl", ScreenState)[0]


def test_frozen_labels_cover_the_states_and_name_repo_vue_files():
    labels = _labels()
    assert len(labels) == 208
    assert {lbl.url_pattern for lbl in labels} == {"/", "/graphql", "/settings"}
    assert all(lbl.oracle_file.endswith(".vue") and lbl.oracle_file.startswith("packages/") for lbl in labels)
    assert sum(1 for lbl in labels if lbl.owner_hops >= 1) / len(labels) > 0.8  # RECON: 82% need wrapper hops


def test_scoring_the_frozen_links_against_the_frozen_labels_reproduces_the_live_run():
    results = score_units(_labels(), _screens(), _links(), indexed_paths(_index()))
    m = metrics(results, 208)
    assert m["units"] == 124 and m["excluded"] == {"not_in_crawl": 44, "file_not_on_main": 0}
    assert m["scored"] == 80
    assert (m["linker_coverage"]["k"], m["linker_coverage"]["n"]) == (59, 80)
    assert (m["top1_precision_of_covered"]["k"], m["top1_precision_of_covered"]["n"]) == (24, 59)
    assert (m["hit_at_3_of_covered"]["k"], m["hit_at_3_of_covered"]["n"]) == (39, 59)
    assert set(m["by_top1_method"]) == {"i18n_key", "none"}
    assert m["join_levels"] == {"state": 79, "pattern": 1}


def test_the_frozen_labels_replay_through_run_eval_as_hand_labels(tmp_path: Path):
    run = make_run(tmp_path)
    crawl = run.path("crawl", "x").parent
    shutil.copy(CRAWL / "screens.jsonl", crawl / "screens.jsonl")
    run.path("code", "index.json").write_text(_index().model_dump_json(), encoding="utf-8")
    run.path("links", "links.jsonl").write_text("".join(link.model_dump_json() + "\n" for link in _links()))
    rows = json.loads(LABELS.read_text(encoding="utf-8"))["labels"]
    run.path("eval", "hand_labels.json").write_text(json.dumps(rows), encoding="utf-8")
    res = run_eval(
        run, TargetConfig.load("hoppscotch"), None, None, ["oracle"], options=EvalOptions(live_oracle=False)
    )
    out = res["evals"]["oracle"]
    assert out["status"] == "fallback_hand_labels" and out["scoring"]["scored"] == 80
    md = (run.run_dir / "eval" / "eval.md").read_text(encoding="utf-8")
    assert "top-1 precision (of covered): 24/59" in md and "exists only because this target publishes" in md


def test_stability_fixture_reports_give_the_known_agreement(tmp_path: Path):
    run = make_run(tmp_path)
    out = run_stability(
        run,
        TargetConfig.load("hoppscotch"),
        None,
        None,
        n=5,
        min_agreement=0.6,
        demoted_confidence=0.49,
        fixture_dir=EVAL / "stability",
    )
    v = out["per_pr"]["6645"]
    assert out["status"] == "ok" and v["n_completed"] == 5 and v["n_requested"] == 5
    assert v["unstable_items"] == ["itm_569ebd490a4d"]  # 2 of 5 runs
    rows = {r["id"]: r for r in v["items"]}
    assert rows["itm_0f38c031a1e5"]["agreement"] == 0.8 and rows["itm_0f38c031a1e5"]["band_agreement"] == 0.75
    assert v["always_present"] == 8
    view = json.loads((run.run_dir / "eval" / "stable_view.json").read_text())["per_pr"]["6645"]
    demoted = [i for i in view["items"] if i["demoted"]]
    assert [d["item"]["id"] for d in demoted] == ["itm_569ebd490a4d"]
    assert demoted[0]["item"]["band"] == "possible" and demoted[0]["original_band"] == "possible"
    assert demoted[0]["item"]["confidence"] <= 0.49 and demoted[0]["changed"] is False  # already possible
