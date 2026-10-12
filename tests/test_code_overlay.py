"""PR head overlay (case 39, D24): the base index with a PR's changed files replaced, against a local git origin.

The origin has `main` (the indexed base) and a pull-request head that adds two components, renames a third,
re-points one `components.d.ts` entry, edits the locale file, deletes a helper and touches unindexed files.
The strongest check is equivalence: for a head that is a direct descendant of the base, overlaying the changed
files must give the same graph as indexing the head commit from scratch.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from blastradius.code.adapters.vue import VueAdapter
from blastradius.code.clone import ensure_repo
from blastradius.code.index import edge_evidence_id, reverse_closure
from blastradius.code.overlay import run_overlay
from blastradius.config import Settings
from blastradius.failures import FailureCode
from blastradius.models import CodeIndex, FileClass
from blastradius.run import RunContext
from blastradius.targets import CodeConfig, CrawlConfig, SpecConfig, TargetConfig

APP = "packages/acme-app/src"
DATA = "packages/acme-data/src"
LOCALE = "packages/acme-app/locales/en.json"
PR = 7

BASE_FILES = {
    f"{APP}/components.d.ts": """declare module 'vue' {
  export interface GlobalComponents {
    AppHeader: typeof import('./components/app/Header.vue')['default']
    AppFooter: typeof import('./components/app/Footer.vue')['default']
    SharedItem: typeof import('./components/shared/Item.vue')['default']
    HoppSmartInput: typeof import('@acme/ui')['HoppSmartInput']
  }
}
""",
    f"{APP}/components/app/Header.vue": "<template><header><SharedItem /><HoppSmartInput /></header></template>\n"
                                        "<script setup lang=\"ts\">\nimport { fmt } from '../../helpers/fmt'\n</script>\n",
    f"{APP}/components/app/Footer.vue": "<template><footer>Copyright Acme</footer></template>\n",
    f"{APP}/components/shared/Item.vue": "<template><li>{{ t('shared.item') }}</li></template>\n",
    f"{APP}/pages/index.vue": "<template><div><AppHeader /><AppFooter /><p>{{ t('home.title') }}</p></div></template>\n"
                              "<script setup lang=\"ts\">\nimport { fmt } from '~/helpers/fmt'\n</script>\n",
    f"{APP}/layouts/default.vue": "<template><main><slot /></main></template>\n",
    f"{APP}/helpers/fmt.ts": "export const fmt = (s: string) => s\n",
    f"{APP}/helpers/legacy.ts": "export const old = 1\n",
    f"{APP}/services/env.service.ts": "import { fmt } from '../helpers/fmt'\nimport { old } from '../helpers/legacy'\n"
                                      "export const label = () => fmt(t('env.label'))\n",
    LOCALE: json.dumps({"home": {"title": "Welcome"}, "shared": {"item": "Item"}, "env": {"label": "Env"}}, indent=2),
    f"{DATA}/index.ts": "export const VERSION = 1\n",
    "README.md": "readme\n",
    "pnpm-lock.yaml": "lockfileVersion: 9\n",
}

HEAD_FILES = {
    f"{APP}/components.d.ts": """declare module 'vue' {
  export interface GlobalComponents {
    AppHeader: typeof import('./components/app/Header.vue')['default']
    AppFooter: typeof import('./components/app/Footer.vue')['default']
    EnvironmentsEditor: typeof import('./components/environments/Editor.vue')['default']
    EnvironmentsInput: typeof import('./components/environments/Input.vue')['default']
    SharedItem: typeof import('./components/shared/ItemRow.vue')['default']
    HoppSmartInput: typeof import('@acme/ui')['HoppSmartInput']
  }
}
""",
    f"{APP}/components/environments/Editor.vue": "<template><div><EnvironmentsInput />{{ t('env.edit') }}</div></template>\n"
                                                 "<script setup lang=\"ts\">\nimport { fmt } from '~/helpers/fmt'\n</script>\n",
    f"{APP}/components/environments/Input.vue": "<template><input placeholder=\"Value goes here\" /></template>\n",
    f"{APP}/components/shared/ItemRow.vue": "<template><li>{{ t('shared.item') }} {{ t('shared.row') }}</li></template>\n",
    f"{APP}/pages/index.vue": "<template><div><AppHeader /><EnvironmentsEditor /><p>{{ t('home.title') }}</p></div></template>\n"
                              "<script setup lang=\"ts\">\nimport { fmt } from '~/helpers/fmt'\n</script>\n",
    LOCALE: json.dumps({"home": {"title": "Welcome"}, "shared": {"item": "Item", "row": "Row"},
                        "env": {"label": "Env", "edit": "Edit value"}}, indent=2),
    "README.md": "readme, updated\n",
}
HEAD_DELETED = [f"{APP}/components/shared/Item.vue", f"{APP}/helpers/legacy.ts"]
CHANGED = sorted([*HEAD_FILES, *HEAD_DELETED, "docs/guide.md", "pnpm-lock.yaml"])


def git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def put(origin: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (origin / rel).parent.mkdir(parents=True, exist_ok=True)
        (origin / rel).write_text(text)


def commit(origin: Path, message: str) -> str:
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", message)
    return git(origin, "rev-parse", "HEAD")


def mini_code() -> CodeConfig:
    return CodeConfig(
        repo="acme/mini", ref="main", adapter="vue", roots=[APP, DATA], locale_files=[LOCALE],
        auto_import_dts=f"{APP}/components.d.ts", global_files=[f"{APP}/layouts/default.vue"],
        hub_packages=["@acme/data"], alias={"~/": f"{APP}/"})


def mini_target() -> TargetConfig:
    return TargetConfig(name="mini", display_name="Mini", base_url="https://example.test",
                        crawl=CrawlConfig(seed_urls=["https://example.test/"], egress_allow=["example.test"]),
                        spec=SpecConfig(kind="llms_full", url="https://example.test/llms.txt"), code=mini_code())


class World:
    """A local origin with the base on `main` and the PR head on `refs/pull/7/head`, plus a base index."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.origin = tmp_path / "origin"
        self.origin.mkdir()
        git(self.origin, "init", "-q", "-b", "main")
        git(self.origin, "config", "uploadpack.allowFilter", "true")
        git(self.origin, "config", "uploadpack.allowAnySHA1InWant", "true")
        put(self.origin, BASE_FILES)
        self.base_sha = commit(self.origin, "base")
        git(self.origin, "checkout", "-q", "-b", "pr")
        put(self.origin, HEAD_FILES)
        for rel in HEAD_DELETED:
            (self.origin / rel).unlink()
        (self.origin / "docs").mkdir()
        (self.origin / "docs/guide.md").write_text("guide\n")
        (self.origin / "pnpm-lock.yaml").write_text("lockfileVersion: 10\n")
        self.head_sha = commit(self.origin, "pr head")
        git(self.origin, "update-ref", f"refs/pull/{PR}/head", self.head_sha)
        git(self.origin, "checkout", "-q", "main")
        self.url = f"file://{self.origin}"
        self.cache = tmp_path / "cache"
        self.cfg = mini_target()
        self.baseline, _ = ensure_repo("acme/mini", "main", self.cfg.code.roots,
                                       files=[LOCALE, f"{APP}/components.d.ts"], cache_root=self.cache,
                                       remote=self.url, user_agent="t")
        self.base_index = VueAdapter().index(self.baseline, self.cfg.code, self.base_sha, target="mini", ref="main")
        self.run = RunContext.create("mini", run_id="ov", runs_dir=tmp_path / "runs", settings=Settings())

    def overlay(self, **kw) -> CodeIndex:
        args = {"head_repo": "forker/mini", "head_sha": self.head_sha, "changed_paths": CHANGED,
                "cache_root": self.cache, "remote": self.url}
        args.update(kw)
        return run_overlay(self.run, self.cfg, self.base_index, PR, args.pop("head_repo"), args.pop("head_sha"),
                           args.pop("changed_paths"), **args)

    def full_index_at_head(self) -> CodeIndex:
        work, sha = ensure_repo("acme/mini", self.head_sha, self.cfg.code.roots, files=[LOCALE, f"{APP}/components.d.ts"],
                                cache_root=self.cache, remote=self.url, user_agent="t", worktree="full-head")
        return VueAdapter().index(work, self.cfg.code, sha, target="mini", ref=self.head_sha)

    def tree_fingerprint(self) -> str:
        h = hashlib.sha256()
        for path in sorted(p for p in self.baseline.rglob("*") if p.is_file() and ".git" not in p.parts):
            h.update(str(path.relative_to(self.baseline)).encode() + path.read_bytes())
        return h.hexdigest()


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def failures_of(run: RunContext, code: FailureCode) -> list[dict]:
    return [f for f in run.failures.items if f["code"] == str(code)]


