# Synthetic data and validation

## Data generation

On an empty database, the API seeds five fictional agents across Dhaka and Chittagong areas, three logically separate providers, physical cash, provider balances, recent transactions, seven-day transaction history, and balance history. Identifiers, names, phone numbers, counterparties, balances, and events are synthetic. Seeded random generation uses fixed seeds so the initial dataset is reproducible.

The scenario engine supports:

| Scenario | Intended interpretation | Ground truth |
|---|---|---|
| bKash e-money drawdown | sustained cash-in demand consumes the separate bKash position | critical, not an anomaly judgment |
| repeated Nagad amounts | unusual repeated-value behavior | anomaly requiring review |
| structuring | near-threshold clustered values | anomaly requiring review |
| Rocket delay | late/incomplete provider feed | data-quality issue |
| Nagad salary day | plausible legitimate high volume with observed calendar context | normal / negative control |

Scenario ground truth is used only for evaluation and is never read by the detector. Separately stored operational-context rows represent information that could genuinely be known in advance, such as a salary calendar or a reported demand surge. Compatible velocity/timing signals are treated as contextual rather than promoted, while repeated-value, threshold-cluster, and reconciliation rules still run. The UI and workflow never present evaluation labels as proof about a person.

## Analytical approach

Liquidity projection uses recent balance history and burn rate, with optional learned enrichment when sufficient data exists. Output includes provider, approximate shortage time, confidence, data quality, and reasons. Missing, delayed, or inconsistent data reduces confidence and activates fallback messaging rather than silently producing a strong conclusion.

Anomaly detection combines understandable rule heads such as repeated amounts, velocity change, and near-threshold clustering. Alerts retain record-level evidence, rule reasons, confidence, and uncertainty. A legitimate salary-day scenario tests expected false positives.

## Measured metrics

`GET /metrics/snapshot` and the Metrics page calculate:

- liquidity forecast mean absolute error in minutes;
- warning lead time before simulated shortage;
- anomaly precision, recall, and false-positive rate against injected scenarios;
- explanation coverage (reason, evidence, and uncertainty present);
- API latency p50 and p95 from simulation cycles;
- confidence change under degraded provider data; and
- priority classification alignment with scenario intent.

Values depend on scenarios executed during the current 24-hour evaluation window. When no qualifying sample exists, the metric is `n/a`; the prototype never reports cold-start perfection.

## Engineering validation

The backend suite covers liquidity fallback/recovery, anomaly evidence and negative controls, case lifecycle and provider authorization, support workflow, decision weights, explanation validation/fallback, and responsible language. The frontend production build performs TypeScript validation and compiles every route.

Run:

```bash
cd apps/api && python -m pytest -q
cd ../web && npm run build
```

## Assumptions

- The prototype clock, demand, and balances are simulated.
- Provider feeds share a normalized demo schema; this does not claim real API interoperability.
- Forecast accuracy on synthetic behavior does not establish production performance.
- Thresholds and decision weights are demonstration settings requiring provider-specific calibration and governance.
