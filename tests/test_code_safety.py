"""Read allow-list, failure rows and parse accounting (D10, D24; cases 29, 35; wave-1 review findings).

Every test builds its tree under `tmp_path`: symlinks, broken sources and bad locale files are created at run
time, so nothing here depends on the scratchpad or the network. A fork PR head is untrusted (D06), so the index
must neither follow a link out of the checkout nor stay silent about what it could not read.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from blastradius.code import index as index_mod
from blastradius.code.adapter import SourceTree
from blastradius.code.adapters.vue import INDEXED_EXTS, VueAdapter
from blastradius.code.clone import CloneError, ensure_repo
from blastradius.code.index import run_index
from blastradius.config import Settings
from blastradius.failures import FailureCode
from blastradius.models import CodeIndex
from blastradius.run import RunContext
from blastradius.targets import CodeConfig, CrawlConfig, SpecConfig, TargetConfig

SHA = "c" * 40


def code_cfg(**kw) -> CodeConfig:
    base = {"repo": "acme/safe", "adapter": "vue", "roots": ["src"], "locale_files": ["locales/en.json"],
            "auto_import_dts": "src/components.d.ts"}
    return CodeConfig(**{**base, **kw})


def target_for(cfg: CodeConfig) -> TargetConfig:
    return TargetConfig(name="safe", display_name="Safe", base_url="https://example.test",
                        crawl=CrawlConfig(seed_urls=["https://example.test/"], egress_allow=["example.test"]),
                        spec=SpecConfig(kind="llms_full", url="https://example.test/llms.txt"), code=cfg)


def write(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return root


def new_run(tmp_path: Path, name: str = "r") -> RunContext:
    return RunContext.create("safe", run_id=name, runs_dir=tmp_path / "runs", settings=Settings())


def stub_checkout(monkeypatch: pytest.MonkeyPatch, work: Path) -> None:
    monkeypatch.setattr(index_mod, "ensure_repo", lambda *a, **k: (work, SHA))


def failures_of(run: RunContext, code: FailureCode) -> list[dict]:
    return [f for f in run.failures.items if f["code"] == str(code)]


@pytest.fixture
def linked(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout whose source files, a directory and the locale file are symlinks to files outside it."""
    outside = write(tmp_path / "outside", {
        "secret.ts": "import leaked from 'leaked-package-name'\nexport const k = t('leaked.key')\n",
        "secret.json": json.dumps({"api": {"token": "SUPER-SECRET-VALUE"}}),
        "dir/inner.ts": "import 'inner-leak'\n",
    })
    repo = write(tmp_path / "repo", {
        "src/a.ts": "import './b'\nexport const a = t('a.key')\n",
        "src/b.ts": "export const b = 1\n",
    })
    (repo / "locales").mkdir()
    os.symlink(outside / "secret.ts", repo / "src/evil.ts")
    os.symlink(outside / "dir", repo / "src/evildir")
    os.symlink(tmp_path / "nowhere.ts", repo / "src/dangling.ts")
    os.symlink(outside / "secret.json", repo / "locales/en.json")
    return repo, outside


# ------------------------------------------------------------------------------------ symlink escape
def test_symlinks_are_skipped_counted_and_never_read(linked):
    repo, _ = linked
    tree = SourceTree(repo, code_cfg(), INDEXED_EXTS)
    assert tree.paths == ["src/a.ts", "src/b.ts"]
    assert tree.symlinks == ["locales/en.json", "src/dangling.ts", "src/evil.ts", "src/evildir"]
    for link in tree.symlinks:
        with pytest.raises(PermissionError):
            tree.read_bytes(link)


def test_index_over_a_linked_tree_leaks_nothing_and_reports_the_skips(linked):
    repo, _ = linked
    idx = VueAdapter().index(repo, code_cfg(), SHA)
    assert [f.path for f in idx.files if f.kind.value != "external"] == ["src/a.ts", "src/b.ts"]
    assert idx.i18n == {} and {u.file for u in idx.key_uses} == {"src/a.ts"}
    assert not any("leaked" in f.path or "inner-leak" in f.path for f in idx.files)
    assert idx.stats["symlinks_skipped"] == 4
    assert idx.stats["symlinks_skipped_sample"] == ["locales/en.json", "src/dangling.ts", "src/evil.ts", "src/evildir"]
    assert idx.stats["configured_files"]["locales/en.json"] == "symlink"


