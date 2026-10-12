"""Real-data integration: sparse clone of hoppscotch/hoppscotch pinned to the RECON commit (slow, networked).

Run alone with `pytest tests/test_code_integration.py`; deselect with `-m "not slow"`. The first run fetches
about 3 MB from GitHub (blob-less, depth 1); later runs reuse data/cache/repos. Skipped when the commit is
neither cached nor reachable. Numbers are compared to docs/RECON.md as bands, never tuned to match.
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
    assert 400 <= s["renders"] <= 520                           # RECON: 455
    assert 1250 <= s["tag_pairs_total"] <= 1500                 # RECON: 1,308 (file, tag) pairs
    assert 700 <= s["external_tag_count"] <= 1000               # RECON: 812 external
    assert s["resolved_tag_rate"] >= 0.95                       # RECON: ~97%
    assert 60 <= s["dynamic_component_count"] <= 110            # RECON: 83 `<component :is>`
    assert s["tag_resolution_by"]["dts"] > 1000
    assert len(s["unresolved_tags"]) <= 50 and s["tag_pairs_unresolved"] == len(s["unresolved_tags"])


def test_import_graph_hubs_and_barrels(real):
    idx, _, _ = real
    s = idx.stats
    assert s["imports"] >= 2810                                 # RECON: 2,810 (we also resolve @composables/* etc.)
    # the repo's real tsconfig/vite aliases are in targets/hoppscotch.yaml now, so nothing needs the inference
    assert s["inferred_alias_imports"] == 0
    assert s["unresolved_imports"] < 30
    assert s["hub_importers"]["@hoppscotch/data"] > 107         # RECON: 107 of 413 main-chunk app files
    assert s["barrel_importers"] > 200                          # RECON: 248
    data_index = "packages/hoppscotch-data/src/index.ts"
    assert any(e.dst == data_index and e.src.startswith(COMMON) for e in idx.edges)


def test_i18n_keys_against_recon(real):
    idx, _, _ = real
    s = idx.stats
    assert 1350 <= s["keys_used_in_locale"] <= 1450             # RECON: 1,401 distinct keys used
    assert 3000 <= s["key_call_sites"] <= 3600                  # RECON: about 3,200 call sites
    assert s["dynamic_key_uses"] < 0.02 * s["key_call_sites"]   # case 33: dynamic keys are < 1-2% of calls
    assert 10 <= s["text_literals"] <= 80                       # RECON: about 8 (a stricter definition)


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

    assert 85 <= vue_count(2) <= 105                            # RECON: 2 hops 93/90 .vue (28%)
    assert 140 <= vue_count(3) <= 185                           # RECON: 3 hops 158/156 (47%)
    assert 235 <= vue_count(None) <= 285                        # RECON: unbounded 252/335 (75%)


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
    assert seconds < 30
