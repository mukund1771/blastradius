"""VueAdapter + run_index on the curated fixture tree under tests/fixtures/code/mini.

The fixture is a two-package monorepo (`acme-app`, hub package `acme-data`) built so every behaviour has one
known answer: alias kinds, barrels, a missing codegen output, lazy and dynamic components, a recursive
component, routes with layouts, and i18n uses (including a test file that must not count).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from blastradius.code import index as index_mod
from blastradius.code.adapter import SourceTree, get_adapter
from blastradius.code.adapters.vue import INDEXED_EXTS, VueAdapter
from blastradius.code.index import edge_evidence_id, render_closure, reverse_closure, run_index
from blastradius.config import Settings
from blastradius.failures import FailureCode
from blastradius.models import CodeIndex, FileClass
from blastradius.run import RunContext
from blastradius.targets import CodeConfig, CrawlConfig, SpecConfig, TargetConfig

FIXTURE = Path(__file__).parent / "fixtures" / "code" / "mini"
APP = "packages/acme-app/src"
DATA = "packages/acme-data/src"
SHA = "a" * 40


def mini_code_config() -> CodeConfig:
    return CodeConfig(
        repo="acme/mini", ref="main", adapter="vue", roots=[APP, DATA],
        locale_files=["packages/acme-app/locales/en.json"], auto_import_dts=f"{APP}/components.d.ts",
        global_files=[f"{APP}/layouts/default.vue"], hub_packages=["@acme/data"], alias={"~/": f"{APP}/"})


@pytest.fixture(scope="module")
def idx() -> CodeIndex:
    return VueAdapter().index(FIXTURE, mini_code_config(), SHA)


def edges_of(idx: CodeIndex, kind: str, src: str) -> dict[str, tuple[list[str], str | None]]:
    return {e.dst: (e.symbols, e.via) for e in idx.edges if e.kind == kind and e.src == src}


# ------------------------------------------------------------------------------------- allow-list
def test_source_tree_lists_only_configured_paths_and_refuses_the_rest():
    tree = SourceTree(FIXTURE, mini_code_config(), INDEXED_EXTS)
    assert "packages/acme-app/locales/en.json" in tree.paths
    assert "packages/acme-app/locales/fr.json" not in tree.paths     # a sibling locale is never listed or read
    with pytest.raises(PermissionError):
        tree.read_bytes("packages/acme-app/locales/fr.json")
    with pytest.raises(PermissionError):
        tree.read_bytes("../outside.txt")


def test_get_adapter_by_name():
    assert isinstance(get_adapter("vue"), VueAdapter)
    with pytest.raises(ValueError, match="unknown code adapter"):
        get_adapter("cobol")


# --------------------------------------------------------------------------------- files and classes
def test_files_are_classified_and_stamped(idx: CodeIndex):
    kinds = {f.path: f for f in idx.files}
    assert kinds[f"{APP}/pages/index.vue"].kind is FileClass.PAGE
    assert kinds[f"{APP}/layouts/default.vue"].kind is FileClass.LAYOUT
    assert kinds[f"{APP}/services/__tests__/env.service.spec.ts"].kind is FileClass.TEST
    assert kinds[f"{APP}/components.d.ts"].kind is FileClass.GENERATED
    assert kinds[f"{DATA}/rest/v1.ts"].kind is FileClass.DATA_SCHEMA
    assert kinds["packages/acme-app/locales/en.json"].kind is FileClass.LOCALE
    assert kinds[f"{APP}/pages/index.vue"].package == "@acme/app" and kinds[f"{DATA}/index.ts"].package == "@acme/data"
    # the git blob sha, so a PR head overlay can tell changed files without reading them again
    path = FIXTURE / f"{APP}/composables/i18n.ts"
    git_sha = subprocess.run(["git", "hash-object", str(path)], capture_output=True, text=True, check=True).stdout
    assert kinds[f"{APP}/composables/i18n.ts"].sha == git_sha.strip()
    assert kinds[f"{APP}/helpers/utils/format.ts"].loc == 4


def test_external_packages_are_leaves_and_codegen_outputs_stay_linked(idx: CodeIndex):
    kinds = {f.path: f.kind for f in idx.files}
    for leaf in ("vue", "vue-router", "lodash-es", "~icons"):
        assert kinds[leaf] is FileClass.EXTERNAL
        assert not any(e.src == leaf for e in idx.edges)
    missing = f"{APP}/helpers/backend/graphql.ts"           # imported, but a gitignored codegen output
    assert kinds[missing] is FileClass.GENERATED
    assert missing in edges_of(idx, "IMPORTS", f"{APP}/helpers/backend/queries.ts")
    assert idx.stats["generated_missing_imports"] == 1 and idx.stats["unresolved_imports"] == 0


# ------------------------------------------------------------------------------------- import edges
def test_import_edges_carry_symbols_and_resolve_every_alias_kind(idx: CodeIndex):
    got = edges_of(idx, "IMPORTS", f"{APP}/services/env.service.ts")
    assert got[f"{APP}/helpers/utils/format.ts"][0] == ["default", "fmt"]
    assert got[f"{APP}/helpers/backend/index.ts"][0] == ["*"]                       # namespace import
    assert got[f"{APP}/helpers/backend/runner.ts"][0] == []                         # dynamic import("literal")
    assert got[f"{APP}/services/polyfill.ts"][0] == []                              # side-effect import
    assert got[f"{APP}/types/index.d.ts"][0] == ["Shape"]                           # `import type`, .d.ts probe
    assert got[f"{DATA}/index.ts"][0] == ["RestRequest", "parse"]                   # hub package -> its src
    assert got[f"{APP}/composables/i18n.ts"][1] == "@composables/i18n"             # inferred @dir/* alias
    assert got[f"{APP}/helpers/backend/legacy.ts"][1] == "../helpers/backend/legacy.js"   # .js names a .ts file
    assert got["lodash-es"][0] == ["debounce"] and "vue" in got
    assert idx.stats["nonliteral_dynamic_imports"] == 1                             # import(`./plugins/${name}`)
    assert idx.stats["inferred_alias_imports"] == 2


def test_barrels_reexport_with_symbols_and_count_their_importers(idx: CodeIndex):
    barrel = edges_of(idx, "IMPORTS", f"{APP}/helpers/backend/index.ts")
    assert barrel[f"{APP}/helpers/backend/queries.ts"][0] == ["*"]
    assert barrel[f"{APP}/helpers/backend/runner.ts"][0] == ["RunOptions", "run"]
    rest = edges_of(idx, "IMPORTS", f"{DATA}/rest/index.ts")
    assert rest[f"{DATA}/rest/v1.ts"][0] == ["RestRequest", "parse"]
    assert rest[f"{DATA}/rest/legacy.ts"][0] == ["*"]                               # export * as legacy
    # index.ts in acme-data declares VERSION itself, so it is not a pure barrel
    assert idx.stats["barrel_files"] == 2 and idx.stats["barrel_importers"] == 2
    assert idx.stats["hub_importers"] == {"@acme/data": 2}


def test_test_files_import_but_are_not_i18n_evidence(idx: CodeIndex):
    spec = f"{APP}/services/__tests__/env.service.spec.ts"
    assert f"{APP}/services/env.service.ts" in edges_of(idx, "IMPORTS", spec)
    assert idx.stats["import_statements_from_tests"] == 1
    assert all(u.file != spec for u in idx.key_uses)                                # t("never.counted...")


# ------------------------------------------------------------------------------------ render edges
def test_render_edges_resolution_order_and_holes(idx: CodeIndex):
    layout = edges_of(idx, "RENDERS", f"{APP}/layouts/default.vue")
    assert set(layout) == {f"{APP}/components/app/Header.vue", f"{APP}/components/app/Footer.vue"}
    assert layout[f"{APP}/components/app/Footer.vue"][1] == "app-footer"           # kebab, directory namespace
    page = edges_of(idx, "RENDERS", f"{APP}/pages/index.vue")
    assert set(page) == {f"{APP}/components/app/Header.vue", f"{APP}/components/http/Request.vue",
                         f"{APP}/components/shared/Item.vue"}                       # SharedItem by namespace rule
    req = edges_of(idx, "RENDERS", f"{APP}/components/http/Request.vue")
    assert req[f"{APP}/components/shared/Item.vue"][1] == "Item"                    # explicit import
    assert req[f"{APP}/components/shared/Chart.vue"][1] == "Chart"                  # defineAsyncComponent binding
    assert req[f"{APP}/components/http/Headers.vue"][1] == "HttpHeaders"            # components.d.ts
    # a recursive component has no self edge, only a count
    assert not any(e.src == e.dst for e in idx.edges) and idx.stats["self_references"] == 1


def test_external_tags_are_counted_not_edged_and_unresolved_ones_listed(idx: CodeIndex):
    s = idx.stats
    assert (s["tag_pairs_total"], s["tag_pairs_internal"], s["tag_pairs_external"],
            s["tag_pairs_unresolved"]) == (18, 10, 7, 1)
    assert s["external_tag_count"] == 7
    assert s["resolved_tag_rate"] == round(17 / 18, 4)
    assert s["unresolved_tags"] == [{"file": f"{APP}/pages/index.vue", "tag": "UnknownWidget",
                                     "reason": "no import, not in components.d.ts, no directory-namespace match"}]
    assert s["dynamic_component_count"] == 2 and s["dynamic_components_resolved"] == 1   # case 33 holes
    assert s["tag_resolution_by"]["dts"] == 7 and s["components_dts_entries"] == 6
    assert not any(e.dst in ("vue-tippy", "@acme/ui", "~icons") for e in idx.edges if e.kind == "RENDERS")


# ------------------------------------------------------------------------------------------- i18n
def test_i18n_keys_uses_and_literals(idx: CodeIndex):
    assert idx.i18n["home.title"] == "Welcome to Acme" and len(idx.i18n) == 12
    uses = {(u.file.removeprefix(APP + "/"), u.key, u.dynamic) for u in idx.key_uses}
    assert ("pages/index.vue", "home.cta", False) in uses                            # $t() inside :label=
    assert ("pages/index.vue", "request.url", False) in uses                         # t() inside :placeholder=
    assert {("pages/index.vue", "state.open", False), ("pages/index.vue", "state.closed", False)} <= uses
    assert ("pages/index.vue", "tab.", True) in uses                                 # template literal prefix
    assert ("components/app/Footer.vue", "shared.copy", False) in uses
    assert ("services/env.service.ts", "request.send", False) in uses                # this.t() in a class
    assert not any(k == "commented.out" for _, k, _ in uses)
    s = idx.stats
    assert (s["i18n_keys"], s["keys_used_distinct"], s["keys_used_in_locale"]) == (12, 8, 8)
    assert s["keys_used_not_in_locale"] == 0 and s["dynamic_key_uses"] == 1
    assert s["key_uses"] == len(idx.key_uses) == 10
    texts = {(lit.file.removeprefix(APP + "/"), lit.text) for lit in idx.literals}
    assert ("pages/index.vue", "Welcome back") in texts and ("pages/index.vue", "Send") in texts
    assert s["text_literals"] == 7 and s["attr_text_literals"] == 1


# ------------------------------------------------------------------------------------------ routes
def test_routes_and_layouts(idx: CodeIndex):
    routes = {r.pattern: (r.file.removeprefix(APP + "/pages/"), (r.layout or "").removeprefix(APP + "/layouts/"))
              for r in idx.routes}
    assert routes == {
        "/": ("index.vue", "default.vue"),
        "/settings": ("settings.vue", "empty.vue"),                  # definePage({ meta: { layout } })
        "/realtime": ("realtime.vue", "default.vue"),
        "/realtime/websocket": ("realtime/websocket.vue", "default.vue"),
        "/e/:id": ("e/_id.vue", "empty.vue"),                         # <route lang="yaml">
        "/:catchAll": ("_.vue", "default.vue"),
    }
    assert idx.stats["global_files"] == [f"{APP}/layouts/default.vue"]


def test_stats_class_counts_and_totals(idx: CodeIndex):
    s = idx.stats
    assert s["files_by_class"]["page"] == 6 and s["files_by_class"]["external"] == 5
    assert s["vue_files"] == 15 and s["files_total"] == len(idx.files)
    assert s["imports"] == 23 and s["renders"] == 9 and s["imports_external"] == 8
    assert sum(s["import_statements_by_resolution"].values()) == s["import_statements"]


# ------------------------------------------------------------------------------- reverse closure
def test_reverse_closure_is_hop_limited_and_reaches_through_renders(idx: CodeIndex):
    seed = f"{APP}/helpers/utils/format.ts"
    one = reverse_closure(idx.edges, [seed], max_hops=1)
    assert set(one) == {seed, f"{APP}/helpers/backend/runner.ts", f"{APP}/services/env.service.ts"}
    two = reverse_closure(idx.edges, [seed], max_hops=2)
    assert two[f"{APP}/helpers/backend/index.ts"] == 2 and f"{APP}/services/__tests__/env.service.spec.ts" in two
    deep = reverse_closure(idx.edges, [f"{APP}/components/http/Headers.vue"])
    assert deep[f"{APP}/components/http/Request.vue"] == 1 and deep[f"{APP}/pages/index.vue"] == 2
    assert reverse_closure(idx.edges, [f"{APP}/pages/index.vue"]) == {f"{APP}/pages/index.vue": 0}


def test_render_closure_is_the_reachability_prior_of_a_page_and_its_layout(idx: CodeIndex):
    page, layout = f"{APP}/pages/index.vue", f"{APP}/layouts/default.vue"
    reach = render_closure(idx.edges, [page, layout])
    assert set(reach) == {
        page, layout, f"{APP}/components/app/Header.vue", f"{APP}/components/app/Footer.vue",
        f"{APP}/components/http/Request.vue", f"{APP}/components/http/Headers.vue",
        f"{APP}/components/shared/Item.vue", f"{APP}/components/shared/Chart.vue", f"{DATA}/index.ts"}
    assert reach[f"{APP}/components/http/Headers.vue"] == 2                       # page -> Request -> Headers
    assert f"{APP}/helpers/utils/format.ts" not in reach                           # imports are not renders
    assert render_closure(idx.edges, [page], max_hops=1).keys() == {
        page, f"{APP}/components/app/Header.vue", f"{APP}/components/http/Request.vue",
        f"{APP}/components/shared/Item.vue"}


def test_index_is_deterministic(idx: CodeIndex):
    again = VueAdapter().index(FIXTURE, mini_code_config(), SHA)
    assert again.model_dump_json() == idx.model_dump_json()


# ----------------------------------------------------------------------------------------- run_index
def _target() -> TargetConfig:
    return TargetConfig(name="mini", display_name="Mini", base_url="https://example.test",
                        crawl=CrawlConfig(seed_urls=["https://example.test/"], egress_allow=["example.test"]),
                        spec=SpecConfig(kind="llms_full", url="https://example.test/llms.txt"),
                        code=mini_code_config())


@pytest.fixture
def stub_clone(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, object] = {}

    def fake(repo, ref, roots, *, files=(), user_agent=None, **_):
        seen.update(repo=repo, ref=ref, roots=list(roots), files=list(files), user_agent=user_agent)
        return FIXTURE, SHA

    monkeypatch.setattr(index_mod, "ensure_repo", fake)
    return seen


def test_run_index_writes_index_json_stats_and_evidence(tmp_path: Path, stub_clone):
    run = RunContext.create("mini", run_id="idx-1", runs_dir=tmp_path, settings=Settings())
    out = run_index(run, _target(), pr_number=6645)
    assert stub_clone["ref"] == "main" and stub_clone["repo"] == "acme/mini"
    assert stub_clone["roots"] == [APP, DATA]
    assert stub_clone["files"] == ["packages/acme-app/locales/en.json", f"{APP}/components.d.ts"]
    assert str(stub_clone["user_agent"]).startswith("blastradius/")           # identifying UA, not a browser UA
    on_disk = CodeIndex.model_validate_json((run.run_dir / "code" / "index.json").read_text())
    assert on_disk == out and out.target == "mini" and out.snapshot_id == SHA and out.adapter == "vue"
    assert out.stats["pr_number"] == 6645 and out.stats["pr_overlay"] == "not_applied"
    assert "run_overlay" in out.stats["pr_overlay_reason"]       # the CLI flag must not promise an overlay
    assert run.evidence.exists(out.stats["snapshot_evidence_id"])
    assert all(run.evidence.exists(edge_evidence_id(e)) for e in out.edges)
    assert not run.failures.items
    spans = [r["name"] for r in run.tracer.records if r["event"] == "start"]
    assert "git.sparse_checkout" in spans and "code.parse" in spans


def test_run_index_honours_ref_and_flags_missing_configured_files(tmp_path: Path, stub_clone, monkeypatch):
    cfg = _target()
    cfg.code.locale_files = ["packages/acme-app/locales/zz.json"]
    run = RunContext.create("mini", run_id="idx-2", runs_dir=tmp_path, settings=Settings())
    out = run_index(run, cfg, ref="v1.2.3")
    assert stub_clone["ref"] == "v1.2.3" and out.ref == "v1.2.3"
    assert "pr_number" not in out.stats
    assert out.i18n == {}
    assert [f["code"] for f in run.failures.items] == [str(FailureCode.CONFIG_INVALID)]
    assert "zz.json" in run.failures.items[0]["message"]


def test_run_index_records_a_failure_when_the_checkout_fails(tmp_path: Path, monkeypatch):
    from blastradius.code.clone import CloneError

    def boom(*_a, **_k):
        raise CloneError("ref 'nope' not found on origin")

    monkeypatch.setattr(index_mod, "ensure_repo", boom)
    run = RunContext.create("mini", run_id="idx-3", runs_dir=tmp_path, settings=Settings())
    with pytest.raises(CloneError):
        run_index(run, _target(), ref="nope")
    assert run.failures.items[0]["code"] == str(FailureCode.CODE_CHECKOUT_FAILED)
    assert json.loads((run.run_dir / "failures.jsonl").read_text().splitlines()[0])["code"] == "CODE_CHECKOUT_FAILED"


# ================================================================================ close-out additions
# Rules added after the link engineer's review of the live crawl: literal key uses, registered components,
# a linear template merge, lone surrogates, and the parse-partial marker. Each builds a tiny tree under tmp_path.
def build(tmp_path: Path, files: dict[str, str | bytes], **cfg_kw) -> CodeIndex:
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    kw = {"repo": "acme/co", "adapter": "vue", "roots": ["src"], "locale_files": ["locales/en.json"],
          "auto_import_dts": "src/components.d.ts", "alias": {"~/": "src/"}, **cfg_kw}
    return VueAdapter().index(tmp_path, CodeConfig(**kw), SHA)


EN = json.dumps({"navigation": {"rest": "REST", "realtime": "Realtime"}, "action": {"enable": "Enable"},
                 "other": {"unused": "Unused"}, "home": {"title": "Home"}})
LITERAL_TREE = {
    "locales/en.json": EN,
    "src/components/Sidenav.vue": (
        "<template><nav><a v-for=\"item in items\" :key=\"item.id\">{{ t(item.title) }}</a></nav></template>\n"
        "<script setup lang=\"ts\">\n"
        "const items = [{ id: 'rest', title: \"navigation.rest\" }, { id: 'rt', title: 'navigation.realtime' }]\n"
        "// title: \"other.unused\"\n"
        "const noise = ['not.a.key', 'navigation', \"navigation.rest.extra\"]\n"
        "</script>\n"),
    "src/components/EncodingPicker.vue": (
        "<template><p>{{ t(name(mode)) }}</p></template>\n"
        "<script setup lang=\"ts\">\n"
        "const name = (m: string) => { switch (m) { case 'enable': return \"action.enable\" } }\n"
        "</script>\n"),
    "src/components/Both.vue": (
        "<template><p>{{ t('navigation.rest') }}</p></template>\n"
        "<script setup lang=\"ts\">\nconst again = \"navigation.rest\"\n</script>\n"),
    "src/components/__tests__/Sidenav.spec.ts": "export const x = 'navigation.rest'\n",
    "src/helpers/menu.ts": "export const menu = { label: `home.title` }\n",
}


def test_a_script_literal_equal_to_a_known_key_is_a_static_key_use(tmp_path: Path):
    idx = build(tmp_path, LITERAL_TREE)
    uses = {(u.file, u.key, u.dynamic) for u in idx.key_uses}
    side = "src/components/Sidenav.vue"
    assert {(side, "navigation.rest", False), (side, "navigation.realtime", False),
            ("src/components/EncodingPicker.vue", "action.enable", False),
            ("src/helpers/menu.ts", "home.title", False)} <= uses
    assert not any(k in ("other.unused", "not.a.key", "navigation", "navigation.rest.extra") for _, k, _ in uses)
    assert not any(f.endswith("Sidenav.spec.ts") for f, _, _ in uses)             # tests never count
    assert len([u for u in idx.key_uses if u.file.endswith("Both.vue")]) == 1     # a call and a literal: one use
    s = idx.stats
    assert s["script_literal_key_uses"] == 4 and s["script_literal_keys_distinct"] == 4
    assert s["script_literal_key_use_sample"] == [
        {"file": "src/components/EncodingPicker.vue", "key": "action.enable"},
        {"file": "src/components/Sidenav.vue", "key": "navigation.realtime"},
        {"file": "src/components/Sidenav.vue", "key": "navigation.rest"},
        {"file": "src/helpers/menu.ts", "key": "home.title"}]
    assert s["key_uses"] == len(idx.key_uses) == 5
    assert s["keys_used_distinct"] == 1                  # `keys_used_*` keep meaning "named by a t() call"


def test_literal_key_uses_reach_the_link_stage_as_static_uses_of_the_file(tmp_path: Path):
    from blastradius.link.cascade import build_text_maps

    maps = build_text_maps(build(tmp_path, LITERAL_TREE))
    assert maps.key_files["navigation.rest"] == ("src/components/Both.vue", "src/components/Sidenav.vue")
    assert maps.file_keys["src/components/EncodingPicker.vue"] == ("action.enable",)
    assert maps.value_keys["enable"] == ("action.enable",)


def test_no_locale_means_no_literal_key_uses(tmp_path: Path):
    files = {k: v for k, v in LITERAL_TREE.items() if k != "locales/en.json"}
    idx = build(tmp_path, files)
    assert idx.stats["script_literal_key_uses"] == 0
    assert [(u.file, u.key) for u in idx.key_uses] == [("src/components/Both.vue", "navigation.rest")]   # the call


def test_part_uses_the_base_keys_when_the_locale_file_is_not_re_read(tmp_path: Path):
    """The overlay re-reads only changed files; a changed component must keep its literal key uses."""
    build(tmp_path, LITERAL_TREE)
    cfg = CodeConfig(repo="acme/co", adapter="vue", roots=["src"], locale_files=["locales/en.json"],
                     auto_import_dts="src/components.d.ts", alias={"~/": "src/"})
    known = {f.relative_to(tmp_path).as_posix() for f in tmp_path.rglob("*") if f.is_file()}
    only = {"src/components/Sidenav.vue"}
    blind = VueAdapter().index_part(tmp_path, cfg, known_paths=known, only=only)
    told = VueAdapter().index_part(tmp_path, cfg, known_paths=known, only=only,
                                   known_keys=["navigation.rest", "navigation.realtime"])
    assert blind.key_uses == [] and blind.i18n is None
    assert [(u.key, u.dynamic) for u in told.key_uses] == [("navigation.realtime", False), ("navigation.rest", False)]
    assert told.i18n is None                              # the overlay keeps the base table


REGISTRY_TREE = {
    **{f"src/components/{n}.vue": f"<template><p>{n} body</p></template>\n" for n in "ABCDEFLZ"},
    "src/platform.ts": "export const platform = { ui: {} }\n",
    "src/components/Base.vue": (
        "<template><div>\n"
        "  <component :is=\"current.component\" />\n"
        "  <component :is=\"ok ? B : 'C'\" />\n"
        "  <D />\n"
        "</div></template>\n"
        "<script setup lang=\"ts\">\n"
        "import A from './A.vue'\nimport B from './B.vue'\nimport C from './C.vue'\nimport D from './D.vue'\n"
        "import E from './E.vue'\nimport { platform } from '../platform'\n"
        "const L = defineAsyncComponent(() => import('./L.vue'))\n"
        "const unusedLazy = defineAsyncComponent(() => import('./Z.vue'))\n"
        "const steps = [{ component: A }, { component: markRaw(D) }, L]\n"
        "type Keep = typeof platform\n"
        "</script>\n"),
    # an import named only in the script, but nothing in the template asks for a dynamic component
    "src/components/NoHole.vue": (
        "<template><p>plain</p></template>\n"
        "<script setup lang=\"ts\">\nimport A from './A.vue'\nconst keep = [A]\n</script>\n"),
    # a member chain names no component, and an import that is never used as a value is not registered
    "src/components/Member.vue": (
        "<template><component :is=\"platform.ui.extra\" /></template>\n"
        "<script setup lang=\"ts\">\nimport { platform } from '../platform'\nimport F from './F.vue'\n"
        "const where = platform.ui\n</script>\n"),
    # the hole is explained by the expression, so the registry rule must not add the other import
    "src/components/Explained.vue": (
        "<template><component :is=\"ok ? B : A\" /></template>\n"
        "<script setup lang=\"ts\">\nimport A from './A.vue'\nimport B from './B.vue'\n"
        "import Z from './Z.vue'\nconst z = [Z]\n</script>\n"),
}


def test_registered_components_are_rendered_with_via_registered(tmp_path: Path):
    idx = build(tmp_path, REGISTRY_TREE)
    base = "src/components/Base.vue"
    got = {e.dst: e.via for e in idx.edges if e.kind == "RENDERS" and e.src == base}
    assert got == {
        "src/components/A.vue": "registered",          # in the registry array, rendered by `current.component`
        "src/components/B.vue": "registered",          # a branch of a ternary
        "src/components/C.vue": "registered",          # a quoted component name bound to an import
        "src/components/D.vue": "D",                    # tagged too: the tag stays the evidence
        "src/components/L.vue": "registered",          # a lazy binding that the script passes around
    }
    renders = {(e.src, e.dst) for e in idx.edges if e.kind == "RENDERS"}
    assert not any(src.endswith(("NoHole.vue", "Member.vue")) for src, _ in renders)
    assert ("src/components/Explained.vue", "src/components/Z.vue") not in renders
    assert {("src/components/Explained.vue", "src/components/A.vue"),
            ("src/components/Explained.vue", "src/components/B.vue")} <= renders
    assert not any(e.dst == "src/platform.ts" and e.kind == "RENDERS" for e in idx.edges)
    s = idx.stats
    assert s["registered_renders"] == 6 and s["renders"] == sum(e.kind == "RENDERS" for e in idx.edges)
    assert s["registered_renders_by"] == {"expression": 4, "registry": 2}
    assert s["dynamic_components_unexplained"] == 2          # `current.component` and `platform.ui.extra`


def test_a_registered_edge_is_evidence_like_any_render_edge(tmp_path: Path):
    from blastradius.code.index import edge_evidence_parts

    idx = build(tmp_path, REGISTRY_TREE)
    edge = next(e for e in idx.edges if e.kind == "RENDERS" and e.via == "registered")
    kind, ref, summary = edge_evidence_parts(edge)
    assert kind == "code.render" and summary.endswith("via registered") and ref == f"{edge.src}->{edge.dst}"
    assert render_closure(idx.edges, ["src/components/Base.vue"])["src/components/A.vue"] == 1


def test_many_template_blocks_merge_in_linear_time(tmp_path: Path, monkeypatch):
    """Invalid Vue with N `<template>` blocks: the tag merge must not compare every tag with every earlier one."""
    from blastradius.code.adapters import vue as vue_mod
    from blastradius.code.adapters.vue_sfc import TemplateScan

    comparisons = 0

    class Tag(str):
        __hash__ = str.__hash__

        def __eq__(self, other: object) -> bool:
            nonlocal comparisons
            comparisons += 1
            return str.__eq__(self, other)

    n = 400
    blocks = "".join(f"<template><X{i} /></template>\n" for i in range(n))
    monkeypatch.setattr(vue_mod, "scan_template", lambda src, *, is_native: TemplateScan(
        tags=[Tag(src[src.index("<X") + 1:src.index(" />")]), Tag("Shared")]))
    facts = vue_mod._analyse_vue(blocks)
    assert facts.tags == ["X0", "Shared", *(f"X{i}" for i in range(1, n))]       # first-seen order, no repeats
    assert "multiple_template_blocks" in facts.issues
    assert comparisons < 20 * n                      # list membership made this about n * n / 2 = 80,000


def test_template_tags_merge_keeps_first_seen_order_across_blocks():
    from blastradius.code.adapters.vue import _analyse_vue

    facts = _analyse_vue("<template><Beta /><Alpha /></template><template><Alpha /><Gamma /></template>")
    assert facts.tags == ["Beta", "Alpha", "Gamma"]


def test_a_lone_surrogate_escape_in_the_locale_is_replaced_counted_and_the_index_serialises(tmp_path: Path):
    locale = ('{"a": {"lone": "bad \\ud800 half", "pair": "ok \\ud83d\\ude00 ok", "\\udc00key": "k"},'
              ' "b": "fine"}')
    idx = build(tmp_path, {"locales/en.json": locale, "src/a.vue": "<template><p>{{ t('a.lone') }}</p></template>"})
    assert idx.i18n["a.lone"] == "bad \ufffd half"
    assert idx.i18n["a.pair"] == "ok \U0001f600 ok"             # a valid pair is one character, kept
    assert idx.i18n["a.\ufffdkey"] == "k" and idx.i18n["b"] == "fine"
    assert idx.stats["i18n_surrogates_replaced"] == 2          # one value, one key
    assert idx.stats["parse_error_kinds"] == {"locale_lone_surrogate": 1}
    again = CodeIndex.model_validate_json(idx.model_dump_json())      # used to raise PydanticSerializationError
    assert again.i18n == idx.i18n


def test_flatten_and_scrub_helpers_handle_collisions_in_document_order():
    from blastradius.code.i18n import scrub_surrogates, scrub_yaml_surrogates

    pairs = [("a\ud800", "first"), ("a\udc00", "second"), ("ok", "fine")]      # ruff folds surrogates in dict literals
    out, changed = scrub_surrogates(dict(pairs))
    assert out == {"a\ufffd": "second", "ok": "fine"} and changed == 2
    text, n = scrub_yaml_surrogates('a: "x \\ud800 y \\U0000DC00 z \\u00e9"\nb: plain\n')
    assert n == 2 and text == 'a: "x \\ufffd y \\ufffd z \\u00e9"\nb: plain\n'


def test_a_registered_edge_whose_target_is_gone_is_dropped_never_looked_up_as_a_tag_name(tmp_path: Path):
    """`via="registered"` is not a tag: a component that is literally called `Registered` must not inherit it."""
    from blastradius.models import CodeEdge

    files = {"src/components/Registered.vue": "<template><p>a decoy</p></template>\n",
             "src/components/Host.vue": "<template><p>host</p></template>\n"}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    cfg = CodeConfig(repo="acme/co", adapter="vue", roots=["src"], auto_import_dts="src/components.d.ts")
    gone = CodeEdge(src="src/components/Host.vue", dst="src/components/Old.vue", kind="RENDERS", via="registered")
    tag = CodeEdge(src="src/components/Host.vue", dst="src/components/Old.vue", kind="RENDERS", via="Registered")
    part = VueAdapter().index_part(tmp_path, cfg, known_paths=sorted(files), only=["src/components/Host.vue"],
                                   dangling=[gone, tag])
    assert part.dropped == [gone]
    assert [(e.via, e.dst) for e in part.redirected] == [("Registered", "src/components/Registered.vue")]
