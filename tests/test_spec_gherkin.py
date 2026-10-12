"""Gherkin ingest: parser, title derivation, tag mapping, fetch seam and artifacts (D15, D30; cases 20, 73).

The fixture tree `tests/fixtures/sylius` holds four real Sylius feature files plus one that sits outside
`spec.paths`; a folder-backed `FeatureSource` stands in for `gh api`, so no test here touches the network
except the one marked slow.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from blastradius.config import Settings
from blastradius.failures import FailureCode
from blastradius.ids import sha_full, stable_id
from blastradius.run import RunContext
from blastradius.spec import gherkin
from blastradius.spec.gherkin import (
    FeatureSource,
    GhSource,
    SourceError,
    TreeListing,
    derive,
    ingest_gherkin,
    parse_feature,
    ui_observable,
)
from blastradius.spec.ingest import run_ingest
from blastradius.targets import TargetConfig

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "sylius"
COMMIT = "a" * 40
PATHS = ["features/shop/product", "features/shop/cart", "features/shop/homepage", "features/shop/contact"]


class FolderSource:
    """A `FeatureSource` over a directory; records every file it was asked to read."""

    def __init__(self, root: Path, *, commit: str = COMMIT):
        self.root, self.commit, self.read_paths = root, commit, []

    def resolve(self, repo: str, ref: str) -> str:
        return self.commit

    def list_features(self, repo: str, commit: str, directory: str) -> TreeListing:
        base = self.root / directory
        return TreeListing(commit, tuple(sorted(
            p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())))

    def read(self, repo: str, commit: str, path: str) -> str:
        self.read_paths.append(path)
        return (self.root / path).read_text()


def cfg_for(**spec: Any) -> TargetConfig:
    cfg = TargetConfig.load("sylius")
    update = {"paths": PATHS, **spec}
    return cfg.model_copy(update={"spec": cfg.spec.model_copy(update=update)})


def new_run(tmp_path: Path, name: str = "gherkin") -> RunContext:
    return RunContext.create("sylius", run_id=name, runs_dir=tmp_path / "runs", settings=Settings())


def spec_json(run: RunContext, name: str) -> Any:
    return json.loads((run.run_dir / "spec" / name).read_text())


# ------------------------------------------------------------------------------------------------ parser
OUTLINE = '''# language: en
@shop @ui
Feature: Sorting products
    As a Visitor
    I want to sort products

    Background:
        Given the store has "T-Shirts" taxon
        And I am a logged in customer

    @api
    Scenario Outline: Sorting products by <field>
        When I sort by "<field>"
        Then I should see "<first>" first
        """
        a doc string with
        Scenario: Not a scenario
        """
        Examples:
            | field | first |
            | name  | A     |
            | price | B     |

    @no-ui
    Scenario: Sorting is stable   # trailing comment
        When I sort
        | a | b |

    Rule: Paging
    @javascript
    Scenario: Paging forward
        Then I see page 2
'''


def test_parser_reads_tags_background_outlines_and_ignores_doc_strings_and_tables():
    feature = parse_feature(OUTLINE)
    assert feature.name == "Sorting products" and feature.tags == ("@shop", "@ui")
    assert feature.language == "en"
    assert feature.background == ('Given the store has "T-Shirts" taxon', "And I am a logged in customer")
    assert [s.title for s in feature.scenarios] == [
        "Sorting products by <field>", "Sorting is stable   # trailing comment", "Paging forward"]
    outline, stable, paging = feature.scenarios
    assert outline.keyword == "Scenario Outline" and outline.tags == ("@api",)
    assert outline.raw == "Scenario Outline: Sorting products by <field>"
    assert outline.steps == ('When I sort by "<field>"', 'Then I should see "<first>" first')
    assert stable.tags == ("@no-ui",) and stable.steps == ("When I sort",)
    assert paging.tags == ("@javascript",)                            # a tag after `Rule:` is the scenario's
    assert paging.line == OUTLINE.splitlines().index("    Scenario: Paging forward") + 1


def test_parser_is_total_on_garbage_and_bounded():
    assert parse_feature("").scenarios == ()
    assert parse_feature("\x00\x01 not gherkin\n@@@\n:::").scenarios == ()
    many = "Feature: x\n" + "".join(f"  Scenario: s{i}\n    Given a\n" for i in range(5000))
    got = parse_feature(many)
    assert len(got.scenarios) == 2000 and got.truncated        # a hostile file cannot make unbounded rows


def test_tags_do_not_leak_from_examples_blocks_into_the_next_scenario():
    text = ("Feature: f\n  Scenario Outline: o\n    Given <a>\n    @tmp\n    Examples:\n      | a |\n      | 1 |\n"
            "  Scenario: next\n    Given b\n")
    assert [s.tags for s in parse_feature(text).scenarios] == [(), ()]


def test_ui_observable_follows_the_scenario_tag_before_the_feature_tag():
    assert ui_observable(("@api", "@ui"), ()) == "yes"
    assert ui_observable(("@no-ui",), ("@ui",)) == "no"          # the scenario's own tag wins
    assert ui_observable((), ("@ui",)) == "yes"
    assert ui_observable(("@ui", "@no-ui"), ()) == "unknown"     # contradictory: not guessed
    assert ui_observable(("@api",), ("@shopping_cart",)) == "unknown"


# ------------------------------------------------------------------------------------------------ derive
@pytest.mark.parametrize("title, text, polarity", [
    ("Adding a simple product to the cart", "User can add a simple product to the cart", "must_exist"),
    ("Viewing a detailed page with product's name", "User can view a detailed page with product's name",
     "must_exist"),
    ("Seeing only enabled variants", "User can see only enabled variants", "must_exist"),
    ("Getting only available locales in channel", "User can get only available locales in channel",
     "must_exist"),
    ("Increasing quantity of an item in cart", "User can increase quantity of an item in cart", "must_exist"),
    ("Removing cart item", "User can remove cart item", "must_exist"),
    ("Browsing channels using their default locales", "User can browse channels using their default locales",
     "must_exist"),
    ("Not seeing unavailable variants", "User cannot see unavailable variants", "must_not_exist"),
    ("Not being able to shop using a locale non-existent in the channel",
     "User cannot shop using a locale non-existent in the channel", "must_not_exist"),
    ("Trying to add a product review without specifying a rate",
     "User cannot add a product review without specifying a rate", "must_not_exist"),
    ("Cannot navigate to disabled parent taxon", "User cannot navigate to disabled parent taxon",
     "must_not_exist"),
    ("Denying access to the customers cart by the visitor",
     "User is denied access to the customers cart by the visitor", "must_not_exist"),
    ("The product attributes are listed by their respective position",
     "User can see that the product attributes are listed by their respective position", "must_exist"),
    ('"Add to cart" button is disabled when requested quantity exceeds stock',
     'User can see that "Add to cart" button is disabled when requested quantity exceeds stock', "must_exist"),
])
def test_derive_turns_a_scenario_title_into_a_user_requirement(title: str, text: str, polarity: str):
    assert derive(title) == (text, polarity)


def test_derive_is_pure_and_bounded():
    long = "Viewing " + "very " * 200 + "long title"
    text, _ = derive(long)
    assert text == derive(long)[0] and len(text) <= 420 and text.startswith("User can view very very")


# ------------------------------------------------------------------------------------------------ ingest
def test_ingest_emits_one_requirement_per_scenario_with_fields_from_the_gherkin(tmp_path: Path):
    run, source = new_run(tmp_path), FolderSource(FIXTURE)
    reqs = ingest_gherkin(run, cfg_for(), source=source)
    by_text = {r.quote: r for r in reqs}
    assert len(reqs) == 8 and len(by_text) == 8                       # cart 2, contact 2, homepage 2, product 2
    clearing = by_text["Scenario: Clearing cart"]
    assert clearing.text == "User can clear cart"
    assert clearing.section == "Clearing cart"
    assert clearing.ui_observable == "yes" and clearing.polarity == "must_exist"
    assert clearing.requires_login is True                            # Background: "I am a logged in customer"
    assert clearing.source_kind == "gherkin" and clearing.edition == "all" and clearing.target == "sylius"
    assert clearing.tags == ["@api", "@mink:chromedriver", "@shopping_cart", "@ui", "area:cart"]
    assert clearing.source_url == (
        f"https://github.com/Sylius/Sylius/blob/{COMMIT}/features/shop/cart/shopping_cart/clearing_cart.feature#L13")
    assert clearing.doc_version == f"2.2@{COMMIT[:12]}"
    no_ui = by_text["Scenario: Filtering products by taxon available only for enabled taxon"]
    assert no_ui.ui_observable == "no" and no_ui.requires_login is False
    guest = by_text["Scenario: Requesting contact as a guest"]
    assert guest.requires_login is False and "area:contact" in guest.tags and "@email" in guest.tags


def test_quote_is_the_scenario_title_line_verbatim_and_ids_are_content_hashes(tmp_path: Path):
    reqs = ingest_gherkin(new_run(tmp_path), cfg_for(), source=FolderSource(FIXTURE))
    for r in reqs:
        path = r.source_url.split(f"/blob/{COMMIT}/")[1].split("#")[0]
        assert r.quote in [line.strip() for line in (FIXTURE / path).read_text().splitlines()]
        title = r.quote.split(":", 1)[1].strip()
        assert r.id == stable_id("req", "sylius", title + path)
    assert len({r.id for r in reqs}) == len(reqs)


def test_spec_sha_covers_the_concatenated_sources_and_changes_with_them(tmp_path: Path):
    source = FolderSource(FIXTURE)
    reqs = ingest_gherkin(new_run(tmp_path, "a"), cfg_for(), source=source)
    paths = sorted(p for p in source.read_paths)
    expect = sha_full("".join(f"{p}\x00{(FIXTURE / p).read_text()}\x00" for p in paths))
    assert {r.spec_sha for r in reqs} == {expect}
    # same input, same sha, in a second run directory
    again = ingest_gherkin(new_run(tmp_path, "b"), cfg_for(), source=FolderSource(FIXTURE))
    assert [r.model_dump() for r in again] == [r.model_dump() for r in reqs]


def test_only_configured_paths_are_read(tmp_path: Path):
    source = FolderSource(FIXTURE)
    ingest_gherkin(new_run(tmp_path), cfg_for(), source=source)
    assert source.read_paths and all(p.startswith(("features/shop/product/", "features/shop/cart/",
                                                   "features/shop/homepage/", "features/shop/contact/"))
                                     for p in source.read_paths)
    assert not any("checkout" in p for p in source.read_paths)


def test_artifacts_stats_and_evidence(tmp_path: Path):
    run = new_run(tmp_path)
    reqs = ingest_gherkin(run, cfg_for(), source=FolderSource(FIXTURE))
    assert spec_json(run, "requirements.json") == [r.model_dump(mode="json") for r in reqs]
    stats = spec_json(run, "stats.json")
    assert stats["status"] == "ok" and stats["requirements_kept"] == len(reqs)
    assert stats["by_ui_observable"] == {"no": 1, "yes": 7} and stats["by_polarity"] == {"must_exist": 8}
    assert stats["by_area"] == {"cart": 2, "contact": 2, "homepage": 2, "product": 2}
    assert stats["by_tag"]["@ui"] == 7 and stats["commit"] == COMMIT and stats["features_non_english"] == 0
    pages = spec_json(run, "pages.json")
    assert [p["path"] for p in pages] == sorted(p["path"] for p in pages) and len(pages) == 4
    assert all(run.evidence.exists(e) for e in stats["evidence_by_requirement"].values())
    assert len(stats["evidence_by_requirement"]) == len(reqs)
    assert run.manifest["spec"]["status"] == "ok" and run.failures.items == []


def test_duplicate_titles_in_one_file_get_distinct_ids(tmp_path: Path):
    folder = tmp_path / "src"
    (folder / "features" / "shop" / "cart").mkdir(parents=True)
    (folder / "features" / "shop" / "cart" / "a.feature").write_text(
        "Feature: Dup\n  Scenario: Same\n    Given a\n  Scenario: Same\n    Given b\n  Scenario: Other\n    Given c\n")
    reqs = ingest_gherkin(new_run(tmp_path), cfg_for(paths=["features/shop/cart"]), source=FolderSource(folder))
    assert len(reqs) == 3 and len({r.id for r in reqs}) == 3
    assert reqs[0].id == stable_id("req", "sylius", "Same" + "features/shop/cart/a.feature")   # first: no suffix


def test_cap_keeps_ui_observable_requirements_first_in_document_order(tmp_path: Path):
    run = new_run(tmp_path)
    reqs = ingest_gherkin(run, cfg_for(max_requirements=5), source=FolderSource(FIXTURE))
    assert len(reqs) == 5 and all(r.ui_observable == "yes" for r in reqs)
    assert spec_json(run, "stats.json")["dropped_by_reason"] == {"over_cap": 3}


# --------------------------------------------------------------------------------------------- failures
class Broken:
    def __init__(self, error: SourceError):
        self.error = error

    def resolve(self, repo: str, ref: str) -> str:
        raise self.error

    def list_features(self, repo: str, commit: str, directory: str) -> TreeListing:
        raise self.error

    def read(self, repo: str, commit: str, path: str) -> str:
        raise self.error


def test_missing_gh_is_a_dependency_failure_and_clears_the_previous_artifacts(tmp_path: Path):
    run = new_run(tmp_path)
    assert ingest_gherkin(run, cfg_for(), source=FolderSource(FIXTURE))
    out = ingest_gherkin(run, cfg_for(), source=Broken(SourceError("no gh", missing_binary=True)))
    assert out == []
    assert [f["code"] for f in run.failures.items] == [str(FailureCode.DEP_MISSING)]
    assert spec_json(run, "requirements.json") == [] and spec_json(run, "stats.json")["status"] == "failed"


def test_api_failure_and_empty_listing_are_fetch_failures(tmp_path: Path):
    run = new_run(tmp_path, "api")
    assert ingest_gherkin(run, cfg_for(), source=Broken(SourceError("HTTP 502"))) == []
    assert [f["code"] for f in run.failures.items] == [str(FailureCode.SPEC_FETCH_FAILED)]
    run2 = new_run(tmp_path, "empty")
    assert ingest_gherkin(run2, cfg_for(paths=["features/nothing"]), source=FolderSource(FIXTURE)) == []
    assert [f["code"] for f in run2.failures.items] == [str(FailureCode.SPEC_FETCH_FAILED)]


def test_config_without_repo_or_paths_is_invalid(tmp_path: Path):
    run = new_run(tmp_path)
    cfg = cfg_for(repo=None)
    assert ingest_gherkin(run, cfg, source=FolderSource(FIXTURE)) == []
    assert [f["code"] for f in run.failures.items] == [str(FailureCode.CONFIG_INVALID)]


# ----------------------------------------------------------------------------------- dispatch and gh
def test_run_ingest_dispatches_gherkin_targets_to_this_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gherkin, "GhSource", lambda: FolderSource(FIXTURE))
    run = new_run(tmp_path)
    reqs = run_ingest(run, cfg_for(), llm=None)         # type: ignore[arg-type]  (the gherkin path has no LLM)
    assert len(reqs) == 8 and {r.source_kind for r in reqs} == {"gherkin"}
    assert spec_json(run, "stats.json")["status"] == "ok"


def test_gh_source_commands_and_error_mapping(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    answers = iter([
        (0, COMMIT + "\n", ""),
        (0, json.dumps({"truncated": False, "tree": [
            {"path": "cart/a.feature", "type": "blob"}, {"path": "cart", "type": "tree"},
            {"path": "product/b.feature", "type": "blob"}]}), ""),
        (0, "Feature: raw\n", ""),
        (1, "", "HTTP 404: Not Found"),
    ])

    def fake_run(cmd, **kw):
        calls.append(cmd)
        code, out, err = next(answers)
        return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)

    monkeypatch.setattr(subprocess, "run", fake_run)
    src = GhSource(delay_s=0)
    assert src.resolve("Sylius/Sylius", "2.2") == COMMIT
    listing = src.list_features("Sylius/Sylius", COMMIT, "features/shop")
    assert listing.paths == ("features/shop/cart/a.feature", "features/shop/product/b.feature")
    assert src.read("Sylius/Sylius", COMMIT, "features/shop/cart/a.feature") == "Feature: raw\n"
    with pytest.raises(SourceError, match="404"):
        src.read("Sylius/Sylius", COMMIT, "nope")
    agent = ["-H", f"User-Agent: {Settings().user_agent()}"]            # every request identifies the crawler
    assert all(c[:4] == ["gh", "api", *agent] for c in calls)
    assert calls[0][4:] == ["repos/Sylius/Sylius/commits/2.2", "--jq", ".sha"]
    assert calls[1][4] == f"repos/Sylius/Sylius/git/trees/{COMMIT}:features/shop?recursive=1"
    assert calls[2][4:] == ["-H", "Accept: application/vnd.github.raw",
                            f"repos/Sylius/Sylius/contents/features/shop/cart/a.feature?ref={COMMIT}"]


def test_gh_source_reports_a_missing_binary_and_truncated_trees(monkeypatch: pytest.MonkeyPatch):
    def missing(cmd, **kw):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(SourceError) as info:
        GhSource(delay_s=0).resolve("a/b", "main")
    assert info.value.missing_binary

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(
        returncode=0, stdout=json.dumps({"truncated": True, "tree": []}), stderr=""))
    with pytest.raises(SourceError, match="truncated"):
        GhSource(delay_s=0).list_features("a/b", COMMIT, "features")


def test_source_protocol_is_satisfied_by_both_implementations():
    for impl in (GhSource(delay_s=0), FolderSource(FIXTURE)):
        assert all(hasattr(impl, name) for name in ("resolve", "list_features", "read"))
    assert FeatureSource  # exported seam


# ------------------------------------------------------------------------------------- determinism
_SNIPPET = """
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, "tests")
from test_spec_gherkin import FolderSource, cfg_for, new_run, FIXTURE
from blastradius.spec.gherkin import ingest_gherkin
import tempfile
with tempfile.TemporaryDirectory() as d:
    run = new_run(Path(d))
    reqs = ingest_gherkin(run, cfg_for(), source=FolderSource(FIXTURE))
    blob = json.dumps([r.model_dump(mode="json") for r in reqs], sort_keys=True)
    stats = (run.run_dir / "spec" / "stats.json").read_text()
    print(hashlib.sha256((blob + stats).encode()).hexdigest())
