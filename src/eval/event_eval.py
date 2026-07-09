"""Event early-warning evaluation: lead-time, precision/recall, attribution accuracy.

The "money metric". Against a labeled set of real tariff events (from the Federal
Register), measure whether the system flagged elevated risk before the price moved
(lead time), how precisely it flagged genuine shocks, and whether the top-ranked
event matched the ground-truth cause (top-1 / top-3 attribution accuracy).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class EventEvalResult:
    median_lead_time_days: float
    precision: float
    recall: float
    attribution_top1: float
    attribution_top3: float
    n_events: int


def lead_times(flag_dates: list[pd.Timestamp],
               shock_dates: list[pd.Timestamp],
               max_gap_days: int = 10) -> list[int]:
    """For each labeled shock, days between the earliest matching flag and the shock.

    Positive = early warning. Flags after the shock or beyond ``max_gap_days`` don't count.
    """
    out: list[int] = []
    for shock in shock_dates:
        prior = [(shock - f).days for f in flag_dates if 0 <= (shock - f).days <= max_gap_days]
        if prior:
            out.append(max(prior))
    return out


def precision_recall(flag_dates: list[pd.Timestamp],
                     shock_dates: list[pd.Timestamp],
                     tol_days: int = 5) -> tuple[float, float]:
    """Precision/recall of flags vs labeled shocks within a tolerance window."""
    def matched(a, b):
        return any(abs((x - y).days) <= tol_days for y in b for x in [a])

    tp_flags = [f for f in flag_dates if matched(f, shock_dates)]
    hit_shocks = [s for s in shock_dates if matched(s, flag_dates)]
    precision = len(tp_flags) / len(flag_dates) if flag_dates else float("nan")
    recall = len(hit_shocks) / len(shock_dates) if shock_dates else float("nan")
    return precision, recall


def attribution_accuracy(predicted_top_k: dict[pd.Timestamp, list[str]],
                         truth: dict[pd.Timestamp, str]) -> tuple[float, float]:
    """Top-1 and top-3 accuracy of attributed source URLs vs ground truth."""
    if not truth:
        return float("nan"), float("nan")
    top1 = top3 = 0
    for date, true_url in truth.items():
        preds = predicted_top_k.get(date, [])
        top1 += int(bool(preds) and preds[0] == true_url)
        top3 += int(true_url in preds[:3])
    n = len(truth)
    return top1 / n, top3 / n


def evaluate(flag_dates, shock_dates, predicted_top_k, truth) -> EventEvalResult:
    lt = lead_times(flag_dates, shock_dates)
    prec, rec = precision_recall(flag_dates, shock_dates)
    a1, a3 = attribution_accuracy(predicted_top_k, truth)
    return EventEvalResult(
        median_lead_time_days=float(np.median(lt)) if lt else float("nan"),
        precision=prec,
        recall=rec,
        attribution_top1=a1,
        attribution_top3=a3,
        n_events=len(shock_dates),
    )
