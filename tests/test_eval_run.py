"""evalkit.run: orchestration, the files it writes, and the honesty rules (every rate carries its n)."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from test_eval_support import (
    FakeClient,
    build_chunk,
    claim,
    evidence,
    item,
    make_llm,
    make_run,
    report,
    write_evidence,
    write_report,
)

from blastradius.evalkit import oracle_sourcemap as o
from blastradius.evalkit import run as eval_run
from blastradius.evalkit.judge_offline import JudgeAnswer
from blastradius.evalkit.run import ALL, EvalOptions, run_eval
from blastradius.evalkit.sourcemap import SourceMap, Text
from blastradius.models import Link
from blastradius.targets import TargetConfig

FIX = Path(__file__).parent / "fixtures"
CRAWL = FIX / "crawl" / "live_hoppscotch_45"
INDEX = FIX / "link" / "hoppscotch_main.code_index.json.gz"
SRC = "packages/hoppscotch-common/src/components/"
HOME_TABS = ("Collections", "Parameters", "Untitled")


def cfg() -> TargetConfig:
    return TargetConfig.load("hoppscotch")


def link(src: str, dst: str, conf: float, method: str) -> Link:
    return Link(kind="IMPLEMENTED_BY", src=src, dst=dst, confidence=conf, method=method, evidence_id="ev_1")


def seeded_run(tmp_path: Path, *, with_links: bool = True):
    """A run holding the real 45-action crawl and the real code index, plus three hand-made links."""
    run = make_run(tmp_path)
    crawl = run.path("crawl", "x").parent
    for name in ("screens.jsonl", "transitions.jsonl", "summary.json"):
        shutil.copy(CRAWL / name, crawl / name)
    with gzip.open(INDEX, "rt", encoding="utf-8") as fh:
        run.path("code", "index.json").write_text(fh.read(), encoding="utf-8")
    if with_links:
        rows = [
            link("el_918f4c9b52dc", SRC + "app/Header.vue", 0.9, "i18n_key"),  # Search and commands
            link("el_b8edbefe46bf", SRC + "app/Footer.vue", 0.45, "llm_pick"),
        ]  # Hoppscotch (wrong file)
        run.path("links", "links.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in rows))
    return run


def synthetic_harvest() -> o.Harvest:
    """What a live harvest would return for three crawl elements, a new one and one with no file on main."""
    ui = "../../../../node_modules/.pnpm/@hoppscotch+ui@0.2.6/node_modules/@hoppscotch/ui/dist/index.js"
    code = {
        "btn": (
            "const B={__name:'Button',setup(e){return ()=>h('button',e.label+"
            "'library-button-implementation')}};"
        ),
        "hdr": (
            "const H={__name:'Header',setup(e){const s=useSearch();return ()=>"
            "h(B,{label:'header-owner-text'})}};"
        ),
        "req": (
            "const R={__name:'Request',setup(e){const u=useUrl();return ()=>"
            "h('input',{value:u.value+'request'})}};"
        ),
        "gone": (
            "const G={__name:'Gone',setup(e){const g=useGone();return ()=>"
            "h('b',g.value+'gone-component-text')}};"
        ),
    }
    mods = [
        (ui, code["btn"]),
        ("../../../hoppscotch-common/src/components/app/Header.vue", code["hdr"]),
        ("../../../hoppscotch-common/src/components/http/Request.vue", code["req"]),
        ("../../../hoppscotch-common/src/components/Gone.vue", code["gone"]),
    ]
    text, data = build_chunk(mods)
    data["file"] = "index-test.js"
    chunk = o.LoadedChunk(
        "https://hoppscotch.io/assets/index-test.js",
        Text(text),
        SourceMap(data),
        {
            "url": "https://hoppscotch.io/assets/index-test.js",
            "etag": 'W/"t"',
            "sha256": "ab" * 32,
            "bytes": len(text),
        },
    )

    def setup(name: str) -> str:
        return code[name][code[name].index("setup(") : code[name].rindex("}};") + 1]

    types = {
        "t0": o.ComponentType("Button", (("setup", setup("btn")),)),
        "t1": o.ComponentType("Header", (("setup", setup("hdr")),)),
        "t2": o.ComponentType("Request", (("setup", setup("req")),)),
        "t3": o.ComponentType("Gone", (("setup", setup("gone")),)),
    }
    els = [
        o.SeenElement("button", "Search and commands", 0, "x1", True, 0, ("t0", "t1")),
        o.SeenElement("button", "Hoppscotch", 0, "x2", True, 0, ("t0", "t1")),
        o.SeenElement("textbox", "Enter a URL or paste a cURL command", 0, "x3", True, 0, ("t2",)),
        o.SeenElement("button", "A brand new thing", 0, "x4", True, 0, ("t1",)),
        o.SeenElement("button", "Gone control", 0, "x5", True, 0, ("t3",)),
    ]
    state = o.State(
        "/ [Collections, Parameters, Untitled]", "https://hoppscotch.io/", "/", HOME_TABS, None, els, types
    )
    return o.Harvest(states=[state], chunks=[chunk], visits=1)


def harvest_fn(*_a: Any, **_k: Any) -> o.Harvest:
    return synthetic_harvest()


# ------------------------------------------------------------------------------------------ oracle
def test_oracle_end_to_end_on_a_synthetic_harvest_and_the_real_crawl(tmp_path: Path):
    run = seeded_run(tmp_path)
    res = run_eval(run, cfg(), None, None, ["oracle"], options=EvalOptions(harvest_fn=harvest_fn))
    out = res["evals"]["oracle"]
    assert (
        out["status"] == "ok"
        and "exists only because this target publishes its source maps" in out["statement"]
    )
    assert out["source"].startswith("source-map oracle (harvest: 1 page visits, 1 chunks pinned")
    s = out["scoring"]
    assert s["labels"] == 5 and s["scored"] == 3
    assert s["excluded"] == {"not_in_crawl": 1, "file_not_on_main": 1}
    assert s["linker_coverage"]["k"] == 2 and s["linker_coverage"]["n"] == 3
    assert s["top1_precision_of_covered"]["k"] == 1 and s["top1_precision_of_covered"]["n"] == 2
    assert s["by_top1_method"]["i18n_key"]["top1_correct"]["k"] == 1
    assert s["by_top1_method"]["llm_pick"]["top1_correct"]["k"] == 0
    assert s["by_owner_hops"]["1"]["n"] == 2  # both library-wrapped buttons
    rows = [json.loads(line) for line in (run.run_dir / out["scoring"]["rows"]).read_text().splitlines()]
    assert {r["status"] for r in rows} == {"scored", "not_in_crawl", "file_not_on_main"}
    assert out["oracle"]["attribution"]["basename_equals_type_name"]["n"] == 3


def test_oracle_needs_crawl_links_and_index_and_says_which(tmp_path: Path):
    bare = make_run(tmp_path, "bare")
    out = run_eval(bare, cfg(), None, None, ["oracle"])["evals"]["oracle"]
    assert out["status"] == "skipped" and "crawl/screens.jsonl" in out["reason"]
    nolinks = seeded_run(tmp_path / "b", with_links=False)
    out = run_eval(nolinks, cfg(), None, None, ["oracle"])["evals"]["oracle"]
    assert out["status"] == "skipped" and "links.jsonl" in out["reason"]


def test_failed_harvest_falls_back_to_a_hand_label_template_and_is_not_hidden(tmp_path: Path):
    run = seeded_run(tmp_path)

    def boom(*a: Any, **k: Any) -> o.Harvest:
        raise RuntimeError("chromium missing\nsecond line")

    out = run_eval(run, cfg(), None, None, ["oracle"], options=EvalOptions(harvest_fn=boom))["evals"][
        "oracle"
    ]
    assert out["status"] == "needs_labels" and out["scoring"] is None
    assert out["harvest_error"].startswith("RuntimeError: chromium missing")
    template = json.loads((run.run_dir / "eval" / "hand_labels.template.json").read_text())
    assert 0 < len(template) <= 25 and all(t["oracle_file"] is None and t["name"] for t in template)
    assert [t["name"] for t in template] == [t["name"] for t in json.loads(json.dumps(template))]
    assert any(f["code"] == "DEP_MISSING" for f in run.failures.items)


def test_hand_labels_are_scored_when_the_live_oracle_is_unavailable(tmp_path: Path):
    run = seeded_run(tmp_path)
    labels = [
        {
            "url_pattern": "/",
            "active_tabs": list(HOME_TABS),
            "dialog_title": None,
            "role": "button",
            "name": "Search and commands",
            "ordinal": 0,
            "oracle_file": SRC + "app/Header.vue",
        },
        {
            "url_pattern": "/",
            "active_tabs": list(HOME_TABS),
            "dialog_title": None,
            "role": "button",
            "name": "Hoppscotch",
            "ordinal": 0,
            "oracle_file": None,
        },
    ]  # unlabelled: ignored
    run.path("eval", "hand_labels.json").write_text(json.dumps(labels))
    out = run_eval(run, cfg(), None, None, ["oracle"], options=EvalOptions(live_oracle=False))["evals"][
        "oracle"
    ]
    assert out["status"] == "fallback_hand_labels" and "hand labels" in out["source"]
    assert out["scoring"]["scored"] == 1 and out["scoring"]["top1_precision_of_covered"]["k"] == 1


# ---------------------------------------------------------------------------------- orchestration
def test_which_none_runs_what_can_and_reports_the_rest_as_skipped_with_reasons(tmp_path: Path):
    run = make_run(tmp_path)
    write_evidence(run, evidence(3))
    write_report(run, "6645", report())
    res = run_eval(run, cfg(), None, None, None)
    assert set(res["evals"]) == set(ALL) and res["summary"]["invariants"] == "ok"
    for name in ("recall", "oracle", "judge", "stability"):
        assert res["evals"][name]["status"] == "skipped" and res["evals"][name]["reason"], name
    assert (run.run_dir / "eval" / "results.json").exists() and (run.run_dir / "eval" / "eval.md").exists()


def test_unknown_evaluation_name_is_refused(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown evaluation"):
        run_eval(make_run(tmp_path), cfg(), None, None, ["invariants", "vibes"])


def test_an_invalid_report_fails_the_invariants_and_the_failure_is_in_the_output(tmp_path: Path):
    run = make_run(tmp_path)
    write_evidence(run, evidence(3))
    write_report(run, "1", report())
    write_report(run, "2", report(claims=[claim(1, evidence=["ev_ffffffffffff"]), claim(2)]))
    res = run_eval(run, cfg(), None, None, ["invariants"])
    inv = res["evals"]["invariants"]
    assert inv["n_reports"] == 2 and inv["n_ok"] == 1 and inv["all_ok"] is False
    assert inv["violations_by_invariant"] == {"citations_exist": 1}
    md = (run.run_dir / "eval" / "eval.md").read_text()
    assert "FAILED citations_exist" in md and "ev_ffffffffffff" in md


def test_a_crashing_evaluation_is_an_error_entry_and_a_failure_row_not_a_missing_section(
    tmp_path, monkeypatch
):
    run = make_run(tmp_path)

    def boom(_run: Any) -> dict[str, Any]:
        raise KeyError("surprise")

    monkeypatch.setattr(eval_run, "_invariants", boom)
    res = run_eval(run, cfg(), None, None, ["invariants"])
    assert res["evals"]["invariants"] == {"status": "error", "error": "KeyError: 'surprise'"}
    assert any(
        f["code"] == "NEEDS_HUMAN" and "eval invariants raised" in f["message"] for f in run.failures.items
    )
    assert "## Failures" in (run.run_dir / "eval" / "eval.md").read_text()


def test_every_rate_in_eval_md_carries_its_n(tmp_path: Path):
    run = seeded_run(tmp_path)
    write_evidence(run, evidence(3))
    write_report(run, "6645", report())
    run_eval(run, cfg(), None, None, ["invariants", "oracle"], options=EvalOptions(harvest_fn=harvest_fn))
    md = (run.run_dir / "eval" / "eval.md").read_text()
    wilson_lines = [
        line for line in md.splitlines() if "Wilson 95%" in line and line.lstrip().startswith("- ")
    ]
    assert wilson_lines
    for line in wilson_lines:
        assert re.search(r"\d+/\d+ = \d", line) or "n=0" in line, line
    assert "pre-registered" in md and "sha256" in md


def test_results_record_the_pre_registered_thresholds_and_the_file_hash(tmp_path: Path):
    res = run_eval(make_run(tmp_path), cfg(), None, None, ["invariants"])
    th = res["thresholds"]
    assert th["registered"]["hop_cap"] == 2 and th["registered"]["breadth_fraction_max"] == 0.25
    assert th["eval_only"]["judge_k"] == 3 and th["eval_only"]["seeded_corruptions"] == 20
    assert th["eval_only"]["stability_min_agreement"] == 0.6 and len(th["sha256"]) == 64


def test_judge_through_run_eval_uses_the_run_evidence_and_its_own_purpose(tmp_path: Path):
    run = make_run(tmp_path)
    write_evidence(run, evidence(6))
    claims = [claim(i, f"Environment {i} is changed by this PR on {i} screens.") for i in range(1, 5)]
    write_report(
        run,
        "6645",
        report(claims=claims, direct=[item(i) for i in range(1, 5)], checklist=[], headline="Risk: LOW."),
    )
    client = FakeClient(
        lambda kw: JudgeAnswer(
            f1_entities_in_evidence=True,
            f2_relation_not_stronger=True,
            f3_numbers_match=True,
            can_tell=True,
            quote="renders the value",
        )
    )
    res = run_eval(
        run,
        cfg(),
        None,
        make_llm(run, client, tmp_path),
        ["judge"],
        options=EvalOptions(judge_k=2, n_corruptions=6),
    )
    j = res["evals"]["judge"]
    assert j["status"] == "ok" and j["n_claims"] == 4 and j["natural"]["n"] == 4 and j["k"] == 2
    assert j["natural"]["supported"]["k"] == 4 and j["seeded"]["produced"] == 6
    assert res["llm"]["calls"] == len(client.calls) > 0 and res["llm"]["spent_usd"] > 0
    assert "same vendor family" in (run.run_dir / "eval" / "eval.md").read_text()


def test_stability_is_skipped_offline_instead_of_pretending(tmp_path: Path):
    run = make_run(tmp_path)
    write_report(run, "6645", report())
    llm = make_llm(run, FakeClient(lambda kw: None), tmp_path, offline=True)
    out = run_eval(run, cfg(), None, llm, ["stability"])["evals"]["stability"]
    assert out["status"] == "skipped" and "offline" in out["reason"]


# ------------------------------------------------------------------------------------ determinism
def digest_probe(base: str) -> str:
    """Called in a subprocess under different hash seeds: the sha256 of results.json for a fixed run."""
    tmp = Path(base)
    run = seeded_run(tmp)
    write_evidence(run, evidence(3))
    write_report(run, "6645", report())
    run_eval(run, cfg(), None, None, ["invariants", "oracle"], options=EvalOptions(harvest_fn=harvest_fn))
    return (
        hashlib.sha256((run.run_dir / "eval" / "results.json").read_bytes()).hexdigest()
        + "|"
        + hashlib.sha256((run.run_dir / "eval" / "oracle_elements.jsonl").read_bytes()).hexdigest()
    )


def test_results_are_byte_identical_across_hash_seeds(tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    code = (
        f"import sys; sys.path.insert(0, {str(repo / 'tests')!r}); import test_eval_run as t; "
        f"print(t.digest_probe(sys.argv[1]))"
    )
    digests = set()
    for seed in (0, 1, 7, 99):
        env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": str(repo / "src")}
        out = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path / f"s{seed}")],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert out.returncode == 0, out.stderr[-1500:]
        digests.add(out.stdout.strip().splitlines()[-1])
    assert len(digests) == 1, digests


# ------------------------------------------------------------------- paths from the crawl, thresholds
def test_visit_paths_come_from_what_the_crawl_saw():
    from blastradius.evalkit.artifacts import read_jsonl
    from blastradius.evalkit.oracle_run import crawled_paths
    from blastradius.models import ScreenState

    screens = read_jsonl(CRAWL / "screens.jsonl", ScreenState)[0]
    assert crawled_paths(screens) == ["/", "/graphql", "/settings"]  # wanted, in order, no catch-all
    only_realtime = [s for s in screens if s.url_pattern.startswith("/realtime")]
    got = crawled_paths(only_realtime)
    assert got and all(p.startswith("/realtime") and "*" not in p for p in got) and len(got) <= 6
    assert crawled_paths([]) == []


def test_unusable_registered_thresholds_are_recorded_as_a_failure_not_swallowed(tmp_path, monkeypatch):
    from blastradius.evalkit import prereg

    monkeypatch.setattr(prereg, "load_thresholds", lambda path=None: (_ for _ in ()).throw(ValueError("bad")))
    run = make_run(tmp_path)
    res = run_eval(run, cfg(), None, None, ["invariants"])
    assert res["thresholds"]["sha256"] is None and "built-in minimal" in res["thresholds"]["source"]
    assert res["thresholds"]["registered"]["breadth_fraction_max"] == 0.25
    assert any(f["code"] == "CONFIG_INVALID" and f["context"]["stage"] == "eval" for f in run.failures.items)


def test_judge_and_stability_are_skipped_not_faked_when_no_model_can_be_called(tmp_path, monkeypatch):
    from blastradius.config import Settings

    monkeypatch.setattr(Settings, "has_api_key", staticmethod(lambda: False))
    run = make_run(tmp_path)
    write_evidence(run, evidence(3))
    write_report(run, "6645", report())
    llm = make_llm(run, None, tmp_path)  # no client, no key
    res = run_eval(run, cfg(), None, llm, ["judge", "stability"])
    for name in ("judge", "stability"):
        assert (
            res["evals"][name]["status"] == "skipped" and "ANTHROPIC_API_KEY" in res["evals"][name]["reason"]
        )
    assert res["llm"]["calls"] == 0


def test_a_judge_that_got_no_verdict_is_unavailable_not_ok(tmp_path):
    class Down:
        class messages:
            @staticmethod
            def parse(**kw):
                raise TypeError("Could not resolve authentication method")

    run = make_run(tmp_path)
    write_evidence(run, evidence(6))
    claims = [claim(i) for i in range(1, 5)]
    write_report(
        run,
        "6645",
        report(claims=claims, direct=[item(i) for i in range(1, 5)], checklist=[], headline="Risk: LOW."),
    )
    res = run_eval(run, cfg(), None, make_llm(run, Down, tmp_path), ["judge"])
    j = res["evals"]["judge"]
    assert j["status"] == "unavailable" and "TypeError" in j["reason"] and j["model_verdicts"] == 0
    md = (run.run_dir / "eval" / "eval.md").read_text()
    assert "is a measurement" in md and "UPPER BOUND" not in md


def test_a_stability_run_whose_model_never_answered_says_it_measured_nothing_about_the_model(tmp_path):
    from blastradius.evalkit.stability import run_stability

    run = make_run(tmp_path)
    write_evidence(run, evidence(3))
    write_report(run, "6645", report())
    llm = make_llm(run, FakeClient(lambda kw: None), tmp_path)

    def template_only(scratch, cfg_, store, bypass_llm, pr, i):
        return report()  # the pipeline's deterministic path: no model call is made

    out = run_stability(
        run,
        cfg(),
        None,
        llm,
        n=3,
        min_agreement=0.6,
        demoted_confidence=0.49,
        pr_numbers=[6645],
        runner=template_only,
    )
    assert out["status"] == "degenerate" and out["model_exercised"] is False and out["llm_calls"] == 0
    assert any("No model call completed" in c for c in out["caveats"])
    assert out["per_pr"]["6645"]["affected_set_jaccard"]["mean"] == 1.0  # reported, but labelled


def test_a_harvest_that_visited_nothing_is_explained_not_scored_as_zero(tmp_path: Path):
    run = seeded_run(tmp_path)

    def nothing(*_a: Any, **_k: Any) -> o.Harvest:
        return o.Harvest(visits=0, notes=["/: skipped, robots.txt disallows or is unreadable"])

    out = run_eval(run, cfg(), None, None, ["oracle"], options=EvalOptions(harvest_fn=nothing))["evals"][
        "oracle"
    ]
    assert out["status"] == "needs_labels" and out["scoring"] is None
    assert "no labels after 0 visits" in out["harvest_error"] and "robots.txt" in out["harvest_error"]
