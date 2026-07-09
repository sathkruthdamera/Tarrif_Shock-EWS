"""Chronos-2 zero-shot forecasting -> quantile forecast.

Wraps the Chronos-2 (Bolt) time-series foundation model for zero-shot forecasting.
No per-series training: the model is loaded once and conditioned on recent history.
Returns quantile forecasts that the conformal wrapper then calibrates.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DEFAULT_MODEL = "amazon/chronos-bolt-base"


@dataclass
class QuantileForecast:
    """Quantile forecast for a single series over a horizon."""

    index: pd.DatetimeIndex          # forecast timestamps
    quantiles: list[float]           # e.g. [0.05, 0.5, 0.95]
    values: np.ndarray               # shape (horizon, len(quantiles))

    def q(self, level: float) -> pd.Series:
        """Return the forecast path for a given quantile level."""
        j = self.quantiles.index(level)
        return pd.Series(self.values[:, j], index=self.index, name=f"q{level}")

    @property
    def median(self) -> pd.Series:
        return self.q(0.5)


class ChronosForecaster:
    """Thin wrapper around a Chronos-2 pipeline for zero-shot quantile forecasts."""

    def __init__(self, model_name: str = DEFAULT_MODEL, device: str | None = None):
        self.model_name = model_name
        self.device = device
        self._pipeline = None

    def _load(self):
        if self._pipeline is None:
            from chronos import ChronosBoltPipeline

            self._pipeline = ChronosBoltPipeline.from_pretrained(self.model_name)
        return self._pipeline

    def forecast(self, history: pd.Series, horizon: int,
                 quantiles: list[float]) -> QuantileForecast:
        """Produce a zero-shot quantile forecast.

        Parameters
        ----------
        history:   observed series (context).
        horizon:   number of future business days to predict.
        quantiles: quantile levels to return.
        """
        import torch

        pipe = self._load()
        ctx = torch.tensor(history.to_numpy(dtype="float32"))
        qs = pipe.predict_quantiles(
            context=ctx, prediction_length=horizon, quantile_levels=quantiles
        )[0].numpy()  # shape (horizon, len(quantiles))
        idx = pd.bdate_range(history.index[-1], periods=horizon + 1, freq="B")[1:]
        return QuantileForecast(index=idx, quantiles=list(quantiles), values=qs)


if __name__ == "__main__":  # pragma: no cover
    s = pd.Series(np.cumsum(np.random.randn(600)) + 50,
                  index=pd.bdate_range("2022-01-01", periods=600))
    fc = ChronosForecaster().forecast(s, horizon=10, quantiles=[0.05, 0.5, 0.95])
    print(fc.median)
