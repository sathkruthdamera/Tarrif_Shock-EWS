"""Early-warning metrics and the alert artifact schema (post-v2 gap G1)."""
import json

import numpy as np
import pandas as pd

from src.eval.event_eval import attribution_accuracy, lead_times, precision_recall
from src.pipeline import Alert, deliver


# ---------------------------------------------------------------------------
# event_eval metrics
# ---------------------------------------------------------------------------

def _d(s):
    return pd.Timestamp(s)


def test_lead_times_measure_earliest_warning():
    flags = [_d("2024-03-01"), _d("2024-03-05")]
    shocks = [_d("2024-03-07")]
    # earliest qualifying flag is 6 days before the shock
    assert lead_times(flags, shocks, max_gap_days=10) == [6]


def test_lead_times_ignore_flags_after_shock_or_too_early():
    flags = [_d("2024-02-01"), _d("2024-03-10")]      # too early / after
    shocks = [_d("2024-03-07")]
    assert lead_times(flags, shocks, max_gap_days=10) == []


def test_precision_recall_tolerance_window():
    flags = [_d("2024-03-05"), _d("2024-06-01")]      # one hit, one false alarm
    shocks = [_d("2024-03-07")]
    precision, recall = precision_recall(flags, shocks, tol_days=5)
    assert precision == 0.5 and recall == 1.0


def test_attribution_accuracy_top1_top3():
    truth = {_d("2024-03-07"): "url_true"}
    preds = {_d("2024-03-07"): ["url_other", "url_true", "url_x"]}
    top1, top3 = attribution_accuracy(preds, truth)
    assert top1 == 0.0 and top3 == 1.0


# ---------------------------------------------------------------------------
# alert artifact schema (v2-W3)
# ---------------------------------------------------------------------------

def _cfg(tmp_webhook=""):
    return {"vertical": "testvertical", "target": {"symbol": "TEST=F"},
            "alerting": {"webhook_url": tmp_webhook}}


def test_deliver_writes_artifact_even_with_zero_alerts(monkeypatch, tmp_path):
    import src.pipeline as pl
    monkeypatch.setattr(pl, "ROOT", tmp_path)
    path = deliver([], _cfg(), as_of=pd.Timestamp("2026-07-12"))
    payload = json.loads(path.read_text())
    assert payload["n_alerts"] == 0 and payload["alerts"] == []
    assert payload["vertical"] == "testvertical"
    assert path.name == "testvertical_20260712.json"


def test_deliver_serializes_full_alert_schema(monkeypatch, tmp_path):
    import src.pipeline as pl
    monkeypatch.setattr(pl, "ROOT", tmp_path)
    alert = Alert(breach_date=pd.Timestamp("2026-07-10"), actual=97.5,
                  lower=98.0, upper=104.0, direction="down",
                  changepoint_agrees=True, garch_agrees=False,
                  aci_coverage=0.901, breach_run=2, drivers=[])
    path = deliver([alert], _cfg(), as_of=pd.Timestamp("2026-07-12"))
    a = json.loads(path.read_text())["alerts"][0]
    assert a["breach_date"] == "2026-07-10"
    assert a["interval"] == [98.0, 104.0]
    assert a["direction"] == "down"
    assert a["breach_run_days"] == 2
    assert a["changepoint_agrees"] is True and a["garch_agrees"] is False
    assert "not a causal claim" in a["note"]


def test_deliver_webhook_failure_does_not_raise(monkeypatch, tmp_path):
    """Delivery failures must never kill the batch."""
    import src.pipeline as pl
    monkeypatch.setattr(pl, "ROOT", tmp_path)
    path = deliver([], _cfg(tmp_webhook="http://127.0.0.1:9/unreachable"),
                   as_of=pd.Timestamp("2026-07-12"))
    assert path.exists()   # artifact written despite webhook failure
