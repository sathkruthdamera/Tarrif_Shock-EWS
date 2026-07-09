"""Event ingestion: Federal Register (anchor) + news (supplement) -> event table.

The Federal Register API is free, structured JSON, timestamped, and queryable by
agency + keyword. It is the authoritative event source; news is only a supplement.
Output is a normalized table: (published, agency, title, text, source_url, origin).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FR_URL = "https://www.federalregister.gov/api/v1/documents.json"

EVENT_COLUMNS = ["published", "agency", "title", "text", "source_url", "origin"]


def load_federal_register(agencies: list[str], keywords: list[str],
                          start: str = "2015-01-01") -> pd.DataFrame:
    """Query the Federal Register for tariff/trade documents.

    Returns a normalized event DataFrame (see ``EVENT_COLUMNS``), origin=``federal_register``.
    """
    term = " OR ".join(keywords)
    params = {
        "conditions[term]": term,
        "conditions[agencies][]": agencies,
        "conditions[publication_date][gte]": start,
        "per_page": 1000,
        "order": "newest",
        "fields[]": ["title", "abstract", "publication_date", "agency_names", "html_url"],
    }
    resp = requests.get(FR_URL, params=params, timeout=60)
    resp.raise_for_status()
    docs = resp.json().get("results", [])
    rows = [
        {
            "published": pd.to_datetime(d.get("publication_date")),
            "agency": ", ".join(d.get("agency_names", []) or []),
            "title": d.get("title", ""),
            "text": d.get("abstract") or d.get("title", ""),
            "source_url": d.get("html_url", ""),
            "origin": "federal_register",
        }
        for d in docs
    ]
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def load_news(keywords: list[str], start: str = "2015-01-01") -> pd.DataFrame:
    """Supplemental news events. Stub: wire GDELT / NewsAPI free tier here.

    Kept intentionally thin, news is noisy and only supplements the anchor source.
    """
    # TODO: implement GDELT / NewsAPI pull; return same EVENT_COLUMNS, origin="news".
    return pd.DataFrame(columns=EVENT_COLUMNS)


def build_event_table(cfg: dict, cache: bool = True) -> pd.DataFrame:
    """Assemble the normalized event table from all configured sources."""
    ev = cfg["events"]
    fr = load_federal_register(
        ev["federal_register"]["agencies"], ev["federal_register"]["keywords"]
    )
    news = load_news(ev.get("news", {}).get("keywords", []))
    table = (
        pd.concat([fr, news], ignore_index=True)
        .dropna(subset=["published"])
        .sort_values("published")
        .reset_index(drop=True)
    )
    if cache:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        table.to_parquet(DATA_DIR / f"events_{cfg['vertical']}.parquet")
    return table


if __name__ == "__main__":  # pragma: no cover
    import yaml

    cfg = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "config" / "steel.yaml"))
    print(build_event_table(cfg).tail())
