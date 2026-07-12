"""Core calibration math: CQR offset and Adaptive Conformal Inference.

These are the load-bearing guarantees of the whole system (post-v2 gap G1):
if either drifts, every alert becomes untrustworthy. All tests are synthetic,
deterministic (seeded), and network-free.
"""
import numpy as np
import pandas as pd
import pytest

from src.forecast.conformal import CalibratedInterval, aci_cqr, apply_cqr, cqr_offset


def test_cqr_offset_reaches_target_coverage():
    """A too-narrow band widened by the CQR offset must hit ~target coverage."""
    rng = np.random.default_rng(0)
    y = rng.normal(0, 1, 5000)
    lo = np.full_like(y, -0.5)
    hi = np.full_like(y, 0.5)
    q = cqr_offset(lo, hi, y, target_coverage=0.90)
    cov = np.mean((y >= lo - q) & (y <= hi + q))
    assert 0.88 <= cov <= 0.92


def test_cqr_offset_can_tighten_an_overwide_band():
    """CQR must also be able to SHRINK a band that over-covers (negative offset)."""
    rng = np.random.default_rng(1)
    y = rng.normal(0, 1, 5000)
    lo = np.full_like(y, -10.0)
    hi = np.full_like(y, 10.0)
    q = cqr_offset(lo, hi, y, target_coverage=0.90)
    assert q < 0
    cov = np.mean((y >= lo - q) & (y <= hi + q))
    assert 0.88 <= cov <= 0.92


def test_cqr_offset_empty_input_is_zero():
    assert cqr_offset(np.array([]), np.array([]), np.array([]), 0.9) == 0.0


def test_aci_holds_coverage_on_stationary_noise():
    rng = np.random.default_rng(2)
    n = 3000
    y = rng.normal(0, 1, n)
    lo = np.full(n, -0.8)
    hi = np.full(n, 0.8)
    out = aci_cqr(lo, hi, y, target_coverage=0.90, gamma=0.02, warmup=100)
    assert abs(out["realized_coverage"] - 0.90) <= 0.03


def test_aci_adapts_through_a_variance_regime_shift():
    """The reason ACI exists: coverage must hold when volatility doubles midway."""
    rng = np.random.default_rng(3)
    n = 4000
    y = np.concatenate([rng.normal(0, 1, n // 2), rng.normal(0, 2.5, n // 2)])
    lo = np.full(n, -1.0)
    hi = np.full(n, 1.0)
    out = aci_cqr(lo, hi, y, target_coverage=0.90, gamma=0.02, warmup=100)
    assert abs(out["realized_coverage"] - 0.90) <= 0.04
    # coverage inside the turbulent second half specifically must not collapse
    second_half = out["covered"][n // 2:]
    assert second_half.mean() >= 0.84


def test_apply_cqr_breaches_flag_out_of_band_points():
    idx = pd.bdate_range("2024-01-01", periods=4)
    interval = apply_cqr(idx, lower=np.array([9.0, 9, 9, 9]),
                         median=np.array([10.0, 10, 10, 10]),
                         upper=np.array([11.0, 11, 11, 11]),
                         offset=0.5, target_coverage=0.9)
    assert isinstance(interval, CalibratedInterval)
    actual = pd.Series([10.0, 8.0, 12.0, 10.4], index=idx)  # in, below, above, in
    breaches = interval.breaches(actual)
    assert list(breaches.values) == [False, True, True, False]


@pytest.mark.parametrize("target", [0.8, 0.9, 0.95])
def test_cqr_respects_different_targets(target):
    rng = np.random.default_rng(4)
    y = rng.normal(0, 1, 6000)
    lo = np.full_like(y, -0.3)
    hi = np.full_like(y, 0.3)
    q = cqr_offset(lo, hi, y, target_coverage=target)
    cov = np.mean((y >= lo - q) & (y <= hi + q))
    assert abs(cov - target) <= 0.02
