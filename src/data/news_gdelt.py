"""GDELT announcement-dated news events (the v2 news layer, now real).

Why: the step-4 permutation test showed Federal Register PUBLICATION dates lag the
market-moving ANNOUNCEMENT (often by 1-2 weeks), so FR-dated co-occurrence understates
any true association. The expert-standard fix is a news-intensity index: the EPU index
(Baker-Bloom-Davis, QJE 2016) and the GPR index both count topical news articles per
day, normalized by total news volume; announcement days appear as volume spikes.

This module builds exactly that from the free GDELT DOC 2.0 API:
  - ``mode=TimelineVol`` returns daily topical coverage as a PERCENT of all global
    coverage (EPU-style normalization for free),
  - spike days (z-score vs a trailing window, no lookahead) become announcement-dated
    events, collapsed into episodes whose onset is the event date.

API etiquette: GDELT allows ~1 request / 5 s, so pulls are chunked by year with 6 s
pacing, 429-aware retries, and a parquet cache so the network cost is paid once.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
PACING_S = 20.0
MAX_RETRIES = 5
UA = "tariff-shock-ews/0.1 (research; single-user; contact: damerasathkruth@gmail.com)"


def _slug(query: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")[:60]


def _fetch_year(query: str, year: int, session: requests.Session) -> pd.Series:
    """One TimelineVol call for one calendar year; retries on 429/timeouts."""
    params = {
        "query": query,
        "mode": "TimelineVol",
        "format": "json",
        "STARTDATETIME": f"{year}0101000000",
        "ENDDATETIME": f"{year}1231235959",
    }
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(GDELT_URL, params=params, timeout=90)
            if r.status_code == 429:
                time.sleep(60 * (attempt + 1))   # GDELT block penalties last minutes
                continue
            r.raise_for_status()
            if "json" not in (r.headers.get("content-type") or ""):
                raise RuntimeError(f"GDELT non-JSON response: {r.text[:120]}")
            data = r.json().get("timeline", [{}])[0].get("data", [])
            return pd.Series({pd.to_datetime(d["date"]).tz_localize(None): d["value"]
                              for d in data}, dtype=float)
        except (requests.Timeout, requests.ConnectionError):
            time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"GDELT fetch failed for {year} after {MAX_RETRIES} attempts")


def load_gdelt_volume(query: str, start_year: int = 2017, end_year: int | None = None,
                      cache: bool = True) -> pd.Series:
    """Daily topical news volume (% of all global coverage) for ``query``.

    Chunked by year with polite pacing; cached to data/gdelt_<slug>.parquet.
    """
    end_year = end_year or pd.Timestamp.now().year
    cache_path = DATA_DIR / f"gdelt_{_slug(query)}.parquet"
    cached = (pd.read_parquet(cache_path)["volume"]
              if cache and cache_path.exists() else pd.Series(dtype=float))
    if len(cached):
        cached.index = pd.to_datetime(cached.index)

    have_years = set(cached.index.year) if len(cached) else set()
    # always refetch the current (incomplete) year
    need = [y for y in range(start_year, end_year + 1)
            if y not in have_years or y == end_year]
    session = requests.Session()
    session.headers["User-Agent"] = UA
    vol = cached[~cached.index.year.isin(need)] if len(cached) else pd.Series(dtype=float)
    for i, y in enumerate(need):
        if i:
            time.sleep(PACING_S)
        part = _fetch_year(query, y, session)
        vol = pd.concat([vol, part]).sort_index()
        vol = vol[~vol.index.duplicated(keep="last")].rename("volume")
        if cache:  # persist after EVERY year so throttling never loses progress
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            vol.to_frame().to_parquet(cache_path)
        print(f"      gdelt {query!r}: fetched {y}", flush=True)
    return vol.rename("volume")


def announcement_events(volume: pd.Series, z_min: float = 3.0,
                        trailing_days: int = 365) -> pd.DataFrame:
    """Announcement-dated events = onsets of news-volume spike episodes.

    A day is a spike when its volume exceeds trailing mean + ``z_min`` trailing std
    (both shifted one day, so no lookahead). Consecutive spike days collapse into one
    episode; the first day is the announcement date. Returns a DataFrame with
    ``published`` (episode onset), ``z`` (peak z in episode) and ``days`` (length).
    """
    mu = volume.shift(1).rolling(trailing_days, min_periods=60).mean()
    sd = volume.shift(1).rolling(trailing_days, min_periods=60).std()
    z = (volume - mu) / sd
    is_spike = z >= z_min
    events, cur = [], None
    for d, flag in is_spike.items():
        if flag and cur is None:
            cur = {"published": d, "z": float(z[d]), "days": 1}
        elif flag:
            cur["z"] = max(cur["z"], float(z[d])); cur["days"] += 1
        elif cur is not None:
            events.append(cur); cur = None
    if cur is not None:
        events.append(cur)
    return pd.DataFrame(events, columns=["published", "z", "days"])
