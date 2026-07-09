"""Daily Trade Policy Uncertainty (TPU) index: expert-built announcement-dated events.

Caldara, Iacoviello, Molligo, Prestipino & Raffo, "The Economic Effects of Trade
Policy Uncertainty" (JME 2020). The daily TPU index counts major-newspaper articles
discussing trade policy uncertainty, normalized to a long-run mean of 100. Because it
is built from same-day news coverage, spikes are ANNOUNCEMENT-dated, exactly the
event timing the step-4 diagnosis said Federal Register publication dates lack.

Free download from the authors' site (no key, no rate limit); cached to parquet.
Spike-episode detection is shared with the GDELT module (same z-score logic), so the
two sources are interchangeable event feeds; GDELT remains the fallback (it works but
its public API throttles aggressively; see news_gdelt.py).
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
TPU_URL = "https://www.matteoiacoviello.com/tpu_files/tpu_web_latest.xlsx"
UA = {"User-Agent": "tariff-shock-ews/0.1 (research; contact: damerasathkruth@gmail.com)"}


def load_tpu_daily(cache: bool = True, refresh: bool = False) -> pd.Series:
    """Daily TPU index (``TPUD_index``), cached to data/tpu_daily.parquet."""
    cache_path = DATA_DIR / "tpu_daily.parquet"
    if cache and cache_path.exists() and not refresh:
        s = pd.read_parquet(cache_path)["tpu"]
        s.index = pd.to_datetime(s.index)
        return s
    r = requests.get(TPU_URL, timeout=90, headers=UA)
    r.raise_for_status()
    df = pd.read_excel(io.BytesIO(r.content), "TPU_DAILY")
    df["DATE"] = pd.to_datetime(df["DATE"])
    s = df.set_index("DATE")["TPUD_index"].dropna().rename("tpu")
    if cache:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        s.to_frame().to_parquet(cache_path)
    return s
