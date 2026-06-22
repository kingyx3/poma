# Architecture

## Chosen architecture

```text
Ubuntu VPS
  -> cron every 5 minutes
  -> POMA monitor command
  -> IB Gateway on same VPS
  -> IBKR
```

The app checks the Nasdaq/NYSE market calendar on every run and only rebalances when:

1. Today is a US trading day.
2. The market has been open for at least `REBALANCE_AFTER_OPEN_MINUTES`.
3. The local state file says today's rebalance has not already completed.

This avoids hardcoding Singapore or New York cron times and handles daylight saving time through the market-calendar library.

## Why this is simpler and cheaper

Removed by design:

- Cloud Run
- Cloud Scheduler
- Artifact Registry
- Secret Manager
- Terraform
- Remote executor API
- Multi-environment deployment machinery

This eliminates the main sources of accidental cloud bill growth. The only always-on component is the VPS you already need for IB Gateway.

## Runtime files

```text
state/rebalance_state.json   # last completed trading session
reports/*.json               # generated rebalance reports
.env                         # local secrets/config; never commit
```

## Failure modes

| Failure | Mitigation |
|---|---|
| US DST changes | Market calendar decides the rebalance window. |
| US holiday / half-day | Market calendar returns the correct session schedule. |
| Repeated cron invocations | State file allows only one rebalance per session. |
| Excess turnover | Turnover guard blocks execution. |
| Accidental live trading | `ALLOW_LIVE_TRADING=true` required for live mode. |
| Cloud cost growth | No cloud runtime, registry, scheduler, or secret store. |