def test_a_symlinked_root_or_ancestor_yields_no_files(tmp_path: Path):
    outside = write(tmp_path / "outside", {"src/x.ts": "import 'leak'\n", "pkg/y.ts": "export {}\n"})
    repo = tmp_path / "repo"
    repo.mkdir()
    os.symlink(outside, repo / "packages")           # an ancestor of the root is a link
    os.symlink(outside / "pkg", repo / "src")        # the root itself is a link
    cfg = code_cfg(roots=["packages/src", "src"], locale_files=[], auto_import_dts=None)
    tree = SourceTree(repo, cfg, INDEXED_EXTS)
    assert tree.paths == []
    assert [(r.root, r.status) for r in tree.roots] == [("packages/src", "escapes_checkout"), ("src", "symlink")]
    with pytest.raises(PermissionError):
        tree.read_bytes("packages/src/x.ts")


def test_a_file_swapped_for_a_link_after_listing_is_refused(tmp_path: Path):
    outside = write(tmp_path / "outside", {"secret.ts": "export const secret = 1\n"})
    repo = write(tmp_path / "repo", {"src/a.ts": "export const a = 1\n"})
    tree = SourceTree(repo, code_cfg(locale_files=[], auto_import_dts=None), INDEXED_EXTS)
    assert tree.read_bytes("src/a.ts") == b"export const a = 1\n"
    (repo / "src/a.ts").unlink()
    os.symlink(outside / "secret.ts", repo / "src/a.ts")
    with pytest.raises(PermissionError):
        tree.read_bytes("src/a.ts")


def test_a_checkout_with_committed_symlinks_is_indexed_safely_and_recorded(tmp_path: Path, monkeypatch):
    outside = write(tmp_path / "outside", {"secret.json": json.dumps({"api": {"token": "SUPER-SECRET-VALUE"}}),
                                           "secret.ts": "import x from 'leaked-package-name'\n"})
    origin = write(tmp_path / "origin", {"src/a.ts": "import './b'\nexport const a = t('a.key')\n",
                                         "src/b.ts": "export const b = 1\n"})
    (origin / "locales").mkdir()
    os.symlink(outside / "secret.ts", origin / "src/evil.ts")
    os.symlink(outside / "secret.json", origin / "locales/en.json")
    for args in (["init", "-q", "-b", "main"], ["config", "uploadpack.allowFilter", "true"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false", "commit", "-q",
                 "-m", "one"]):
        subprocess.run(["git", *args], cwd=origin, check=True, capture_output=True)
    real = ensure_repo
    monkeypatch.setattr(index_mod, "ensure_repo", lambda repo, ref, roots, **kw: real(
        repo, ref, roots, cache_root=tmp_path / "cache", remote=f"file://{origin}", **kw))
    run = new_run(tmp_path)
    idx = run_index(run, target_for(code_cfg(auto_import_dts=None)))
    assert idx.i18n == {} and not any(f.path == "leaked-package-name" for f in idx.files)
    partial = failures_of(run, FailureCode.CODE_PARSE_PARTIAL)
    assert len(partial) == 1 and partial[0]["context"]["symlinks_skipped"] == 2


