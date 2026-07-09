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


_FR_FIELDS = ["title", "abstract", "publication_date", "agency_names", "html_url", "type"]


def _fr_query(term: str, document_types: list[str], start: str,
              max_events: int) -> list[dict]:
    """Page through the Federal Register API for one search term."""
    out: list[dict] = []
    page = 1
    while len(out) < max_events:
        params = [
            ("conditions[term]", term),
            ("conditions[publication_date][gte]", start),
            ("per_page", 100),
            ("page", page),
            ("order", "newest"),
        ]
        params += [("conditions[type][]", t) for t in document_types]
        params += [("fields[]", f) for f in _FR_FIELDS]
        resp = requests.get(FR_URL, params=params, timeout=60)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            break
        out.extend(results)
        page += 1
    return out[:max_events]


def load_federal_register(terms: list[str], document_types: list[str],
                          start: str = "2016-01-01",
                          max_events: int = 300) -> pd.DataFrame:
    """Query the Federal Register for tariff/trade documents.

    Queries each focused ``term`` (e.g. "Section 232 steel tariff") restricted to
    high-signal ``document_types`` (Presidential documents, rules, notices), then
    de-duplicates by URL. Restricting by document type instead of agency is what keeps
    the canonical Section 232 steel *proclamations* (Presidential documents) in scope.
    Returns a normalized event DataFrame (see ``EVENT_COLUMNS``), origin=``federal_register``.
    """
    docs: list[dict] = []
    for term in terms:
        docs.extend(_fr_query(term, document_types, start, max_events))
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
    df = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    return df.drop_duplicates(subset=["source_url"]).reset_index(drop=True)


def load_news(keywords: list[str], start: str = "2015-01-01") -> pd.DataFrame:
    """Supplemental news events. Stub: wire GDELT / NewsAPI free tier here.

    Kept intentionally thin, news is noisy and only supplements the anchor source.
    """
    # TODO: implement GDELT / NewsAPI pull; return same EVENT_COLUMNS, origin="news".
    return pd.DataFrame(columns=EVENT_COLUMNS)


def build_event_table(cfg: dict, cache: bool = True) -> pd.DataFrame:
    """Assemble the normalized event table from all configured sources."""
    ev = cfg["events"]
    frcfg = ev["federal_register"]
    fr = load_federal_register(
        frcfg["terms"], frcfg["document_types"],
        start=frcfg.get("start", "2016-01-01"),
        max_events=frcfg.get("max_events", 300),
    )
    news = load_news(ev.get("news", {}).get("keywords", []))
    table = pd.concat([fr, news], ignore_index=True)
    # concat with an empty news frame can coerce the datetime column to object; restore it
    table["published"] = pd.to_datetime(table["published"], errors="coerce")
    table = (
        table.dropna(subset=["published"])
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