# ------------------------------------------------------------------------------------- the overlay
def test_overlay_equals_a_fresh_index_of_the_head_commit(world: World):
    out = world.overlay()
    fresh = world.full_index_at_head()
    for name in ("files", "edges", "routes", "i18n", "key_uses", "literals"):
        assert getattr(out, name) == getattr(fresh, name), name
    assert out.target == "mini" and out.snapshot_id == world.base_sha and out.adapter == "vue"
    for key in ("files_total", "files_in_tree", "files_by_class", "vue_files", "imports", "imports_external",
                "renders", "i18n_keys", "key_uses", "routes", "components_dts_entries"):
        assert out.stats[key] == fresh.stats[key], key               # the recounted stats match a fresh index


def test_changed_added_and_deleted_files_are_reported_in_stats(world: World):
    s = world.overlay().stats
    assert s["pr_overlay"] == "applied" and s["pr_number"] == PR
    assert s["overlay_head_sha"] == world.head_sha and s["overlay_head_repo"] == "forker/mini"
    assert s["overlay_base_snapshot"] == world.base_sha
    assert s["overlay_added"] == [f"{APP}/components/environments/Editor.vue", f"{APP}/components/environments/Input.vue",
                                  f"{APP}/components/shared/ItemRow.vue"]
    assert s["overlay_modified"] == [LOCALE, f"{APP}/components.d.ts", f"{APP}/pages/index.vue"]
    assert s["overlay_deleted"] == sorted(HEAD_DELETED)
    assert s["overlay_skipped"] == {"unindexed": 3}                     # README.md, docs/guide.md, pnpm-lock.yaml
    assert (s["overlay_dts_entries_base"], s["overlay_dts_entries_head"], s["components_dts_entries"]) == (4, 6, 6)
    assert s["files_total"] == len(world.overlay().files) and s["overlay_stats_from_base"]


