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

REPO = Path(__file__).resolve().parents[1]

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
                         timeout=120)
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
