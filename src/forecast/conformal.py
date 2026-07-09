"""Split-conformal calibration wrapper -> guaranteed-coverage intervals.

Chronos-2 quantiles are sharp but not coverage-guaranteed. Split-conformal calibration
uses a held-out tail of history to compute nonconformity scores, then widens the raw
quantile band so the empirical coverage matches the nominal target (e.g. 90%).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .chronos_model import ChronosForecaster, QuantileForecast


@dataclass
class CalibratedInterval:
    """Calibrated forecast interval."""

    index: pd.DatetimeIndex
    lower: np.ndarray
    median: np.ndarray
    upper: np.ndarray
    coverage_target: float

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {"lower": self.lower, "median": self.median, "upper": self.upper},
            index=self.index,
        )

    def breaches(self, actual: pd.Series) -> pd.Series:
        """Boolean series: True where the actual falls outside the interval."""
        a = actual.reindex(self.index)
        return (a < self.to_frame()["lower"]) | (a > self.to_frame()["upper"])


class ConformalCalibrator:
    """Split-conformal wrapper around a base quantile forecaster."""

    def __init__(self, forecaster: ChronosForecaster, target_coverage: float = 0.90,
                 calib_fraction: float = 0.2):
        self.forecaster = forecaster
        self.target_coverage = target_coverage
        self.calib_fraction = calib_fraction
        self._q_hat: float | None = None

    def calibrate(self, history: pd.Series, horizon: int, quantiles: list[float]) -> float:
        """Estimate the conformal correction ``q_hat`` from a held-out tail.

        Walks one-step-ahead over the calibration tail, collects absolute residuals
        against the median forecast, and takes the ``target_coverage`` empirical quantile.
        """
        n_calib = max(20, int(len(history) * self.calib_fraction))
        residuals: list[float] = []
        for t in range(len(history) - n_calib, len(history) - 1):
            ctx = history.iloc[:t]
            fc = self.forecaster.forecast(ctx, horizon=1, quantiles=quantiles)
            residuals.append(abs(history.iloc[t] - fc.median.iloc[0]))
        scores = np.asarray(residuals)
        self._q_hat = float(np.quantile(scores, self.target_coverage))
        return self._q_hat

    def predict(self, history: pd.Series, horizon: int,
                quantiles: list[float]) -> CalibratedInterval:
        """Produce a calibrated interval; calibrate first if not already done."""
        if self._q_hat is None:
            self.calibrate(history, horizon, quantiles)
        fc: QuantileForecast = self.forecaster.forecast(history, horizon, quantiles)
        med = fc.median.to_numpy()
        return CalibratedInterval(
            index=fc.index,
            lower=med - self._q_hat,
            median=med,
            upper=med + self._q_hat,
            coverage_target=self.target_coverage,
        )


def empirical_coverage(interval: CalibratedInterval, actual: pd.Series) -> float:
    """Fraction of actuals that fall inside the interval (should match nominal)."""
    inside = ~interval.breaches(actual)
    return float(inside.mean())


# ---------------------------------------------------------------------------
# Conformalized Quantile Regression (CQR), for quantile-native models (TimesFM).
# Romano, Patterson & Candes (2019): given a model's lower/upper quantile band,
# widen (or tighten) it by a single data-driven offset so the interval attains
# the target marginal coverage on exchangeable data.
# ---------------------------------------------------------------------------

def cqr_offset(lower: np.ndarray, upper: np.ndarray, actual: np.ndarray,
               target_coverage: float = 0.90) -> float:
    """Compute the CQR conformal offset ``Q`` from a calibration set.

    Nonconformity score per point: ``E = max(lower - y, y - upper)`` (negative when
    inside the band). ``Q`` is the finite-sample-adjusted empirical quantile of E.
    The calibrated band is ``[lower - Q, upper + Q]``.
    """
    lower = np.asarray(lower, float); upper = np.asarray(upper, float)
    actual = np.asarray(actual, float)
    scores = np.maximum(lower - actual, actual - upper)
    n = len(scores)
    if n == 0:
        return 0.0
    # finite-sample rank: ceil((n+1)(1-alpha)) / n, clipped to [0, 1]
    level = min(1.0, np.ceil((n + 1) * target_coverage) / n)
    return float(np.quantile(scores, level, method="higher"))


def apply_cqr(index: pd.DatetimeIndex, lower: np.ndarray, median: np.ndarray,
              upper: np.ndarray, offset: float, target_coverage: float) -> CalibratedInterval:
    """Apply a CQR offset to a raw quantile band, returning a calibrated interval."""
    return CalibratedInterval(
        index=index,
        lower=np.asarray(lower, float) - offset,
        median=np.asarray(median, float),
        upper=np.asarray(upper, float) + offset,
        coverage_target=target_coverage,
    )


# ---------------------------------------------------------------------------
# Adaptive Conformal Inference (ACI), Gibbs & Candes (2021).
# A single fixed CQR offset under-covers when volatility regimes shift (calm
# calibration window -> crisis test window). ACI keeps target coverage online by
# nudging the effective miscoverage level up when it just missed and down when it
# just covered, so the interval widens in turbulent regimes and tightens in calm ones.
# ---------------------------------------------------------------------------

def aci_cqr(lower: np.ndarray, upper: np.ndarray, actual: np.ndarray,
            target_coverage: float = 0.90, gamma: float = 0.02,
            warmup: int = 50) -> dict:
    """Run ACI over a time-ordered sequence of CQR bands.

    At each step the offset is the empirical ``1 - alpha_t`` quantile of past
    nonconformity scores ``E = max(lower - y, y - upper)``; ``alpha_t`` is updated
    online by ``alpha_{t+1} = alpha_t + gamma * (alpha - err_t)``.

    Returns a dict with per-step ``covered`` / ``offset`` arrays and the realized
    coverage after warmup.
    """
    lower = np.asarray(lower, float); upper = np.asarray(upper, float)
    actual = np.asarray(actual, float)
    scores = np.maximum(lower - actual, actual - upper)
    n = len(actual)
    alpha = 1.0 - target_coverage
    a_t = alpha
    covered = np.zeros(n, dtype=bool)
    offsets = np.zeros(n)
    for t in range(n):
        past = scores[:t]
        if len(past) == 0:
            off = 0.0
        else:
            lvl = float(np.clip(1.0 - a_t, 0.0, 1.0))
            off = float(np.quantile(past, lvl, method="higher"))
        offsets[t] = off
        covered[t] = (actual[t] >= lower[t] - off) and (actual[t] <= upper[t] + off)
        err = 0.0 if covered[t] else 1.0
        a_t = float(np.clip(a_t + gamma * (alpha - err), 0.0, 1.0))
    realized = float(covered[warmup:].mean()) if n > warmup else float(covered.mean())
    return {"covered": covered, "offset": offsets, "realized_coverage": realized,
            "warmup": warmup}
