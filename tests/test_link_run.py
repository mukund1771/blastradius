"""Stage entry points end to end (`run_link`, `run_gaps`, `run_flows`): artifacts on disk, idempotence,
degraded modes, cross-process determinism, and the import rule (link/ never imports evalkit/)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from link_helpers import (
    FakeClient,
    RaisingClient,
    RecordingStore,
    cfg,
    combined_responder,
    make_llm,
    make_run,
    read_jsonl_dicts,
)

from blastradius.link.flows import run_flows
from blastradius.link.gaps import run_gaps
from blastradius.link.link import run_link
from blastradius.models import Gap, Link

REPO = Path(__file__).resolve().parents[1]


def test_run_link_writes_artifacts_and_reports_what_it_did(tmp_path: Path):
    run = make_run(tmp_path)
    client = FakeClient(combined_responder())
    llm = make_llm(run, client, tmp_path)
    store = RecordingStore()
    out = run_link(run, cfg(), store, llm)

    rows = read_jsonl_dicts(run.run_dir / "links" / "links.jsonl")
    links = [Link.model_validate(r) for r in rows]
    assert len(links) == out["links"]["total"] == len(store.links) > 600
    assert links == sorted(links, key=lambda l: (l.kind, l.src, l.dst, l.method))
    assert {l.kind for l in links} == {"IMPLEMENTED_BY", "REALIZED_BY"}
    assert all(run.evidence.exists(l.evidence_id) for l in links)
    assert all((l.judge is not None) == (l.method in ("llm_pick", "bm25_judge")) for l in links)
    assert all(0.0 < l.confidence <= 1.0 for l in links)

    ui, req = out["ui_to_code"], out["req_to_ui"]
    assert ui["links_by_method"]["i18n_key"] > 0 and ui["linked_pct_named"] >= 70
    assert set(ui["confidence_histogram"]["i18n_key"]) <= {"0.15", "0.23", "0.30", "0.45", "0.90"}
    assert ui["unresolved_reasons"]["no_text"] == 159 and "wall_clock_s" in ui
    assert req["status"]["judged"] == 12 and req["confidence_histogram"]["bm25_judge"]
    assert out["llm"]["calls"] == len(client.calls) == ui["llm_pick_calls"] + req["judge_calls"]
    assert out["llm"]["elements_resolved_without_llm"] == ui["elements_resolved_without_llm"] > 0
    assert out["graph"] == {"implemented_by": out["links"]["implemented_by"]} and out["wall_clock_s"] >= 0

    state = json.loads((run.run_dir / "links" / "link_state.json").read_text())
    assert state["bundle_hash"] == "c81a0323d1f5b4f5" and state["target"] == "hoppscotch"
    # what gaps compares with the current artifacts before it trusts the state (case 48)
    assert state["screens"] == 25 and len(state["crawl_sha"]) == 16
    assert state["code_snapshot"] == "63273f8e599e73f0fb38c0739314e8d46b6bc83d"
    assert len(state["unresolved"]) == sum(ui["unresolved_reasons"].values())
    assert {r["status"] for r in state["requirements"].values()} == {"judged", "skipped", "no_candidates"}


def test_run_link_twice_is_byte_identical_and_the_second_pass_is_all_cache_hits(tmp_path: Path):
    run = make_run(tmp_path)
    client = FakeClient(combined_responder())
    llm = make_llm(run, client, tmp_path)
    first = run_link(run, cfg(), None, llm)
    calls_after_first = len(client.calls)
    digest = {n: (run.run_dir / "links" / n).read_bytes() for n in ("links.jsonl", "link_state.json")}
    second = run_link(run, cfg(), None, llm)
    assert len(client.calls) == calls_after_first                       # nothing re-asked
    assert second["llm"]["calls"] == second["llm"]["cache_hits"] == first["llm"]["calls"] > 0
    assert second["llm"]["spent_usd"] == 0.0 and second["llm"]["avoided_usd"] > 0
    assert {n: (run.run_dir / "links" / n).read_bytes() for n in digest} == digest


def test_missing_inputs_are_reported_and_recorded_not_hidden(tmp_path: Path):
    run = make_run(tmp_path, spec=False, code=False)
    out = run_link(run, cfg(), None, None)
    assert out["ui_to_code"]["status"] == "skipped" and out["req_to_ui"]["status"] == "skipped"
    codes = [f["code"] for f in run.failures.items]
    assert "CONFIG_INVALID" in codes and "SPEC_FETCH_FAILED" in codes
    assert out["links"]["total"] == 0


def test_without_an_api_key_the_deterministic_links_survive_and_absence_is_not_claimed(tmp_path: Path):
    run = make_run(tmp_path)
    client = RaisingClient()
    llm = make_llm(run, client, tmp_path)
    out = run_link(run, cfg(), None, llm)
    assert out["links"]["implemented_by"] > 600 and out["links"]["realized_by"] == 0
    assert out["ui_to_code"]["unresolved_reasons"].get("llm_budget", 0) > 0
    assert out["req_to_ui"]["status"]["unjudged"] == 12
    assert client.calls == 6                                      # each stage gives up after 3 failed attempts
    gaps = run_gaps(run, cfg(), None, llm)
    rows = [g for g in read_jsonl_dicts(run.run_dir / "links" / "gaps.jsonl") if g["kind"] == "NO_UI_EVIDENCE"]
    # 12 judgeable requirements: 10 had BM25 candidates but no judge ran (JUDGE_UNAVAILABLE, no claim), 1 had
    # none at all (so "no UI text overlaps it" needs no judge), and 1 is not known to be UI observable
    unavailable = [g for g in rows if g["reason"] == "JUDGE_UNAVAILABLE"]
    searched = [g for g in rows if g["reason"] != "JUDGE_UNAVAILABLE"]
    assert len(rows) == 11 and len(unavailable) == 10 and len(searched) == 1
    assert {g["confidence"] for g in unavailable} == {0.0} and all(g["confidence"] > 0 for g in searched)
    assert gaps["requirements_not_gapped_unclassified"] == 1
    assert gaps["by_reason"]["NO_UI_EVIDENCE/JUDGE_UNAVAILABLE"] == 10


def test_all_three_stages_in_sequence_write_valid_rows(tmp_path: Path):
    run = make_run(tmp_path)
    llm = make_llm(run, FakeClient(combined_responder()), tmp_path)
    store = RecordingStore()
    run_link(run, cfg(), store, llm)
    run_gaps(run, cfg(), store, llm)
    flows = run_flows(run, cfg(), store)
    gaps = [Gap.model_validate(r) for r in read_jsonl_dicts(run.run_dir / "links" / "gaps.jsonl")]
    assert len(gaps) == len({(g.id, g.bundle_hash) for g in gaps})
    assert all(run.evidence.exists(g.evidence_id) for g in gaps if g.evidence_id)
    assert all(g.confidence == 0.0 for g in gaps if g.kind.value != "NO_UI_EVIDENCE")
    assert all(0.0 <= g.confidence <= 0.6 for g in gaps)
    assert flows and len(store.flows) == len(flows) and store.targets[-1] == "hoppscotch"


_SNIPPET = r"""
import hashlib, sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests")
from link_helpers import FakeClient, RecordingStore, cfg, combined_responder, make_llm, make_run
from blastradius.link.flows import run_flows
from blastradius.link.gaps import run_gaps
from blastradius.link.link import run_link
tmp = Path(tempfile.mkdtemp())
run = make_run(tmp)
llm = make_llm(run, FakeClient(combined_responder()), tmp)
store = RecordingStore()
run_link(run, cfg(), store, llm)
run_gaps(run, cfg(), store, llm)
run_flows(run, cfg(), store)
for name in ("links.jsonl", "gaps.jsonl", "flows.jsonl", "link_state.json"):
    print(name, hashlib.sha256((run.run_dir / "links" / name).read_bytes()).hexdigest())
