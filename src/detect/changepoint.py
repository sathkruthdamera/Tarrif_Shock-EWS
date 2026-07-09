"""Changepoint detection on forecast residuals (PELT / BOCPD).

An independent structural-break check. When an interval breach coincides with a detected
changepoint, confidence in a genuine regime shift (and its attribution) rises.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def detect_pelt(signal: pd.Series, penalty: float = 10.0,
                model: str = "rbf") -> list[pd.Timestamp]:
    """Detect changepoints via PELT (ruptures). Returns changepoint timestamps.

    ``model`` is the ruptures cost: "l2" (mean shift, good on price/volatility levels)
    or "rbf" (distributional). For volatility-regime breaks, pass a rolling-std series
    with ``model="l2"``; near-white-noise returns give no usable breaks.
    """
    import ruptures as rpt

    x = signal.dropna().to_numpy()
    algo = rpt.Pelt(model=model).fit(x)
    bkps = algo.predict(pen=penalty)          # indices (last point is len(x))
    idx = signal.dropna().index
    return [idx[b - 1] for b in bkps if 0 < b <= len(idx)]


def volatility_changepoints(prices: pd.Series, window: int = 20,
                            penalty: float = 8.0) -> list[pd.Timestamp]:
    """Convenience: PELT changepoints on rolling return volatility (regime breaks).

    Volatility is expressed in percent (x100) so the ``penalty`` is on a human-readable
    scale; on the raw ~0.0x return-std scale the same penalty would suppress all breaks.
    """
    vol = prices.pct_change().rolling(window).std().dropna() * 100.0
    return detect_pelt(vol, penalty=penalty, model="l2")


def detect_bocpd(residuals: pd.Series, hazard: float = 1 / 100.0) -> list[pd.Timestamp]:
    """Bayesian online changepoint detection (alternative to PELT). Stub.

    Placeholder for a streaming BOCPD implementation; PELT is the default in v1.
    """
    # TODO: implement BOCPD run-length posterior; return changepoint timestamps.
    raise NotImplementedError("BOCPD not yet implemented; use detect_pelt for v1.")


def detect(residuals: pd.Series, cfg: dict) -> list[pd.Timestamp]:
    """Dispatch on the configured changepoint method."""
    method = cfg["detection"].get("changepoint_method", "pelt")
    if method == "pelt":
        return detect_pelt(residuals, penalty=cfg["detection"].get("penalty", 10.0))
    if method == "bocpd":
        return detect_bocpd(residuals)
    raise ValueError(f"Unknown changepoint method: {method!r}")


def agrees_with_breach(changepoints: list[pd.Timestamp], breach_date: pd.Timestamp,
                       tol_days: int = 3) -> bool:
    """True if any changepoint lands within ``tol_days`` of the breach."""
    return any(abs((cp - breach_date).days) <= tol_days for cp in changepoints)
