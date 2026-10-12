"""Closed failure taxonomy. Every failure the pipeline can report is one of these codes.

The report's "could not determine" section is generated from failures.jsonl, so a code that is not in
this enum cannot reach a user. Each code carries the stage that owns it and what the user should do.
"""

from __future__ import annotations

from enum import StrEnum


class FailureCode(StrEnum):
    # access
    ACCESS_BLOCKED = "ACCESS_BLOCKED"
    ACCESS_SUSPECT = "ACCESS_SUSPECT"
    ACCESS_DISALLOWED = "ACCESS_DISALLOWED"
    # crawl
    SELECTOR_DRIFT = "SELECTOR_DRIFT"
    STATE_NOT_RECOVERABLE = "STATE_NOT_RECOVERABLE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    ACTION_BLOCKED_BY_POLICY = "ACTION_BLOCKED_BY_POLICY"
    # llm
    LLM_SCHEMA_VIOLATION = "LLM_SCHEMA_VIOLATION"
    LLM_REFUSAL = "LLM_REFUSAL"
    LLM_TRUNCATED = "LLM_TRUNCATED"
    LLM_BUDGET_EXCEEDED = "LLM_BUDGET_EXCEEDED"
    LLM_API_ERROR = "LLM_API_ERROR"
    LLM_OFFLINE_MISS = "LLM_OFFLINE_MISS"
    # spec
    QUOTE_NOT_IN_SOURCE = "QUOTE_NOT_IN_SOURCE"
    SPEC_STALE = "SPEC_STALE"
    SPEC_FETCH_FAILED = "SPEC_FETCH_FAILED"
    # code
    CODE_CHECKOUT_FAILED = "CODE_CHECKOUT_FAILED"
    CODE_PARSE_PARTIAL = "CODE_PARSE_PARTIAL"
    # graph
    GRAPH_CONN_FAILED = "GRAPH_CONN_FAILED"
    GRAPH_WRITE_FAILED = "GRAPH_WRITE_FAILED"
    GRAPH_READ_FAILED = "GRAPH_READ_FAILED"
    GRAPH_PRUNE_SKIPPED = "GRAPH_PRUNE_SKIPPED"
    # reasoning
    PR_PATH_MISSING_AT_HEAD = "PR_PATH_MISSING_AT_HEAD"
    STALE_BASE = "STALE_BASE"
    VERIFIER_REJECTED = "VERIFIER_REJECTED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    # env
    CONFIG_INVALID = "CONFIG_INVALID"
    DEP_MISSING = "DEP_MISSING"


# code -> (owning stage, retryable, what the user should do)
META: dict[FailureCode, tuple[str, bool, str]] = {
    FailureCode.ACCESS_BLOCKED: ("access", False, "The app's WAF/bot management blocked the crawler. Ask the owner to allowlist it, or supply a recorded session."),
    FailureCode.ACCESS_SUSPECT: ("access", False, "A capture looked like a challenge or error page. Review it before it enters the graph."),
    FailureCode.ACCESS_DISALLOWED: ("access", False, "robots.txt disallows (or could not be read for) this path; it was skipped by policy."),
    FailureCode.SELECTOR_DRIFT: ("crawl", True, "An element vanished between observation and action; the explorer re-observed."),
    FailureCode.STATE_NOT_RECOVERABLE: ("crawl", False, "Could not return to a known base state; budget was spent re-navigating."),
    FailureCode.BUDGET_EXHAUSTED: ("crawl", False, "Action, time, or spend budget reached; coverage is partial and recorded as effort."),
    FailureCode.ACTION_BLOCKED_BY_POLICY: ("crawl", False, "A destructive, external, or authenticating action was refused by the safety policy."),
    FailureCode.LLM_SCHEMA_VIOLATION: ("llm", True, "The model output did not satisfy the schema."),
    FailureCode.LLM_REFUSAL: ("llm", False, "The model declined the request (stop_reason=refusal)."),
    FailureCode.LLM_TRUNCATED: ("llm", True, "The response hit max_tokens."),
    FailureCode.LLM_BUDGET_EXCEEDED: ("llm", False, "The per-run spend breaker tripped. Raise BLASTRADIUS_MAX_USD or reduce scope."),
    FailureCode.LLM_API_ERROR: ("llm", True, "Anthropic API error after SDK retries."),
    FailureCode.LLM_OFFLINE_MISS: ("llm", False, "Offline mode and no cached response for this request."),
    FailureCode.QUOTE_NOT_IN_SOURCE: ("spec", False, "A requirement's quote is not a substring of the source; it was dropped."),
    FailureCode.SPEC_STALE: ("spec", False, "The spec's newest version predates the app version; absences may be spec drift."),
    FailureCode.SPEC_FETCH_FAILED: ("spec", True, "The spec could not be fetched or contained no pages (transport error, HTTP error, empty document)."),
    FailureCode.CODE_CHECKOUT_FAILED: ("code", True, "Could not fetch or check out the repository ref (network, auth, or ref not found)."),
    FailureCode.CODE_PARSE_PARTIAL: ("code", False, "Some source files could not be parsed; the index is incomplete and says how many."),
    FailureCode.GRAPH_CONN_FAILED: ("graph", True, "Could not connect to Neo4j. Is Docker running? Run `blastradius doctor`."),
    FailureCode.GRAPH_WRITE_FAILED: ("graph", True, "A graph write failed."),
    FailureCode.GRAPH_READ_FAILED: ("graph", True, "A graph read failed (query error, timeout, or transaction memory limit)."),
    FailureCode.GRAPH_PRUNE_SKIPPED: ("graph", False, "An artifact was partial, so the graph kept its previous state for that layer instead of pruning."),
    FailureCode.PR_PATH_MISSING_AT_HEAD: ("reason", False, "A changed path does not exist in the indexed snapshot (stale base, rename, or new file)."),
    FailureCode.STALE_BASE: ("reason", False, "The PR base is behind main and overlaps changed files; the diff may not apply as shown."),
    FailureCode.VERIFIER_REJECTED: ("reason", True, "The report cited evidence that does not exist or overstated confidence."),
    FailureCode.NEEDS_HUMAN: ("reason", False, "Evidence was insufficient; a human should look at this item."),
    FailureCode.CONFIG_INVALID: ("env", False, "Configuration is invalid; run `blastradius doctor`."),
    FailureCode.DEP_MISSING: ("env", False, "A required dependency or binary is missing; run `blastradius doctor`."),
}
