"""The link stage against a live Neo4j: real 45-action crawl + real code index loaded under a unique
`__test__<random>` target, linked with the heuristic stand-ins, then read back from the graph.

Skips when Neo4j is unreachable. The target is removed in a finally block; nothing else is touched.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from link_helpers import (
    CRAWL,
    LINK_FIXTURES,
    FakeClient,
    cfg,
    combined_responder,
    make_llm,
    real_index,
)

from blastradius.config import Settings
from blastradius.graph.store import GraphConnectionError, GraphStore
from blastradius.link.flows import run_flows
from blastradius.link.gaps import run_gaps
from blastradius.link.link import run_link
from blastradius.models import CrawlSummary, Requirement, ScreenState, Transition
from blastradius.run import RunContext


def neo4j_settings() -> Settings:
    """Connection settings from the environment, without reading any .env file."""
    env = os.environ.get
    return Settings(neo4j_uri=env("NEO4J_URI", "bolt://localhost:7687"), neo4j_user=env("NEO4J_USER", "neo4j"),
                    neo4j_password=env("NEO4J_PASSWORD", "blastradius-local"))


@pytest.fixture
def target() -> str:
    return f"__test__{uuid.uuid4().hex[:10]}"


@pytest.fixture
def store(target: str) -> Iterator[GraphStore]:
    try:
        s = GraphStore.from_settings(neo4j_settings(), attempts=1, target=target)
    except GraphConnectionError as exc:
        pytest.skip(f"Neo4j is not reachable: {exc}")
    s.ensure_schema()
    try:
        yield s
    finally:
        s.clear_target(target)
        s.close()


def test_link_gaps_and_flows_land_in_the_graph(tmp_path: Path, target: str, store: GraphStore):
    config = cfg().model_copy(update={"name": target})
    run = RunContext.create(target, run_id="graph-link", runs_dir=tmp_path / "runs",
                            settings=Settings(max_usd=100.0))
    screens = [ScreenState.model_validate_json(l).model_copy(update={"target": target})
               for l in (CRAWL / "screens.jsonl").read_text().splitlines()]
    transitions = [Transition.model_validate_json(l) for l in (CRAWL / "transitions.jsonl").read_text().splitlines()]
    summary = CrawlSummary.model_validate_json((CRAWL / "summary.json").read_text()).model_copy(
        update={"target": target})
    reqs = [Requirement.model_validate({**r, "target": target})
            for r in json.loads((LINK_FIXTURES / "requirements.json").read_text())]
    index = real_index().model_copy(update={"target": target})

    run.path("crawl", "screens.jsonl").write_text("".join(s.model_dump_json() + "\n" for s in screens))
    run.path("crawl", "transitions.jsonl").write_text("".join(t.model_dump_json() + "\n" for t in transitions))
    run.path("crawl", "summary.json").write_text(summary.model_dump_json())
    run.path("spec", "requirements.json").write_text(json.dumps([r.model_dump(mode="json") for r in reqs]))
    run.path("spec", "stats.json").write_text(json.dumps({"stale": True}))
    run.path("code", "index.json").write_text(index.model_dump_json())

    store.write_code(index, run.run_id)
    store.write_crawl(screens, transitions, run.run_id, summary.bundle_hash, target=target)
    store.write_requirements(reqs, run.run_id, target=target)

    llm = make_llm(run, FakeClient(combined_responder()), tmp_path)
    # a store bound to the run rejects a link whose evidence id is not in the run's ledger
    bound = GraphStore.from_settings(neo4j_settings(), run, attempts=1, target=target)
    try:
        linked = run_link(run, config, bound, llm)
    finally:
        bound.close()
    graph = linked["graph"]
    assert graph["skipped_missing_src"] == graph["skipped_missing_dst"] == 0          # every id resolves
    assert graph["skipped_unknown_evidence"] == graph["skipped_invalid_confidence"] == 0
    assert graph["implemented_by"] > 0 and graph["realized_by"] == linked["links"]["realized_by"] > 0

    counts = store.counts(target)
    n_edges = counts["IMPLEMENTED_BY"]
    assert 0 < n_edges <= linked["links"]["implemented_by"]       # rows of one canonical element collapse
    assert n_edges == graph["implemented_by"] and counts["REALIZED_BY"] > 0
    assert graph["implemented_by"] + graph["duplicates_merged"] == linked["links"]["implemented_by"]

    gaps = run_gaps(run, config, store, llm)
    assert gaps["graph"]["subjects_unresolved"] == 0 and gaps["graph"]["searched_unresolved"] == 0
    assert store.counts(target)["Gap"] == gaps["gaps"]

    flows = run_flows(run, config, store)
    after = store.counts(target)
    assert after["Flow"] == len(flows) > 0 and after["STEP"] == sum(len(f.steps) for f in flows)

    # idempotent: a second pass over the same artifacts changes no node or edge
    run_link(run, config, store, llm)
    run_gaps(run, config, store, llm)
    run_flows(run, config, store)
    assert store.counts(target) == after