# ----------------------------------------------------------------------------- silent empty index
def test_missing_roots_are_failures_and_the_index_says_it_is_empty(tmp_path: Path, monkeypatch):
    repo = write(tmp_path / "repo", {"src/only.ts": "export const x = 1\n"})
    stub_checkout(monkeypatch, repo)
    run = new_run(tmp_path)
    cfg = code_cfg(roots=["packages/nope/src", "packages/gone/src"], locale_files=[], auto_import_dts=None)
    idx = run_index(run, target_for(cfg))
    assert idx.stats["index_empty"] is True and idx.stats["files_in_tree"] == 0
    assert idx.stats["resolved_tag_rate"] is None            # nothing was measured: not "100% resolved"
    assert [(r["root"], r["status"]) for r in idx.stats["code_roots"]] == [
        ("packages/nope/src", "missing"), ("packages/gone/src", "missing")]
    bad = failures_of(run, FailureCode.CONFIG_INVALID)
    assert sorted(f["context"]["root"] for f in bad if "root" in f["context"]) == [
        "packages/gone/src", "packages/nope/src"]
    assert any(f["context"].get("index_empty") for f in bad)
    assert CodeIndex.model_validate_json((run.run_dir / "code" / "index.json").read_text()) == idx


def test_one_missing_root_among_good_ones_is_reported_alone(tmp_path: Path, monkeypatch):
    repo = write(tmp_path / "repo", {"src/a.ts": "export const a = 1\n", "docs-only/README.md": "x"})
    stub_checkout(monkeypatch, repo)
    run = new_run(tmp_path)
    cfg = code_cfg(roots=["src", "packages/nope/src", "docs-only"], locale_files=[], auto_import_dts=None)
    idx = run_index(run, target_for(cfg))
    assert idx.stats["index_empty"] is False
    assert {r["root"]: r["status"] for r in idx.stats["code_roots"]} == {
        "src": "ok", "packages/nope/src": "missing", "docs-only": "empty"}
    roots = sorted(f["context"]["root"] for f in failures_of(run, FailureCode.CONFIG_INVALID))
    assert roots == ["docs-only", "packages/nope/src"]


def test_a_tree_with_tags_reports_a_real_rate(tmp_path: Path):
    repo = write(tmp_path / "repo", {"src/A.vue": "<template><Missing/></template>\n"})
    idx = VueAdapter().index(repo, code_cfg(locale_files=[], auto_import_dts=None), SHA)
    assert idx.stats["tag_pairs_total"] == 1 and idx.stats["resolved_tag_rate"] == 0.0


# ------------------------------------------------------------------------------ parse accounting
@pytest.fixture
def broken(tmp_path: Path) -> Path:
    return write(tmp_path / "broken", {
        "src/ok.vue": "<template><div>Hello there</div></template>\n<script setup lang=\"ts\">\n"
                      "import { a } from './broken'\n</script>\n",
        "src/broken.ts": "import { x } from './ok.vue'\nexport const = = = {{{{\nimport { y } from './never'\n",
        "src/enc.ts": b"import z from './ok.vue'\n// \xff\xfe bad bytes\n",
        "src/pug.vue": "<template lang=\"pug\">\ndiv Pug\n</template>\n<script setup></script>\n",
        "src/two.vue": "<template><A/></template>\n<template><B/></template>\n",
        "src/tsrc.vue": "<template src=\"./t.html\"></template>\n",
        "locales/en.json": "{ not json",
    })


def test_parse_problems_are_counted_in_stats(broken: Path):
    idx = VueAdapter().index(broken, code_cfg(auto_import_dts=None), SHA)
    s = idx.stats
    assert s["parse_error_kinds"] == {
        "locale_parse_error": 1, "multiple_template_blocks": 1, "script_parse_error": 1,
        "template_lang_skipped": 1, "template_src_not_scanned": 1, "undecodable_bytes": 1}
    assert s["parse_errors"] == 6 and s["parse_error_files"] == 6
    assert s["parse_error_sample"] == [
        {"file": "locales/en.json", "kind": "locale_parse_error"},
        {"file": "src/broken.ts", "kind": "script_parse_error"},
        {"file": "src/enc.ts", "kind": "undecodable_bytes"},
        {"file": "src/pug.vue", "kind": "template_lang_skipped"},
        {"file": "src/tsrc.vue", "kind": "template_src_not_scanned"},
        {"file": "src/two.vue", "kind": "multiple_template_blocks"}]
    assert s["i18n_parse_error"] is True and idx.i18n == {}


