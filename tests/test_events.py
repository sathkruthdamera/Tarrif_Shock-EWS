"""Severity rules and attribution ranking (post-v2 gap G1).

Attribution is tested with a stub embedder so no sentence-transformers model is
loaded; the ranking math (relevance x severity x recency) is what's under test.
"""
import pandas as pd
import pytest

from src.events.attribute import attribute_break
from src.events.severity import score_text, severity

CFG = {
    "attribution": {
        "lookback_days": 14,
        "weights": {"relevance": 0.5, "severity": 0.3, "recency": 0.2},
        "top_k": 3,
    },
    "events": {"shock_prototypes": ["New tariff imposed on steel imports."]},
}


class StubEmbedder:
    """Deterministic relevance without loading a model: score by keyword."""

    def relevance(self, events: pd.DataFrame, prototypes: list[str]) -> pd.Series:
        scores = events["text"].str.contains("tariff", case=False).astype(float)
        return scores.rename("relevance")


def _events(rows):
    df = pd.DataFrame(rows, columns=["published", "agency", "title", "text",
                                     "source_url", "origin"])
    df["published"] = pd.to_datetime(df["published"])
    return df


# ---------------------------------------------------------------------------
# severity rules
# ---------------------------------------------------------------------------

def test_section_232_scores_high():
    assert score_text("Section 232 duties on steel raised 25%") >= 0.5


def test_adjusting_imports_proclamation_language_is_scored():
    # the exact language of the key proclamations; the rule added after step 2
    assert score_text("Adjusting Imports of Steel Into the United States") >= 0.35


def test_empty_and_irrelevant_text_score_zero():
    assert score_text("") == 0.0
    assert score_text("Routine meeting minutes about office supplies") == 0.0


def test_severity_capped_at_one():
    txt = ("Section 232 Section 301 antidumping countervailing tariff duty 50% "
           "effective immediately export ban quota proclamation")
    assert score_text(txt) == 1.0


def test_severity_vectorized_matches_scalar():
    ev = _events([
        ["2024-01-02", "X", "Section 232 tariff", "Section 232 tariff", "u1", "fr"],
        ["2024-01-03", "X", "minutes", "routine minutes", "u2", "fr"],
    ])
    s = severity(ev)
    assert s.iloc[0] > s.iloc[1] == 0.0


# ---------------------------------------------------------------------------
# attribution ranking
# ---------------------------------------------------------------------------

def test_attribution_ranks_recent_relevant_event_first():
    breach = pd.Timestamp("2024-06-14")
    ev = _events([
        ["2024-06-12", "USTR", "New steel tariff imposed", "New steel tariff imposed 25%", "u_recent", "fr"],
        ["2024-06-03", "USTR", "Old tariff notice", "tariff notice", "u_old", "fr"],
        ["2024-06-13", "X", "Unrelated filing", "office relocation notice", "u_irrelevant", "fr"],
    ])
    drivers = attribute_break(breach, ev, CFG, StubEmbedder())
    assert drivers
    assert drivers[0].source_url == "u_recent"
    urls = [d.source_url for d in drivers]
    # irrelevant event (relevance 0) must not outrank tariff events
    assert urls.index("u_recent") < urls.index("u_irrelevant")


def test_attribution_excludes_events_outside_lookback_and_future():
    breach = pd.Timestamp("2024-06-14")
    ev = _events([
        ["2024-05-01", "X", "too old tariff", "tariff", "u_old", "fr"],
        ["2024-06-20", "X", "future tariff", "tariff", "u_future", "fr"],
    ])
    assert attribute_break(breach, ev, CFG, StubEmbedder()) == []


def test_attribution_handles_object_dtype_published():
    """Regression: concat with an empty frame coerced published to object dtype."""
    breach = pd.Timestamp("2024-06-14")
    ev = _events([["2024-06-12", "X", "steel tariff", "steel tariff", "u1", "fr"]])
    ev["published"] = ev["published"].astype(object)   # simulate the bug condition
    drivers = attribute_break(breach, ev, CFG, StubEmbedder())
    assert drivers and drivers[0].source_url == "u1"


def test_attribution_respects_top_k():
    breach = pd.Timestamp("2024-06-14")
    rows = [[f"2024-06-{d:02d}", "X", f"tariff {d}", "tariff", f"u{d}", "fr"]
            for d in range(2, 14)]
    drivers = attribute_break(breach, _events(rows), CFG, StubEmbedder())
    assert len(drivers) == CFG["attribution"]["top_k"]
