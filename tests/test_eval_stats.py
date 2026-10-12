"""evalkit.stats: values checked against known results, not against the implementation."""

from __future__ import annotations

import math

import pytest

from blastradius.evalkit.stats import (
    Proportion,
    bootstrap_ci,
    cohens_kappa,
    fmt_rate,
    jaccard,
    mean,
    wilson_interval,
)


def test_wilson_27_of_30_matches_the_recon_reference():
    lo, hi = wilson_interval(27, 30)
    assert lo == pytest.approx(0.744, abs=0.001) and hi == pytest.approx(0.965, abs=0.001)


@pytest.mark.parametrize(
    ("k", "n", "lo", "hi"),
    [
        (0, 10, 0.0, 0.2775),  # published: Wilson 95% for 0/10
        (10, 10, 0.7225, 1.0),
        (5, 10, 0.2366, 0.7634),
        (1, 3, 0.0615, 0.7923),
    ],
)
def test_wilson_reference_values(k: int, n: int, lo: float, hi: float):
    got = wilson_interval(k, n)
    assert got[0] == pytest.approx(lo, abs=0.001) and got[1] == pytest.approx(hi, abs=0.001)


def test_wilson_no_data_is_the_vacuous_interval_and_rejects_bad_counts():
    assert wilson_interval(0, 0) == (0.0, 1.0)
    for k, n in ((-1, 3), (4, 3)):
        with pytest.raises(ValueError):
            wilson_interval(k, n)


def test_proportion_always_prints_its_n():
    p = Proportion.of(27, 30)
    assert p.fmt().startswith("27/30 = 90.0%") and "Wilson 95%" in p.fmt()
    assert Proportion.of(0, 0).fmt() == "n=0 (no data)"
    assert Proportion.of(0, 0).p is None and Proportion.of(0, 0).as_dict()["p"] is None
    assert fmt_rate(p.as_dict()) == p.fmt()
    assert fmt_rate(Proportion.of(0, 0).as_dict()) == "n=0 (no data)"


def test_kappa_known_values():
    truth = ["S"] * 27 + ["U"] * 3
    assert cohens_kappa(truth, ["S"] * 30) == pytest.approx(0.0)  # always-supported at 90% prevalence
    assert cohens_kappa(truth, truth) == pytest.approx(1.0)
    # classic 2x2: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no -> po 0.7, pe 0.5, kappa 0.4
    a = ["y"] * 25 + ["n"] * 25
    b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    assert cohens_kappa(a, b) == pytest.approx(0.4)


def test_kappa_undefined_cases_and_length_mismatch():
    assert cohens_kappa([], []) is None
    assert cohens_kappa(["x", "x"], ["x", "x"]) is None
    with pytest.raises(ValueError):
        cohens_kappa(["a"], ["a", "b"])


def test_bootstrap_is_deterministic_and_brackets_the_mean():
    values = [0.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0]
    a = bootstrap_ci(values, seed=7, n_resamples=500)
    assert a == bootstrap_ci(values, seed=7, n_resamples=500)
    assert a != bootstrap_ci(values, seed=8, n_resamples=500)
    assert a[0] <= mean(values) <= a[1]
    assert bootstrap_ci([0.5] * 6, seed=1, n_resamples=50) == (0.5, 0.5)
    with pytest.raises(ValueError):
        bootstrap_ci([])


def test_jaccard_and_mean():
    assert jaccard({1, 2}, {2, 3}) == pytest.approx(1 / 3)
    assert jaccard(set(), set()) == 1.0
    assert mean([1, 2, 3]) == 2 and math.isclose(mean([0.1] * 3), 0.1)
    with pytest.raises(ValueError):
        mean([])
