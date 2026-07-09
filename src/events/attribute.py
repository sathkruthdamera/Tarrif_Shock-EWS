"""Attribution: rank candidate events for a detected break.

When an actual value breaches the calibrated interval, rank recent events by
``relevance x severity x recency`` and return the top-k as the likely drivers, each
with its verifiable source link. This is an explainable ranking heuristic, not a causal
identification guarantee, the source link is always surfaced for human verification.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .embed import EventEmbedder
from .severity import severity


@dataclass
class AttributedEvent:
    published: pd.Timestamp
    title: str
    source_url: str
    relevance: float
    severity: float
    recency: float
    score: float


def _recency_weight(days_before: np.ndarray, lookback: int) -> np.ndarray:
    """Linear decay: an event on the breach day = 1.0, at the lookback edge = 0."""
    return np.clip(1.0 - days_before / max(lookback, 1), 0.0, 1.0)


def attribute_break(
    breach_date: pd.Timestamp,
    events: pd.DataFrame,
    cfg: dict,
    embedder: EventEmbedder | None = None,
) -> list[AttributedEvent]:
    """Return the top-k events most likely to explain a break on ``breach_date``."""
    acfg = cfg["attribution"]
    lookback = acfg["lookback_days"]
    w = acfg["weights"]
    top_k = acfg.get("top_k", 3)

    published = pd.to_datetime(events["published"], errors="coerce")
    window = events[
        (published <= breach_date)
        & (published >= breach_date - pd.Timedelta(days=lookback))
    ].copy()
    if window.empty:
        return []
    window["published"] = pd.to_datetime(window["published"], errors="coerce")

    embedder = embedder or EventEmbedder()
    prototypes = cfg["events"]["shock_prototypes"]
    rel = embedder.relevance(window, prototypes)
    sev = severity(window)
    days_before = (breach_date - window["published"]).dt.days.to_numpy()
    rec = pd.Series(_recency_weight(days_before, lookback), index=window.index)

    score = w["relevance"] * rel + w["severity"] * sev + w["recency"] * rec
    window = window.assign(relevance=rel, severity=sev, recency=rec, score=score)
    window = window.sort_values("score", ascending=False).head(top_k)

    return [
        AttributedEvent(
            published=r.published,
            title=r.title,
            source_url=r.source_url,
            relevance=float(r.relevance),
            severity=float(r.severity),
            recency=float(r.recency),
            score=float(r.score),
        )
        for r in window.itertuples()
    ]
