"""Gap A: attribution retest with ANNOUNCEMENT-dated events (daily TPU index).

Step 4 found no significant breach<->event association using Federal Register
PUBLICATION dates and diagnosed why: publication lags the market-moving announcement.
This script swaps in announcement-dated events and re-runs the identical
pre-registered design so the two results are directly comparable.

Event source: the daily Trade Policy Uncertainty index (Caldara-Iacoviello et al.,
JME 2020), an expert-built newspaper-count index whose spikes are announcement-dated
by construction (validated: 2018-03-01 z-spike ~531 vs median 74; 2025-02-10 ~538;
2025-06-02 ~1179). GDELT volume spikes were implemented first (src/data/news_gdelt.py)
but the public GDELT API hard-throttles multi-year pulls, so TPU is primary and GDELT
remains the documented fallback.

Design (fixed before seeing any result, same as step 4):
  - flags: ACI online breaches on HRC=F (cached bands, identical to step 4),
  - events: TPU spike-episode onsets, z>=3 vs trailing 365d (shifted, no lookahead),
  - PRIMARY: event within 5 calendar days before the breach,
  - sensitivity: 3/10/14d windows, plus z>=2 events,
  - arbiter: circular-shift permutation test, B=2000, seed 12345.

Run (repo root, in venv):
    ./.venv/Scripts/python.exe scripts/step6_announcement_attribution.py
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

from src.data.news_gdelt import announcement_events   # noqa: E402  (source-agnostic)
from src.data.news_tpu import load_tpu_daily          # noqa: E402
from src.forecast.conformal import aci_cqr            # noqa: E402

SYMBOL = "HRC=F"
TARGET_COV = 0.90
Z_PRIMARY = 3.0
PRIMARY_WINDOW = 5
SENSITIVITY_WINDOWS = [3, 10, 14]
N_PERM = 2000
CACHE = ROOT / "outputs" / "cache" / "gap1_hrc_bands.npz"
OUT_FIG = ROOT / "outputs" / "figures" / "hrc_breaches_tpu_announcements.png"
OUT_JSON = ROOT / "outputs" / "step6_announcement_summary.json"


def perm_test(breach_mask: np.ndarray, has_event: np.ndarray,
              rng: np.random.Generator) -> dict:
    obs = float(has_event[breach_mask].mean())
    n = len(breach_mask)
    null = np.empty(N_PERM)
    for b in range(N_PERM):
        null[b] = has_event[np.roll(breach_mask, int(rng.integers(1, n)))].mean()
    return {"observed": obs, "base": float(has_event.mean()),
            "null_mean": float(null.mean()), "p_value": float((null >= obs).mean())}


def main() -> None:
    print("[1/4] TPU announcement events + cached HRC bands ...")
    tpu = load_tpu_daily()
    ev = announcement_events(tpu, z_min=Z_PRIMARY)
    ev_dates = pd.to_datetime(ev["published"]).sort_values()
    print(f"      {len(ev)} announcement events (z>={Z_PRIMARY}) "
          f"from {tpu.index.min().date()}..{tpu.index.max().date()}")

    z = np.load(CACHE, allow_pickle=False)
    dates = pd.to_datetime(z["dates"])
    order = np.argsort(dates.values)
    dates, lo, hi, actual = dates[order], z["lo"][order], z["hi"][order], z["actual"][order]

    print("[2/4] ACI online flags (identical to step 4) ...")
    aci = aci_cqr(lo, hi, actual, TARGET_COV, gamma=0.02, warmup=50)
    breach = ~aci["covered"]
    breach[:aci["warmup"]] = False
    print(f"      coverage {aci['realized_coverage']:.1%}, "
          f"{int(breach.sum())} breach-days / {len(dates) - aci['warmup']}")

    print("[3/4] Permutation tests ...")
    rng = np.random.default_rng(12345)
    d_index = pd.to_datetime(dates)

    def flags_for(evd: pd.Series, window: int) -> np.ndarray:
        return np.array([bool(len(evd[(evd <= d) &
                        (evd >= d - pd.Timedelta(days=window))])) for d in d_index])

    primary = perm_test(breach, flags_for(ev_dates, PRIMARY_WINDOW), rng)
    print(f"      PRIMARY  z>={Z_PRIMARY}, {PRIMARY_WINDOW}d: "
          f"hit={primary['observed']:.1%} base={primary['base']:.1%} "
          f"p={primary['p_value']:.4f}")

    sensitivity = {}
    for w in SENSITIVITY_WINDOWS:
        r = perm_test(breach, flags_for(ev_dates, w), rng)
        sensitivity[f"z3_{w}d"] = {k: round(v, 4) for k, v in r.items()}
        print(f"      sens     z>={Z_PRIMARY}, {w:2d}d: hit={r['observed']:.1%} "
              f"base={r['base']:.1%} p={r['p_value']:.4f}")
    ev2 = pd.to_datetime(announcement_events(tpu, z_min=2.0)["published"]).sort_values()
    r2 = perm_test(breach, flags_for(ev2, PRIMARY_WINDOW), rng)
    sensitivity[f"z2_{PRIMARY_WINDOW}d"] = {k: round(v, 4) for k, v in r2.items()}
    print(f"      sens     z>=2.0, {PRIMARY_WINDOW}d ({len(ev2)} events): "
          f"hit={r2['observed']:.1%} base={r2['base']:.1%} p={r2['p_value']:.4f}")

    print("[4/4] Plotting ...")
    OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 1, figsize=(13, 7.5), sharex=True,
                             gridspec_kw={"height_ratios": [2, 1]})
    ax = axes[0]
    ax.plot(d_index, actual, color="#334155", lw=1.0, label="HRC=F (scanned days)")
    has5 = flags_for(ev_dates, PRIMARY_WINDOW)
    bm, be = breach & ~has5, breach & has5
    ax.scatter(d_index[bm], actual[bm], color="#94A3B8", s=14,
               label="Breach (no announcement in 5d)")
    ax.scatter(d_index[be], actual[be], color="#B00020", s=28,
               label="Breach (announcement in 5d)")
    ax.set_ylabel("USD / short ton"); ax.legend(loc="upper left", fontsize=8)
    ax.set_title("HRC breaches vs TPU announcement-dated trade-policy events",
                 fontsize=12, weight="bold")
    ax.grid(alpha=0.25)
    ax2 = axes[1]
    view = tpu[tpu.index >= d_index.min()]
    ax2.plot(view.index, view.values, color="#2E5496", lw=0.7, label="Daily TPU index")
    for d in ev_dates[ev_dates >= d_index.min()]:
        ax2.axvline(d, color="#3F7D3A", alpha=0.5, lw=1.0)
    ax2.set_ylabel("TPU index"); ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(OUT_FIG, dpi=150)
    print(f"      saved {OUT_FIG.relative_to(ROOT)}")

    p = primary["p_value"]
    summary = {
        "symbol": SYMBOL,
        "event_source": "Daily TPU index (Caldara-Iacoviello et al., JME 2020) "
                        "spike onsets; announcement-dated by construction",
        "framing": "identical pre-registered design as step 4 (5d primary window, "
                   "same ACI flags, same arbiter, seed 12345); only the event dating "
                   "changed from FR publication to news announcement",
        "n_announcement_events_z3": int(len(ev)),
        "aci_realized_coverage": round(aci["realized_coverage"], 4),
        "n_breach_days": int(breach.sum()),
        "primary_test": {k: round(v, 4) for k, v in primary.items()},
        "primary_significant_at_0.05": bool(p <= 0.05),
        "step4_fr_dated_comparison": {"primary_p": 0.4605, "note": "FR publication-dated"},
        "sensitivity": sensitivity,
        "gdelt_status": "implemented (src/data/news_gdelt.py) but public API "
                        "hard-throttles multi-year pulls; retained as fallback",
        "figure": str(OUT_FIG.relative_to(ROOT)).replace("\\", "/"),
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2))
    print(f"      saved {OUT_JSON.relative_to(ROOT)}")
    print(f"\nPRIMARY VERDICT: {'SIGNIFICANT' if p <= 0.05 else 'NOT significant'} "
          f"(p={p:.4f}) vs step-4 FR-dated p=0.4605")
    print("DONE.")


if __name__ == "__main__":
    main()
