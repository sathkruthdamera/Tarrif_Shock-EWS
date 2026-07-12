"""Orchestration: forecast -> monitor -> attribute -> alert (production path).

Daily-batch pipeline for one vertical, using the components proven in the
validation scripts (steps 1-5):

  1. ingest price + event data,
  2. maintain a cached history of TimesFM quantile bands over trailing
     non-overlapping H-day blocks (only new blocks are recomputed each run),
  3. calibrate ONLINE with Adaptive Conformal Inference (ACI, Gibbs & Candes
     2021), which holds target coverage through volatility regimes where a
     fixed split-conformal offset drifts (proven in scripts/step3),
  4. flag breaches in the most recent block; cross-check each against a
     GARCH(1,1) volatility band and volatility-regime changepoints,
  5. attribute each breach to candidate events (verifiable candidate-surfacing,
     NOT a causal claim; see the step-4 permutation result) and emit alerts.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from dotenv import load_dotenv

from src.data import events as events_mod
from src.data import prices as prices_mod
from src.detect import changepoint
from src.events.attribute import AttributedEvent, attribute_break
from src.events.embed import EventEmbedder
from src.eval.backtest import garch_band
from src.forecast.conformal import aci_cqr
from src.forecast.timesfm_model import TimesFMForecaster

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


@dataclass
class Alert:
    breach_date: pd.Timestamp
    actual: float
    lower: float
    upper: float
    direction: str
    changepoint_agrees: bool
    garch_agrees: bool
    aci_coverage: float
    breach_run: int = 1
    drivers: list[AttributedEvent] = field(default_factory=list)

    def summary(self) -> str:
        top = self.drivers[0] if self.drivers else None
        driver = (f"{top.title} ({top.published.date()}, {top.source_url})"
                  if top else "no candidate event in window")
        return (
            f"[EWS ALERT] {self.breach_date.date()}: actual={self.actual:.2f} "
            f"{self.direction} outside [{self.lower:.2f}, {self.upper:.2f}] | "
            f"changepoint_agrees={self.changepoint_agrees} "
            f"garch_agrees={self.garch_agrees} | "
            f"ACI coverage={self.aci_coverage:.1%} | candidate driver: {driver}"
        )


def load_config(path: str | Path) -> dict:
    return yaml.safe_load(open(path))


def _band_history(series: pd.Series, cfg: dict,
                  forecaster: TimesFMForecaster) -> pd.DataFrame:
    """Cached per-day quantile bands over trailing non-overlapping H-day blocks.

    Cache lives at data/bands_<vertical>.parquet; each run only computes blocks
    whose dates are not yet cached, so the daily batch costs one forecast.
    """
    fcfg = cfg["forecast"]
    horizon = fcfg["horizon_days"]
    n_blocks = cfg["calibration"].get("history_blocks", 60)
    quantiles = fcfg["quantiles"]

    cache_path = DATA_DIR / f"bands_{cfg['vertical']}.parquet"
    cached = pd.read_parquet(cache_path) if cache_path.exists() else pd.DataFrame()
    have = set(pd.to_datetime(cached["date"])) if len(cached) else set()

    last_origin = len(series) - horizon
    origins = [last_origin - k * horizon for k in range(n_blocks)][::-1]
    origins = [t for t in origins if t > horizon]

    rows = []
    for t in origins:
        block = series.iloc[t:t + horizon]
        if all(d in have for d in block.index):
            continue
        fc = forecaster.forecast(series.iloc[:t], horizon, quantiles)
        for j, (d, y) in enumerate(block.items()):
            rows.append({"date": d, "lo": float(fc.q(quantiles[0]).iloc[j]),
                         "median": float(fc.q(0.5).iloc[j]),
                         "hi": float(fc.q(quantiles[-1]).iloc[j]),
                         "actual": float(y)})
    if rows:
        cached = (pd.concat([cached, pd.DataFrame(rows)], ignore_index=True)
                  .drop_duplicates(subset=["date"], keep="last"))
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        cached.to_parquet(cache_path)
    cached["date"] = pd.to_datetime(cached["date"])
    keep_from = series.index[origins[0]]
    return cached[cached["date"] >= keep_from].sort_values("date").reset_index(drop=True)


def run(cfg: dict) -> list[Alert]:
    """Run the end-to-end production pipeline for one vertical; return alerts."""
    cal, fcfg, det = cfg["calibration"], cfg["forecast"], cfg["detection"]
    horizon = fcfg["horizon_days"]

    panel = prices_mod.build_price_panel(cfg)
    target = panel[cfg["target"]["symbol"]].dropna()
    event_table = events_mod.build_event_table(cfg)

    forecaster = TimesFMForecaster(max_context=fcfg.get("context_days", 1024),
                                   max_horizon=max(64, horizon))
    bands = _band_history(target, cfg, forecaster)

    # ACI online over the whole cached history (no lookahead by construction)
    aci = aci_cqr(bands["lo"].to_numpy(), bands["hi"].to_numpy(),
                  bands["actual"].to_numpy(), cal["target_coverage"],
                  gamma=cal.get("gamma", 0.02), warmup=cal.get("warmup_days", 50))
    covered, offsets = aci["covered"], aci["offset"]

    # alert only on the most recent block, and never inside the warmup
    recent = bands.index[-horizon:]
    cps = changepoint.volatility_changepoints(
        target, window=det.get("vol_window", 20), penalty=det.get("penalty", 8.0))
    g_lo, g_hi = (garch_band(target.iloc[:-horizon], horizon, cal["target_coverage"])
                  if cal.get("garch_cross_check", True) else (None, None))

    # alert filters, forward-evaluated in scripts/step7_alert_precision.py:
    # F1 (changepoint agreement) cut noise-to-signal 10x with recall preserved -> default ON;
    # F2 (min_breach_run>=2) dropped the only true positive (gap-style shock) -> default OFF.
    filt = cfg.get("alert_filters", {})
    require_cp = filt.get("require_changepoint", False)
    cp_tol = filt.get("changepoint_tol_days", 7)
    min_run = filt.get("min_breach_run", 1)

    embedder = EventEmbedder()
    alerts: list[Alert] = []
    for j, i in enumerate(recent):
        if i < aci["warmup"] or covered[i]:
            continue
        d = bands.loc[i, "date"]
        y = bands.loc[i, "actual"]
        lo_i = bands.loc[i, "lo"] - offsets[i]
        hi_i = bands.loc[i, "hi"] + offsets[i]
        run = 1
        while i - run >= 0 and not covered[i - run]:
            run += 1
        cp_agrees = changepoint.agrees_with_breach(cps, d, tol_days=cp_tol)
        if (require_cp and not cp_agrees) or run < min_run:
            continue
        garch_agrees = bool(g_lo is not None and (y < g_lo[j] or y > g_hi[j]))
        alerts.append(Alert(
            breach_date=d, actual=float(y), lower=float(lo_i), upper=float(hi_i),
            direction="down" if y < lo_i else "up",
            changepoint_agrees=cp_agrees,
            garch_agrees=garch_agrees,
            aci_coverage=aci["realized_coverage"],
            breach_run=run,
            drivers=attribute_break(d, event_table, cfg, embedder),
        ))
    return alerts


def deliver(alerts: list[Alert], cfg: dict, as_of: pd.Timestamp) -> Path:
    """v2-W3: write a machine-readable alert artifact; optionally POST to a webhook.

    Artifact: outputs/alerts/<vertical>_<YYYYMMDD>.json (written even when there are
    zero alerts, so downstream consumers can distinguish 'ran clean' from 'did not
    run'). Webhook: cfg['alerting']['webhook_url'], inert unless set.
    """
    import json

    payload = {
        "vertical": cfg["vertical"],
        "as_of": str(as_of.date()),
        "target": cfg["target"]["symbol"],
        "n_alerts": len(alerts),
        "alerts": [
            {
                "breach_date": str(a.breach_date.date()),
                "actual": round(a.actual, 4),
                "interval": [round(a.lower, 4), round(a.upper, 4)],
                "direction": a.direction,
                "breach_run_days": a.breach_run,
                "changepoint_agrees": a.changepoint_agrees,
                "garch_agrees": a.garch_agrees,
                "aci_realized_coverage": round(a.aci_coverage, 4),
                "candidate_drivers": [
                    {"title": d.title, "published": str(d.published.date()),
                     "source_url": d.source_url, "score": round(d.score, 3)}
                    for d in a.drivers
                ],
                "note": "candidate-surfacing, not a causal claim",
            }
            for a in alerts
        ],
    }
    out_dir = ROOT / "outputs" / "alerts"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{cfg['vertical']}_{as_of.strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(payload, indent=2))

    url = (cfg.get("alerting") or {}).get("webhook_url") or ""
    if url:
        import requests

        try:
            requests.post(url, json=payload, timeout=30).raise_for_status()
            print(f"webhook delivered to {url}")
        except Exception as exc:  # delivery must not kill the batch
            print(f"webhook delivery FAILED (artifact still written): {exc}")
    return path


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Tariff & Geopolitical Shock EWS")
    ap.add_argument("--config", default=str(ROOT / "config" / "steel.yaml"))
    args = ap.parse_args()

    cfg = load_config(args.config)
    alerts = run(cfg)
    if not alerts:
        print("No interval breaches in the most recent block (post-warmup).")
    for a in alerts:
        print(a.summary())
    artifact = deliver(alerts, cfg, as_of=pd.Timestamp.now())
    print(f"alert artifact: {artifact.relative_to(ROOT)}")


if __name__ == "__main__":  # pragma: no cover
    main()
