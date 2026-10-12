"""Pre-registered thresholds the eval kit grades against (plan section 9; D17, D22).

Two sources, kept apart so the product's file is never edited from here:
  * `eval/thresholds.json` (read through `reason.thresholds.load_thresholds`, the product's own reader): hop
    cap, breadth fraction and the CORRECT confidence floor.
  * `EVAL_ONLY` below: numbers only the eval kit uses (stability agreement, judge k, seeded corruptions,
    stability N, the "high-confidence" floor of synthetic recall). They were fixed before any run on a demo PR
    and live in code because `Thresholds` forbids extra keys. A change must be logged in docs/DECISIONS.md.

`resolve()` returns the values together with where they came from and the sha256 of the registered file, so
results.json shows which pre-registration a number was judged against.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from ..config import ROOT
from ..ids import sha_full
from ..models import MAX_HOPS
from ..reason.thresholds import load_thresholds

THRESHOLDS_PATH = ROOT / "eval" / "thresholds.json"
BUILTIN_MINIMAL = {"breadth_fraction_max": 0.25, "hop_cap": MAX_HOPS, "min_confidence_correct": 0.5}


@dataclass(frozen=True)
class EvalOnly:
    stability_min_agreement: float = 0.6   # an item present in fewer than 60% of repeated runs is demoted
    stability_n: int = 5                   # plan section 9.5: N=5 live, N=100 as arithmetic only
    judge_k: int = 3                       # samples per claim; 2-1 goes to needs_review
    seeded_corruptions: int = 20
    high_confidence: float = 0.8           # models.band_for CONFIRMED boundary: synthetic recall's link floor
    demoted_confidence: float = 0.49       # top of the "possible" band: what a demoted item is capped to


EVAL_ONLY = EvalOnly()


def resolve() -> dict[str, Any]:
    """The thresholds in force, their provenance and the registered file's hash."""
    out: dict[str, Any] = {"eval_only": asdict(EVAL_ONLY)}
    try:
        t = load_thresholds(THRESHOLDS_PATH)
        out["registered"] = {
            "hop_cap": t.retrieval.max_hops,
            "breadth_fraction_max": t.grade.breadth_fraction_h2,
            "min_confidence_correct": t.grade.min_element_confidence,
        }
        out["source"] = str(THRESHOLDS_PATH.relative_to(ROOT))
        out["sha256"] = sha_full(THRESHOLDS_PATH.read_text(encoding="utf-8"))
        out["registered_on"] = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8")).get("registered")
    except (FileNotFoundError, ValueError) as exc:     # ValidationError is a ValueError
        out["registered"] = {"hop_cap": BUILTIN_MINIMAL["hop_cap"],
                             "breadth_fraction_max": BUILTIN_MINIMAL["breadth_fraction_max"],
                             "min_confidence_correct": BUILTIN_MINIMAL["min_confidence_correct"]}
        out["source"] = f"built-in minimal defaults: eval/thresholds.json unusable ({type(exc).__name__})"
        out["sha256"] = None
        out["registered_on"] = None
    return out
