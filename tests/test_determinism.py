"""Cross-process determinism.

Python randomises str hashing per process, so any output that depends on set/dict-of-set iteration order
differs between runs while an in-process test stays green. Ids and evidence derive from these outputs (D27),
so every artifact-producing stage must be identical under different PYTHONHASHSEED values.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RECON_SHA = "63273f8e599e73f0fb38c0739314e8d46b6bc83d"
REAL_CLONE = REPO / "data" / "cache" / "repos" / "hoppscotch__hoppscotch"

# tests/fixtures/code/det: one component importing 24 distinct '~icons/lucide/*' specifiers that all collapse into
# one external node, plus three spellings of one child tag. The first-encountered `via` used to depend on the
# per-process string hash seed; the old logic produced several distinct digests across seeds on this tree.
_CODE_INDEX_SNIPPET = """
import hashlib, sys
from pathlib import Path
from blastradius.code.adapters.vue import VueAdapter
from blastradius.targets import CodeConfig
APP = "packages/acme-app/src"
cfg = CodeConfig(repo="acme/det", ref="main", adapter="vue", roots=[APP],
                 locale_files=["packages/acme-app/locales/en.json"], auto_import_dts=f"{APP}/components.d.ts",
                 alias={"~/": f"{APP}/"})
idx = VueAdapter().index(Path("tests/fixtures/code/det"), cfg, "b" * 40)
print(hashlib.sha256(idx.model_dump_json().encode()).hexdigest())
"""


def _digest_under_seed(snippet: str, seed: int) -> str:
    env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": f"{REPO / 'src'}"}
    out = subprocess.run([sys.executable, "-c", snippet], cwd=REPO, env=env, capture_output=True, text=True,
                         timeout=120, check=False)
    assert out.returncode == 0, out.stderr[-800:]
    return out.stdout.strip().splitlines()[-1]


def test_vue_code_index_is_identical_across_hash_seeds():
    digests = {_digest_under_seed(_CODE_INDEX_SNIPPET, seed) for seed in (0, 1, 2, 3, 5, 97)}
    assert len(digests) == 1, f"code index depends on hash seed: {digests}"


def test_digest_helper_actually_varies_with_a_seed_dependent_output():
    """Guards the guard: a set-order dependent snippet must differ across seeds, or this test file is blind."""
    snippet = "print(''.join(sorted({'a%d' % i for i in range(50)}, key=hash)))"
    assert len({_digest_under_seed(snippet, s) for s in (0, 1, 2, 3)}) > 1
    assert hashlib.sha256(b"x").hexdigest()  # keeps the import honest


# tree-sitter's QueryCursor.captures() returns nodes in a per-process order (review wave 1), so anything built
# from the capture order must be re-sorted into document order before it reaches an edge, a `via` or a sample.
_IMPORT_ORDER_SNIPPET = """
from blastradius.code.jsmodules import extract_imports
src = "\\n".join(f"import {{ a{i} }} from '~icons/lucide/m{i:02d}'" for i in range(40)) + "\\n"
src += "\\n".join(f"const l{i} = () => import('./lazy{i:02d}')" for i in range(10)) + "\\n"
src += "// t('c.one')\\nconst z = 1 // t('c.two')\\n"
facts = extract_imports(src)
print(",".join(b.specifier for b in facts.imports))
print(",".join(str(a) for a, _ in facts.comment_spans))
"""


def test_script_imports_come_back_in_document_order_in_every_process():
    expected = [f"~icons/lucide/m{i:02d}" for i in range(40)] + [f"./lazy{i:02d}" for i in range(10)]
    for seed in (0, 1, 2, 3):
        env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": f"{REPO / 'src'}"}
        out = subprocess.run([sys.executable, "-c", _IMPORT_ORDER_SNIPPET], cwd=REPO, env=env, capture_output=True,
                             text=True, timeout=120, check=False)
        assert out.returncode == 0, out.stderr[-800:]
        specifiers, comment_starts = out.stdout.strip().splitlines()
        assert specifiers.split(",") == expected, f"seed {seed}: imports are not in document order"
        starts = [int(n) for n in comment_starts.split(",")]
        assert starts == sorted(starts)


_REAL_INDEX_SNIPPET = """
import hashlib
from pathlib import Path
from blastradius.code.adapters.vue import VueAdapter
from blastradius.targets import TargetConfig
cfg = TargetConfig.load("hoppscotch").code
idx = VueAdapter().index(Path("data/cache/repos/hoppscotch__hoppscotch"), cfg, "RECON_SHA")
print(hashlib.sha256(idx.model_dump_json().encode()).hexdigest())
""".replace("RECON_SHA", RECON_SHA)


@pytest.mark.slow
def test_real_clone_index_is_identical_across_five_hash_seeds():
    head = subprocess.run(["git", "-C", str(REAL_CLONE), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=False)
    if head.returncode != 0 or head.stdout.strip() != RECON_SHA:
        pytest.skip(f"the cached hoppscotch clone is not at {RECON_SHA[:10]}")
    digests = {seed: _digest_under_seed(_REAL_INDEX_SNIPPET, seed) for seed in (0, 1, 2, 3, 4)}
    assert len(set(digests.values())) == 1, f"real index depends on the process: {digests}"


# The close-out rules iterate sets (literal keys, registered targets, issue kinds): a tree that exercises all of
# them must hash the same in every process, and the digest must include the new stats.
_CLOSEOUT_TREE = """
import hashlib, json, sys, tempfile
from pathlib import Path
from blastradius.code.adapters.vue import VueAdapter
from blastradius.targets import CodeConfig

