"""v2-W1: covariate-aware forecasting eval (TimesFM XReg vs v1), pre-registered.

Head-to-head on IDENTICAL HRC=F rolling origins (same grid as steps 4-7):
  arm A (v1):   TimesFM 2.5 zero-shot, no covariates,
  arm B (XReg): TimesFM 2.5 forecast_with_covariates with the pre-registered set
                {UUP, CL=F, HG=F}; horizon covariates = carry-forward persistence.

Both arms get identical ACI online calibration (gamma=0.02, warmup=50, target 90%).

Pre-registered decision rule (design doc sheet 12, fixed before results):
  PRIMARY   mean ACI-calibrated interval width (post-warmup) at matched coverage;
  GUARD     XReg realized coverage must not degrade (>= 88%);
  SECONDARY MASE on the q50 median.
  XReg ships only if PRIMARY improves with the guard intact; otherwise v1 stands
  and Moirai-2 becomes the next candidate.

Forecasts cached to outputs/cache/step8_xreg_bands.npz (--refresh to recompute).
Run (repo root, in venv):
    ./.venv/Scripts/python.exe scripts/step8_covariates_eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.prices import load_covariates                  # noqa: E402
from src.forecast.conformal import aci_cqr                   # noqa: E402
from src.forecast.timesfm_model import TimesFMForecaster     # noqa: E402
from src.forecast.timesfm_xreg import TimesFMXRegForecaster  # noqa: E402

SYMBOL = "HRC=F"
START = "2017-01-01"
SCAN_FROM = "2018-06-01"
HORIZON = 10
TARGET_COV = 0.90
RAW_Q = [0.1, 0.5, 0.9]
COVARIATES = ["UUP", "CL=F", "HG=F"]        # pre-registered, sheet 12
COVERAGE_GUARD = 0.88
CACHE = ROOT / "outputs" / "cache" / "step8_xreg_bands.npz"
OUT_FIG = ROOT / "outputs" / "figures" / "hrc_xreg_vs_v1_width.png"
OUT_JSON = ROOT / "outputs" / "step8_covariates_eval.json"


def load_prices() -> pd.Series:
    import yfinance as yf
    df = yf.download(SYMBOL, start=START, auto_adjust=True, progress=False)
    col = df["Close"]
    s = (col.iloc[:, 0] if hasattr(col, "columns") else col).rename(SYMBOL)
    return s.asfreq("B").ffill().dropna()


def compute_arms(series: pd.Series, refresh: bool):
    if CACHE.exists() and not refresh:
        z = np.load(CACHE, allow_pickle=False)
        return {k: z[k] for k in z.files}
    cov = load_covariates(COVARIATES, start=START)
    v1 = TimesFMForecaster(max_context=1024, max_horizon=64)
    xr = TimesFMXRegForecaster(cov, max_context=1024, max_horizon=64)

    start_idx = series.index.get_indexer([pd.Timestamp(SCAN_FROM)], method="nearest")[0]
    origins = list(range(start_idx, len(series) - HORIZON, HORIZON))
    store = {k: [] for k in ["dates", "actual",
                             "a_lo", "a_med", "a_hi", "b_lo", "b_med", "b_hi"]}
    for k, t in enumerate(origins):
        hist = series.iloc[:t]
        block = series.iloc[t:t + HORIZON]
        fa = v1.forecast(hist, HORIZON, RAW_Q)
        fb = xr.forecast(hist, HORIZON, RAW_Q)
        store["dates"].extend(block.index.values)
        store["actual"].extend(block.to_numpy())
        store["a_lo"].extend(fa.q(0.1)); store["a_med"].extend(fa.q(0.5)); store["a_hi"].extend(fa.q(0.9))
        store["b_lo"].extend(fb.q(0.1)); store["b_med"].extend(fb.q(0.5)); store["b_hi"].extend(fb.q(0.9))
        print(f"      block {k + 1}/{len(origins)} @ {series.index[t].date()}", end="\r")
    print()
    out = {k: (np.array(v, dtype="datetime64[ns]").view("int64") if k == "dates"
               else np.asarray(v, dtype=float)) for k, v in store.items()}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(CACHE, **out)
    return out


def main() -> None:
    refresh = "--refresh" in sys.argv
    print(f"[1/4] Loading {SYMBOL} + computing both arms (cached) ...")
    series = load_prices()
    z = compute_arms(series, refresh)
    dates = pd.to_datetime(pd.Series(z["dates"]).astype("int64"))
    order = np.argsort(dates.values)
    dates = pd.DatetimeIndex(dates.values[order])
    A = {k: z[f"a_{k}"][order] for k in ["lo", "med", "hi"]}
    B = {k: z[f"b_{k}"][order] for k in ["lo", "med", "hi"]}
    actual = z["actual"][order]

    print("[2/4] ACI per arm (identical settings) ...")
    res = {}
    for name, arm in [("v1", A), ("xreg", B)]:
        aci = aci_cqr(arm["lo"], arm["hi"], actual, TARGET_COV, gamma=0.02, warmup=50)
        w = aci["warmup"]
        width = (arm["hi"] + aci["offset"]) - (arm["lo"] - aci["offset"])
        insample = series[series.index < dates[w]]
        denom = float(np.mean(np.abs(np.diff(insample.to_numpy()))))
        res[name] = {
            "coverage": aci["realized_coverage"],
            "mean_width": float(width[w:].mean()),
            "mase": float(np.mean(np.abs(actual[w:] - arm["med"][w:])) / denom),
            "width_series": pd.Series(width, index=dates),
        }
        print(f"      {name:5s} coverage={res[name]['coverage']:.1%}  "
              f"mean width={res[name]['mean_width']:.2f}  MASE={res[name]['mase']:.3f}")

    print("[3/4] Pre-registered decision ...")
    dw = (res["xreg"]["mean_width"] - res["v1"]["mean_width"]) / res["v1"]["mean_width"]
    guard_ok = res["xreg"]["coverage"] >= COVERAGE_GUARD
    primary_improved = dw < 0
    ships = bool(primary_improved and guard_ok)
    print(f"      width change: {dw:+.1%} | coverage guard (>= {COVERAGE_GUARD:.0%}): "
          f"{'OK' if guard_ok else 'FAILED'} | XReg ships: {ships}")

    print("[4/4] Figure + summary ...")
    OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12, 5))
    for name, color in [("v1", "#334155"), ("xreg", "#2E5496")]:
        roll = res[name]["width_series"].rolling(60).mean()
        ax.plot(roll.index, roll.values, color=color, lw=1.4,
                label=f"{name}: mean width {res[name]['mean_width']:.1f}, "
                      f"coverage {res[name]['coverage']:.0%}")
    ax.set_title(f"{SYMBOL}: ACI-calibrated interval width (60d rolling), "
                 f"XReg({', '.join(COVARIATES)}) vs v1", fontsize=12, weight="bold")
    ax.set_ylabel("interval width (USD/ton)")
    ax.legend(loc="upper left", fontsize=9); ax.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(OUT_FIG, dpi=150)
    print(f"      saved {OUT_FIG.relative_to(ROOT)}")

    summary = {
        "symbol": SYMBOL,
        "framing": "pre-registered (design doc sheet 12): primary = mean ACI width at "
                   "matched coverage; guard = XReg coverage >= 88%; secondary = MASE; "
                   "horizon covariates are carry-forward persistence (no lookahead)",
        "covariates": COVARIATES,
        "arms": {n: {k: round(v, 4) for k, v in r.items() if k != "width_series"}
                 for n, r in res.items()},
        "primary_width_change_pct": round(100 * dw, 2),
        "coverage_guard_ok": guard_ok,
        "xreg_ships": ships,
        "decision": ("XReg becomes the default backbone path" if ships else
                     "v1 stands; per the decision rule, Moirai-2 is the next candidate "
                     "(or covariates are dropped if judged not worth the complexity)"),
        "figure": str(OUT_FIG.relative_to(ROOT)).replace("\\", "/"),
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2))
    print(f"      saved {OUT_JSON.relative_to(ROOT)}")
    print(f"\nDECISION: {summary['decision']}")
    print("DONE.")


if __name__ == "__main__":
    main()
