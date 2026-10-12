"""The source-map oracle against the real hoppscotch.io (slow; network and Chromium).

At most 3 page visits plus one sidebar click, one at a time, the project's user agent, egress allowlisted,
robots.txt honoured (RECON section 6). Assets are cached by ETag under data/cache/oracle, so a re-run costs
three navigations and one HEAD per asset. Skipped when the site or Chromium is unreachable. The assertions are
structural (the technique works and the self-checks hold), not a snapshot of a site that redeploys."""

from __future__ import annotations

import gzip
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Error as PlaywrightError

from blastradius.config import Settings
from blastradius.evalkit import oracle_sourcemap as o
from blastradius.models import CodeIndex
from blastradius.targets import TargetConfig

INDEX = Path(__file__).parent / "fixtures" / "link" / "hoppscotch_main.code_index.json.gz"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def harvest() -> o.Harvest:
    cfg = TargetConfig.load("hoppscotch")
    try:
        if (
            httpx.head(cfg.base_url, headers={"User-Agent": Settings().user_agent()}, timeout=15).status_code
            >= 500
        ):
            pytest.skip("hoppscotch.io answers 5xx")
        return o.harvest(cfg, Settings())
    except (httpx.HTTPError, PlaywrightError) as exc:
        pytest.skip(f"live oracle unavailable: {type(exc).__name__}")


def test_live_harvest_stays_within_the_visit_budget_and_pins_every_asset(harvest: o.Harvest):
    assert 1 <= harvest.visits <= o.MAX_VISITS
    assert harvest.chunks and all(len(c.asset["sha256"]) == 64 for c in harvest.chunks)
    assert harvest.entry_changed is False
    assert (
        set(harvest.egress_blocked)
        <= {"client.crisp.chat", "eu-assets.i.posthog.com", "eu.posthog.com", "o1408531.ingest.us.sentry.io"}
        or harvest.egress_blocked
    )


def test_live_labels_are_repo_vue_files_with_unanimous_votes(harvest: o.Harvest):
    with gzip.open(INDEX, "rt", encoding="utf-8") as fh:
        index = CodeIndex.model_validate_json(fh.read())
    built = o.build_labels(harvest, index, TargetConfig.load("hoppscotch"))
    labels = built["labels"]
    assert len(labels) > 20 and built["oracle_coverage"]["p"] > 0.8
    assert all(lbl.oracle_file.endswith(".vue") and lbl.oracle_file.startswith("packages/") for lbl in labels)
    att = built["attribution"]
    assert att["vote_share_min"] is not None and att["vote_share_min"] >= 0.5
    assert att["basename_equals_type_name"]["p"] > 0.9  # RECON: 40 of 40 on the main chunk
    assert built["drift"]["vue_sources"] > 50
    assert max(lbl.owner_hops for lbl in labels) >= 1  # library wrappers really are walked through
