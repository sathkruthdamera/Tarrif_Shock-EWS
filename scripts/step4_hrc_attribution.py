"""Gap 1: retry attribution significance on a steel-pure target (HRC futures).

The step-3 permutation test said the breach<->event association on SLX was NOT
significant (p=0.134): SLX is too diversified and the event set too dense. This
script re-runs the test exactly as the verdict prescribed:

  - target: HRC=F (CME hot-rolled coil steel futures), the instrument Section 232
    tariffs act on directly, instead of a diversified ETF,
  - events: HIGH-SEVERITY only (severity >= SEV_MIN via the rule tagger),
  - window: TIGHT 5-day primary window (pre-registered before looking at p),
    with 3/10/14-day windows reported as sensitivity only,
  - calibration: ACI online (Gibbs & Candes), no lookahead, per step-3 finding,
  - arbiter: the same circular-shift permutation test (B=2000).

The primary claim stands or falls on the pre-registered (sev>=SEV_MIN, 5d) cell.
Run (repo root, in venv):
    ./.venv/Scripts/python.exe scripts/step4_hrc_attribution.py [--refresh]
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
from src.events.severity import severity                      # noqa: E402
from src.forecast.conformal import aci_cqr                    # noqa: E402
from src.forecast.timesfm_model import TimesFMForecaster      # noqa: E402

SYMBOL = "HRC=F"
START = "2017-01-01"
SCAN_FROM = "2018-06-01"
HORIZON = 10
TARGET_COV = 0.90
RAW_Q = [0.1, 0.5, 0.9]
SEV_MIN = 0.7                 # pre-registered: high-severity events only
PRIMARY_WINDOW = 5            # pre-registered primary lookback (calendar days)
SENSITIVITY_WINDOWS = [3, 10, 14]
N_PERM = 2000
CACHE = ROOT / "outputs" / "cache" / "gap1_hrc_bands.npz"
OUT_FIG = ROOT / "outputs" / "figures" / "hrc_breaches_high_severity.png"
OUT_JSON = ROOT / "outputs" / "step4_hrc_summary.json"


def load_prices() -> pd.Series:
    import yfinance as yf
    df = yf.download(SYMBOL, start=START, auto_adjust=True, progress=False)
    col = df["Close"]
    s = (col.iloc[:, 0] if hasattr(col, "columns") else col).rename(SYMBOL)
    return s.asfreq("B").ffill().dropna()


def compute_bands(series: pd.Series, refresh: bool):
    if CACHE.exists() and not refresh:
        z = np.load(CACHE, allow_pickle=False)
        return pd.to_datetime(z["dates"]), z["lo"], z["hi"], z["actual"]
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
        print(f"      block {k + 1}/{len(origins)} @ {series.index[t].date()}", end="\r")
    print()
    dates = np.array(dates, dtype="datetime64[ns]")
    lo, hi, act = np.array(lo), np.array(hi), np.array(act)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(CACHE, dates=dates.view("int64"), lo=lo, hi=hi, actual=act)
    return pd.to_datetime(dates), lo, hi, act


def perm_test(breach_mask: np.ndarray, has_event: np.ndarray,
              rng: np.random.Generator, n_perm: int = N_PERM) -> dict:
    """Circular-shift permutation test of breach<->event co-occurrence."""
    if breach_mask.sum() == 0:
        return {"observed": float("nan"), "base": float(has_event.mean()),
                "null_mean": float("nan"), "p_value": float("nan")}
    obs = float(has_event[breach_mask].mean())
    n = len(breach_mask)
    null = np.empty(n_perm)
    for b in range(n_perm):
        null[b] = has_event[np.roll(breach_mask, int(rng.integers(1, n)))].mean()
    return {"observed": obs, "base": float(has_event.mean()),
            "null_mean": float(null.mean()),
            "p_value": float((null >= obs).mean())}


def main() -> None:
    refresh = "--refresh" in sys.argv
    cfg = yaml.safe_load(open(ROOT / "config" / "steel.yaml"))

    print(f"[1/5] Loading {SYMBOL} + events ...")
    series = load_prices()
    events = build_event_table(cfg, cache=False)
    events = events.assign(severity=severity(events))
    hi_sev = events[events["severity"] >= SEV_MIN]
    ev_all = pd.to_datetime(events["published"]).sort_values()
    ev_hi = pd.to_datetime(hi_sev["published"]).sort_values()
    print(f"      {len(series)} bdays; events: {len(events)} total, "
          f"{len(hi_sev)} high-severity (sev>={SEV_MIN})")

    print("[2/5] TimesFM bands on HRC (cached) ...")
    dates, lo, hi, actual = compute_bands(series, refresh)
    order = np.argsort(dates.values)
    dates, lo, hi, actual = dates[order], lo[order], hi[order], actual[order]

    print("[3/5] ACI online calibration (no lookahead) ...")
    aci = aci_cqr(lo, hi, actual, TARGET_COV, gamma=0.02, warmup=50)
    covered, offsets = aci["covered"], aci["offset"]
    warm = aci["warmup"]
    breach_mask = ~covered
    breach_mask[:warm] = False          # discard warmup, offsets not yet meaningful
    print(f"      ACI realized coverage (post-warmup): {aci['realized_coverage']:.1%}; "
          f"breach-days: {int(breach_mask.sum())}/{len(dates) - warm}")

    print("[4/5] Permutation tests ...")
    rng = np.random.default_rng(12345)
    d_index = pd.to_datetime(dates)

    def event_flags(ev_dates: pd.Series, window: int) -> np.ndarray:
        return np.array([bool(len(ev_dates[(ev_dates <= d) &
                        (ev_dates >= d - pd.Timedelta(days=window))])) for d in d_index])

    # PRIMARY (pre-registered): high-severity events, 5-day window
    has_hi_5 = event_flags(ev_hi, PRIMARY_WINDOW)
    primary = perm_test(breach_mask, has_hi_5, rng)
    print(f"      PRIMARY  sev>={SEV_MIN}, {PRIMARY_WINDOW}d: hit={primary['observed']:.1%} "
          f"base={primary['base']:.1%} p={primary['p_value']:.4f}")

    sensitivity = {}
    for w in SENSITIVITY_WINDOWS:
        r = perm_test(breach_mask, event_flags(ev_hi, w), rng)
        sensitivity[f"high_sev_{w}d"] = {k: round(v, 4) for k, v in r.items()}
        print(f"      sens     sev>={SEV_MIN}, {w:2d}d: hit={r['observed']:.1%} "
              f"base={r['base']:.1%} p={r['p_value']:.4f}")
    # reference: all events, 14d (the SLX-style cell, for comparability)
    r_all = perm_test(breach_mask, event_flags(ev_all, 14), rng)
    sensitivity["all_events_14d_reference"] = {k: round(v, 4) for k, v in r_all.items()}
    print(f"      ref      all events, 14d: hit={r_all['observed']:.1%} "
          f"base={r_all['base']:.1%} p={r_all['p_value']:.4f}")

    print("[5/5] Plotting ...")
    OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
    view = series[series.index >= pd.Timestamp(SCAN_FROM)]
    fig, ax = plt.subplots(figsize=(13, 5.5))
    ax.plot(view.index, view.values, color="#334155", lw=1.0, label="HRC=F")
    bdates = d_index[breach_mask]
    bacts = actual[breach_mask]
    hit_mask = has_hi_5[breach_mask]
    if (~hit_mask).any():
        ax.scatter(bdates[~hit_mask], bacts[~hit_mask], color="#94A3B8", s=16,
                   label="Breach (no high-sev event in 5d)")
    if hit_mask.any():
        ax.scatter(bdates[hit_mask], bacts[hit_mask], color="#B00020", s=30,
                   label="Breach (high-sev event in 5d)")
    for d in ev_hi[ev_hi >= pd.Timestamp(SCAN_FROM)]:
        ax.axvline(d, color="#3F7D3A", alpha=0.25, lw=0.9)
    ax.set_title(f"HRC steel futures: ACI breaches vs high-severity tariff events "
                 f"(green lines, sev>={SEV_MIN})", fontsize=12, weight="bold")
    ax.set_ylabel("USD / short ton")
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    ax.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(OUT_FIG, dpi=150)
    print(f"      saved {OUT_FIG.relative_to(ROOT)}")

    p = primary["p_value"]
    summary = {
        "symbol": SYMBOL,
        "framing": "pre-registered primary test: ACI online breaches x high-severity "
                   f"events (sev>={SEV_MIN}) within {PRIMARY_WINDOW} days; "
                   "sensitivity windows reported but not the claim",
        "n_events_total": int(len(events)),
        "n_events_high_severity": int(len(hi_sev)),
        "aci_realized_coverage": round(aci["realized_coverage"], 4),
        "n_breach_days": int(breach_mask.sum()),
        "n_days_scanned": int(len(dates) - warm),
        "primary_test": {k: round(v, 4) for k, v in primary.items()},
        "primary_significant_at_0.05": bool(p <= 0.05),
        "sensitivity": sensitivity,
        "figure": str(OUT_FIG.relative_to(ROOT)).replace("\\", "/"),
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2))
    print(f"      saved {OUT_JSON.relative_to(ROOT)}")
    verdict = "SIGNIFICANT" if p <= 0.05 else "NOT significant"
    print(f"\nPRIMARY VERDICT: {verdict} (p={p:.4f})")
    print("DONE.")


if __name__ == "__main__":
    main()