def test_added_components_are_not_orphaned_and_the_renamed_one_is_re_resolved(world: World):
    base, out = world.base_index, world.overlay()
    editor, field = f"{APP}/components/environments/Editor.vue", f"{APP}/components/environments/Input.vue"
    assert not any(e.dst in (editor, field) for e in base.edges)
    rendered = {(e.src, e.dst, e.via) for e in out.edges if e.kind == "RENDERS"}
    assert (f"{APP}/pages/index.vue", editor, "EnvironmentsEditor") in rendered
    assert (editor, field, "EnvironmentsInput") in rendered
    assert (f"{APP}/components/app/Header.vue", f"{APP}/components/shared/ItemRow.vue", "SharedItem") in rendered
    assert not any(f"{APP}/components/shared/Item.vue" in (e.src, e.dst) for e in out.edges)
    assert f"{APP}/pages/index.vue" in reverse_closure(out.edges, [field])
    s = out.stats
    assert s["overlay_edges_reresolved"] == 1 and s["overlay_edges_dropped"] == 1    # legacy.ts import has no target
    assert out.i18n["env.edit"] == "Edit value" and "env.edit" not in world.base_index.i18n


def test_deleted_files_leave_the_index_and_new_files_are_classified(world: World):
    out = world.overlay()
    kinds = {f.path: f for f in out.files}
    assert f"{APP}/helpers/legacy.ts" not in kinds and f"{APP}/components/shared/Item.vue" not in kinds
    assert kinds[f"{APP}/components/environments/Editor.vue"].kind is FileClass.COMPONENT
    assert kinds[f"{APP}/components/environments/Editor.vue"].package == "@acme/app"
    blob = git(world.origin, "rev-parse", f"{world.head_sha}:{APP}/components/environments/Editor.vue")
    assert kinds[f"{APP}/components/environments/Editor.vue"].sha == blob
    assert any(r.file == f"{APP}/pages/index.vue" and r.layout == f"{APP}/layouts/default.vue" for r in out.routes)


