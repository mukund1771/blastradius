"""Shared helpers for the reason tests: cached GitHub responses, a fake `gh`, a fake Anthropic client, and a
Neo4j "world" built from the real Hoppscotch index, the real 45-action crawl and a few hand-made links.

Nothing here reads a secret or touches the network. Database helpers write under a unique `__test__<random>`
target and `World.close` removes exactly that target.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from pydantic import BaseModel

from blastradius.code.index import edge_evidence_parts
from blastradius.config import Settings
from blastradius.graph.store import GraphConnectionError, GraphStore
from blastradius.llm import LLM
from blastradius.models import CodeIndex, Flow, Link, Requirement, ScreenState, Transition
from blastradius.reason.pr import GhError
from blastradius.run import RunContext
from blastradius.targets import TargetConfig

FIXTURES = Path(__file__).parent / "fixtures"
REASON = FIXTURES / "reason"
CRAWL = FIXTURES / "crawl" / "live_hoppscotch_45"
LINK = FIXTURES / "link"
SNAPSHOT = "63273f8e599e73f0fb38c0739314e8d46b6bc83d"
REPO = "hoppscotch/hoppscotch"
COMMON = "packages/hoppscotch-common/src/"
LOCALE = "packages/hoppscotch-common/locales/en.json"


def cfg() -> TargetConfig:
    return TargetConfig.load("hoppscotch")


def new_run(runs_dir: Path, run_id: str = "reason-test", target: str = "hoppscotch") -> RunContext:
    """A run directory with explicit settings, so no test reads a .env file."""
    return RunContext.create(target, run_id=run_id, runs_dir=runs_dir, settings=Settings(max_usd=100.0))


# --------------------------------------------------------------------------------------- fake gh
def _gz(name: str) -> str:
    with gzip.open(REASON / name, "rt", encoding="utf-8") as fh:
        return fh.read()


class FakeGh:
    """Serves the cached responses of one or more demo PRs; records every path asked for."""

    def __init__(self, numbers: tuple[int, ...] = (6645, 6641, 6658)) -> None:
        self.calls: list[str] = []
        self.prs: dict[int, dict[str, Any]] = {}
        self.files: dict[int, list[dict[str, Any]]] = {}
        self.compare: dict[int, dict[str, Any]] = {}
        for n in numbers:
            self.prs[n] = json.loads((REASON / f"pr_{n}.json").read_text())
            self.files[n] = json.loads((REASON / f"files_{n}.json").read_text())
            self.compare[n] = json.loads((REASON / f"compare_{n}.json").read_text())
        self.raw_by_ref: dict[tuple[str, str, str], str] = {}
        base_text = _gz("locale_base_ac145e7.json.gz")
        for n in (6645, 6641):
            pr = self.prs.get(n)
            if pr:
                self.raw_by_ref[(REPO, LOCALE, pr["base"]["sha"])] = base_text
        for n, name in ((6645, "locale_head_6645.json.gz"), (6641, "locale_head_6641.json.gz")):
            pr = self.prs.get(n)
            if pr:
                self.raw_by_ref[(pr["head"]["repo"]["full_name"], LOCALE, pr["head"]["sha"])] = _gz(name)
        self.fail_paths: set[str] = set()

    def set_head(self, number: int, sha: str) -> None:
        self.prs[number]["head"]["sha"] = sha

    def api(self, path: str) -> Any:
        self.calls.append(path)
        bare, _, query = path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if any(bare.startswith(p) for p in self.fail_paths):
            raise GhError(f"gh api failed (1) for {bare}: simulated")
        m = re.fullmatch(rf"repos/{REPO}/pulls/(\d+)", bare)
        if m and int(m.group(1)) in self.prs:
            return self.prs[int(m.group(1))]
        m = re.fullmatch(rf"repos/{REPO}/pulls/(\d+)/files", bare)
        if m and int(m.group(1)) in self.files:
            page, per = int(params.get("page", 1)), int(params.get("per_page", 30))
            rows = self.files[int(m.group(1))]
            return rows[(page - 1) * per: page * per]
        m = re.fullmatch(rf"repos/{REPO}/compare/([0-9a-f]{{40}})\.\.\.(.+)", bare)
        if m:
            for n, pr in self.prs.items():
                if pr["base"]["sha"] == m.group(1):
                    body = dict(self.compare[n])
                    page, per = int(params.get("page", 1)), int(params.get("per_page", 30))
                    body["files"] = body["files"][(page - 1) * per: page * per]
                    return body
        raise GhError(f"gh api failed (1) for {bare}: 404 (no fixture)")

    def raw(self, path: str) -> str:
        self.calls.append(path)
        bare, _, query = path.partition("?")
        m = re.fullmatch(r"repos/([^/]+/[^/]+)/contents/(.+)", bare)
        ref = unquote(query.split("ref=", 1)[1]) if "ref=" in query else ""
        if m and (m.group(1), unquote(m.group(2)), ref) in self.raw_by_ref:
            return self.raw_by_ref[(m.group(1), unquote(m.group(2)), ref)]
        raise GhError(f"gh api failed (1) for {bare}: 404 (no fixture)")


# ------------------------------------------------------------------------------------- fake llm
class _Usage:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 300, 60, 0, 0


class _Resp:
    def __init__(self, parsed: BaseModel):
        self.parsed_output, self.stop_reason, self.stop_details = parsed, "end_turn", None
        self._request_id, self.usage = "req_fake", _Usage()


class _Messages:
    def __init__(self, responder: Callable[[dict[str, Any]], BaseModel]):
        self.responder, self.calls = responder, []

    def parse(self, **kw: Any) -> _Resp:
        self.calls.append(kw)
        return _Resp(self.responder(kw))


class FakeClient:
    def __init__(self, responder: Callable[[dict[str, Any]], BaseModel]):
        self.messages = _Messages(responder)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.messages.calls


def make_llm(run: RunContext, client: Any, tmp_path: Path) -> LLM:
    llm = LLM(run, client=client, cache_dir=tmp_path / "cache" / "llm", settings=Settings(max_usd=100.0))
    llm._models.update({"cheap": "claude-haiku-5-5", "mid": "claude-sonnet-5-5", "strong": "claude-opus-5-5"})
    return llm


# --------------------------------------------------------------------------------------------- world
def real_index() -> CodeIndex:
    with gzip.open(LINK / "hoppscotch_main.code_index.json.gz", "rt", encoding="utf-8") as fh:
        return CodeIndex.model_validate_json(fh.read())


def neo4j_settings() -> Settings:
    env = os.environ.get
    return Settings(neo4j_uri=env("NEO4J_URI", "bolt://localhost:7687"), neo4j_user=env("NEO4J_USER", "neo4j"),
                    neo4j_password=env("NEO4J_PASSWORD", "blastradius-local"), max_usd=100.0)


def screens() -> list[ScreenState]:
    return [ScreenState.model_validate_json(line) for line in (CRAWL / "screens.jsonl").read_text().splitlines()
            if line.strip()]


def transitions() -> list[Transition]:
    return [Transition.model_validate_json(line) for line in (CRAWL / "transitions.jsonl").read_text().splitlines()
            if line.strip()]


# element observation id -> (file, confidence, method): the hand-made IMPLEMENTED_BY links
def link_plan(index: CodeIndex) -> dict[str, tuple[str, float, str]]:
    hub = "packages/hoppscotch-data/src/index.ts"
    ui_importer = min(e.src for e in index.edges if e.dst == hub and e.src.endswith(".vue")
                      and "/components/http/" in e.src)
    return {
        "el_30f24fa5dad0": (COMMON + "components/environments/Selector.vue", 0.9, "i18n_key"),     # Select environment
        "el_1d8b1db7fec9": (COMMON + "components/environments/index.vue", 0.7, "literal_text"),     # Global
        "el_5a873aa6da97": (COMMON + "components/environments/Add.vue", 0.9, "i18n_key"),           # Add new
        "el_e6013427e8b4": (COMMON + "pages/index.vue", 0.9, "i18n_key"),                           # Save My Workspace
        "el_2a3350d27104": (COMMON + "components/http/Headers.vue", 0.9, "i18n_key"),               # Headers
        "el_6c08185436a7": (COMMON + "components/collections/index.vue", 0.9, "i18n_key"),          # Collections
        "el_4c270bc987b1": (COMMON + "components/collections/index.vue", 0.9, "i18n_key"),          # New
        "el_681b8fb86edb": (COMMON + "components/collections/ImportExport.vue", 0.6, "llm_pick"),   # Import
        "el_74608e47c879": (ui_importer, 0.9, "i18n_key"),                                          # Send
        "el_944e4a310e89": (COMMON + "helpers/actions.ts", 0.9, "i18n_key"),                        # Switch protocol
    }


class World:
    """A run directory plus a Neo4j target holding the real code index, the real crawl, links, a requirement
    and a flow. Skips the calling test when Neo4j is unreachable."""

    def __init__(self, tmp_path: Path, *, with_links: bool = True, with_crawl: bool = True,
                 target: str | None = None):
        self.tmp_path = tmp_path
        self.target = target or f"__test__{uuid.uuid4().hex[:10]}"
        self.run = new_run(tmp_path / "runs", "reason-test", self.target)
        self.cfg = cfg().model_copy(update={"name": self.target})
        self.index = real_index().model_copy(update={"target": self.target})
        try:
            self.store = GraphStore.from_settings(neo4j_settings(), run=self.run, attempts=1, target=self.target)
        except GraphConnectionError as exc:
            pytest.skip(f"Neo4j is not reachable: {exc}")
        self.store.ensure_schema()
        self.requirement: Requirement | None = None
        self.flow: Flow | None = None
        try:
            self._load(with_links=with_links, with_crawl=with_crawl)
        except BaseException:
            self.close()
            raise

    def _load(self, *, with_links: bool, with_crawl: bool) -> None:
        for e in self.index.edges:
            self.run.evidence.add(*edge_evidence_parts(e))
        self.run.path("code", "index.json").write_text(self.index.model_dump_json(), encoding="utf-8")
        self.store.write_code(self.index, self.run.run_id)
        if not with_crawl:
            return
        scr = [s.model_copy(update={"target": self.target}) for s in screens()]
        self.store.write_crawl(scr, transitions(), self.run.run_id, "c81a0323d1f5b4f5")
        crawl = self.run.path("crawl", "x").parent
        for name in ("screens.jsonl", "transitions.jsonl", "summary.json"):
            shutil.copy(CRAWL / name, crawl / name)
        if not with_links:
            return
        links: list[Link] = []
        home = sorted(s.id for s in scr if s.url_pattern == "/")       # per-screen attribution, as the linker does
        for el, (file, conf, method) in sorted(link_plan(self.index).items()):
            ev = self.run.evidence.add(f"link.{method}", el, f"{el} -> {file}")
            links.append(Link(kind="IMPLEMENTED_BY", src=el, dst=file, confidence=conf, method=method,
                              evidence_id=ev, screen_ids=home))
        self.store.write_links(links, self.run.run_id)
        self.requirement = Requirement(
            id="req_envedit0001", target=self.target, text="User can select an environment and see its variables",
            quote="Select the environment from the dropdown to see its variables", source_url="https://docs.example/env",
            section="Environments", edition="cloud-web", ui_observable="yes", spec_sha="spec0001")
        self.store.write_requirements([self.requirement], self.run.run_id)
        ev = self.run.evidence.add("link.bm25_judge", "req_envedit0001", "requirement realised by the selector")
        self.store.write_links([Link(kind="REALIZED_BY", src="req_envedit0001", dst="el_30f24fa5dad0",
                                     confidence=0.8, method="bm25_judge", evidence_id=ev)], self.run.run_id)
        self.flow = Flow(id="flw_env0000001", name="Switch the active environment",
                         steps=["el_12bb7a04cc57", "el_30f24fa5dad0"], screens=["scr_fe5240a416f9"])
        self.store.write_flows([self.flow], self.run.run_id)

    def close(self) -> None:
        try:
            self.store.clear_target(self.target)
        finally:
            self.store.close()


def add_evidence(run: RunContext, n: int) -> list[str]:
    return [run.evidence.add("test.row", f"r{i}", f"row {i}") for i in range(n)]


# ------------------------------------------------------------------------------------------ overlay
def overlay_for(number: int, base: CodeIndex | None = None) -> CodeIndex:
    """The PR head overlay of a demo PR, rebuilt from the stored delta (produced once by the real
    `code.overlay.run_overlay`): the base index with the PR's changed files replaced."""
    from blastradius.models import CodeEdge, CodeFile, KeyUse

    delta = json.loads((REASON / "overlay_deltas.json").read_text())[str(number)]
    base = base or real_index()
    stats = delta["stats"]
    replaced = {*stats["overlay_added"], *stats["overlay_modified"], *stats["overlay_deleted"]}
    dropped = {(e["src"], e["dst"], e["kind"]) for e in delta["edges_removed"]}
    edges = [e for e in base.edges if (e.src, e.dst, e.kind) not in dropped]
    edges += [CodeEdge.model_validate(e) for e in delta["edges_added"]]
    i18n = {k: v for k, v in base.i18n.items() if k not in set(delta["i18n_removed"])}
    i18n.update(delta["i18n_changed"])
    key_uses = [u for u in base.key_uses if u.file not in replaced]
    key_uses += [KeyUse.model_validate(u) for u in delta["key_uses"]]
    files = [*base.files, *[CodeFile.model_validate(f) for f in delta["files_added"]]]
    return base.model_copy(update={
        "files": files, "edges": edges, "i18n": i18n, "key_uses": key_uses,
        "stats": {**base.stats, "pr_overlay": "applied", "pr_number": number, **stats}})


