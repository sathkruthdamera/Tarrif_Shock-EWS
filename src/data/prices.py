"""Price & macro loaders (yfinance / FRED) -> normalized daily parquet.

Pulls the target series (SLX proxy for HRC) plus FRED macro context, caches raw
pulls to ``data/`` as parquet, and returns a tidy daily DataFrame. Every record is
stamped with a pull date for reproducibility.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def load_target(symbol: str, field: str = "Close", start: str = "2015-01-01") -> pd.Series:
    """Load the target price series via yfinance.

    Parameters
    ----------
    symbol: yfinance ticker (e.g. ``SLX``).
    field:  OHLCV field to keep (default ``Close``).
    start:  ISO start date.

    Returns
    -------
    A business-day indexed ``pd.Series`` named ``symbol``.
    """
    import yfinance as yf

    df = yf.download(symbol, start=start, auto_adjust=True, progress=False)
    if df.empty:
        raise RuntimeError(f"No data returned for {symbol!r}")
    s = df[field].rename(symbol)
    s.index = pd.to_datetime(s.index)
    return s.asfreq("B").ffill()


def load_fred(series_ids: dict[str, str], start: str = "2015-01-01") -> pd.DataFrame:
    """Load FRED macro series. ``series_ids`` maps friendly name -> FRED id.

    Requires ``FRED_API_KEY`` in the environment (free key).
    """
    from fredapi import Fred

    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY not set (see .env.example)")
    fred = Fred(api_key=key)
    cols = {name: fred.get_series(sid, observation_start=start) for name, sid in series_ids.items()}
    return pd.DataFrame(cols).asfreq("B").ffill()


def build_price_panel(cfg: dict, cache: bool = True) -> pd.DataFrame:
    """Assemble target + macro into one daily panel and optionally cache to parquet."""
    target = load_target(cfg["target"]["symbol"], cfg["target"].get("field", "Close"))
    macro = load_fred(cfg.get("macro", {}))
    panel = pd.concat([target, macro], axis=1).ffill().dropna(how="all")
    if cache:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        panel.to_parquet(DATA_DIR / f"prices_{cfg['vertical']}.parquet")
    return panel


if __name__ == "__main__":  # pragma: no cover
    import yaml

    cfg = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "config" / "steel.yaml"))
    print(build_price_panel(cfg).tail())
