"""Pre-registered thresholds (D17; plan section 7): the single source is `eval/thresholds.json`.

The file was written before the grader ran on any demo PR; the eval kit reads the same file, so a number cannot
drift between the product and its evaluation. A file that is missing or does not validate is an error, never a
silent default: a threshold nobody registered is a threshold nobody can defend.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator

from ..config import ROOT
from ..models import MAX_HOPS, Strict

THRESHOLDS_PATH = ROOT / "eval" / "thresholds.json"


class RetrievalThresholds(Strict):
    max_hops: int
    forward_hops: int = Field(ge=0, le=1)
    max_seeds: int = Field(ge=1)
    tier2_cap: int = Field(ge=1)
    checklist_max: int = Field(ge=1)
    undetermined_top: int = Field(ge=1)


class GradeThresholds(Strict):
    breadth_fraction_h2: float = Field(gt=0, le=1)
    hub_fraction_h1: float = Field(gt=0, le=1)
    min_element_confidence: float = Field(ge=0, le=1)
    max_rounds: int = Field(ge=1, le=2)


class CapThresholds(Strict):
    ambiguous_indirect: float = Field(ge=0, lt=0.5)
    additive_reader: float = Field(ge=0, lt=0.5)
    symbol_miss: float = Field(ge=0, lt=0.5)
    type_only: float = Field(ge=0, lt=0.5)
    forward: float = Field(ge=0, lt=0.5)
    locale_route: float = Field(ge=0, lt=0.5)
    copy_other_element: float = Field(ge=0, lt=0.5)
    judge_demote: float = Field(ge=0, lt=0.5)
    unmapped_probe: float = Field(ge=0, lt=0.5)


class ConfidenceThresholds(Strict):
    stored_data: float = Field(ge=0, le=1)
    global_change: float = Field(ge=0, le=1, alias="global")


class JudgeThresholds(Strict):
    max_candidates: int = Field(ge=1, le=12)
    max_calls: int = Field(ge=0, le=6)
    samples: int = Field(ge=1, le=2)
    max_reason_words: int = Field(ge=1)
    reserve: int = Field(ge=0, le=12)
    max_nominations: int = Field(ge=0, le=6)
    shuffle_seed: str


class Thresholds(Strict):
    schema_version: int
    registered: str
    status: str = ""
    doc: dict[str, str] = Field(default_factory=dict, alias="_doc")
    retrieval: RetrievalThresholds
    grade: GradeThresholds
    caps: CapThresholds
    confidence: ConfidenceThresholds
    judge: JudgeThresholds

    @model_validator(mode="after")
    def _hops_match_models(self) -> Thresholds:
        if self.retrieval.max_hops != MAX_HOPS:
            raise ValueError(f"retrieval.max_hops {self.retrieval.max_hops} != models.MAX_HOPS {MAX_HOPS}")
        return self


@lru_cache(maxsize=4)
def _load(path: str) -> Thresholds:
    return Thresholds.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def load_thresholds(path: Path | None = None) -> Thresholds:
    """The registered thresholds. Raises FileNotFoundError or pydantic.ValidationError when they are not usable."""
    return _load(str(path or THRESHOLDS_PATH))
