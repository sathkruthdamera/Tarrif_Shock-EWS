# Tariff & Geopolitical Shock Early-Warning System (EWS)

An early-warning system for tariff- and geopolitics-driven shocks in commodity and
supply-chain time series. It combines three current techniques into one pipeline:

1. **Zero-shot foundation-model forecasting** with calibrated prediction intervals.
2. **Event-informed reasoning** over tariff / policy announcements.
3. **Causal break attribution** that names the most likely driver of a move, with a
   verifiable source link.

**v1 vertical:** steel / steel-exposed sectors (HRC futures, `SLX` ETF proxy).

---

## Worked example (populated after the first backtest)

> This section is intentionally a placeholder until the first end-to-end backtest is
> run. It will hold **one dated, falsifiable example**:
>
> - the calibrated forecast interval for the target series as of date `T`,
> - the Federal Register tariff notice on date `T+k`,
> - the actual price move and the interval breach,
> - the attributed driver and the **lead time** the system would have given.
>
> No results are shown here yet because none have been produced. Nothing in this
> section is fabricated.

---

## How it works

```
numeric series --> Chronos-2 (zero-shot) --> conformal wrapper --> calibrated interval
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
src/forecast/  Chronos-2 zero-shot forecast + conformal calibration
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
