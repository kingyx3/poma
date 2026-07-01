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
paper/live: one AccountSnapshot read (broker cash + positions + net liquidation)
  -> MANAGED_CAP_MODE (broker_total | min_of_broker_total_and_cap)
  -> STRATEGY_ALLOCATIONS (PortfolioCapitalPlan)
      -> every allocated non-cash sleeve is executed via the strategy registry
      -> cash passive sleeve (never traded)
```

For paper/live, POMA reads the configured IBKR account's USD cash, current positions, and net liquidation in a single broker call (`AccountSnapshot`) before every rebalance, instead of separate balance and position reads. `MANAGED_CAP_MODE=broker_total` (default) sizes the rebalance off that full broker equity; `min_of_broker_total_and_cap` instead sizes off `min(broker equity, MANAGED_CAP_USD)`. `STRATEGY_ALLOCATIONS` then splits the resolved value across named sleeves and cannot exceed 100%.

The engine runs every sleeve with a positive allocation, not a single hardcoded strategy: each registered `Strategy` builds its own `StrategyTargetBook` against its sleeve's capital, and a `portfolio_constructor` step combines all sleeves' targets into one portfolio-level target per ticker (summing overlapping tickers across strategies and keeping per-strategy attribution for reports). The default `rank_velocity_size_equal_weight=0.98,cash=0.02` uses 98% of the resolved portfolio value for trades and leaves 2% in passive cash. Cash is not a hidden active-strategy buffer.

`DRY_RUN_PORTFOLIO_VALUE_USD` is used only in `dry_run` mode so local reports can still be generated without an IBKR account connection. In paper/live, inability to read a positive broker account snapshot blocks execution rather than using that fallback for order sizing.

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
  -> paper/live: read one broker AccountSnapshot (cash + positions + net liquidation)
  -> resolve portfolio value via MANAGED_CAP_MODE
  -> build a StrategyTargetBook per allocated strategy sleeve
  -> combine sleeve target books into one portfolio-level target per ticker
  -> validate target/risk/order guards (including buying-power)
  -> write report + execution journal (state/orders/<run_id>.json)
  -> dry_run: Telegram summary only
  -> paper/live: execution-start Telegram alert
      -> broker readiness check: connected, authenticated, configured account visible
      -> if unavailable: deduplicated broker-unavailable alert + no order-created spam
      -> if ready: submit orders and emit broker-accepted status/final/failure callbacks
      -> write final report + reconciliation (state/reconciliations/<run_id>.json)
      -> Telegram final summary
      -> local state status: completed or completed_with_order_issues
```

Order status notifications are best-effort. Telegram failure never causes duplicate trading attempts or changes local run-state semantics.

## Runtime files

```text
state/rebalance_state.json       # last completed trading session
                                     # includes completed_with_order_issues as terminal
state/orders/<run_id>.json       # planned trades, strategy attribution, target book hash,
                                     # and the expected account snapshot; written before submission
state/reconciliations/<run_id>.json  # order results and a post-trade account snapshot;
                                     # written after submission, best-effort

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
| Excess turnover | Turnover guard blocks execution. |
| Broker balance unavailable | Paper/live execution blocks before order sizing can use stale configured capital. |
| Accidental live trading | `ALLOW_LIVE_TRADING=true` required for live mode. |
| Missing deploy config | CI/CD `.env` rendering and runtime validation fail before deployment. |
| Wrong IBKR account | Deploy validation requires an account id; `poma ibkr-check` verifies it appears in managed accounts. |
| Gateway not authenticated or connection lost before order acceptance | Broker readiness and per-order connection guards mark the batch `BrokerUnavailable` without emitting misleading `Created` alerts. |
| Order timeout/cancel/failure | Order lifecycle alert plus final `completed_with_order_issues` state. |
| Public SSH exposure | Terraform only allows SSH through IAP TCP forwarding. |