"""


def test_requirements_are_identical_across_hash_seeds():
    digests = set()
    for seed in (0, 1, 2, 7):
        env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": f"{REPO / 'src'}"}
        out = subprocess.run([sys.executable, "-c", _SNIPPET], cwd=REPO, env=env, capture_output=True,
                             text=True, timeout=120, check=False)
        assert out.returncode == 0, out.stderr[-600:]
        digests.add(out.stdout.strip().splitlines()[-1])
    assert len(digests) == 1


# ------------------------------------------------------------------------------------------------ real
@pytest.mark.slow
def test_real_sylius_branch_yields_the_expected_slice(tmp_path: Path):
    """Networked: the six shop feature directories of Sylius/Sylius at 2.2 (gh api, about 100 raw reads)."""
    run = new_run(tmp_path)
    cfg = TargetConfig.load("sylius")
    try:
        reqs = ingest_gherkin(run, cfg)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"GitHub unreachable: {exc}")
    if not reqs:
        pytest.skip(f"no requirements fetched: {[f['message'] for f in run.failures.items]}")
    stats = spec_json(run, "stats.json")
    assert 230 <= len(reqs) <= 320                                   # 265 at 28c4862bbc02
    assert stats["by_ui_observable"].get("yes", 0) >= 200 and stats["by_ui_observable"].get("no", 0) >= 15
    assert set(stats["by_area"]) == {"cart", "contact", "currency", "homepage", "locale", "product"}
    assert all(r.source_kind == "gherkin" and r.quote.split(":")[0].startswith("Scenario") for r in reqs)
