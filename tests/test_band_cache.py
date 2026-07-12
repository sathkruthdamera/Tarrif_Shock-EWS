"""Band-cache config guard (post-v2 gap G4): a cache built under a different
symbol/horizon/quantile config must be invalidated, not silently reused.

Uses a stub forecaster so no model is loaded; DATA_DIR is redirected to tmp.
"""
import numpy as np
import pandas as pd

import src.pipeline as pl
from src.forecast.chronos_model import QuantileForecast


class StubForecaster:
    """Constant bands; counts calls so cache hits are observable."""

    def __init__(self):
        self.calls = 0

    def forecast(self, history, horizon, quantiles):
        self.calls += 1
        idx = pd.bdate_range(history.index[-1], periods=horizon + 1, freq="B")[1:]
        med = np.full(horizon, float(history.iloc[-1]))
        vals = np.column_stack([med - 1, med, med + 1])
        return QuantileForecast(index=idx, quantiles=list(quantiles), values=vals)


def _cfg(horizon=5, symbol="TEST=F"):
    return {"vertical": "testband", "target": {"symbol": symbol},
            "forecast": {"horizon_days": horizon, "quantiles": [0.1, 0.5, 0.9]},
            "calibration": {"history_blocks": 4}}


def _series(n=200):
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.Series(np.linspace(100, 120, n), index=idx)


def test_cache_hit_skips_recompute(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "DATA_DIR", tmp_path)
    series, cfg = _series(), _cfg()
    f1 = StubForecaster()
    bands1 = pl._band_history(series, cfg, f1)
    assert f1.calls > 0 and len(bands1) > 0
    assert (tmp_path / "bands_testband.meta.json").exists()

    f2 = StubForecaster()
    bands2 = pl._band_history(series, cfg, f2)
    assert f2.calls == 0                      # full cache hit
    assert len(bands2) == len(bands1)


def test_horizon_change_invalidates_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "DATA_DIR", tmp_path)
    series = _series()
    pl._band_history(series, _cfg(horizon=5), StubForecaster())

    f2 = StubForecaster()
    pl._band_history(series, _cfg(horizon=10), f2)
    assert f2.calls > 0                       # recomputed, not silently reused


def test_symbol_change_invalidates_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "DATA_DIR", tmp_path)
    series = _series()
    pl._band_history(series, _cfg(symbol="A=F"), StubForecaster())

    f2 = StubForecaster()
    pl._band_history(series, _cfg(symbol="B=F"), f2)
    assert f2.calls > 0


def test_missing_meta_invalidates_cache(monkeypatch, tmp_path):
    """Caches created before the guard existed (no sidecar) must recompute."""
    monkeypatch.setattr(pl, "DATA_DIR", tmp_path)
    series, cfg = _series(), _cfg()
    pl._band_history(series, cfg, StubForecaster())
    (tmp_path / "bands_testband.meta.json").unlink()

    f2 = StubForecaster()
    pl._band_history(series, cfg, f2)
    assert f2.calls > 0
