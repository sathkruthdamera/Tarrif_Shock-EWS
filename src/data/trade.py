"""US Census international-trade loader (steel HS codes) -> monthly parquet.

Uses the free Census International Trade API to pull monthly import/export volume
and value by HS code. Monthly cadence; forward-filled to daily when joined.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
CENSUS_URL = "https://api.census.gov/data/timeseries/intltrade/imports/hs"


def load_trade(hs_codes: list[str], start_year: int = 2015) -> pd.DataFrame:
    """Pull monthly steel import value/quantity by HS code.

    Requires ``CENSUS_API_KEY`` (free). Returns a monthly-indexed DataFrame with one
    column per HS code (general customs value).
    """
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        raise RuntimeError("CENSUS_API_KEY not set (see .env.example)")

    frames = []
    for hs in hs_codes:
        params = {
            "get": "GEN_VAL_MO,I_COMMODITY",
            "I_COMMODITY": hs,
            "time": f"from {start_year}-01",
            "key": key,
        }
        resp = requests.get(CENSUS_URL, params=params, timeout=60)
        resp.raise_for_status()
        rows = resp.json()
        df = pd.DataFrame(rows[1:], columns=rows[0])
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")["GEN_VAL_MO"].astype(float).rename(f"import_val_{hs}")
        frames.append(df)
    return pd.concat(frames, axis=1).sort_index()


def build_trade_panel(cfg: dict, cache: bool = True) -> pd.DataFrame:
    """Load configured HS codes and optionally cache to parquet."""
    df = load_trade(cfg["trade"]["hs_codes"])
    if cache:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(DATA_DIR / f"trade_{cfg['vertical']}.parquet")
    return df


if __name__ == "__main__":  # pragma: no cover
    import yaml

    cfg = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "config" / "steel.yaml"))
    print(build_trade_panel(cfg).tail())