def test_the_baseline_worktree_and_the_base_index_are_not_modified(world: World):
    before_tree, before_index = world.tree_fingerprint(), world.base_index.model_dump_json()
    head_before = git(world.baseline, "rev-parse", "HEAD")
    world.overlay()
    assert git(world.baseline, "rev-parse", "HEAD") == head_before == world.base_sha
    assert git(world.baseline, "status", "--porcelain") == ""
    assert world.tree_fingerprint() == before_tree
    assert world.base_index.model_dump_json() == before_index
    assert (world.baseline / f"{APP}/helpers/legacy.ts").exists()             # still the base content


def test_the_overlay_checks_out_only_the_requested_files(world: World):
    world.overlay()
    worktrees = [p for p in world.cache.iterdir() if p.name.startswith("acme__mini@")]
    assert len(worktrees) == 1 and world.head_sha[:12] in worktrees[0].name
    present = sorted(p.relative_to(worktrees[0]).as_posix() for p in worktrees[0].rglob("*")
                     if p.is_file() and ".git" not in p.parts)
    assert present == sorted(p for p in HEAD_FILES if p.startswith("packages/"))          # no README, no lockfile
    assert f"{APP}/helpers/fmt.ts" not in present                                          # unchanged: never fetched


def test_overlay_records_evidence_and_an_artifact(world: World):
    base_ids = {edge_evidence_id(e) for e in world.base_index.edges}
    out = world.overlay()
    new_edges = [e for e in out.edges if edge_evidence_id(e) not in base_ids]
    assert new_edges and all(world.run.evidence.exists(edge_evidence_id(e)) for e in new_edges)
    assert world.run.evidence.exists(out.stats["overlay_evidence_id"])
    on_disk = CodeIndex.model_validate_json((world.run.run_dir / "code" / f"overlay_{PR}.json").read_text())
    assert on_disk == out
    spans = [r["name"] for r in world.run.tracer.records if r["event"] == "start"]
    assert "git.fetch_pr_head" in spans and "code.overlay" in spans
    assert not world.run.failures.items


def test_overlay_is_idempotent(world: World):
    first = world.overlay().model_dump_json()
    assert world.overlay().model_dump_json() == first


# ------------------------------------------------------------------------------------ hostile input
def test_hostile_and_unindexable_paths_are_skipped_and_counted(world: World):
    paths = [*CHANGED, "../outside.ts", "/etc/passwd", "a/../../b.ts", "", f"{APP}/x\x00.ts", "C:\\win.ts",
             f"{APP}/components/app/Header.vue", f"{APP}/components/app/Header.vue", f"{APP}/ghost.vue"]
    s = world.overlay(changed_paths=paths).stats
    assert s["overlay_skipped"] == {"absent_in_both": 1, "invalid_path": 6, "unindexed": 3}
    assert f"{APP}/components/app/Header.vue" in s["overlay_modified"]        # present at head, listed twice: once


def test_a_symlink_at_head_is_never_read_and_is_recorded(world: World, tmp_path: Path):
    secret = tmp_path / "secret.ts"
    secret.write_text("import leaked from 'leaked-package-name'\nexport const leak = t('leaked.key')\n")
    git(world.origin, "checkout", "-q", "pr")
    (world.origin / f"{APP}/helpers/fmt.ts").unlink()
    os.symlink(secret, world.origin / f"{APP}/helpers/fmt.ts")
    linked = commit(world.origin, "link")
    git(world.origin, "update-ref", f"refs/pull/{PR}/head", linked)
    git(world.origin, "checkout", "-q", "main")
    out = world.overlay(head_sha=linked, changed_paths=[*CHANGED, f"{APP}/helpers/fmt.ts"])
    assert not any("leaked" in f.path for f in out.files) and not any(u.key == "leaked.key" for u in out.key_uses)
    assert f"{APP}/helpers/fmt.ts" not in {f.path for f in out.files}        # a link is not a regular file
    assert out.stats["overlay_symlinks_skipped"] == 1
    assert len(failures_of(world.run, FailureCode.CODE_PARSE_PARTIAL)) == 1