def overlay_fn_for(number: int) -> Callable[..., CodeIndex]:
    def fn(run: RunContext, cfg_: TargetConfig, base: CodeIndex, pr_number: int, head_repo: str, head_sha: str,
           changed: list[str]) -> CodeIndex:
        assert pr_number == number
        return overlay_for(number, base)

    return fn


def pr_and_retrieval(w: World, number: int, *, diffs=None, overlay=None, gh: FakeGh | None = None):
    """fetch_pr + mark_snapshot + locale diffs + retrieve for one demo PR against a World."""
    from blastradius.reason.pr import fetch_locale_diffs, fetch_pr, mark_snapshot
    from blastradius.reason.retrieve import retrieve

    gh = gh or FakeGh()
    pr = fetch_pr(w.run, w.cfg, gh, number, compare_to=SNAPSHOT)
    mark_snapshot(w.run, pr, w.store)
    locales = fetch_locale_diffs(w.run, w.cfg, gh, pr) if diffs is None else diffs
    retr = retrieve(w.run, w.cfg, w.store, pr, locales, base_index=w.index, overlay=overlay)
    return pr, retr, locales


# -------------------------------------------------------------------------------------- a small report
def sample_report(tmp_path: Path, *, stored: bool = False, glob: bool = False):
    """A consistent Report + Assembly + run for the narrate and verify tests (no database)."""
    from blastradius.models import Band
    from blastradius.reason.assemble import Assembly
    from blastradius.reason.risk import derive_risk
    from blastradius.report.model import (
        Claim,
        Report,
        ReportItem,
        RequirementRisk,
        SnapshotInfo,
        Undetermined,
    )

    run = new_run(tmp_path / "r", "sample")
    ev = [run.evidence.add("test.row", f"r{i}", f"row {i}") for i in range(8)]

    def item(i: int, tier: str, label: str, band: Band, conf: float, text: str, **kw: Any) -> ReportItem:
        return ReportItem(id=f"itm_{i:012x}", tier=tier, screen=kw.pop("screen", label), label=label,
                          what_changed=text, band=band, confidence=conf, evidence_ids=[ev[i]], **kw)

    direct = [item(1, "direct", "Home page", Band.CONFIRMED, 0.9,
                   "This screen is changed by this PR. Controls involved: “Select environment”.",
                   flows=["Switch the active environment"], requirement_ids=["req_envedit0001"])]
    indirect = [item(2, "indirect", "GraphQL page", Band.LIKELY, 0.6,
                     "This screen is probably affected by this PR. It uses the shared selector code.",
                     via="the shared selector code"),
                item(3, "indirect", "Settings page", Band.POSSIBLE, 0.4,
                     "This screen might be affected by this PR; worth a quick look.", via="the shared selector code")]
    globals_ = [item(4, "global", "The shared page frame (layout) around every screen", Band.CONFIRMED, 0.85,
                     "This PR edits the page layout (default). Every screen is changed by this PR to some degree.",
                     screen=None)] if glob else []
    stored_ = [item(5, "stored_data", "Saved collection data", Band.CONFIRMED, 0.85,
                    "The saved collection data format is changed by this PR.", screen=None)] if stored else []
    reqs = [RequirementRisk(requirement_id="req_envedit0001", statement="User can select an environment",
                            quote="Select the environment from the dropdown", section="Environments",
                            status="at_risk", band=Band.LIKELY, evidence_ids=[ev[6]])]
    items = [*direct, *indirect, *globals_, *stored_]
    claims, basis = [], {}
    for n, it in enumerate(items):
        c = Claim(id=f"clm_{n:012x}", text=f"{it.label}: {it.what_changed}", evidence_ids=list(it.evidence_ids),
                  band=it.band)
        claims.append(c)
        basis[c.id] = [{"evidence_id": it.evidence_ids[0], "confidence": it.confidence}]
    report = Report(
        target="hoppscotch", pr_number=6645, pr_title="edit environment values", risk_level="UNKNOWN",
        headline="", confidence_line="", direct=direct, indirect=indirect, global_changes=globals_,
        stored_data=stored_, requirements=reqs,
        undetermined=[Undetermined(code="STALE_BASE", subject="the whole PR", why="The base is behind.",
                                   what_to_check="Rebase.", evidence_ids=[ev[7]])],
        not_checked=["Only English text was compared."],
        snapshot=SnapshotInfo(target="hoppscotch", repo="hoppscotch/hoppscotch", indexed_commit="63273f8e599e",
                              pr_head_sha="b" * 40), claims=claims)
    report.risk_level = derive_risk(report)
    controls = {"itm_000000000001": [("button", "Select environment")],
                "itm_000000000002": [("button", "Save My Workspace")]}
    asm = Assembly(report=report, basis=basis, review=[], expected_direct=[i.id for i in direct], overflow=[],
                   impact={}, counts={"screens": 3, "flows": 1, "requirements": 1, "direct": 1, "indirect": 2,
                                      "indirect_shown": 2, "global": len(globals_), "stored_data": len(stored_),
                                      "screen_nodes": 5}, controls=controls)
    return report, asm, run
