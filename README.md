# Tariff & Geopolitical Shock Early-Warning System (EWS)

An early-warning system for tariff- and geopolitics-driven shocks in commodity and
supply-chain time series. It combines three current techniques into one pipeline:

1. **Zero-shot foundation-model forecasting** with calibrated prediction intervals.
2. **Event-informed reasoning** over tariff / policy announcements.
3. **Causal break attribution** that names the most likely driver of a move, with a
   verifiable source link.

**v1 vertical:** steel / steel-exposed sectors (HRC futures, `SLX` ETF proxy).

---

## Worked example (build-step 1: SLX, TimesFM 2.5 + CQR)

First end-to-end forecast, run on real `SLX` closes (2016-01-04 to 2026-07-08) with
**TimesFM 2.5** zero-shot and a **split-conformal (CQR)** calibrated interval.

![SLX TimesFM 2.5 forecast with conformal-calibrated interval](outputs/figures/slx_timesfm_calibrated_interval.png)

**Interval calibration (the point of the exercise).** Across held-out rolling origins:

| Interval | Empirical coverage |
| --- | --- |
| TimesFM raw q10-q90 band (nominal 80%) | 79.2% |
| CQR-calibrated (target 90%) | 91.5% |

TimesFM's raw deciles are already close to calibrated at 80%; CQR adds a data-driven
offset of `Q = 1.55` (USD) to the band to reach the 90% target. That is what turns a
forecast into a risk signal with a coverage guarantee.

**Point accuracy (honest baseline check).** 10-business-day-ahead MASE on the test origins:

| Model | MASE |
| --- | --- |
| Seasonal-naive | 4.21 |
| ARIMA(1,1,1) | 4.22 |
| TimesFM 2.5 | 4.33 |

For a liquid, near-random-walk ETF at daily frequency, point forecasting barely beats a
random walk, and TimesFM lands marginally behind naive here. That is the expected result
and it is stated plainly: the value of this system is the **calibrated interval** and
(next) **event attribution**, not point accuracy on an efficient price series.

**Headline holdout.** Forecasting the 10 business days from 2026-06-24, the actual `SLX`
path (which fell from ~101 to ~97) stayed inside the calibrated 90% interval throughout:
**0 breaches**. A breach is exactly the signal the next build steps (event monitor plus
attribution) are designed to act on.

> Reproduce: `./.venv/Scripts/python.exe scripts/step1_forecast_slx.py`
> (all numbers in [`outputs/step1_summary.json`](outputs/step1_summary.json)).

---

## How it works

```
numeric series --> TimesFM 2.5 (zero-shot) --> CQR conformal --> calibrated interval
                                                                          |
event stream  --> embeddings + severity tags --> relevance/timing --------+
                                                                          v
     actual breaches interval?  -->  changepoint cross-check  -->  attribution --> alert
```

The full design (use cases, data sources, model trade study, architecture, evaluation
plan, risk register) lives in [`docs/Tariff_Shock_EWS_Solution_Design.xlsx`](docs/Tariff_Shock_EWS_Solution_Design.xlsx).

## Repository layout

```
config/    per-vertical configuration (steel.yaml)
src/data/  ingestion: prices, trade volumes, events
src/forecast/  TimesFM 2.5 zero-shot forecast + conformal (CQR) calibration
src/events/    event embedding, severity tagging, attribution
src/detect/    changepoint detection (cross-check)
src/eval/      rolling-origin backtest + event early-warning evaluation
src/pipeline.py  orchestrates forecast -> monitor -> attribute -> alert
notebooks/  case-study demo
data/       gitignored raw + parquet cache
outputs/    generated figures
```

## Getting started

```bash
# Python 3.11+
pip install -e .            # or: uv pip install -e .
cp .env.example .env        # add FRED_API_KEY, CENSUS_API_KEY (both free)
python -m src.pipeline --config config/steel.yaml
```

## Data sources (all free / public)

| Layer            | Source                                             | Access                    |
|------------------|----------------------------------------------------|---------------------------|
| Target series    | Steel HRC futures / `SLX` ETF proxy                | `yfinance`                |
| Macro            | FRED (steel PPI, industrial production, USD index) | `fredapi` + free key      |
| Trade volumes    | US Census trade (HS 72xx)                          | Census API + free key     |
| Shipping cost    | Freightos Baltic Index / Baltic Dry proxy          | free tier                 |
| Events (anchor)  | Federal Register (Section 232/301, USTR, Commerce) | Federal Register API      |
| Events (supp.)   | News headlines                                     | GDELT / NewsAPI free tier |

The **Federal Register API** is the anchor: free, structured JSON, timestamped, and
queryable by agency + keyword, which makes attribution auditable rather than hand-wavy.

## Limitations

- v1 is a **single vertical (steel)**, daily cadence, offline backtest. No live
  alerting infrastructure and no intraday data.
- The forecast + event fusion is **decoupled, not end-to-end trained**, chosen for
  interpretability and low compute; a jointly-trained multimodal model is a v2 idea.
- Attribution is a ranking heuristic (relevance x severity x recency), not a causal
  identification guarantee; it always surfaces a source link for human verification.
- News feeds are noisy; the system anchors on the Federal Register and uses news only
  to supplement.
- Nothing here constitutes trading or investment advice.

## License

MIT (see [`LICENSE`](LICENSE)).
