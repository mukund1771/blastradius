"""Builders shared by the evalkit tests: valid reports, evidence rows, a fake Anthropic client."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from blastradius.config import Settings
from blastradius.llm import LLM
from blastradius.run import RunContext


# ------------------------------------------------------------------------------------------ report
def item(
    i: int,
    *,
    tier: str = "direct",
    band: str = "confirmed",
    screen: str | None = None,
    evidence: list[str] | None = None,
    flows: list[str] | None = None,
    **kw: Any,
) -> dict[str, Any]:
    return {
        "id": f"itm_{i:012x}",
        "tier": tier,
        "screen": screen or f"Screen {i}",
        "label": f"Label {i}",
        "what_changed": kw.pop("what_changed", f"The variable editor {i} is changed by this PR."),
        "band": band,
        "confidence": {"confirmed": 0.9, "likely": 0.6, "possible": 0.3, "unknown": 0.1}[band],
        "evidence_ids": evidence if evidence is not None else [f"ev_{i:012x}"],
        "flows": flows or [],
        **kw,
    }


def claim(
    i: int, text: str | None = None, *, band: str = "confirmed", evidence: list[str] | None = None
) -> dict[str, Any]:
    return {
        "id": f"clm_{i:012x}",
        "text": text or f"Environment {i} is changed by this PR.",
        "band": band,
        "evidence_ids": evidence if evidence is not None else [f"ev_{i:012x}"],
    }


def report(**over: Any) -> dict[str, Any]:
    """A report that satisfies every invariant, with two direct items and two claims (evidence ev_1, ev_2)."""
    base: dict[str, Any] = {
        "target": "hoppscotch",
        "pr_number": 6645,
        "pr_title": "edit environment values",
        "risk_level": "MEDIUM",
        "headline": "Blast radius: PR #6645. Risk: MEDIUM. Touches Environments. 2 screens, 1 user flow.",
        "confidence_line": "2 confirmed, 0 probable; 0 could not be determined.",
        "checklist": [
            {
                "text": "Open Environments and edit a value.",
                "why": "The editor changed.",
                "item_ids": [f"itm_{1:012x}"],
            }
        ],
        "direct": [item(1, flows=["Edit an environment"]), item(2)],
        "indirect": [],
        "global_changes": [],
        "stored_data": [],
        "requirements": [],
        "undetermined": [],
        "not_checked": ["One viewport only."],
        "snapshot": {"target": "hoppscotch", "repo": "hoppscotch/hoppscotch", "indexed_commit": "63273f8"},
        "claims": [claim(1), claim(2)],
    }
    base.update(over)
    return base


def evidence(n: int = 2, *, summary: str = "") -> dict[str, dict[str, Any]]:
    return {
        f"ev_{i:012x}": {
            "evidence_id": f"ev_{i:012x}",
            "kind": "link.i18n_key",
            "ref": f"el_{i}",
            "summary": summary or f"Evidence {i}: the file Env{i}.vue renders the value editor.",
            "confidence": 0.9,
            "method": "i18n_key",
        }
        for i in range(1, n + 1)
    }


# --------------------------------------------------------------------------------------- fake client
class _Usage:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 300, 60, 0, 0


class _Resp:
    def __init__(self, parsed: BaseModel):
        self.parsed_output = parsed
        self.stop_reason = "end_turn"
        self.stop_details = None
        self._request_id = "req_fake"
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


def make_run(tmp_path: Path, run_id: str = "eval-test", target: str = "hoppscotch") -> RunContext:
    return RunContext.create(
        target, run_id=run_id, runs_dir=tmp_path / "runs", settings=Settings(max_usd=100.0)
    )


def make_llm(run: RunContext, client: Any, tmp_path: Path, *, offline: bool = False) -> LLM:
    llm = LLM(
        run,
        client=client,
        cache_dir=tmp_path / "cache" / "llm",
        settings=Settings(max_usd=100.0),
        offline=offline,
    )
    for tier, model in (
        ("cheap", "claude-haiku-5-5"),
        ("mid", "claude-sonnet-5-5"),
        ("strong", "claude-opus-5-5"),
    ):
        llm._models[tier] = model
    return llm


def write_report(run: RunContext, pr: str, data: dict[str, Any]) -> Path:
    path = run.path("reason", pr, "report.json")
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_evidence(run: RunContext, rows: dict[str, dict[str, Any]]) -> None:
    run.path("evidence.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows.values()), encoding="utf-8"
    )


# ---------------------------------------------------------------------------------------- source maps
_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def vlq(n: int) -> str:
    v = (-n << 1) | 1 if n < 0 else n << 1
    out = ""
    while True:
        digit, v = v & 31, v >> 5
        out += _B64[digit | (32 if v else 0)]
        if not v:
            return out


def encode_mappings(lines: list[list[tuple[int, int, int, int]]]) -> str:
    """Absolute (gen_col, source, orig_line, orig_col) segments per line -> a v3 `mappings` string."""
    src = oline = ocol = 0
    out: list[str] = []
    for line in lines:
        gcol, segs = 0, []
        for col, s, ol, oc in line:
            segs.append(vlq(col - gcol) + vlq(s - src) + vlq(ol - oline) + vlq(oc - ocol))
            gcol, src, oline, ocol = col, s, ol, oc
        out.append(",".join(segs))
    return ";".join(out)


def build_chunk(modules: list[tuple[str, str]], *, step: int = 6) -> tuple[str, dict[str, Any]]:
    """Concatenate module code on ONE line and map every `step` characters to its module's source index."""
    text, segments = "", []
    for idx, (_, code) in enumerate(modules):
        for off in range(0, len(code), step):
            segments.append((len(text) + off, idx, 0, off))
        text += code
    data = {
        "version": 3,
        "file": "chunk.js",
        "sources": [p for p, _ in modules],
        "sourcesContent": [f"// source of {p}\n" for p, _ in modules],
        "names": [],
        "mappings": encode_mappings([segments]),
    }
    return text, data
