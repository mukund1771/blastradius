"""Shared fakes for the spec tests: a scripted LLM client, a mock HTTP document, a run in tmp_path.

Not a test module (it holds no tests); pytest puts tests/ on sys.path so the spec test files import it.
No network and no API key: the LLM is the fake-client pattern of tests/test_foundation.py.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from blastradius.config import Settings
from blastradius.llm import LLM
from blastradius.run import RunContext
from blastradius.spec.extract import ExtractedItem, ExtractedPage
from blastradius.targets import TargetConfig

FIXTURES = Path(__file__).parent / "fixtures" / "spec"
EXCERPT = (FIXTURES / "llms_excerpt.txt").read_bytes()
GATED = (FIXTURES / "llms_gated_excerpt.txt").read_bytes()    # real AI Features + Authorization text
SPEC_URL = "https://docs.hoppscotch.io/llms-full.txt"


class Resp:
    def __init__(self, parsed: Any, stop_reason: str = "end_turn"):
        self.parsed_output, self.stop_reason, self.stop_details = parsed, stop_reason, None
        self._request_id = "req_fake"

        class U:
            input_tokens, output_tokens = 1000, 200
            cache_read_input_tokens = cache_creation_input_tokens = 0

        self.usage = U()


class Messages:
    def __init__(self, responder: Callable[[dict[str, Any]], Any]):
        self.responder, self.calls = responder, []

    def parse(self, **kw: Any) -> Any:
        self.calls.append(kw)
        out = self.responder(kw)
        return out if isinstance(out, Resp) else Resp(out)


class FakeClient:
    def __init__(self, responder: Callable[[dict[str, Any]], Any]):
        self.messages = Messages(responder)


def item(quote: str, text: str = "User can do the thing", edition: str = "unknown",
         polarity: str = "must_exist", ui: str = "yes", login: bool = False) -> ExtractedItem:
    return ExtractedItem.model_validate({"text": text, "quote": quote, "edition": edition,
                                         "polarity": polarity, "ui_observable": ui, "requires_login": login})


def page_of(*items: ExtractedItem) -> ExtractedPage:
    return ExtractedPage(requirements=list(items))


def user_of(kw: dict[str, Any]) -> str:
    return kw["messages"][0]["content"]


def make_run(tmp_path: Path, name: str = "ingest") -> RunContext:
    return RunContext.create("hoppscotch", run_id=name, runs_dir=tmp_path / "runs",
                             settings=Settings(max_usd=5.0))


def make_llm(run: RunContext, tmp_path: Path, responder: Callable[[dict[str, Any]], Any], **kw: Any):
    client = FakeClient(responder)
    llm = LLM(run, client=client, cache_dir=tmp_path / "cache" / "llm", **kw)
    llm._models["mid"] = "claude-sonnet-5-5"
    return llm, client


def doc_client(content: bytes = EXCERPT, status: int = 200,
               headers: dict[str, str] | None = None) -> httpx.Client:
    headers = {"content-type": "text/plain"} if headers is None else headers
    return httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(status, content=content, headers=headers)))


def handler_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def doc(*pages: tuple[str, str], base: str = "https://docs.x.test/documentation/features") -> bytes:
    """An llms-full.txt-shaped document: `# title`, `Source: url`, blank, body; pages p0, p1, ..."""
    parts = [f"# {title}\nSource: {base}/p{i}\n\n{body}\n" for i, (title, body) in enumerate(pages)]
    return "\n\n".join(parts).encode()


def codes(run: RunContext) -> list[str]:
    return [f["code"] for f in run.failures.items]


def spec_file(run: RunContext, name: str) -> Any:
    return json.loads((run.run_dir / "spec" / name).read_text())


def real_cfg(**spec_update: Any) -> TargetConfig:
    """The real targets/hoppscotch.yaml (so the yaml is guarded), with optional spec overrides."""
    cfg = TargetConfig.load("hoppscotch")
    return cfg.model_copy(update={"spec": cfg.spec.model_copy(update=spec_update)}) if spec_update else cfg
