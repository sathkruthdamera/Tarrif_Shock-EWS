"""TimesFM 2.5 zero-shot forecasting -> quantile forecast.

v1 forecasting backbone. Google's TimesFM 2.5 (200M params) is a decoder-only
time-series foundation model that forecasts zero-shot and, with its continuous
quantile head, returns calibrated decile quantiles (q10..q90) directly. Those
deciles are then tightened/widened to the target coverage by the conformal wrapper
(see ``conformal.conformalize_quantiles``).

Chosen over Chronos-2 for v1 because it ships a vetted skill + preflight checker,
is CPU-friendly (~1.5 GB RAM), and its quantile head removes the original
"point-centric" objection. See docs/ design workbook, "Model Selection" sheet.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .chronos_model import QuantileForecast

CHECKPOINT = "google/timesfm-2.5-200m-pytorch"

# TimesFM returns a length-10 quantile vector per step:
# index 0 = mean, 1 = q10, 2 = q20, ... 5 = q50 (median) ... 9 = q90.
_TFM_QUANTILE_INDEX = {round(0.1 * i, 1): i for i in range(1, 10)}  # 0.1->1 ... 0.9->9


class TimesFMForecaster:
    """Wrapper around TimesFM 2.5 for zero-shot quantile forecasts.

    Parameters
    ----------
    max_context: longest history window fed to the model (truncates longer).
    max_horizon: maximum forecast horizon the compiled model supports.
    infer_is_positive: clamp forecasts >= 0. True is correct for price *levels*
        (SLX is always positive); set False for returns or any signed series.
    """

    def __init__(self, max_context: int = 1024, max_horizon: int = 64,
                 infer_is_positive: bool = True):
        self.max_context = max_context
        self.max_horizon = max_horizon
        self.infer_is_positive = infer_is_positive
        self._model = None

    def _load(self):
        if self._model is None:
            import timesfm
            import torch

            torch.set_float32_matmul_precision("high")
            model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(CHECKPOINT)
            model.compile(
                timesfm.ForecastConfig(
                    max_context=self.max_context,
                    max_horizon=self.max_horizon,
                    normalize_inputs=True,
                    use_continuous_quantile_head=True,
                    force_flip_invariance=True,
                    infer_is_positive=self.infer_is_positive,
                    fix_quantile_crossing=True,
                )
            )
            self._model = model
        return self._model

    def forecast(self, history: pd.Series, horizon: int,
                 quantiles: list[float]) -> QuantileForecast:
        """Zero-shot quantile forecast.

        ``quantiles`` must be a subset of TimesFM's deciles (0.1..0.9). The 90%
        target interval is reached afterwards by conformal calibration on the
        q10/q90 band, so requesting [0.1, 0.5, 0.9] here is the intended usage.
        """
        for q in quantiles:
            if round(q, 1) not in _TFM_QUANTILE_INDEX:
                raise ValueError(
                    f"TimesFM exposes deciles 0.1..0.9; {q} is not available. "
                    "Use the conformal wrapper to reach other coverage levels."
                )
        model = self._load()
        values = history.to_numpy(dtype="float32")
        _, q_fc = model.forecast(horizon=horizon, inputs=[values])  # (1, H, 10)
        cols = [q_fc[0, :, _TFM_QUANTILE_INDEX[round(q, 1)]] for q in quantiles]
        arr = np.column_stack(cols)
        idx = pd.bdate_range(history.index[-1], periods=horizon + 1, freq="B")[1:]
        return QuantileForecast(index=idx, quantiles=list(quantiles), values=arr)


if __name__ == "__main__":  # pragma: no cover
    s = pd.Series(np.cumsum(np.random.randn(400)) + 100,
                  index=pd.bdate_range("2022-01-01", periods=400))
    fc = TimesFMForecaster().forecast(s, horizon=10, quantiles=[0.1, 0.5, 0.9])
    print(fc.to_frame() if hasattr(fc, "to_frame") else fc.values)
