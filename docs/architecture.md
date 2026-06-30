# Architecture

## Chosen architecture

```text
Ubuntu host
  -> cron every 5 minutes
  -> POMA monitor command
  -> Yahoo/yfinance market data
  -> local snapshot store
  -> IB Gateway on same host
  -> IBKR
```

The host can be any small VPS. The included Terraform path provisions a GCP free-tier-aligned `e2-micro` VM for this same one-host design.

The app checks the market calendar on every run and only rebalances when:

1. Today is a US trading day.
2. The market has been open for at least `REBALANCE_AFTER_OPEN_MINUTES`.
3. The local state file says today's rebalance has not already completed or reached an order-issue terminal state.

## Capital allocation boundary

```text
broker USD cash + broker stock market value  # paper/live
PORTFOLIO_VALUE_USD                         # dry-run fallback only
  -> STRATEGY_ALLOCATIONS
      -> rank_velocity_size_equal_weight active sleeve
      -> cash passive sleeve
      -> future strategy sleeves
```

Paper/live rebalances derive the full portfolio value from the configured broker account immediately before order generation: USD cash balance plus current stock positions market value. `STRATEGY_ALLOCATIONS` then splits that dynamic portfolio value across named sleeves and cannot exceed 100%. The current active strategy receives only its allocated sleeve, so the default `rank_velocity_size_equal_weight=0.98,cash=0.02` uses 98% of broker-derived portfolio value for trades and leaves 2% in passive cash. Cash is not a hidden active-strategy buffer. `PORTFOLIO_VALUE_USD` remains only as a deterministic dry-run fallback.

## Market data provider boundary

The strategy code consumes normalized snapshots and does not depend directly on the data adapter implementation. The production adapter is Yahoo/yfinance and returns snapshots with at least:

```text
ticker
market_cap
price
```

Optional provider fields such as `name`, `exchange`, `volume`, `dollar_volume`, `float_shares`, `shares_outstanding`, `source`, and `as_of` are preserved when available.

Default provider:

```text
DATA_PROVIDER=yahoo
UNIVERSE=us_top_market_cap
```

`DATA_PROVIDER=fixture` remains available for tests and PR dry-runs. Future providers should be added as a new `MarketDataClient` implementation and registered in `build_data_client()`; engine and strategy code should not change.

## GCP e2-micro deployment path

```text
GitHub Actions
  -> resolve CI defaults and selected GitHub Environment secrets
  -> render .env
  -> validate rendered runtime config
  -> validate market-data provider when DATA_PROVIDER=yahoo
  -> Terraform apply for one GCP e2-micro VM
  -> upload repo package + .env over IAP SSH
  -> run Docker Compose dry-run smoke test
  -> install cron
  -> send Telegram deploy result
```

Terraform creates one small VM, one standard boot disk, one dedicated VPC/subnet, and one SSH firewall rule limited to the IAP TCP forwarding range.

## Runtime order flow

```text
plan rebalance
  -> paper/live: read broker USD cash and current stock market value
  -> allocate dynamic portfolio value across strategy sleeves
  -> validate target/risk/order guards
  -> dry_run: write report + Telegram summary only
  -> paper/live: execution-start Telegram alert
      -> broker readiness check: connected, authenticated, configured account visible
      -> if unavailable: deduplicated broker-unavailable alert + no order-created spam
      -> if ready: submit orders and emit broker-accepted status/final/failure callbacks
      -> write final report
      -> Telegram final summary
      -> local state status: completed or completed_with_order_issues
```

Order status notifications are best-effort. Telegram failure never causes duplicate trading attempts or changes local run-state semantics.

## Runtime files

```text
state/rebalance_state.json       # last completed trading session
                                     # includes completed_with_order_issues as terminal

data/market_snapshots/*.csv      # provider snapshots and market-cap ranks
reports/*.json                   # generated rebalance reports
.env                             # host-local secrets/config; never commit
```

## Failure modes

| Failure | Mitigation |
|---|---|
| US DST changes | Market calendar decides the rebalance window. |
| US holiday / half-day | Market calendar returns the correct session schedule. |
| Repeated cron invocations | State file allows only one rebalance attempt per session. |
| Missing rank history | Engine falls back to current market-cap selection and writes a warning. |
| Missing broker cash/account valuation | Paper/live rebalance fails before order generation. |
| Excess turnover | Turnover guard blocks execution. |
| Accidental live trading | `ALLOW_LIVE_TRADING=true` required for live mode. |
| Missing deploy config | CI/CD `.env` rendering and runtime validation fail before deployment. |
| Wrong IBKR account | Deploy validation requires an account id; `poma ibkr-check` verifies it appears in managed accounts. |
| Gateway not authenticated or connection lost before order acceptance | Broker readiness and per-order connection guards mark the batch `BrokerUnavailable` without emitting misleading `Created` alerts. |
| Order timeout/cancel/failure | Order lifecycle alert plus final `completed_with_order_issues` state. |
| Public SSH exposure | Terraform only allows SSH through IAP TCP forwarding. |