def test_head_files_with_syntax_errors_are_counted_like_in_the_base_index(world: World):
    git(world.origin, "checkout", "-q", "pr")
    put(world.origin, {f"{APP}/helpers/fmt.ts": "import { x } from './legacy'\nexport const = = =\n"})
    broken = commit(world.origin, "broken")
    git(world.origin, "checkout", "-q", "main")
    out = world.overlay(head_sha=broken, changed_paths=[*CHANGED, f"{APP}/helpers/fmt.ts"])
    assert out.stats["overlay_parse_error_kinds"] == {"script_parse_error": 1}
    assert len(failures_of(world.run, FailureCode.CODE_PARSE_PARTIAL)) == 1


# ----------------------------------------------------------------------------------- pinning, failure
def test_a_locale_file_removed_at_the_head_leaves_no_messages(world: World):
    git(world.origin, "checkout", "-q", "pr")
    (world.origin / LOCALE).unlink()
    gone = commit(world.origin, "drop locale")
    git(world.origin, "checkout", "-q", "main")
    out = world.overlay(head_sha=gone)
    assert out.i18n == {} and LOCALE in out.stats["overlay_deleted"] and out.stats["i18n_keys"] == 0
    assert world.base_index.i18n                              # the base index keeps its messages


def test_the_pinned_head_sha_wins_when_the_pull_ref_moved(world: World):
    git(world.origin, "checkout", "-q", "pr")
    put(world.origin, {f"{APP}/components/environments/Input.vue": "<template><input /></template>\n",
                       f"{APP}/components/environments/Extra.vue": "<template><b>Extra</b></template>\n"})
    newer = commit(world.origin, "force-pushed")
    git(world.origin, "update-ref", f"refs/pull/{PR}/head", newer)
    git(world.origin, "checkout", "-q", "main")
    out = world.overlay(changed_paths=[*CHANGED, f"{APP}/components/environments/Extra.vue"])
    assert out.stats["pr_overlay"] == "applied" and out.stats["overlay_head_sha"] == world.head_sha
    assert out.stats["overlay_pull_ref_moved"] is True and out.stats["overlay_pull_ref_tip"] == newer
    assert f"{APP}/components/environments/Extra.vue" not in {f.path for f in out.files}   # not in the pinned head
    fresh = world.full_index_at_head()
    assert out.files == fresh.files and out.edges == fresh.edges
    assert world.overlay().stats["overlay_pull_ref_moved"] is True


def test_an_unfetchable_head_degrades_to_the_base_index_with_a_failure_row(world: World):
    out = world.overlay(head_sha="d" * 40)                   # the pull ref exists but is a different commit
    assert out.stats["pr_overlay"] == "failed" and out.stats["pr_overlay_reason"]
    assert out.files == world.base_index.files and out.edges == world.base_index.edges
    rows = failures_of(world.run, FailureCode.CODE_CHECKOUT_FAILED)
    assert len(rows) == 1 and "refs/pull/7/head" in rows[0]["message"]
    git(world.origin, "update-ref", "-d", f"refs/pull/{PR}/head")
    assert world.overlay(head_sha="e" * 40).stats["pr_overlay"] == "failed"      # no pull ref at all now
    assert len(failures_of(world.run, FailureCode.CODE_CHECKOUT_FAILED)) == 2


def test_a_bad_repo_name_or_sha_is_refused_before_any_git_runs(world: World):
    for kw in ({"head_sha": "not-a-sha"}, {"head_repo": "../../etc"}, {"head_repo": "a b/c"}):
        out = world.overlay(**kw)
        assert out.stats["pr_overlay"] == "failed"
    assert not (world.cache / "acme__mini@not-a-sha").exists()


