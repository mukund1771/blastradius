"""The only place that talks to the Anthropic API.

Properties this module guarantees (each is a design decision, see docs/DECISIONS.md):
  D07  structured output only: every call returns a validated pydantic object or raises
  D26  no sampling parameters (5.5 models reject them); determinism = content cache + schema + k-agreement
  D27  cache key never includes run_id; a cache hit still emits an llm_call span and a ledger line
  D28  per-run spend breaker; untrusted text must be wrapped by the caller (safety.untrusted)
  D02  model ids are resolved at runtime via the Models API, with a preference list per tier
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Literal, TypeVar

import anthropic
from pydantic import BaseModel

from .config import CACHE_DIR, Settings
from .failures import FailureCode
from .ids import canonical_json, sha_full
from .run import RunContext

T = TypeVar("T", bound=BaseModel)
Tier = Literal["cheap", "mid", "strong"]

CACHE_VERSION = "1"

# Preference order per tier. The first id the Models API will retrieve wins; later ids are fallbacks.
TIER_PREFERENCE: dict[str, tuple[str, ...]] = {
    "cheap": ("claude-haiku-5-5", "claude-haiku-4-5"),
    "mid": ("claude-sonnet-5-5",),
    "strong": ("claude-opus-5-5",),
}
# Models that reject output_config.effort (errors on Haiku 4.5 per the API docs).
_NO_EFFORT_PREFIXES = ("claude-haiku-4-5",)


class LLMError(RuntimeError):
    code: FailureCode = FailureCode.LLM_API_ERROR


class LLMRefusal(LLMError):
    code = FailureCode.LLM_REFUSAL


class LLMTruncated(LLMError):
    code = FailureCode.LLM_TRUNCATED


class LLMBudgetExceeded(LLMError):
    code = FailureCode.LLM_BUDGET_EXCEEDED


class LLMOfflineMiss(LLMError):
    code = FailureCode.LLM_OFFLINE_MISS


class LLMSchemaViolation(LLMError):
    code = FailureCode.LLM_SCHEMA_VIOLATION


def supports_effort(model: str) -> bool:
    return not model.startswith(_NO_EFFORT_PREFIXES)


def _usage_dict(usage: Any) -> dict[str, int]:
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    }


class LLM:
    def __init__(self, run: RunContext, *, offline: bool | None = None, client: Any = None,
                 cache_dir: Path | None = None, settings: Settings | None = None):
        self.run = run
        self.settings = settings or run.settings
        self.offline = bool(offline) or os.environ.get("BLASTRADIUS_OFFLINE") == "1"
        self._client = client
        self.cache_dir = cache_dir or (CACHE_DIR / "llm")
        self._models: dict[str, str] = {}

    # ---- client and model resolution ---------------------------------------------------------
    @property
    def client(self) -> Any:
        if self._client is None:
            from .config import load_dotenv

            load_dotenv()
            self._client = anthropic.Anthropic(max_retries=3, timeout=180.0)
        return self._client

    def model_for(self, tier: Tier) -> str:
        """Resolve a tier to a concrete model id (env override > Models API > last preference)."""
        if tier in self._models:
            return self._models[tier]
        override = os.environ.get(f"BLASTRADIUS_MODEL_{tier.upper()}")
        if override:
            self._models[tier] = override
            return override
        prefs = TIER_PREFERENCE[tier]
        memo = self.cache_dir.parent / "models.json"
        memo_data: dict[str, str] = {}
        if memo.exists():
            try:
                memo_data = json.loads(memo.read_text())
            except json.JSONDecodeError:
                memo_data = {}
        if memo_data.get(tier) in prefs:
            self._models[tier] = memo_data[tier]
            return self._models[tier]
        chosen = prefs[-1]
        if not self.offline and Settings.has_api_key():
            for cand in prefs:
                try:
                    self.client.models.retrieve(cand)
                    chosen = cand
                    break
                except anthropic.NotFoundError:
                    continue
                except anthropic.APIError:
                    break  # transient: keep the safe fallback, do not memoise
            else:
                chosen = prefs[-1]
            memo.parent.mkdir(parents=True, exist_ok=True)
            memo_data[tier] = chosen
            memo.write_text(json.dumps(memo_data, indent=2))
        self._models[tier] = chosen
        return chosen

    # ---- structured call ---------------------------------------------------------------------
    def structured(self, *, purpose: str, tier: Tier, system: str, user: str, schema: type[T],
                   max_tokens: int = 8192, effort: str | None = None, access_verdict: str = "OK",
                   bypass_cache: bool = False, prompt_version: str = "1") -> T:
        model = self.model_for(tier)
        use_effort = effort if (effort and supports_effort(model)) else None
        key = sha_full(canonical_json({
            "cv": CACHE_VERSION, "model": model, "system": system, "user": user,
            "schema": schema.model_json_schema(), "effort": use_effort, "max_tokens": max_tokens,
            "access": access_verdict, "pv": prompt_version,
        }))
        tracer = self.run.tracer
        with tracer.span("llm_call", purpose, model=model, tier=tier, prompt_sha=key[:16],
                         schema=schema.__name__, effort=use_effort, access_verdict=access_verdict) as sp:
            path = self.cache_dir / key[:2] / f"{key}.json"
            if not bypass_cache and path.exists():
                try:
                    rec = json.loads(path.read_text())
                    out = schema.model_validate(rec["output"])
                except Exception:  # noqa: BLE001 - corrupt cache entry: fall through to a live call
                    rec, out = None, None
                if out is not None and rec is not None:
                    self.run.ledger.record(span_id=sp.span_id, stage=None, purpose=purpose, model=model,
                                           usage=rec.get("usage", {}), cache_hit=True,
                                           original_span_id=rec.get("span_id"))
                    sp.set(cache_hit=True, **{f"tok_{k}": v for k, v in rec.get("usage", {}).items()})
                    return out
            sp.set(cache_hit=False)
            if self.offline:
                self.run.failures.record(FailureCode.LLM_OFFLINE_MISS, purpose)
                sp.fail(FailureCode.LLM_OFFLINE_MISS, purpose)
                raise LLMOfflineMiss(f"offline and no cached response for {purpose} ({key[:12]})")
            if self.run.ledger.total >= self.settings.max_usd:
                self.run.failures.record(FailureCode.LLM_BUDGET_EXCEEDED,
                                         f"spent ${self.run.ledger.total:.2f} >= max ${self.settings.max_usd:.2f}")
                sp.fail(FailureCode.LLM_BUDGET_EXCEEDED)
                raise LLMBudgetExceeded(f"run spend breaker at ${self.settings.max_usd:.2f}")

            kwargs: dict[str, Any] = dict(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}], output_format=schema,
            )
            if use_effort:
                kwargs["output_config"] = {"effort": use_effort}
            t0 = time.monotonic()
            try:
                resp = self.client.messages.parse(**kwargs)
            except anthropic.RateLimitError as exc:
                return self._api_failure(sp, exc, "rate limited after SDK retries")
            except anthropic.NotFoundError as exc:
                return self._api_failure(sp, exc, f"model {model} not found or not available")
            except anthropic.BadRequestError as exc:
                return self._api_failure(sp, exc, f"bad request: {exc.message}")
            except anthropic.APIStatusError as exc:
                return self._api_failure(sp, exc, f"HTTP {exc.status_code}")
            except anthropic.APIConnectionError as exc:
                return self._api_failure(sp, exc, "network error")

            usage = _usage_dict(resp.usage)
            rec_cost = self.run.ledger.record(span_id=sp.span_id, stage=None, purpose=purpose, model=model,
                                              usage=usage, cache_hit=False)
            sp.set(latency_ms=int((time.monotonic() - t0) * 1000), stop_reason=resp.stop_reason,
                   cost_usd=rec_cost["cost_usd"], request_id=getattr(resp, "_request_id", None),
                   **{f"tok_{k}": v for k, v in usage.items()})

            if resp.stop_reason == "refusal":
                category = getattr(getattr(resp, "stop_details", None), "category", None)
                self.run.failures.record(FailureCode.LLM_REFUSAL, f"{purpose}: refusal ({category})")
                sp.fail(FailureCode.LLM_REFUSAL, str(category))
                raise LLMRefusal(f"{purpose}: model refused ({category})")
            if resp.stop_reason == "max_tokens":
                self.run.failures.record(FailureCode.LLM_TRUNCATED, f"{purpose}: hit max_tokens={max_tokens}")
                sp.fail(FailureCode.LLM_TRUNCATED)
                raise LLMTruncated(f"{purpose}: hit max_tokens={max_tokens}")
            parsed = getattr(resp, "parsed_output", None)
            if parsed is None:
                self.run.failures.record(FailureCode.LLM_SCHEMA_VIOLATION, purpose)
                sp.fail(FailureCode.LLM_SCHEMA_VIOLATION)
                raise LLMSchemaViolation(f"{purpose}: no parsed output")

            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "output": parsed.model_dump(mode="json"), "usage": usage, "model": model,
                "span_id": sp.span_id, "request_id": getattr(resp, "_request_id", None),
                "purpose": purpose, "cache_version": CACHE_VERSION,
            }))
            tmp.replace(path)
            return parsed

    def _api_failure(self, sp: Any, exc: Exception, message: str) -> Any:
        self.run.failures.record(FailureCode.LLM_API_ERROR, message)
        sp.fail(FailureCode.LLM_API_ERROR, message)
        raise LLMError(message) from exc
