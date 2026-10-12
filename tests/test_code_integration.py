"""Real-data integration: sparse clone of hoppscotch/hoppscotch pinned to the RECON commit (slow, networked).

Run alone with `pytest tests/test_code_integration.py`; deselect with `-m "not slow"`. The first run fetches
about 3 MB from GitHub (blob-less, depth 1); later runs reuse data/cache/repos. Skipped when the commit is
neither cached nor reachable.

Bands are +-5% of the value this indexer measured at the pinned commit (wave-1 fix pass, 2026-10-12): the
commit is fixed, so a move of more than 5% is a behaviour change, not noise. The RECON figure beside each
band came from an earlier, separate parse of the same commit and is there for orientation only.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from blastradius.code.adapters.vue import VueAdapter
from blastradius.code.clone import CloneError, ensure_repo
from blastradius.code.index import reverse_closure
from blastradius.models import CodeIndex, FileClass
from blastradius.targets import TargetConfig

pytestmark = pytest.mark.slow

RECON_SHA = "63273f8e599e73f0fb38c0739314e8d46b6bc83d"   # main == tag 2026.9.0 at recon time
COMMON = "packages/hoppscotch-common/src"


@pytest.fixture(scope="module")
def real() -> tuple[CodeIndex, Path, float]:
    cfg = TargetConfig.load("hoppscotch").code
    try:
        repo, sha = ensure_repo(cfg.repo, RECON_SHA, cfg.roots, files=[*cfg.locale_files, cfg.auto_import_dts or ""])
    except (CloneError, FileNotFoundError) as exc:
        pytest.skip(f"hoppscotch/hoppscotch@{RECON_SHA[:10]} is not cached and could not be fetched: {exc}")
    assert sha == RECON_SHA
    started = time.monotonic()
    idx = VueAdapter().index(repo, cfg, sha)
    return idx, repo, time.monotonic() - started


def test_inventory_matches_recon(real):
    idx, _, _ = real
    vue = [f for f in idx.files if f.path.startswith(COMMON + "/") and f.path.endswith(".vue")]
    # sha is None for the imported-but-uncommitted codegen output (a synthetic GENERATED node)
    ts = [f for f in idx.files if f.path.startswith(COMMON + "/") and f.path.endswith(".ts") and f.sha]
    assert len(vue) == 335 and len(ts) == 522                  # RECON section 1: 335 .vue, 522 .ts
    assert len(idx.i18n) == 2198                               # RECON: 2,198 leaf keys
    kinds = {f.path: f.kind for f in idx.files}
    assert kinds[f"{COMMON}/components.d.ts"] is FileClass.GENERATED
    assert kinds[f"{COMMON}/layouts/default.vue"] is FileClass.LAYOUT
    assert kinds["packages/hoppscotch-data/src/collection/v/12.ts"] is FileClass.DATA_SCHEMA
    assert kinds["@hoppscotch/ui"] is FileClass.EXTERNAL
    assert idx.stats["global_files"] == [f"{COMMON}/layouts/default.vue"]


def test_only_allow_listed_paths_are_indexed(real):
    idx, _, _ = real
    cfg = TargetConfig.load("hoppscotch").code
    roots = tuple(r + "/" for r in cfg.roots)
    paths = [f.path for f in idx.files if f.kind is not FileClass.EXTERNAL]
    assert [p for p in paths if not p.startswith(roots) and p not in cfg.locale_files] == []
    # the checkout holds 34 locale files (cone mode selects a directory); only en.json is listed or read
    assert [p for p in paths if "/locales/" in p] == cfg.locale_files


def test_render_graph_against_recon(real):
    idx, _, _ = real
    s = idx.stats
    assert 440 <= s["renders"] <= 486                           # measured 463; RECON: 455
    assert 1348 <= s["tag_pairs_total"] <= 1490                 # measured 1,419; RECON: 1,308 (file, tag) pairs
    assert 902 <= s["external_tag_count"] <= 996                # measured 949; RECON: 812 external
    assert s["resolved_tag_rate"] >= 0.947                      # measured 0.9972; RECON: ~97%
    assert 78 <= s["dynamic_component_count"] <= 86             # measured 82; RECON: 83 `<component :is>`
    assert 1257 <= s["tag_resolution_by"]["dts"] <= 1389        # measured 1,323
    assert s["tag_pairs_unresolved"] == len(s["unresolved_tags"]) == 4


def test_import_graph_hubs_and_barrels(real):
    idx, _, _ = real
    s = idx.stats
    assert 3541 <= s["imports"] <= 3913                         # measured 3,727; RECON: 2,810 (no alias table there)
    # the repo's real tsconfig/vite aliases are in targets/hoppscotch.yaml, so nothing needs the inference
    assert s["inferred_alias_imports"] == 0
    assert s["unresolved_imports"] == 8                         # measured 8: package.json, scss assets, `../steps`
    assert not any(u["specifier"] == "@hoppscotch/common/src/platform/instance"
                   for u in s["unresolved_imports_sample"])     # alias target missing: the workspace rule resolves it
    assert 236 <= s["hub_importers"]["@hoppscotch/data"] <= 260             # measured 248 without tests (RECON: 107/413)
    assert 269 <= s["hub_importers_with_tests"]["@hoppscotch/data"] <= 297  # measured 283 with the 35 test files
    assert 275 <= s["barrel_importers"] <= 303                  # measured 289; RECON: 248
    data_index = "packages/hoppscotch-data/src/index.ts"
    assert any(e.dst == data_index and e.src.startswith(COMMON) for e in idx.edges)


def test_the_generated_graphql_client_is_a_ts_node(real):
    idx, _, _ = real
    node = "packages/hoppscotch-selfhost-web/src/api/generated/graphql.ts"
    by_path = {f.path: f for f in idx.files}
    assert by_path[node].kind is FileClass.GENERATED and by_path[node].sha is None
    assert "packages/hoppscotch-selfhost-web/src/api/generated/graphql" not in by_path
    assert idx.stats["generated_missing_imports"] == 75


def test_nothing_is_silently_unread(real):
    idx, _, _ = real
    s = idx.stats
    assert s["index_empty"] is False and s["symlinks_skipped"] == 0
    assert [(r["root"], r["status"]) for r in s["code_roots"]] == [
        (r, "ok") for r in TargetConfig.load("hoppscotch").code.roots]
    assert set(s["configured_files"].values()) == {"ok"}
    # two real files make tree-sitter recover from an error (a call-signature overload and a .d.ts); both are
    # reported, and no import statement is lost (checked against a line scan when this test was written)
    assert s["parse_error_kinds"] == {"script_parse_error": 2}
    assert [e["file"] for e in s["parse_error_sample"]] == [
        f"{COMMON}/helpers/actions.ts", f"{COMMON}/types/post-request.d.ts"]


def test_i18n_keys_against_recon(real):
    idx, _, _ = real
    s = idx.stats
    assert 1330 <= s["keys_used_in_locale"] <= 1470             # measured 1,400; RECON: 1,401 distinct keys used
    assert 3137 <= s["key_call_sites"] <= 3467                  # measured 3,302; RECON: about 3,200 call sites
    assert s["dynamic_key_uses"] == 6 and s["dynamic_key_uses"] < 0.02 * s["key_call_sites"]   # case 33: < 1-2%
    assert 39 <= s["text_literals"] <= 43                       # measured 41; RECON: about 8 (a stricter definition)


def test_routes_match_the_pages_tree(real):
    idx, _, _ = real
    routes = {r.pattern for r in idx.routes}
    assert {"/", "/graphql", "/realtime", "/realtime/websocket", "/settings", "/import", "/:catchAll"} <= routes
    by_file = {r.file.removeprefix(f"{COMMON}/pages/"): r for r in idx.routes}
    assert by_file["index.vue"].layout == f"{COMMON}/layouts/default.vue"
    assert by_file["enter.vue"].layout == f"{COMMON}/layouts/empty.vue"      # <route lang="yaml"> meta.layout
    assert by_file["view/_id/_version.vue"].pattern == "/view/:id/:version"


def test_services_radius_for_pr_6645_matches_recon(real):
    idx, _, _ = real
    services = [f"{COMMON}/services/current-environment-value.service.ts",
                f"{COMMON}/services/secret-environment.service.ts"]
    kinds = {f.path: f.kind for f in idx.files}

    def vue_count(hops: int | None) -> int:
        reached = reverse_closure(idx.edges, services, max_hops=hops)
        return sum(1 for p in reached if p.endswith(".vue") and kinds[p] is not FileClass.TEST)

    assert 89 <= vue_count(2) <= 99                             # measured 94; RECON: 2 hops 93/90 .vue (28%)
    assert 162 <= vue_count(3) <= 179                           # measured 170; RECON: 3 hops 158/156 (47%)
    assert 247 <= vue_count(None) <= 273                        # measured 260; RECON: unbounded 252/335 (75%)


def test_component_table_fallback_reproduces_the_committed_dts(real):
    from blastradius.code.adapters.vue_components import namespaced_name, parse_components_dts

    idx, repo, _ = real
    dts_path = f"{COMMON}/components.d.ts"
    table = parse_components_dts((repo / dts_path).read_text(), dts_path)
    internal = {n: e.path for n, e in table.items() if e.path}
    prefix = f"{COMMON}/components/"
    assert len(internal) >= 300
    wrong = {n: p for n, p in internal.items() if namespaced_name(p.removeprefix(prefix)) != n}
    assert wrong == {}
    on_disk = {f.path for f in idx.files if f.path.startswith(prefix) and f.path.endswith(".vue")}
    assert on_disk == set(internal.values())


def test_indexing_is_fast_enough_to_run_on_every_pr(real):
    _, _, seconds = real
    assert seconds < 30                                         # measured 1.7 s on a warm cache


# ------------------------------------------------------------------------------- close-out additions
def test_real_script_literals_that_name_keys_are_static_uses(real):
    """The two components the link engineer named: keys live in the script, a template renders `t(<variable>)`."""
    idx, _, _ = real
    uses = {(u.file, u.key, u.dynamic) for u in idx.key_uses}
    assert (f"{COMMON}/components/app/Sidenav.vue", "navigation.rest", False) in uses
    assert (f"{COMMON}/components/app/Sidenav.vue", "navigation.realtime", False) in uses
    assert (f"{COMMON}/components/smart/EncodingPicker.vue", "action.enable", False) in uses
    s = idx.stats
    assert 120 <= s["script_literal_key_uses"] <= 155           # measured 137 on 2026-10-12
    assert s["key_uses"] == len(idx.key_uses)
    assert 1330 <= s["keys_used_in_locale"] <= 1470             # unchanged: still what t() calls name


def test_real_registered_components_and_the_partial_marker(real):
    idx, _, _ = real
    registered = {(e.src, e.dst) for e in idx.edges if e.via == "registered"}
    assert (f"{COMMON}/components/importExport/Base.vue",
            f"{COMMON}/components/importExport/ImportExportSteps/ImportSummary.vue") in registered
    assert (f"{COMMON}/components/history/Personal.vue", f"{COMMON}/components/history/rest/Card.vue") in registered
    assert idx.stats["registered_renders"] == len(registered) and 4 <= len(registered) <= 9    # measured 6
    # a package object (`platform.ui.extra`) or a function (`pick(x)`) named in an :is hole is never a component
    assert not any(dst.endswith("platform/index.ts") for _, dst in registered)
    # two files tree-sitter cannot fully parse among 1,273 are counted, and the index is still complete
    assert idx.stats["parse_errors"] == 2 and idx.stats["parse_partial"] is False
