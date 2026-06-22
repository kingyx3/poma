# Architecture

## Components

```text
Data Provider
  -> Cloud Run Job
  -> Rebalance report
  -> Remote Executor API
  -> IB Gateway on VPS
  -> IBKR
```

## Why not full serverless with IBKR?

For normal retail IBKR accounts, the execution path generally requires a locally running authenticated gateway/session. POMA therefore treats the broker gateway as the only always-on component. The strategy itself remains stateless and serverless-friendly.

## Failure modes handled by design

| Failure | Mitigation |
|---|---|
| Missing data | Provider validation fails before target generation. |
| Excessive turnover | `MAX_TURNOVER_PCT` blocks execution. |
| Live mode accidental enablement | `ALLOW_LIVE_TRADING=true` required. |
| Tiny uneconomic trades | `MIN_TRADE_NOTIONAL_USD` filter. |
| Concentration risk | `MAX_POSITION_PCT` cap. |
| Broker outage | Cloud Run job writes report before execution attempt. |

## Suggested future hardening

- Add a dedicated FastAPI executor service with IBKR order/fill reconciliation.
- Add persistent Postgres/Supabase trade ledger.
- Add Telegram alerts for run summary and failures.
- Add backtesting module with historical constituent snapshots.
- Add OpenTelemetry or structured log export.
