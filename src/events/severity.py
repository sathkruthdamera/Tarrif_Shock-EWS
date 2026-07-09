"""Rule-based severity tagging -> 0..1 severity score.

Transparent, auditable rules (not a black box) grade how impactful an event is likely
to be: explicit tariff percentages, statutory triggers (Section 232/301), immediacy,
and duty actions. Kept simple on purpose so a human can see why a score was assigned.
"""
from __future__ import annotations

import re

import pandas as pd

# (regex, weight) rules. Scores are summed then squashed to 0..1.
RULES: list[tuple[str, float]] = [
    (r"section\s*232", 0.5),
    (r"section\s*301", 0.5),
    (r"antidumping|countervailing", 0.4),
    (r"tariff|duty|duties", 0.3),
    (r"effective immediately|effective at once", 0.4),
    (r"\b(\d{2,3})\s*%", 0.4),          # explicit high tariff percentage
    (r"export (control|restriction|ban)", 0.4),
    (r"quota|suspension|revoke", 0.3),
    # Section 232 steel proclamations carry this exact language but rarely the word
    # "tariff" in the title/abstract; score them so key events are not missed.
    (r"adjusting imports|proclamation|increase the (duty|tariff)", 0.35),
]


def score_text(text: str) -> float:
    """Severity score in [0, 1] for a single event text."""
    t = (text or "").lower()
    raw = sum(w for pat, w in RULES if re.search(pat, t))
    return min(1.0, raw)


def severity(events: pd.DataFrame) -> pd.Series:
    """Vectorized severity scoring over an event table."""
    if events.empty:
        return pd.Series(dtype="float64")
    combined = (events["title"].fillna("") + ". " + events["text"].fillna(""))
    return combined.map(score_text).rename("severity")
