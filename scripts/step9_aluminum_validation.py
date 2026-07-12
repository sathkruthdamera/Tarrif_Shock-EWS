"""Post-v2 gap G2: validate aluminum (ALI=F) calibration.

W2 proved the aluminum vertical RUNS; this validates that ACI actually delivers
the ~90% coverage the alerts assume, using the band history the pipeline cached
(data/bands_aluminum.parquet, trailing non-overlapping blocks). No model calls.

Run (repo root, in venv):
    ./.venv/Scripts/python.exe scripts/step9_aluminum_validation.py
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

TARGET_COV = 0.90
BANDS = ROOT / "data" / "bands_aluminum.parquet"
OUT_JSON = ROOT / "outputs" / "step9_aluminum_validation.json"


def main() -> None:
    bands = pd.read_parquet(BANDS).sort_values("date").reset_index(drop=True)
    print(f"[1/2] {len(bands)} cached band days "
          f"({pd.to_datetime(bands['date']).min().date()} -> "
          f"{pd.to_datetime(bands['date']).max().date()})")

    aci = aci_cqr(bands["lo"].to_numpy(), bands["hi"].to_numpy(),
                  bands["actual"].to_numpy(), TARGET_COV, gamma=0.02, warmup=50)
    w = aci["warmup"]
    n_eval = len(bands) - w
    breaches = int((~aci["covered"][w:]).sum())
    width = ((bands["hi"] + aci["offset"]) - (bands["lo"] - aci["offset"]))[w:]
    print(f"[2/2] ACI realized coverage: {aci['realized_coverage']:.1%} "
          f"(target {TARGET_COV:.0%}) | breaches {breaches}/{n_eval} | "
          f"mean calibrated width {width.mean():.1f}")

    verdict = "calibrated" if abs(aci["realized_coverage"] - TARGET_COV) <= 0.04 \
        else "NOT calibrated - do not trust aluminum alerts until investigated"
    summary = {
        "vertical": "aluminum", "target": "ALI=F",
        "band_days": int(len(bands)), "eval_days_post_warmup": int(n_eval),
        "target_coverage": TARGET_COV,
        "aci_realized_coverage": round(aci["realized_coverage"], 4),
        "breach_days": breaches,
        "mean_calibrated_width": round(float(width.mean()), 2),
        "verdict": verdict,
        "note": "validated on the pipeline's own cached band history; small sample "
                "(~60 blocks), monitored further via aci_realized_coverage on every alert",
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2))
    print(f"      saved {OUT_JSON.relative_to(ROOT)}")
    print(f"\nVERDICT: {verdict}")


if __name__ == "__main__":
    main()