def test_blob_shas_are_git_blob_shas_even_when_gitattributes_would_convert(world: World):
    git(world.origin, "checkout", "-q", "pr")
    put(world.origin, {".gitattributes": "*.vue text eol=crlf\n*.ts filter=evil\n"})
    attrs = commit(world.origin, "attrs")
    git(world.origin, "checkout", "-q", "main")
    out = world.overlay(head_sha=attrs)
    editor = f"{APP}/components/environments/Editor.vue"
    assert {f.path: f.sha for f in out.files}[editor] == git(world.origin, "rev-parse", f"{attrs}:{editor}")


# ----------------------------------------------------------------------------------------- determinism
_SNIPPET = """
import hashlib, sys, tempfile
from pathlib import Path
sys.path.insert(0, %(tests)r)
import test_code_overlay as t
tmp = Path(tempfile.mkdtemp())
w = t.World(tmp)
print(hashlib.sha256(w.overlay().model_dump_json().encode()).hexdigest())
"""


def test_overlay_is_identical_across_hash_seeds():
    repo = Path(__file__).resolve().parents[1]
    code = _SNIPPET % {"tests": str(repo / "tests")}
    digests = set()
    for seed in (0, 1, 7):
        env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": str(repo / "src"),
               "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}
        out = subprocess.run([sys.executable, "-c", code], cwd=repo, env=env, capture_output=True, text=True,
                             timeout=180, check=False)
        assert out.returncode == 0, out.stderr[-1000:]
        digests.add(out.stdout.strip().splitlines()[-1])
    assert len(digests) == 1


# ------------------------------------------------------------------------------- the real PR #6645
REAL_CLONE = Path(__file__).resolve().parents[1] / "data" / "cache" / "repos" / "hoppscotch__hoppscotch"
RECON_SHA = "63273f8e599e73f0fb38c0739314e8d46b6bc83d"
PR_6645_HEAD = "64ba4ff75eff7bef868b010947f50e9b6b326540"            # a fork head (safwanyp/hoppscotch), pinned
COMMON = "packages/hoppscotch-common"
PR_6645_FILES = [
    f"{COMMON}/locales/en.json", f"{COMMON}/src/components.d.ts",
    f"{COMMON}/src/components/environments/CurrentValueInput.vue", f"{COMMON}/src/components/environments/Editor.vue",
    f"{COMMON}/src/components/environments/Selector.vue", f"{COMMON}/src/components/environments/index.vue",
    f"{COMMON}/src/components/environments/my/Environment.vue", f"{COMMON}/src/components/environments/my/index.vue",
    f"{COMMON}/src/components/environments/teams/Environment.vue",
    f"{COMMON}/src/components/environments/teams/index.vue",
    f"{COMMON}/src/services/__tests__/current-environment-value.service.spec.ts",
    f"{COMMON}/src/services/__tests__/secret-environment.service.spec.ts",
    f"{COMMON}/src/services/current-environment-value.service.ts",
    f"{COMMON}/src/services/secret-environment.service.ts",
]


