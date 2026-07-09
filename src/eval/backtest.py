"""Rolling-origin backtest: coverage (headline) + MASE.

Coverage is the headline metric: a foundation model that cannot beat seasonal-naive on
calibration is a red flag. MASE (scale-free) is the point-accuracy check. A frozen
holdout is kept out of development entirely.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.forecast.conformal import ConformalCalibrator, empirical_coverage


def mase(actual: pd.Series, forecast: pd.Series, insample: pd.Series,
         season: int = 1) -> float:
    """Mean Absolute Scaled Error vs a seasonal-naive in-sample benchmark."""
    denom = np.mean(np.abs(insample.diff(season).dropna()))
    if denom == 0:
        return float("nan")
    return float(np.mean(np.abs(actual.values - forecast.values)) / denom)


def garch_band(history: pd.Series, horizon: int, coverage: float = 0.90):
    """GARCH(1,1) volatility interval baseline for an H-step price forecast.

    Point path is a random walk (last price); the band widens with the cumulative
    GARCH-forecast return variance. Returns ``(lower, upper)`` price arrays of
    length ``horizon``. This is the classical volatility baseline the design's
    gauntlet calls for, alongside seasonal-naive and ARIMA.
    """
    from arch import arch_model
    from scipy.stats import norm

    p0 = float(history.iloc[-1])
    rets = 100.0 * np.log(history / history.shift(1)).dropna()
    res = arch_model(rets, mean="Zero", vol="Garch", p=1, q=1).fit(disp="off")
    fvar = res.forecast(horizon=horizon, reindex=False).variance.values[-1]  # pct^2 per step
    cum_sd = np.sqrt(np.cumsum(fvar)) / 100.0                                 # log-return sd
    z = float(norm.ppf(0.5 + coverage / 2.0))
    lower = p0 * np.exp(-z * cum_sd)
    upper = p0 * np.exp(z * cum_sd)
    return lower, upper


@dataclass
class BacktestResult:
    coverage: float
    target_coverage: float
    mean_interval_width: float
    mase: float
    n_origins: int

    def passes_calibration(self, tol: float = 0.05) -> bool:
        return abs(self.coverage - self.target_coverage) <= tol


def rolling_origin(
    series: pd.Series,
    calibrator: ConformalCalibrator,
    horizon: int,
    quantiles: list[float],
    step: int = 5,
    holdout_months: int = 6,
) -> BacktestResult:
    """Expanding-window rolling-origin backtest over everything before the holdout."""
    cutoff = series.index.max() - pd.DateOffset(months=holdout_months)
    dev = series[series.index <= cutoff]

    covs, widths, errors = [], [], []
    start = max(int(len(dev) * 0.5), 100)
    for t in range(start, len(dev) - horizon, step):
        history = dev.iloc[:t]
        actual = dev.iloc[t: t + horizon]
        interval = calibrator.predict(history, horizon, quantiles)
        covs.append(empirical_coverage(interval, actual))
        widths.append(float(np.mean(interval.upper - interval.lower)))
        errors.append(np.mean(np.abs(actual.values - interval.median)))

    insample = dev.iloc[:start]
    return BacktestResult(
        coverage=float(np.mean(covs)) if covs else float("nan"),
        target_coverage=calibrator.target_coverage,
        mean_interval_width=float(np.mean(widths)) if widths else float("nan"),
        mase=float(np.mean(errors) / np.mean(np.abs(insample.diff().dropna())))
        if errors else float("nan"),
        n_origins=len(covs),
    )
