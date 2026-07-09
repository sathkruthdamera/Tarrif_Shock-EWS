"""Gap B: evaluate the two PRE-SPECIFIED alert-precision filters.

The step-5 forward eval measured precision 3.6% (55 flags, 1 real shock episode) and
the Build Log pre-specified exactly two candidate fixes BEFORE any evaluation:

  F1. changepoint agreement: alert only if a volatility-regime changepoint lies
      within +/-7 days of the breach day,
  F2. multi-day breach run: alert only on the k-th consecutive breach day (k=2),

both standard persistence-filter practice in the early-warning literature
(Kaminsky-Lizondo-Reinhart signals approach; consecutive-signal filtering keeps most
true positives while cutting false alarms). This script evaluates F1, F2 and F1+F2 on
the SAME forward window as step 5 (HRC=F, 2024-07..2026-07), reporting precision,
recall, lead time, and the KLR noise-to-signal ratio for each variant.

Honest caveat carried throughout: the window has n=1 shock episode, so these are
case-study numbers guiding a config default, not validated rates.

Run (repo root, in venv):
    ./.venv/Scripts/python.exe scripts/step7_alert_precision.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.detect import changepoint                            # noqa: E402
from src.eval.event_eval import lead_times, precision_recall  # noqa: E402
from src.forecast.conformal import aci_cqr                    # noqa: E402

SYMBOL = "HRC=F"
START = "2017-01-01"
FORWARD_FROM = "2024-07-01"
SHOCK_RET_WIN = 10
SHOCK_PCTL = 95
WARN_WIN = 10
TOL_DAYS = 10
TARGET_COV = 0.90
CP_TOL_DAYS = 7
MIN_RUN = 2
CACHE = ROOT / "outputs" / "cache" / "gap1_hrc_bands.npz"
OUT_JSON = ROOT / "outputs" / "step7_alert_precision.json"


def load_prices() -> pd.Series:
    import yfinance as yf
    df = yf.download(SYMBOL, start=START, auto_adjust=True, progress=False)
    col = df["Close"]
    s = (col.iloc[:, 0] if hasattr(col, "columns") else col).rename(SYMBOL)
    return s.asfreq("B").ffill().dropna()


def shock_onsets(series: pd.Series, cutoff: pd.Timestamp) -> list:
    absret = (series / series.shift(SHOCK_RET_WIN) - 1.0).abs()
    thresh = float(np.nanpercentile(absret[absret.index < cutoff], SHOCK_PCTL))
    onsets, prev = [], False
    for d, flag in (absret[absret.index >= cutoff] > thresh).items():
        if flag and not prev:
            onsets.append(d)
        prev = flag
    return onsets


def klr_nsr(flag_dates: list, onsets: list, all_days: pd.DatetimeIndex,
            warn_win: int = WARN_WIN) -> float:
    """Kaminsky-Lizondo-Reinhart noise-to-signal ratio.

    NSR = P(flag | outside pre-shock window) / P(flag | inside pre-shock window).
    Lower is better; > 1 means the indicator is mostly noise.
    """
    in_win = np.zeros(len(all_days), dtype=bool)
    for o in onsets:
        in_win |= (all_days >= o - pd.Timedelta(days=warn_win)) & (all_days <= o)
    flags = np.isin(all_days, pd.DatetimeIndex(flag_dates))
    p_good = flags[in_win].mean() if in_win.any() else np.nan
    p_noise = flags[~in_win].mean() if (~in_win).any() else np.nan
    return float(p_noise / p_good) if p_good else float("inf")


def evaluate(name: str, flag_dates: list, onsets: list,
             all_days: pd.DatetimeIndex) -> dict:
    lts = lead_times(flag_dates, onsets, max_gap_days=WARN_WIN)
    prec, rec = precision_recall(flag_dates, onsets, tol_days=TOL_DAYS)
    nsr = klr_nsr(flag_dates, onsets, all_days)
    row = {"variant": name, "n_flags": len(flag_dates),
           "recall": f"{len(lts)}/{len(onsets)}",
           "precision": round(prec, 4) if not np.isnan(prec) else None,
           "median_lead_days": float(np.median(lts)) if lts else None,
           "klr_noise_to_signal": round(nsr, 3) if np.isfinite(nsr) else None}
    print(f"      {name:24s} flags={row['n_flags']:3d}  recall={row['recall']}  "
          f"precision={prec if not np.isnan(prec) else float('nan'):.1%}  "
          f"NSR={row['klr_noise_to_signal']}")
    return row


def main() -> None:
    cfg = yaml.safe_load(open(ROOT / "config" / "steel.yaml"))
    cutoff = pd.Timestamp(FORWARD_FROM)

    print("[1/3] Data: prices, cached bands, ACI flags, changepoints ...")
    series = load_prices()
    z = np.load(CACHE, allow_pickle=False)
    dates = pd.to_datetime(z["dates"])
    order = np.argsort(dates.values)
    dates, lo, hi, actual = dates[order], z["lo"][order], z["hi"][order], z["actual"][order]
    aci = aci_cqr(lo, hi, actual, TARGET_COV, gamma=0.02, warmup=50)
    breach = ~aci["covered"]
    breach[:aci["warmup"]] = False

    det = cfg["detection"]
    cps = changepoint.volatility_changepoints(
        series, window=det.get("vol_window", 20), penalty=det.get("penalty", 8.0))
    onsets = shock_onsets(series, cutoff)
    fwd_mask = dates >= cutoff
    all_days = pd.DatetimeIndex(dates[fwd_mask])
    print(f"      forward days={len(all_days)}, shock onsets={len(onsets)} "
          f"({[str(o.date()) for o in onsets]}), changepoints={len(cps)}")

    print("[2/3] Filter variants (pre-specified F1/F2) ...")
    base_flags = [d for d, b in zip(dates, breach) if b and d >= cutoff]

    # F1: changepoint within +/- CP_TOL_DAYS
    f1_flags = [d for d in base_flags
                if changepoint.agrees_with_breach(cps, d, tol_days=CP_TOL_DAYS)]

    # F2: k-th consecutive breach day (business-day adjacency on the scan grid)
    date_pos = {d: i for i, d in enumerate(dates)}
    f2_flags = []
    for d in base_flags:
        i = date_pos[d]
        run = 1
        while i - run >= 0 and breach[i - run] and \
                (dates[i - run + 1] - dates[i - run]).days <= 4:
            run += 1
        if run >= MIN_RUN:
            f2_flags.append(d)

    f12_flags = [d for d in f2_flags
                 if changepoint.agrees_with_breach(cps, d, tol_days=CP_TOL_DAYS)]

    rows = [
        evaluate("baseline (no filter)", base_flags, onsets, all_days),
        evaluate(f"F1 changepoint +/-{CP_TOL_DAYS}d", f1_flags, onsets, all_days),
        evaluate(f"F2 run>={MIN_RUN} days", f2_flags, onsets, all_days),
        evaluate("F1+F2 combined", f12_flags, onsets, all_days),
    ]

    print("[3/3] Summary ...")
    summary = {
        "symbol": SYMBOL,
        "framing": "filters F1/F2 were pre-specified in the design doc Build Log "
                   "BEFORE evaluation (persistence-filter practice per "
                   "Kaminsky-Lizondo-Reinhart); evaluated on the step-5 forward "
                   "window; n=1 shock -> case-study numbers guiding config defaults, "
                   "not validated rates",
        "forward_window": f"{FORWARD_FROM} to {dates.max().date()}",
        "shock_onsets": [str(o.date()) for o in onsets],
        "variants": rows,
        "config_decision": "expose alert_filters.min_breach_run and "
                           "alert_filters.require_changepoint in config; defaults "
                           "chosen from these numbers and documented in the README",
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2))
    print(f"      saved {OUT_JSON.relative_to(ROOT)}")
    print("\nDONE.")


if __name__ == "__main__":
    main()