@pytest.mark.slow
def test_overlay_of_the_real_pr_6645_fork_head(tmp_path: Path):
    head = subprocess.run(["git", "-C", str(REAL_CLONE), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=False)
    if head.returncode != 0 or head.stdout.strip() != RECON_SHA:
        pytest.skip("the cached hoppscotch clone is not at the RECON commit")
    cfg = TargetConfig.load("hoppscotch")
    before = subprocess.run(["git", "-C", str(REAL_CLONE), "status", "--porcelain"], capture_output=True,
                            text=True, check=False).stdout
    base = VueAdapter().index(REAL_CLONE, cfg.code, RECON_SHA, target="hoppscotch", ref=RECON_SHA)
    run = RunContext.create("hoppscotch", run_id="ov-real", runs_dir=tmp_path / "runs", settings=Settings())
    out = run_overlay(run, cfg, base, 6645, "safwanyp/hoppscotch", PR_6645_HEAD, PR_6645_FILES,
                      cache_root=tmp_path / "cache")
    if out.stats["pr_overlay"] != "applied":
        pytest.skip(f"PR head not fetchable here: {out.stats.get('pr_overlay_reason')}")
    s = out.stats
    editor = f"{COMMON}/src/components/environments/Editor.vue"
    value_input = f"{COMMON}/src/components/environments/CurrentValueInput.vue"
    assert s["overlay_added"] == [value_input, editor]
    assert len(s["overlay_modified"]) == 12 and s["overlay_deleted"] == []
    # the PR branch is stale (case 41): its table has the two new rows but lacks two rows main gained since
    assert (s["overlay_dts_entries_base"], s["overlay_dts_entries_head"]) == (372, 372)
    assert s["overlay_pull_ref_moved"] is False and s["overlay_parse_errors"] == 0
    rendered = {e.dst for e in out.edges if e.kind == "RENDERS"}
    assert editor in rendered and value_input in rendered
    assert not any(e.dst in (editor, value_input) for e in base.edges)               # orphaned in the base index
    assert len(out.files) == len(base.files) + 2
    assert subprocess.run(["git", "-C", str(REAL_CLONE), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=False).stdout.strip() == RECON_SHA
    assert subprocess.run(["git", "-C", str(REAL_CLONE), "status", "--porcelain"], capture_output=True, text=True,
                          check=False).stdout == before


# ================================================================================= close-out additions
def _inject_issue(monkeypatch: pytest.MonkeyPatch, path: str, kind: str) -> None:
    real = VueAdapter.index_part

    def spy(self, *args, **kw):
        part = real(self, *args, **kw)
        part.issues[path] = sorted({*part.issues.get(path, []), kind})
        return part

    monkeypatch.setattr(VueAdapter, "index_part", spy)


def test_a_head_file_that_could_not_be_decoded_makes_the_overlay_partial(world: World, monkeypatch):
    _inject_issue(monkeypatch, f"{APP}/pages/index.vue", "undecodable_bytes")
    s = world.overlay().stats
    assert s["parse_partial"] is True and s["overlay_parse_partial"] is True
    assert s["overlay_parse_partial_kinds"] == ["undecodable_bytes"]
    assert s["overlay_parse_errors"] == 1                       # the plain count is kept alongside


def test_a_head_file_with_a_syntax_error_is_counted_but_not_partial(world: World, monkeypatch):
    _inject_issue(monkeypatch, f"{APP}/pages/index.vue", "script_parse_error")
    s = world.overlay().stats
    assert s["parse_partial"] is False and s["overlay_parse_partial"] is False
    assert s["overlay_parse_errors"] == 1 and s["overlay_parse_partial_kinds"] == []


def test_an_overlay_never_makes_an_incomplete_base_complete(world: World):
    world.base_index.stats["parse_partial"] = True
    s = world.overlay().stats
    assert s["parse_partial"] is True and s["overlay_parse_partial"] is False


def test_overlay_keeps_literal_key_uses_of_a_changed_component_when_the_locale_is_unchanged(
        tmp_path: Path, monkeypatch):
    """The locale file is not among the changed files, so the head's table is not read: the base keys decide."""
    nav = f"{APP}/components/Nav.vue"
    template = "<template><a v-for=\"i in items\">{{ t(i.title) }}</a></template>\n"
    base = {
        nav: template + "<script setup lang=\"ts\">\nconst items = [{ title: 'nav.old' }]\n</script>\n",
        LOCALE: json.dumps({"nav": {"home": "Home", "old": "Old", "extra": "Extra"}}),
        f"{APP}/components.d.ts": "declare module 'vue' {\n  export interface GlobalComponents {\n  }\n}\n",
    }
    head = {nav: template + "<script setup lang=\"ts\">\nconst items = [{ title: 'nav.home' }, "
                            "{ title: \"nav.extra\" }, { title: 'nav.missing' }]\n</script>\n"}
    monkeypatch.setitem(globals(), "BASE_FILES", base)
    monkeypatch.setitem(globals(), "HEAD_FILES", head)
    monkeypatch.setitem(globals(), "HEAD_DELETED", [])
    w = World(tmp_path)
    assert [(u.key, u.dynamic) for u in w.base_index.key_uses] == [("nav.old", False)]
    out = w.overlay(changed_paths=[nav])
    assert [(u.file, u.key, u.dynamic) for u in out.key_uses] == [
        (nav, "nav.extra", False), (nav, "nav.home", False)]       # nav.missing is not a key of the base locale
    assert out.key_uses == w.full_index_at_head().key_uses         # what a fresh index of the head gives
