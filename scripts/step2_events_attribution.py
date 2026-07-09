"""Build-step 2: event monitor + breach attribution on real SLX history.

Retrospective attribution study (not a forward early-warning claim, that is a later
build step). It:
  1. pulls real SLX closes + real Federal Register steel-tariff proclamations/rules,
  2. zero-shot forecasts non-overlapping H-day blocks over history (cached to disk),
  3. calibrates the q10/q90 band to 90% coverage with full-sample CQR,
  4. flags the ~10% of days that still fall outside the calibrated interval (breaches),
  5. attributes each breach to the most likely event (relevance x severity x recency),
  6. cross-checks with volatility-regime changepoints,
  7. reports realized coverage, an event-density baseline (so the hit-rate is not
     oversold), the best proclamation-linked example, a figure, and a JSON summary.

Forecasts are cached in outputs/cache/step2_bands.npz; pass --refresh to recompute.

Run (from repo root, inside the venv):
    ./.venv/Scripts/python.exe scripts/step2_events_attribution.py
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
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.events import build_event_table                 # noqa: E402
from src.detect import changepoint                            # noqa: E402
from src.events.attribute import attribute_break              # noqa: E402
from src.events.embed import EventEmbedder                    # noqa: E402
from src.forecast.conformal import cqr_offset                 # noqa: E402
from src.forecast.timesfm_model import TimesFMForecaster      # noqa: E402

SYMBOL = "SLX"
START = "2016-01-01"
SCAN_FROM = "2018-06-01"
HORIZON = 10
TARGET_COV = 0.90
RAW_Q = [0.1, 0.5, 0.9]
CACHE = ROOT / "outputs" / "cache" / "step2_bands.npz"
OUT_FIG = ROOT / "outputs" / "figures" / "slx_breaches_attribution.png"
OUT_JSON = ROOT / "outputs" / "step2_summary.json"
PROCLAMATION = "adjusting imports of steel"


def load_prices() -> pd.Series:
    import yfinance as yf
    df = yf.download(SYMBOL, start=START, auto_adjust=True, progress=False)
    col = df["Close"]
    s = (col.iloc[:, 0] if hasattr(col, "columns") else col).rename(SYMBOL)
    return s.asfreq("B").ffill().dropna()


def compute_bands(series: pd.Series, refresh: bool):
    """Return flattened (dates, lo_raw, hi_raw, actual) over non-overlapping blocks."""
    if CACHE.exists() and not refresh:
        z = np.load(CACHE, allow_pickle=False)
        return (pd.to_datetime(z["dates"]), z["lo"], z["hi"], z["actual"])
    forecaster = TimesFMForecaster(max_context=1024, max_horizon=64)
    start_idx = series.index.get_indexer([pd.Timestamp(SCAN_FROM)], method="nearest")[0]
    origins = list(range(start_idx, len(series) - HORIZON, HORIZON))
    dates, lo, hi, act = [], [], [], []
    for k, t in enumerate(origins):
        fc = forecaster.forecast(series.iloc[:t], HORIZON, RAW_Q)
        block = series.iloc[t:t + HORIZON]
        dates.extend(block.index.values)
        lo.extend(fc.q(0.1).to_numpy()); hi.extend(fc.q(0.9).to_numpy())
        act.extend(block.to_numpy())
        print(f"      forecast block {k + 1}/{len(origins)} @ {series.index[t].date()}", end="\r")
    print()
    dates = np.array(dates, dtype="datetime64[ns]")
    lo, hi, act = np.array(lo), np.array(hi), np.array(act)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(CACHE, dates=dates.view("int64"), lo=lo, hi=hi, actual=act)
    return pd.to_datetime(dates), lo, hi, act


def main() -> None:
    refresh = "--refresh" in sys.argv
    cfg = yaml.safe_load(open(ROOT / "config" / "steel.yaml"))
    lookback = cfg["attribution"]["lookback_days"]

    print("[1/6] Loading SLX + Federal Register events ...")
    series = load_prices()
    events = build_event_table(cfg, cache=False)
    tariff_dates = pd.to_datetime(events["published"]).sort_values()
    print(f"      {len(series)} bdays; {len(events)} events "
          f"({tariff_dates.min().date()}..{tariff_dates.max().date()})")

    print("[2/6] Forecast bands (TimesFM, cached) ...")
    dates, lo_raw, hi_raw, actual = compute_bands(series, refresh)

    print("[3/6] Full-sample CQR calibration + breach detection ...")
    Q = cqr_offset(lo_raw, hi_raw, actual, TARGET_COV)
    lo_cal, hi_cal = lo_raw - Q, hi_raw + Q
    inside = (actual >= lo_cal) & (actual <= hi_cal)
    realized_cov = float(inside.mean())
    breach_mask = ~inside
    bdates = pd.to_datetime(dates[breach_mask])
    print(f"      Q={Q:.3f}  realized coverage={realized_cov:.1%} (target {TARGET_COV:.0%})  "
          f"breach-days={int(breach_mask.sum())}/{len(dates)}")

    print("[4/6] Volatility-regime changepoints ...")
    det = cfg["detection"]
    cps = changepoint.volatility_changepoints(series, window=det.get("vol_window", 20),
                                              penalty=det.get("penalty", 8.0))
    print(f"      {len(cps)} regime changepoints")

    print("[5/6] Attributing breaches ...")
    embedder = EventEmbedder()
    attributed = []
    for d, y, lo_i, hi_i in zip(bdates, actual[breach_mask],
                                lo_cal[breach_mask], hi_cal[breach_mask]):
        drivers = attribute_break(d, events, cfg, embedder)
        top = drivers[0] if drivers else None
        in_win = tariff_dates[(tariff_dates <= d) & (tariff_dates >= d - pd.Timedelta(days=lookback))]
        attributed.append(dict(date=d, actual=float(y), lower=float(lo_i), upper=float(hi_i),
                               direction="down" if y < lo_i else "up",
                               has_event=bool(len(in_win)),
                               cp_agree=changepoint.agrees_with_breach(cps, d, tol_days=7),
                               top=top))

    # honest metrics: breach hit-rate vs the baseline co-occurrence of ANY scan day
    n_with_event = sum(a["has_event"] for a in attributed)
    all_dates = pd.to_datetime(dates)
    base = np.mean([bool(len(tariff_dates[(tariff_dates <= d) &
                    (tariff_dates >= d - pd.Timedelta(days=lookback))])) for d in all_dates])
    print(f"      breaches with tariff event within {lookback}d: {n_with_event}/{len(attributed)} "
          f"({n_with_event/len(attributed):.0%}) vs base rate on all days {base:.0%}")

    # best example: a breach attributed to a steel PROCLAMATION, changepoint agreement preferred
    procs = [a for a in attributed if a["top"] and PROCLAMATION in a["top"].title.lower()]
    procs.sort(key=lambda a: (a["cp_agree"], a["top"].relevance), reverse=True)
    best = procs[0] if procs else None
    if best:
        print(f"      best proclamation-linked breach: {best['date'].date()} "
              f"({best['direction']}) -> {best['top'].published.date()} "
              f"| {best['top'].title[:52]} | cp_agree={best['cp_agree']}")

    print("[6/6] Plotting ...")
    OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
    view = series[series.index >= pd.Timestamp(SCAN_FROM)]
    fig, ax = plt.subplots(figsize=(13, 5.5))
    ax.plot(view.index, view.values, color="#334155", lw=1.0, label="SLX")
    be = [a for a in attributed if a["has_event"]]
    bn = [a for a in attributed if not a["has_event"]]
    if bn:
        ax.scatter([a["date"] for a in bn], [a["actual"] for a in bn],
                   color="#94A3B8", s=16, label="Breach (no event in window)")
    if be:
        ax.scatter([a["date"] for a in be], [a["actual"] for a in be],
                   color="#B00020", s=24, label="Breach (event in window)")
    for d in [c for c in cps if c >= pd.Timestamp(SCAN_FROM)]:
        ax.axvline(d, color="#2E5496", alpha=0.22, lw=0.9)
    if best:
        ax.annotate(f"{best['date'].date()} breach\n<- {best['top'].title[:34]}\n({best['top'].published.date()})",
                    xy=(best["date"], best["actual"]), xytext=(12, -66),
                    textcoords="offset points", fontsize=8,
                    arrowprops=dict(arrowstyle="->", color="#B00020"),
                    bbox=dict(boxstyle="round", fc="#FFF3F3", ec="#B00020", alpha=0.9))
    ax.set_title(f"{SYMBOL}: retrospective interval breaches + attribution "
                 f"(blue = volatility changepoints)", fontsize=12, weight="bold")
    ax.set_ylabel("Adj. close (USD)")
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT_FIG, dpi=150)
    print(f"      saved {OUT_FIG.relative_to(ROOT)}")

    summary = {
        "symbol": SYMBOL, "scan_from": SCAN_FROM, "data_end": str(series.index[-1].date()),
        "framing": "retrospective attribution (full-sample CQR), not forward early-warning",
        "n_events": int(len(events)), "cqr_offset": round(Q, 4),
        "realized_coverage": round(realized_cov, 4),
        "n_breach_days": int(breach_mask.sum()), "total_days_scanned": int(len(dates)),
        "n_breaches_with_event": int(n_with_event),
        "event_density_base_rate": round(float(base), 4),
        "hit_rate_note": "hit-rate is near the base rate: events are dense, so co-occurrence "
                         "is largely density-driven, not evidence of causal attribution",
        "n_changepoints": len(cps),
        "best_proclamation_example": None if not best else {
            "breach_date": str(best["date"].date()), "direction": best["direction"],
            "actual": round(best["actual"], 2),
            "interval": [round(best["lower"], 2), round(best["upper"], 2)],
            "changepoint_agrees": best["cp_agree"],
            "attributed_event": {
                "title": best["top"].title, "published": str(best["top"].published.date()),
                "source_url": best["top"].source_url,
                "relevance": round(best["top"].relevance, 3),
                "severity": round(best["top"].severity, 3),
                "recency": round(best["top"].recency, 3),
                "score": round(best["top"].score, 3),
            },
        },
        "figure": str(OUT_FIG.relative_to(ROOT)).replace("\\", "/"),
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2))
    print(f"      saved {OUT_JSON.relative_to(ROOT)}")
    print("\nDONE.")


if __name__ == "__main__":
    main()
