"""Build-step 1: real zero-shot TimesFM forecast of SLX with a calibrated interval.

Pipeline demonstrated end to end:
  1. pull real SLX daily closes (yfinance),
  2. zero-shot forecast with TimesFM 2.5 (native q10/q50/q90 deciles),
  3. calibrate the band to 90% coverage with split-conformal CQR,
  4. benchmark against ARIMA + seasonal-naive baselines (statsmodels),
  5. plot history + forecast + calibrated interval + holdout, flag breaches,
  6. write a JSON summary for the README worked example.

Run (from repo root, inside the venv):
    ./.venv/Scripts/python.exe scripts/step1_forecast_slx.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.forecast.conformal import apply_cqr, cqr_offset  # noqa: E402
from src.forecast.timesfm_model import TimesFMForecaster   # noqa: E402

# ---- config -----------------------------------------------------------------
SYMBOL = "SLX"
START = "2016-01-01"
HORIZON = 10
TARGET_COV = 0.90
RAW_Q = [0.1, 0.5, 0.9]          # TimesFM 80% band (deciles)
N_ORIGINS = 32                   # rolling calibration/eval origins
CALIB_FRAC = 0.6                 # first 60% of origins -> offset; rest -> eval
OUT_FIG = ROOT / "outputs" / "figures" / "slx_timesfm_calibrated_interval.png"
OUT_JSON = ROOT / "outputs" / "step1_summary.json"


def load_prices() -> pd.Series:
    import yfinance as yf
    df = yf.download(SYMBOL, start=START, auto_adjust=True, progress=False)
    if df.empty:
        raise RuntimeError(f"No data for {SYMBOL}")
    col = df["Close"]
    s = (col.iloc[:, 0] if hasattr(col, "columns") else col).rename(SYMBOL)
    return s.asfreq("B").ffill().dropna()


def arima_forecast(history: pd.Series, horizon: int) -> np.ndarray:
    from statsmodels.tsa.arima.model import ARIMA
    try:
        fit = ARIMA(history.to_numpy(), order=(1, 1, 1)).fit()
        return np.asarray(fit.forecast(steps=horizon), dtype=float)
    except Exception:
        return np.repeat(history.iloc[-1], horizon)  # fall back to naive


def mase(actual: np.ndarray, pred: np.ndarray, insample: pd.Series) -> float:
    denom = np.mean(np.abs(np.diff(insample.to_numpy())))
    return float(np.mean(np.abs(actual - pred)) / denom) if denom else float("nan")


def main() -> None:
    print(f"[1/5] Loading {SYMBOL} ...")
    series = load_prices()
    print(f"      {len(series)} business days: {series.index[0].date()} -> {series.index[-1].date()}")

    forecaster = TimesFMForecaster(max_context=1024, max_horizon=64)

    # rolling origins over the tail (leave the final HORIZON as the headline holdout)
    end = len(series) - HORIZON
    origins = np.linspace(int(end * 0.72), end - HORIZON, N_ORIGINS, dtype=int)
    origins = np.unique(origins)
    n_calib = int(len(origins) * CALIB_FRAC)

    print(f"[2/5] TimesFM zero-shot over {len(origins)} rolling origins ...")
    rows = []
    for k, t in enumerate(origins):
        hist = series.iloc[:t]
        actual = series.iloc[t:t + HORIZON].to_numpy()
        fc = forecaster.forecast(hist, HORIZON, RAW_Q)
        lo, med, hi = fc.q(0.1).to_numpy(), fc.q(0.5).to_numpy(), fc.q(0.9).to_numpy()
        arima = arima_forecast(hist, HORIZON)
        naive = np.repeat(hist.iloc[-1], HORIZON)
        rows.append(dict(t=t, lo=lo, med=med, hi=hi, actual=actual,
                         arima=arima, naive=naive, insample=hist))
        print(f"      origin {k + 1}/{len(origins)} @ {series.index[t].date()}", end="\r")
    print()

    calib, test = rows[:n_calib], rows[n_calib:]

    # ---- CQR offset from calibration origins -------------------------------
    print("[3/5] Calibrating band with split-conformal CQR ...")
    c_lo = np.concatenate([r["lo"] for r in calib])
    c_hi = np.concatenate([r["hi"] for r in calib])
    c_y = np.concatenate([r["actual"] for r in calib])
    Q = cqr_offset(c_lo, c_hi, c_y, TARGET_COV)

    def coverage(recs, lo_adj=0.0, hi_adj=0.0):
        y = np.concatenate([r["actual"] for r in recs])
        lo = np.concatenate([r["lo"] for r in recs]) - lo_adj
        hi = np.concatenate([r["hi"] for r in recs]) + hi_adj
        return float(np.mean((y >= lo) & (y <= hi)))

    raw_cov = coverage(test)
    cqr_cov = coverage(test, Q, Q)

    # ---- baselines on test origins -----------------------------------------
    tfm_mase = float(np.mean([mase(r["actual"], r["med"], r["insample"]) for r in test]))
    arima_mase = float(np.mean([mase(r["actual"], r["arima"], r["insample"]) for r in test]))
    naive_mase = float(np.mean([mase(r["actual"], r["naive"], r["insample"]) for r in test]))

    print(f"      CQR offset Q = {Q:.3f}")
    print(f"      raw 80% band coverage (test): {raw_cov:.1%}")
    print(f"      CQR-calibrated coverage (test): {cqr_cov:.1%}  (target {TARGET_COV:.0%})")
    print(f"      MASE  TimesFM={tfm_mase:.3f}  ARIMA={arima_mase:.3f}  naive={naive_mase:.3f}")

    # ---- headline forecast on the final holdout ----------------------------
    print("[4/5] Headline forecast on the final holdout ...")
    hist = series.iloc[:-HORIZON]
    actual = series.iloc[-HORIZON:]
    fc = forecaster.forecast(hist, HORIZON, RAW_Q)
    interval = apply_cqr(fc.index, fc.q(0.1).to_numpy(), fc.q(0.5).to_numpy(),
                         fc.q(0.9).to_numpy(), Q, TARGET_COV)
    frame = interval.to_frame()
    breaches = interval.breaches(actual)
    n_breach = int(breaches.sum())

    # ---- plot ---------------------------------------------------------------
    print("[5/5] Plotting ...")
    OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
    ctx = series.iloc[-150:]
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.plot(ctx.index, ctx.values, color="#334155", lw=1.3, label="SLX history")
    ax.plot(fc.index, frame["median"], color="#2E5496", lw=1.8, ls="--", label="TimesFM median")
    ax.fill_between(fc.index, fc.q(0.1), fc.q(0.9), color="#2E5496", alpha=0.12,
                    label="Raw 80% band (q10-q90)")
    ax.fill_between(fc.index, frame["lower"], frame["upper"], color="#3F7D3A", alpha=0.18,
                    label=f"CQR-calibrated {TARGET_COV:.0%} interval")
    ax.plot(actual.index, actual.values, color="#0F172A", marker="o", ms=4, lw=1.0,
            label="Actual (holdout)")
    if n_breach:
        b = actual[breaches.values]
        ax.scatter(b.index, b.values, color="#B00020", zorder=5, s=55,
                   label="Interval breach")
    ax.set_title(f"{SYMBOL}: TimesFM 2.5 zero-shot forecast with conformal-calibrated interval",
                 fontsize=12, weight="bold")
    ax.set_ylabel("Adj. close (USD)")
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT_FIG, dpi=150)
    print(f"      saved {OUT_FIG.relative_to(ROOT)}")

    # ---- summary JSON -------------------------------------------------------
    summary = {
        "symbol": SYMBOL,
        "data_start": str(series.index[0].date()),
        "data_end": str(series.index[-1].date()),
        "horizon_days": HORIZON,
        "target_coverage": TARGET_COV,
        "cqr_offset": round(Q, 4),
        "raw_80_coverage_test": round(raw_cov, 4),
        "cqr_coverage_test": round(cqr_cov, 4),
        "mase": {"timesfm": round(tfm_mase, 4), "arima": round(arima_mase, 4),
                 "seasonal_naive": round(naive_mase, 4)},
        "headline_forecast": {
            "origin_date": str(hist.index[-1].date()),
            "forecast_dates": [str(d.date()) for d in fc.index],
            "median": [round(float(v), 2) for v in frame["median"]],
            "lower": [round(float(v), 2) for v in frame["lower"]],
            "upper": [round(float(v), 2) for v in frame["upper"]],
            "actual": [round(float(v), 2) for v in actual.to_numpy()],
            "n_breaches": n_breach,
        },
        "figure": str(OUT_FIG.relative_to(ROOT)).replace("\\", "/"),
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2))
    print(f"      saved {OUT_JSON.relative_to(ROOT)}")
    print("\nDONE.")


if __name__ == "__main__":
    main()