def test_a_clean_tree_has_no_parse_problems(tmp_path: Path):
    repo = write(tmp_path / "clean", {"src/a.ts": "import './b'\n", "src/b.ts": "export const b = 1\n"})
    idx = VueAdapter().index(repo, code_cfg(locale_files=[], auto_import_dts=None), SHA)
    assert idx.stats["parse_errors"] == 0 and idx.stats["parse_error_sample"] == []
    assert idx.stats["symlinks_skipped"] == 0 and idx.stats["index_empty"] is False


def test_run_index_records_one_parse_partial_failure_with_counts(broken: Path, tmp_path: Path, monkeypatch):
    stub_checkout(monkeypatch, broken)
    run = new_run(tmp_path)
    run_index(run, target_for(code_cfg(auto_import_dts=None)))
    rows = failures_of(run, FailureCode.CODE_PARSE_PARTIAL)
    assert len(rows) == 1
    ctx = rows[0]["context"]
    assert ctx["parse_errors"] == 6 and ctx["kinds"]["script_parse_error"] == 1
    assert ctx["symlinks_skipped"] == 0 and "6 problem" in rows[0]["message"]


def test_the_checkout_failure_modes_have_their_own_codes(tmp_path: Path, monkeypatch):
    cfg = target_for(code_cfg())

    def run_with(exc: Exception, name: str) -> RunContext:
        def boom(*_a, **_k):
            raise exc

        monkeypatch.setattr(index_mod, "ensure_repo", boom)
        run = new_run(tmp_path, name)
        with pytest.raises(type(exc)):
            run_index(run, cfg, ref="nope")
        return run

    assert [f["code"] for f in run_with(CloneError("ref 'nope' not found on origin"), "co").failures.items] == [
        str(FailureCode.CODE_CHECKOUT_FAILED)]
    assert [f["code"] for f in run_with(FileNotFoundError("git"), "dep").failures.items] == [
        str(FailureCode.DEP_MISSING)]
    from blastradius.code.clone import InvalidSparsePath

    assert [f["code"] for f in run_with(InvalidSparsePath("sparse path must be relative"), "cfg").failures.items] == [
        str(FailureCode.CONFIG_INVALID)]


def test_run_index_states_explicitly_that_a_pr_overlay_was_not_applied(tmp_path: Path, monkeypatch):
    repo = write(tmp_path / "repo", {"src/a.ts": "export const a = 1\n"})
    stub_checkout(monkeypatch, repo)
    cfg = target_for(code_cfg(locale_files=[], auto_import_dts=None))
    with_pr = run_index(new_run(tmp_path, "pr"), cfg, pr_number=6645)
    assert with_pr.stats["pr_number"] == 6645 and with_pr.stats["pr_overlay"] == "not_applied"
    assert "run_overlay" in with_pr.stats["pr_overlay_reason"]
    without = run_index(new_run(tmp_path, "nopr"), cfg)
    assert "pr_overlay" not in without.stats and "pr_number" not in without.stats


def test_adapter_stamps_the_target_and_ref_it_is_given(tmp_path: Path):
    repo = write(tmp_path / "repo", {"src/a.ts": "export const a = 1\n"})
    cfg = code_cfg(locale_files=[], auto_import_dts=None)
    idx = VueAdapter().index(repo, cfg, SHA, target="safe", ref="v9")
    assert (idx.target, idx.ref, idx.repo) == ("safe", "v9", "acme/safe")
    assert VueAdapter().index(repo, cfg, SHA).target == "acme/safe"


def test_hub_importers_exclude_tests_and_report_both(tmp_path: Path):
    repo = write(tmp_path / "hub", {
        "packages/acme-data/src/index.ts": "export const v = 1\n",
        "packages/acme-app/src/a.ts": "import { v } from '@acme/data'\n",
        "packages/acme-app/src/b.ts": "import { v } from '@acme/data'\n",
        "packages/acme-app/src/__tests__/a.spec.ts": "import { v } from '@acme/data'\n",
    })
    cfg = code_cfg(roots=["packages/acme-app/src", "packages/acme-data/src"], locale_files=[],
                   auto_import_dts=None, hub_packages=["@acme/data"])
    s = VueAdapter().index(repo, cfg, SHA).stats
    assert s["hub_importers"] == {"@acme/data": 2}
    assert s["hub_importers_with_tests"] == {"@acme/data": 3}