root = Path(tempfile.mkdtemp())
files = {
    "locales/en.json": json.dumps({"nav": {f"k{i:02d}": f"Item {i}" for i in range(30)}}),
    "src/components/Base.vue": (
        "<template><component :is=\\"current.component\\" /><component :is=\\"a ? C00 : 'C01'\\" /></template>\\n"
        "<template><Z1 /><Z0 /></template>\\n<script setup lang=\\"ts\\">\\n"
        + "".join(f"import C{i:02d} from './C{i:02d}.vue'\\n" for i in range(24))
        + "const all = [" + ", ".join(f"C{i:02d}" for i in range(24)) + "]\\n"
        + "const keys = [" + ", ".join(f"'nav.k{i:02d}'" for i in range(30)) + "]\\n"
        "</script>\\n"),
    "src/bad.ts": "export const = = {{{{\\n",
    "src/enc.ts": b"export const x = 1 // \\xff\\n",
}
for i in range(24):
    files[f"src/components/C{i:02d}.vue"] = f"<template><p>c{i}</p></template>\\n"
for rel, content in files.items():
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
cfg = CodeConfig(repo="acme/co", ref="main", adapter="vue", roots=["src"], locale_files=["locales/en.json"])
"""
_CLOSEOUT_INDEX = _CLOSEOUT_TREE + """
idx = VueAdapter().index(root, cfg, "c" * 40)
assert idx.stats["registered_renders"] >= 24 and idx.stats["script_literal_key_uses"] == 30, idx.stats
assert idx.stats["parse_partial"] is True
print(hashlib.sha256(idx.model_dump_json().encode()).hexdigest())
"""
_CLOSEOUT_PART = _CLOSEOUT_TREE + """
part = VueAdapter().index_part(root, cfg, known_paths=sorted(files), only=["src/components/Base.vue"],
                               known_keys=[f"nav.k{i:02d}" for i in range(30)])
assert len(part.key_uses) == 30 and any(e.via == "registered" for e in part.edges)
dump = [[e.model_dump() for e in part.edges], [u.model_dump() for u in part.key_uses], part.issues]
print(hashlib.sha256(json.dumps(dump, sort_keys=True).encode()).hexdigest())
"""


def test_close_out_rules_are_identical_across_hash_seeds():
    digests = {_digest_under_seed(_CLOSEOUT_INDEX, seed) for seed in (0, 1, 2, 3, 5, 97)}
    assert len(digests) == 1, f"registered / literal / partial rules depend on the hash seed: {digests}"


def test_the_overlay_part_is_identical_across_hash_seeds():
    digests = {_digest_under_seed(_CLOSEOUT_PART, seed) for seed in (0, 1, 2, 4)}
    assert len(digests) == 1, digests
