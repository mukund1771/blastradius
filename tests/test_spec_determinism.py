"""Cross-process determinism of the spec artifacts (rule 4: byte-identical under any PYTHONHASHSEED).

Requirement ids are content hashes, so any set-order dependence in tags, conflicts or dedupe would change ids
and artifacts between processes while an in-process test stays green.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# A document that exercises every order-sensitive path: cross-page duplicates with conflicting editions and
# polarities (conflict tags), siblings, a negated edition callout, a cue-gated negative, several chunks.
_SNIPPET = r"""
import hashlib, json, sys, tempfile
from pathlib import Path
import httpx
sys.path.insert(0, "tests")
from test_spec_support import doc, doc_client, item, make_llm, make_run, page_of, real_cfg, user_of
from blastradius.spec.ingest import run_ingest

PIN = "You can pin a request to the top of the list."
REN = "You can rename a workspace from the workspace settings button."
NEG = "Folders can no longer be pinned to the top of the sidebar list."
content = doc(
    ("Alpha", f"Intro.\n\n<Info>Only available in the Desktop App.</Info>\n\n{PIN}\n\n{REN}"),
    ("Beta", f"Intro.\n\n<Info>Only available in the Self-Hosted Edition.</Info>\n\n{PIN}"),
    ("Gamma", f"Intro.\n\n<Note>Pinning is coming soon.</Note>\n\n{PIN}\n\n{NEG}"),
    ("Delta", "<Warning>Available on Hoppscotch Cloud and the Desktop app. They are not supported in "
              f"Self-Hosted Edition.</Warning>\n\n## Ask\n\n{REN}"))

def respond(kw):
    u = user_of(kw)
    if "# Alpha\n" in u:
        return page_of(item(PIN, edition="desktop"), item(REN, edition="all"))
    if "# Beta\n" in u:
        return page_of(item(PIN, edition="self-host"))
    if "# Gamma\n" in u:
        return page_of(item(PIN, polarity="must_not_exist"), item(NEG, polarity="must_exist"),
                       item(NEG, text="User can pin folders again"))
    return page_of(item(REN, edition="self-host"), item(REN, edition="desktop"))

tmp = Path(tempfile.mkdtemp())
run = make_run(tmp)
llm, _ = make_llm(run, tmp, respond)
reqs = run_ingest(run, real_cfg(), llm, client=doc_client(content))
spec = run.run_dir / "spec"
stats = json.loads((spec / "stats.json").read_text())
for volatile in ("fetched_at",):
    stats.pop(volatile)
blob = ((spec / "requirements.json").read_text() + (spec / "pages.json").read_text()
        + json.dumps(stats, sort_keys=True))
assert len(reqs) == 3, len(reqs)
print(hashlib.sha256(blob.encode()).hexdigest())
"""


def _digest(seed: int) -> str:
    env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": str(REPO / "src")}
    out = subprocess.run([sys.executable, "-c", _SNIPPET], cwd=REPO, env=env, capture_output=True, text=True,
                         timeout=180, check=False)
    assert out.returncode == 0, out.stderr[-1500:]
    return out.stdout.strip().splitlines()[-1]


def test_spec_artifacts_are_identical_across_hash_seeds():
    digests = {_digest(seed) for seed in (0, 1, 2, 3, 5, 97)}
    assert len(digests) == 1, f"spec artifacts depend on the hash seed: {digests}"
    assert hashlib.sha256(b"").hexdigest()          # keeps the import honest
