"""Post-v2 gap G3: ACI learning-rate (gamma) sensitivity sweep.

gamma=0.02 was adopted from the ACI literature and never checked on our series.
Sweep gamma over {0.005, 0.01, 0.02, 0.05, 0.1} on the two cached band histories
(HRC=F daily-scan bands and SLX retrospective bands). No model calls.

Pre-registered acceptance (design doc sheet 12, fixed before results): KEEP 0.02
unless another gamma clearly dominates |coverage - 90%| on BOTH series ("clearly"
= at least 1 percentage point closer on each). Width is reported as context only.

Run (repo root, in venv):
    ./.venv/Scripts/python.exe scripts/step10_gamma_sweep.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.forecast.conformal import aci_cqr   # noqa: E402

TARGET = 0.90
GAMMAS = [0.005, 0.01, 0.02, 0.05, 0.1]
DEFAULT = 0.02
SOURCES = {
    "HRC=F": ROOT / "outputs" / "cache" / "gap1_hrc_bands.npz",
    "SLX": ROOT / "outputs" / "cache" / "step2_bands.npz",
}
OUT_JSON = ROOT / "outputs" / "step10_gamma_sweep.json"


def load_bands(path: Path):
    z = np.load(path, allow_pickle=False)
    dates = pd.to_datetime(z["dates"])
    order = np.argsort(dates.values)
    return z["lo"][order], z["hi"][order], z["actual"][order]


def main() -> None:
    results: dict[str, dict] = {}
    for name, path in SOURCES.items():
        lo, hi, actual = load_bands(path)
        rows = {}
        print(f"[{name}] {len(actual)} band days")
        for g in GAMMAS:
            aci = aci_cqr(lo, hi, actual, TARGET, gamma=g, warmup=50)
            w = aci["warmup"]
            width = float(((hi + aci["offset"]) - (lo - aci["offset"]))[w:].mean())
            rows[str(g)] = {"coverage": round(aci["realized_coverage"], 4),
                            "abs_dev_pct": round(100 * abs(aci["realized_coverage"] - TARGET), 2),
                            "mean_width": round(width, 2)}
            print(f"    gamma={g:<6} coverage={aci['realized_coverage']:.2%} "
                  f"|dev|={rows[str(g)]['abs_dev_pct']:.2f}pp width={width:.2f}")
        results[name] = rows

    # pre-registered decision: another gamma must beat 0.02 by >= 1pp on BOTH series
    default_dev = {n: results[n][str(DEFAULT)]["abs_dev_pct"] for n in SOURCES}
    challengers = {}
    for g in GAMMAS:
        if g == DEFAULT:
            continue
        if all(results[n][str(g)]["abs_dev_pct"] <= default_dev[n] - 1.0 for n in SOURCES):
            challengers[g] = {n: results[n][str(g)]["abs_dev_pct"] for n in SOURCES}
    keep_default = not challengers
    decision = (f"KEEP gamma={DEFAULT}: no challenger dominates by >=1pp on both series"
                if keep_default else
                f"CHANGE default: {list(challengers)} dominate on both series")
    print(f"\nDECISION: {decision}")

    OUT_JSON.write_text(json.dumps({
        "target_coverage": TARGET, "gammas": GAMMAS, "default": DEFAULT,
        "framing": "pre-registered: keep 0.02 unless another gamma is >=1pp closer to "
                   "target coverage on BOTH cached series; width is context only",
        "results": results,
        "default_abs_dev_pct": default_dev,
        "dominating_challengers": {str(k): v for k, v in challengers.items()},
        "decision": decision,
    }, indent=2))
    print(f"saved {OUT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
