"""Orchestration: forecast -> monitor -> attribute -> alert.

Ties the layers together for one vertical:
  1. ingest price + event data,
  2. produce a calibrated forecast interval (Chronos-2 + conformal),
  3. monitor actuals for interval breaches (with a changepoint cross-check),
  4. attribute each breach to the most likely event,
  5. emit an early-warning alert.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv

from src.data import events as events_mod
from src.data import prices as prices_mod
from src.detect import changepoint
from src.events.attribute import AttributedEvent, attribute_break
from src.events.embed import EventEmbedder
from src.forecast.chronos_model import ChronosForecaster
from src.forecast.conformal import ConformalCalibrator

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Alert:
    breach_date: pd.Timestamp
    actual: float
    lower: float
    upper: float
    changepoint_agrees: bool
    drivers: list[AttributedEvent] = field(default_factory=list)

    def summary(self) -> str:
        top = self.drivers[0] if self.drivers else None
        driver = f"{top.title} ({top.source_url})" if top else "no candidate event found"
        return (
            f"[EWS ALERT] {self.breach_date.date()}: actual={self.actual:.2f} "
            f"outside [{self.lower:.2f}, {self.upper:.2f}] | "
            f"changepoint_agrees={self.changepoint_agrees} | driver: {driver}"
        )


def load_config(path: str | Path) -> dict:
    return yaml.safe_load(open(path))


def run(cfg: dict) -> list[Alert]:
    """Run the end-to-end pipeline for one vertical and return any alerts."""
    panel = prices_mod.build_price_panel(cfg)
    target = panel[cfg["target"]["symbol"]].dropna()
    event_table = events_mod.build_event_table(cfg)

    fcfg, cal = cfg["forecast"], cfg["calibration"]
    forecaster = ChronosForecaster()
    calibrator = ConformalCalibrator(
        forecaster,
        target_coverage=cal["target_coverage"],
        calib_fraction=cal["calib_fraction"],
    )

    history = target.iloc[: -fcfg["horizon_days"]]
    actual = target.iloc[-fcfg["horizon_days"]:]
    interval = calibrator.predict(history, fcfg["horizon_days"], fcfg["quantiles"])

    breaches = interval.breaches(actual)
    residuals = (actual.reindex(interval.index) - interval.to_frame()["median"])
    cps = changepoint.detect(residuals, cfg) if breaches.any() else []

    embedder = EventEmbedder()
    alerts: list[Alert] = []
    frame = interval.to_frame()
    for date, is_breach in breaches.items():
        if not is_breach:
            continue
        drivers = attribute_break(date, event_table, cfg, embedder)
        alerts.append(
            Alert(
                breach_date=date,
                actual=float(actual.reindex(interval.index)[date]),
                lower=float(frame.loc[date, "lower"]),
                upper=float(frame.loc[date, "upper"]),
                changepoint_agrees=changepoint.agrees_with_breach(cps, date),
                drivers=drivers,
            )
        )
    return alerts


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Tariff & Geopolitical Shock EWS")
    ap.add_argument("--config", default=str(ROOT / "config" / "steel.yaml"))
    args = ap.parse_args()

    cfg = load_config(args.config)
    alerts = run(cfg)
    if not alerts:
        print("No interval breaches in the evaluation window.")
    for a in alerts:
        print(a.summary())


if __name__ == "__main__":  # pragma: no cover
    main()