def test_a_locale_file_nested_too_deep_is_a_reported_parse_failure_not_a_crash(tmp_path: Path):
    repo = write(tmp_path / "deep", {"src/a.ts": "export const a = 1\n",
                                      "locales/en.json": '{"a":' * 20000 + "1" + "}" * 20000})
    idx = VueAdapter().index(repo, code_cfg(auto_import_dts=None), SHA)
    assert idx.i18n == {} and idx.stats["parse_error_kinds"] == {"locale_parse_error": 1}


def test_a_json_locale_that_is_not_an_object_is_a_parse_failure(tmp_path: Path):
    repo = write(tmp_path / "list", {"src/a.ts": "export const a = 1\n", "locales/en.json": "[1, 2]"})
    idx = VueAdapter().index(repo, code_cfg(auto_import_dts=None), SHA)
    assert idx.i18n == {} and idx.stats["parse_error_kinds"] == {"locale_parse_error": 1}


def test_a_tree_of_hostile_files_is_indexed_quickly_and_flags_what_it_could_not_read(tmp_path: Path):
    """A fork PR head is untrusted: nothing in it may stall or crash the indexer."""
    import time

    hostile_script = ("export <![CDATA[b<script>-->\u00e9}}<style>\u00e9{\x00import #slot</layout: 'x'}}'export `}"
                      "</script></script>v-pre<!--{<!--@click=import <![CDATA[&amp; <Component")
    files = {
        "src/open_tags.vue": "<template>" + "<div " * 50000 + "</template>",
        "src/open_interp.vue": "<template>" + "{{ " * 50000,
        "src/blank.vue": "\n" * 300000 + "<template><A/></template>",
        "src/calls.ts": "t(" * 30000,
        "src/hostile.ts": hostile_script,
        "src/route.vue": '<template><A/></template>\n<route lang="yaml">' + "[" * 50000 + "</route>\n",
        "src/nested.vue": "<template>" + "<template v-if=x>" * 5000 + "x" + "</template>" * 5000 + "</template>",
        "src/components.d.ts": "\n" * 200000,
        "src/ok.ts": "import './calls'\nexport const ok = t('fine.key')\n",
        "locales/en.json": '{"a":' * 20000 + "1" + "}" * 20000,
    }
    repo = write(tmp_path / "hostile", files)
    started = time.perf_counter()
    idx = VueAdapter().index(repo, code_cfg(), SHA)
    assert time.perf_counter() - started < 15
    assert any(u.key == "fine.key" for u in idx.key_uses)            # the good file was still indexed
    kinds = idx.stats["parse_error_kinds"]
    assert kinds["script_parse_aborted"] == 1 and kinds["locale_parse_error"] == 1


def test_a_file_that_cannot_be_read_after_listing_is_flagged_not_fatal(tmp_path: Path, monkeypatch):
    from blastradius.code import adapter as adapter_mod

    repo = write(tmp_path / "race", {"src/a.ts": "import './b'\n", "src/b.ts": "export const b = 1\n",
                                      "src/c.vue": "<template><b>Hello there</b></template>\n"})
    real = adapter_mod.SourceTree.read_bytes

    def flaky(self, path):
        if path == "src/c.vue":
            raise PermissionError("swapped for a link after the listing")
        return real(self, path)

    monkeypatch.setattr(adapter_mod.SourceTree, "read_bytes", flaky)
    idx = VueAdapter().index(repo, code_cfg(locale_files=[], auto_import_dts=None), SHA)
    assert idx.stats["parse_error_sample"] == [{"file": "src/c.vue", "kind": "unreadable_file"}]
    assert {f.path for f in idx.files} == {"src/a.ts", "src/b.ts"}
    assert idx.stats["app_source_files"] == 2
