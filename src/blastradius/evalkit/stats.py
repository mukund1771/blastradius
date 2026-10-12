"""Small-sample statistics for the eval kit (D22): Wilson interval, Cohen's kappa, deterministic bootstrap.

Everything here is a pure function of its arguments (the bootstrap takes an explicit seed), so a number
printed in eval.md is reproducible from results.json. Rates are always carried with their n:
`Proportion.fmt()` never prints a percentage without the fraction it came from.

Reference values (computed locally, docs/RECON.md section 8): 27/30 = 90%, Wilson 95% [74.4%, 96.5%]; an
always-"supported" judge at 90% prevalence has kappa 0.00 although its accuracy is 90%.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass

Z95 = 1.959963984540054


def _r4(value: float | None) -> float | None:
    """Four decimals: keeps results.json byte-stable across platforms' float formatting."""
    return None if value is None else round(value, 4)


def wilson_interval(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials. n == 0 has no information: the vacuous [0, 1]."""
    if n < 0 or k < 0 or k > n:
        raise ValueError(f"need 0 <= k <= n, got k={k} n={n}")
    if n == 0:
        return 0.0, 1.0
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True)
class Proportion:
    """k of n with its Wilson 95% interval."""

    k: int
    n: int
    lo: float
    hi: float

    @classmethod
    def of(cls, k: int, n: int) -> Proportion:
        lo, hi = wilson_interval(k, n)
        return cls(k, n, lo, hi)

    @property
    def p(self) -> float | None:
        return None if self.n == 0 else self.k / self.n

    def as_dict(self) -> dict[str, float | int | None]:
        return {"k": self.k, "n": self.n, "p": _r4(self.p), "wilson95_lo": _r4(self.lo),
                "wilson95_hi": _r4(self.hi)}

    def fmt(self) -> str:
        if self.n == 0:
            return "n=0 (no data)"
        return (f"{self.k}/{self.n} = {100 * self.k / self.n:.1f}% "
                f"(Wilson 95% {100 * self.lo:.1f}% to {100 * self.hi:.1f}%)")


def cohens_kappa(a: Sequence[Hashable], b: Sequence[Hashable]) -> float | None:
    """Chance-corrected agreement of two raters over the same items.

    None when it is undefined: no items, or both raters used one and the same single category (expected
    agreement is 1, so 0/0). Raises when the sequences differ in length."""
    if len(a) != len(b):
        raise ValueError(f"raters must label the same items: {len(a)} != {len(b)}")
    n = len(a)
    if n == 0:
        return None
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[c] * cb[c] for c in ca.keys() | cb.keys()) / (n * n)
    if math.isclose(expected, 1.0):
        return None
    return (observed - expected) / (1.0 - expected)


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean of no values")
    return math.fsum(values) / len(values)


def bootstrap_ci(values: Sequence[float], stat: Callable[[Sequence[float]], float] = mean, *,
                 n_resamples: int = 2000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap interval of `stat` over `values`, deterministic for a given seed."""
    if not values:
        raise ValueError("bootstrap of no values")
    if not 0 < alpha < 1 or n_resamples < 1:
        raise ValueError("need 0 < alpha < 1 and n_resamples >= 1")
    rng = random.Random(seed)
    n = len(values)
    stats = sorted(stat([values[rng.randrange(n)] for _ in range(n)]) for _ in range(n_resamples))
    lo = stats[min(n_resamples - 1, max(0, math.floor(alpha / 2 * n_resamples)))]
    hi = stats[min(n_resamples - 1, max(0, math.ceil((1 - alpha / 2) * n_resamples) - 1))]
    return lo, hi


def jaccard(a: frozenset[Hashable] | set[Hashable], b: frozenset[Hashable] | set[Hashable]) -> float:
    """|a & b| / |a | b|; two empty sets are identical (1.0)."""
    union = len(a | b)
    return 1.0 if union == 0 else len(a & b) / union


def fmt_rate(d: dict[str, float | int | None] | None) -> str:
    """Render a `Proportion.as_dict()` the same way `Proportion.fmt()` does; n is always in the text."""
    if not d or not d.get("n"):
        return "n=0 (no data)"
    k, n = int(d["k"] or 0), int(d["n"] or 0)
    lo, hi = float(d["wilson95_lo"] or 0.0), float(d["wilson95_hi"] or 0.0)
    return f"{k}/{n} = {100 * k / n:.1f}% (Wilson 95% {100 * lo:.1f}% to {100 * hi:.1f}%)"
