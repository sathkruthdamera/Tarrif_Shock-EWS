"""TimesFM 2.5 covariate-aware forecasting (v2-W1).

Wraps ``forecast_with_covariates`` (timesfm[xreg]; needs jax) with the same
``QuantileForecast`` interface as the v1 forecaster, so both arms drop into the
identical ACI calibration and evaluation code.

No-lookahead contract: dynamic covariates must span context + horizon; future
covariate values are CARRY-FORWARD PERSISTENCE (the last observed value repeated),
never realized futures. Covariate set is pre-registered in design doc sheet 12:
UUP (USD), CL=F (oil), HG=F (copper).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .chronos_model import QuantileForecast
from .timesfm_model import CHECKPOINT, _TFM_QUANTILE_INDEX


class TimesFMXRegForecaster:
    """Covariate-aware TimesFM 2.5 (XReg) with the v1 forecaster's interface."""

    def __init__(self, covariates: pd.DataFrame, max_context: int = 1024,
                 max_horizon: int = 64, infer_is_positive: bool = True,
                 xreg_mode: str = "xreg + timesfm"):
        self.covariates = covariates.asfreq("B").ffill()
        self.max_context = max_context
        self.max_horizon = max_horizon
        self.infer_is_positive = infer_is_positive
        self.xreg_mode = xreg_mode
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
                    return_backcast=True,   # required by the XReg path
                )
            )
            self._model = model
        return self._model

    def _covariate_arrays(self, history: pd.Series, horizon: int) -> dict[str, list]:
        """Context-aligned covariates extended by carry-forward for the horizon."""
        cov = self.covariates.reindex(history.index).ffill().bfill()
        out: dict[str, list] = {}
        for name in cov.columns:
            ctx = cov[name].to_numpy(dtype="float32")
            future = np.repeat(ctx[-1], horizon).astype("float32")  # persistence
            out[name] = [np.concatenate([ctx, future])]
        return out

    def forecast(self, history: pd.Series, horizon: int,
                 quantiles: list[float]) -> QuantileForecast:
        for q in quantiles:
            if round(q, 1) not in _TFM_QUANTILE_INDEX:
                raise ValueError(f"TimesFM exposes deciles 0.1..0.9; got {q}")
        model = self._load()
        values = history.to_numpy(dtype="float32")
        _, q_out = model.forecast_with_covariates(
            inputs=[values],
            dynamic_numerical_covariates=self._covariate_arrays(history, horizon),
            xreg_mode=self.xreg_mode,
        )
        q_fc = np.asarray(q_out[0])                     # (H, 10) deciles
        cols = [q_fc[:, _TFM_QUANTILE_INDEX[round(q, 1)]] for q in quantiles]
        arr = np.column_stack(cols)
        idx = pd.bdate_range(history.index[-1], periods=horizon + 1, freq="B")[1:]
        return QuantileForecast(index=idx, quantiles=list(quantiles), values=arr)
