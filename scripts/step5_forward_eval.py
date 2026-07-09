"""Gap 2 (G3): forward early-warning evaluation with ACI online flags.

Evaluates the EWS the way the early-warning literature does (signals approach,
Kaminsky-Lizondo-Reinhart style), on a forward split with no lookahead anywhere:

  - flags:  ACI online interval breaches (calibration updates only on the past),
  - shocks: price-shock episodes in the FORWARD window, defined as trailing
    10-day |return| exceeding the 95th percentile computed on the PRE-forward
    period only; consecutive shock days collapse into episodes, onset = first day,
  - metrics: recall (share of shock onsets flagged within the 10 days up to onset),
    precision (share of flags within +/-10d of an onset), median lead time in days
    (via src/eval/event_eval.py, the design's eval module),
  - attribution: for each detected onset, whether the attribution engine surfaces
    at least one candidate event with a source link (candidate-surfacing rate,
    the claim gap-1 left us with; NOT a causal-accuracy claim).

Target: HRC=F (steel-pure, per gap-1). Reuses cached TimesFM bands.
Run (repo root, in venv):
    ./.venv/Scripts/python.exe scripts/step5_forward_eval.py
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
from src.eval.event_eval import lead_times, precision_recall  # noqa: E402
from src.events.attribute import attribute_break              # noqa: E402
from src.events.embed import EventEmbedder                    # noqa: E402
from src.forecast.conformal import aci_cqr                    # noqa: E402

SYMBOL = "HRC=F"
START = "2017-01-01"
FORWARD_FROM = "2024-07-01"   # forward eval split (last ~2 years)
SHOCK_RET_WIN = 10            # trailing window for the shock definition
SHOCK_PCTL = 95               # percentile threshold, computed pre-forward only
WARN_WIN = 10                 # flag within [onset-10d, onset] counts as a warning
TOL_DAYS = 10                 # symmetric tolerance for precision/recall
TARGET_COV = 0.90
CACHE = ROOT / "outputs" / "cache" / "gap1_hrc_bands.npz"
OUT_FIG = ROOT / "outputs" / "figures" / "hrc_forward_early_warning.png"
OUT_JSON = ROOT / "outputs" / "step5_forward_eval.json"


def load_prices() -> pd.Series:
    import yfinance as yf
    df = yf.download(SYMBOL, start=START, auto_adjust=True, progress=False)
    col = df["Close"]
    s = (col.iloc[:, 0] if hasattr(col, "columns") else col).rename(SYMBOL)
    return s.asfreq("B").ffill().dropna()


def _collapse_onsets(is_shock: pd.Series) -> list:
    onsets, prev = [], False
    for d, flag in is_shock.items():
        if flag and not prev:
            onsets.append(d)
        prev = flag
    return onsets


def shock_episodes(series: pd.Series, cutoff: pd.Timestamp) -> tuple[list, float]:
    """PRIMARY shock onsets: threshold fixed on pre-cutoff data only (no lookahead)."""
    absret = (series / series.shift(SHOCK_RET_WIN) - 1.0).abs()
    thresh = float(np.nanpercentile(absret[absret.index < cutoff], SHOCK_PCTL))
    return _collapse_onsets(absret[absret.index >= cutoff] > thresh), thresh


def shock_episodes_rolling(series: pd.Series, cutoff: pd.Timestamp,
                           window: int = 504) -> list:
    """SENSITIVITY shock onsets: rolling trailing-2y percentile threshold.

    Still lookahead-free (each day's threshold uses only the past ``window`` days),
    but regime-adaptive, so a calm 2024-26 is judged against recent volatility, not
    the 2020-22 chaos. Standard practice in the EWS (signals-approach) literature.
    """
    absret = (series / series.shift(SHOCK_RET_WIN) - 1.0).abs()
    roll_thresh = absret.shift(1).rolling(window).quantile(SHOCK_PCTL / 100.0)
    fwd = absret.index >= cutoff
    return _collapse_onsets((absret > roll_thresh)[fwd])


def main() -> None:
    cfg = yaml.safe_load(open(ROOT / "config" / "steel.yaml"))
    cutoff = pd.Timestamp(FORWARD_FROM)

    print(f"[1/5] Loading {SYMBOL} + events + cached bands ...")
    series = load_prices()
    events = build_event_table(cfg, cache=False)
    z = np.load(CACHE, allow_pickle=False)
    dates = pd.to_datetime(z["dates"])
    order = np.argsort(dates.values)
    dates = dates[order]
    lo, hi, actual = z["lo"][order], z["hi"][order], z["actual"][order]

    print("[2/5] ACI online flags (no lookahead) ...")
    aci = aci_cqr(lo, hi, actual, TARGET_COV, gamma=0.02, warmup=50)
    breach = ~aci["covered"]
    breach[:aci["warmup"]] = False
    flag_dates = [d for d, b in zip(dates, breach) if b and d >= cutoff]
    n_fwd_days = int((dates >= cutoff).sum())
    print(f"      forward window {FORWARD_FROM} -> {dates.max().date()}: "
          f"{len(flag_dates)} flags / {n_fwd_days} days")

    print("[3/5] Shock episodes (threshold fixed pre-forward) ...")
    onsets, thresh = shock_episodes(series, cutoff)
    print(f"      threshold |{SHOCK_RET_WIN}d ret| > {thresh:.1%}; "
          f"{len(onsets)} shock onsets: {[str(o.date()) for o in onsets]}")

    print("[4/5] Early-warning metrics (src/eval/event_eval.py) ...")
    lts = lead_times(flag_dates, onsets, max_gap_days=WARN_WIN)
    precision, recall = precision_recall(flag_dates, onsets, tol_days=TOL_DAYS)
    median_lt = float(np.median(lts)) if lts else float("nan")
    print(f"      PRIMARY  recall {len(lts)}/{len(onsets)}  precision={precision:.1%}  "
          f"median lead={median_lt:.0f}d")

    # sensitivity: regime-adaptive rolling threshold (more onsets, still no lookahead)
    onsets_roll = shock_episodes_rolling(series, cutoff)
    lts_r = lead_times(flag_dates, onsets_roll, max_gap_days=WARN_WIN)
    prec_r, rec_r = precision_recall(flag_dates, onsets_roll, tol_days=TOL_DAYS)
    med_r = float(np.median(lts_r)) if lts_r else float("nan")
    print(f"      SENSITIV rolling-2y threshold: {len(onsets_roll)} onsets, "
          f"recall {len(lts_r)}/{len(onsets_roll)}  precision={prec_r:.1%}  "
          f"median lead={med_r:.0f}d")

    # attribution as candidate-surfacing (the post-gap-1 claim)
    embedder = EventEmbedder()
    surfaced = 0
    onset_candidates = {}
    for o in onsets:
        drivers = attribute_break(o, events, cfg, embedder)
        if drivers:
            surfaced += 1
            onset_candidates[str(o.date())] = {
                "title": drivers[0].title, "published": str(drivers[0].published.date()),
                "source_url": drivers[0].source_url, "score": round(drivers[0].score, 3),
            }
    surf_rate = surfaced / len(onsets) if onsets else float("nan")
    print(f"      candidate-surfacing rate at onsets: {surfaced}/{len(onsets)}")

    print("[5/5] Plotting ...")
    OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
    view = series[series.index >= cutoff - pd.Timedelta(days=30)]
    fig, ax = plt.subplots(figsize=(13, 5.5))
    ax.plot(view.index, view.values, color="#334155", lw=1.1, label="HRC=F")
    fd = pd.to_datetime(flag_dates)
    ax.scatter(fd, series.reindex(fd).values, color="#B00020", s=22, zorder=4,
               label="ACI breach flag (online)")
    for i, o in enumerate(onsets_roll):
        ax.axvline(o, color="#9C6500", lw=0.9, alpha=0.4, ls=":",
                   label="Shock onset (rolling sens.)" if i == 0 else None)
    for i, o in enumerate(onsets):
        ax.axvline(o, color="#9C6500", lw=1.6, alpha=0.9,
                   label="Shock onset (primary)" if i == 0 else None)
    ax.axvline(cutoff, color="#2E5496", ls="--", lw=1.2, label="Forward split")
    ax.set_title(f"{SYMBOL}: forward early-warning eval, ACI flags vs price-shock onsets",
                 fontsize=12, weight="bold")
    ax.set_ylabel("USD / short ton")
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    ax.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(OUT_FIG, dpi=150)
    print(f"      saved {OUT_FIG.relative_to(ROOT)}")

    summary = {
        "symbol": SYMBOL,
        "framing": "forward split, no lookahead: ACI online flags; shock threshold "
                   "fixed on pre-forward data; signals-approach EWS metrics",
        "forward_window": f"{FORWARD_FROM} to {dates.max().date()}",
        "n_forward_days": n_fwd_days,
        "n_flags": len(flag_dates),
        "shock_definition": f"trailing {SHOCK_RET_WIN}d |return| > {round(thresh, 4)} "
                            f"(p{SHOCK_PCTL} pre-forward)",
        "n_shock_onsets": len(onsets),
        "shock_onsets": [str(o.date()) for o in onsets],
        "recall_onsets_warned": f"{len(lts)}/{len(onsets)}",
        "precision_flags_near_onset": round(precision, 4),
        "median_lead_time_days": median_lt,
        "lead_times_days": lts,
        "sensitivity_rolling_2y_threshold": {
            "n_shock_onsets": len(onsets_roll),
            "shock_onsets": [str(o.date()) for o in onsets_roll],
            "recall_onsets_warned": f"{len(lts_r)}/{len(onsets_roll)}",
            "precision_flags_near_onset": round(prec_r, 4),
            "median_lead_time_days": med_r,
            "lead_times_days": lts_r,
        },
        "attribution_candidate_surfacing": f"{surfaced}/{len(onsets)}",
        "onset_candidates": onset_candidates,
        "note": "attribution reported as candidate-surfacing only (per gap-1 negative); "
                "precision counts flags within +/-10d of an onset, so isolated flags in "
                "calm stretches are honest false alarms",
        "figure": str(OUT_FIG.relative_to(ROOT)).replace("\\", "/"),
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2))
    print(f"      saved {OUT_JSON.relative_to(ROOT)}")
    print("\nDONE.")


if __name__ == "__main__":
    main()