"""


def _digests(seed: int) -> dict[str, str]:
    env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": str(REPO / "src")}
    out = subprocess.run([sys.executable, "-c", _SNIPPET], check=False, cwd=REPO, env=env, capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-1500:]
    return dict(line.split() for line in out.stdout.strip().splitlines())


def test_artifacts_are_identical_across_hash_seeds():
    runs = [_digests(seed) for seed in (0, 1, 7, 42)]
    assert all(r == runs[0] for r in runs), runs
    assert set(runs[0]) == {"links.jsonl", "gaps.jsonl", "flows.jsonl", "link_state.json"}


def test_link_never_imports_evalkit():
    """docs/ARCHITECTURE.md: the oracle must stay independent of the product's links."""
    pattern = re.compile(r"^\s*(?:from|import)\s+[\w.]*evalkit", re.MULTILINE)
    for path in (REPO / "src" / "blastradius" / "link").glob("*.py"):
        assert not pattern.search(path.read_text()), path
    assert hashlib.sha256(b"x").hexdigest()


def test_corrupt_artifacts_are_failures_and_the_stage_goes_on_without_them(tmp_path: Path):
    """(i) A truncated code index or requirement list used to raise out of `link`."""
    run = make_run(tmp_path)
    for rel in ("code/index.json", "spec/requirements.json", "spec/stats.json"):
        path = run.run_dir / rel
        path.write_text(path.read_text()[: len(path.read_text()) // 3])
    out = run_link(run, cfg(), None, None)
    assert out["ui_to_code"]["status"] == "skipped" and out["req_to_ui"]["status"] == "skipped"
    named = {f["context"]["artifact"] for f in run.failures.items if f["code"] == "CONFIG_INVALID"
             and "ignored" in f["message"]}
    assert named == {"code/index.json", "spec/requirements.json", "spec/stats.json"}
    assert out["inputs"]["index_invalid"] == 1 and out["inputs"]["requirements_invalid"] == 1
    assert (run.run_dir / "links" / "links.jsonl").exists()


def test_a_requirement_list_of_the_wrong_shape_is_a_failure_not_a_crash(tmp_path: Path):
    run = make_run(tmp_path)
    (run.run_dir / "spec" / "requirements.json").write_text('{"not": "a list"}')
    out = run_link(run, cfg(), None, None)
    assert out["inputs"]["requirements_invalid"] == 1 and out["inputs"]["requirements"] == 0
    assert any("not a list" in f["message"] for f in run.failures.items)
