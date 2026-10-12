"""Shared helpers for the link tests: a fake Anthropic client, heuristic stand-ins for the two LLM calls, run
directories built from the real crawl and code-index fixtures, and a recording graph store. Nothing here
touches the network or reads a secret."""

from __future__ import annotations

import gzip
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from blastradius.config import Settings
from blastradius.link.req_to_ui import CandidateVerdict, JudgeOut
from blastradius.link.text import tokens
from blastradius.link.ui_to_code import PickItem, PickOut
from blastradius.llm import LLM
from blastradius.models import CodeIndex, Flow, Gap, Link
from blastradius.run import RunContext
from blastradius.targets import TargetConfig

FIXTURES = Path(__file__).parent / "fixtures"
CRAWL = FIXTURES / "crawl" / "live_hoppscotch_45"
LINK_FIXTURES = FIXTURES / "link"


# ------------------------------------------------------------------------------------------ fake client
class _Resp:
    def __init__(self, parsed: BaseModel):
        self.parsed_output = parsed
        self.stop_reason = "end_turn"
        self.stop_details = None
        self._request_id = "req_fake"

        class _Usage:
            input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 200, 40, 0, 0

        self.usage = _Usage()


class _Messages:
    def __init__(self, responder: Callable[[dict[str, Any]], BaseModel]):
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kw: Any) -> _Resp:
        self.calls.append(kw)
        return _Resp(self.responder(kw))


class FakeClient:
    def __init__(self, responder: Callable[[dict[str, Any]], BaseModel]):
        self.messages = _Messages(responder)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.messages.calls

    def prompts(self, purpose_marker: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if purpose_marker in c["system"]]


class RaisingClient:
    """Stands for a missing API key: every call raises the SDK's TypeError."""

    def __init__(self) -> None:
        self.calls = 0

        class _M:
            def parse(inner, **kw: Any) -> Any:
                self.calls += 1
                raise TypeError("Could not resolve authentication method")

        self.messages = _M()


def make_llm(run: RunContext, client: Any, tmp_path: Path) -> LLM:
    llm = LLM(run, client=client, cache_dir=tmp_path / "cache" / "llm", settings=Settings(max_usd=100.0))
    llm._models["mid"] = "claude-sonnet-5-5"
    return llm


# --------------------------------------------------------------------------- heuristic stand-in responders
_CTRL = re.compile(r'^(e\d+): role=(\S+) name=("(?:[^"\\]|\\.)*")', re.MULTILINE)
_CAND = re.compile(r"^(c\d+): (\S+) \((\S+)\) displays: (.*)$", re.MULTILINE)


def pick_responder(*, none_for: tuple[str, ...] = (), external_for: tuple[str, ...] = ()) -> Callable:
    """Stand-in for the model: picks the candidate that displays the control's name; NONE otherwise."""

    def respond(kw: dict[str, Any]) -> PickOut:
        user = kw["messages"][0]["content"]
        cands = [(m.group(1), m.group(4)) for m in _CAND.finditer(user)]
        picks = []
        for m in _CTRL.finditer(user):
            label, name = m.group(1), json.loads(m.group(3)).casefold()
            hit = next((c for c, shown in cands if f'"{name}"' in shown.casefold()), None)
            if name in none_for or hit is None:
                picks.append(PickItem(control=label, file="NONE", certainty="low", external=name in external_for))
            else:
                picks.append(PickItem(control=label, file=hit, certainty="high", external=False))
        return PickOut(picks=picks)

    return respond


_REQ = re.compile(r"^requirement: (.*)$", re.MULTILINE)
_JCAND = re.compile(r"^(c\d+): \[(element|screen)\] (.*)$", re.MULTILINE)


def judge_responder() -> Callable:
    """Heuristic stand-in for the requirement judge: full when a candidate shares >= 2 content tokens with
    the requirement, partial when it shares exactly one distinctive (>= 8 characters) token, none otherwise.
    Deterministic and order independent."""

    def respond(kw: dict[str, Any]) -> JudgeOut:
        user = kw["messages"][0]["content"]
        req = set(tokens(_REQ.search(user).group(1)))
        verdicts = []
        for m in _JCAND.finditer(user):
            label = m.group(3)
            shared = req & set(tokens(label))
            one_distinctive = len(shared) == 1 and len(next(iter(shared))) >= 8
            verdicts.append(CandidateVerdict(
                id=m.group(1), supports="full" if len(shared) >= 2 else "partial" if one_distinctive else "none"))
        return JudgeOut(verdicts=verdicts, none_of_above=all(v.supports == "none" for v in verdicts))

    return respond


def combined_responder() -> Callable:
    pick, judge = pick_responder(), judge_responder()

    def respond(kw: dict[str, Any]) -> BaseModel:
        return pick(kw) if "source file that renders them" in kw["system"] else judge(kw)

    return respond


# --------------------------------------------------------------------------------------------- fixtures
def real_index() -> CodeIndex:
    with gzip.open(LINK_FIXTURES / "hoppscotch_main.code_index.json.gz", "rt", encoding="utf-8") as fh:
        return CodeIndex.model_validate_json(fh.read())


def make_run(tmp_path: Path, *, spec: bool = True, code: bool = True, run_id: str = "link-test") -> RunContext:
    """A run directory holding the real 45-action crawl, the real code index and the requirement fixture."""
    run = RunContext.create("hoppscotch", run_id=run_id, runs_dir=tmp_path / "runs",
                            settings=Settings(max_usd=100.0))
    crawl = run.path("crawl", "x").parent
    for name in ("screens.jsonl", "transitions.jsonl", "summary.json"):
        shutil.copy(CRAWL / name, crawl / name)
    if code:
        run.path("code", "index.json").write_text(real_index().model_dump_json(), encoding="utf-8")
    if spec:
        shutil.copy(LINK_FIXTURES / "requirements.json", run.path("spec", "requirements.json"))
        shutil.copy(LINK_FIXTURES / "spec_stats.json", run.path("spec", "stats.json"))
    return run


def cfg() -> TargetConfig:
    return TargetConfig.load("hoppscotch")


class RecordingStore:
    """Stands in for GraphStore: records what a stage would write."""

    def __init__(self) -> None:
        self.links: list[Link] = []
        self.gaps: list[Gap] = []
        self.flows: list[Flow] = []
        self.targets: list[str | None] = []

    def write_links(self, links, run_id, *, target=None):
        self.links.extend(links)
        self.targets.append(target)
        return {"implemented_by": sum(1 for l in links if l.kind == "IMPLEMENTED_BY")}

    def write_gaps(self, gaps, run_id, *, target=None):
        self.gaps.extend(gaps)
        self.targets.append(target)
        return {"gaps": len(gaps)}

    def write_flows(self, flows, run_id, *, target=None):
        self.flows.extend(flows)
        self.targets.append(target)
        return {"flows": len(flows)}


def read_jsonl_dicts(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
