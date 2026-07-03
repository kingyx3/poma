# Architecture

This page documents POMA's application architecture and runtime boundaries. Strategy-specific selection logic belongs in [`docs/strategies/`](strategies/), and portfolio sizing/allocation behavior belongs in [`docs/portfolio-management.md`](portfolio-management.md).

## Chosen architecture

```text
Ubuntu host
  -> cron every 5 minutes
  -> POMA monitor command
  -> configured market-data provider
  -> local snapshot store
  -> IB Gateway on same host
  -> IBKR
```

The host can be any small VPS. The included Terraform path provisions a GCP free-tier-aligned `e2-micro` VM for this same one-host design.

The app checks the market calendar on every run and only rebalances when:

1. Today is a US trading day.
2. The market has been open for at least `REBALANCE_AFTER_OPEN_MINUTES`.
3. The local state file says today's rebalance has not already completed or reached an order-issue terminal state.

If the local state file shows today's session as `running` with no terminal status ever recorded, the previous attempt was killed outright (process crash, OOM, VM restart) before it could record an outcome. `poma monitor` resumes that session with the *same* `run_id` instead of starting a fresh one, which lets `ExecutionManager`'s idempotent replay recognize and skip orders the killed attempt already got to the broker. A manual `poma rebalance` invocation always mints a new `run_id` and is not part of this recovery path.

## Capital allocation boundary

```text
paper/live: one AccountSnapshot read (USD cash + USD positions + USD net liquidation)
  -> MANAGED_CAP_MODE resolves managed portfolio value
  -> STRATEGY_ALLOCATIONS creates a PortfolioCapitalPlan
      -> every allocated non-cash sleeve is executed via the strategy registry
      -> cash passive sleeve is reserved and never traded
```

For paper/live, POMA reads the configured IBKR account's actual USD cash, USD-denominated positions, and USD net liquidation in a single broker call (`AccountSnapshot`) before every rebalance. `MANAGED_CAP_MODE` decides whether the full USD-only broker value or a configured cap is used. `STRATEGY_ALLOCATIONS` then splits the resolved value across named sleeves and cannot exceed 100%.

The engine runs every sleeve with a positive allocation. Each registered `Strategy` builds its own `StrategyTargetBook` against its sleeve capital, and a `portfolio_constructor` step combines all sleeves' targets into one portfolio-level target per ticker. Cash is not a hidden active-strategy buffer; reserve cash by allocating a `cash` sleeve. See [`docs/portfolio-management.md`](portfolio-management.md) for the complete strategy-neutral capital model.

`DRY_RUN_PORTFOLIO_VALUE_USD` is used only in `dry_run` mode so local reports can still be generated without an IBKR account connection. In paper/live, inability to read a positive broker account snapshot blocks execution rather than using that fallback for order sizing.

## Market data provider boundary

The strategy layer consumes normalized provider snapshots and should not depend directly on any adapter implementation. Provider snapshots expose at least:

```text
ticker
market_cap
price
```

Optional provider fields such as `name`, `exchange`, `volume`, `dollar_volume`, `float_shares`, `shares_outstanding`, `source`, and `as_of` are preserved when available.

Default provider configuration:

```text
DATA_PROVIDER=yahoo
UNIVERSE=us_top_market_cap
```

`DATA_PROVIDER=fixture` remains available for tests and PR dry-runs. Future providers should be added as a new `MarketDataClient` implementation and registered in `build_data_client()`; engine and portfolio construction code should not change. Strategy-specific docs define how each strategy interprets the normalized snapshot fields.

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
  -> paper/live: check the order ledger for orders not from this exact run
      -> from a prior session, or a different run_id within this session:
          -> STALE_ORDER_POLICY=block (default): block this rebalance until reconciled/cancelled
          -> STALE_ORDER_POLICY=cancel: cancel them, then continue planning
      -> from this run_id: reported informationally only (a retry relies on idempotent replay
         below, not on being blocked by its own still-working orders)
  -> paper/live: read one broker AccountSnapshot (USD cash + USD positions + USD net liquidation)
  -> resolve portfolio value via MANAGED_CAP_MODE
  -> build a StrategyTargetBook per allocated strategy sleeve
  -> combine sleeve target books into one portfolio-level target per ticker
  -> skip trades whose operator-estimated transaction cost makes the rebalance benefit too small
  -> validate target/risk/order guards (including buying-power)
  -> write report + execution journal (state/orders/<run_id>.json)
  -> dry_run: Telegram summary only
  -> paper/live: execution-start Telegram alert
      -> broker readiness check: connected, authenticated, configured account visible
      -> if unavailable: deduplicated broker-unavailable alert + no order-created spam
      -> if ready:
          -> tag every order with an idempotent orderRef (poma:<run_id>:<index>:<ticker>:<side>)
          -> if a ledger entry already exists for that orderRef, open or terminal (a retry of
             this exact run after a crash), do not resubmit it; return an IdempotentReplay result
          -> immediately before each phase: fetch a fresh IBKR execution quote per ticker and
             reprice off it (poma.execution_pricing); block trades that fail a freshness/spread/
             delayed-quote check instead of submitting them (see docs/configuration.md)
          -> submit sell orders first; refresh broker cash before sizing buys against it (an
             unfilled or partially filled sell is not assumed to provide buying power); if the
             refreshed cash does not cover planned buy notional, block the buys as
             BuyingPowerBlocked instead of submitting them
          -> submit buy orders, each with explicit ORDER_TIME_IN_FORCE
          -> record every submission and status change, plus the quote it was priced from, in
             the order ledger
          -> emit broker-accepted status/final/failure callbacks
      -> write final report + reconciliation (state/reconciliations/<run_id>.json)
      -> Telegram final summary
      -> local state status: completed or completed_with_order_issues
```

Order status notifications are best-effort. Telegram failure never causes duplicate trading attempts or changes local run-state semantics.

## Order lifecycle management

Reaching `PreSubmitted`/`Submitted` only means IBKR accepted the order; a limit order can sit there unfilled indefinitely. POMA tracks that separately from broker acceptance with a durable order ledger and an explicit follow-up command:

```text
ExecutionManager (src/poma/execution_manager.py)
  -> tags every trade with a stable orderRef and records a lifecycle ledger entry
  -> a retry of the same run that finds a non-terminal ledger entry for an orderRef does not
     resubmit it; it returns an IdempotentReplay result instead
  -> stages sells before buys, then refreshes broker cash before sizing/submitting buys so a
     rebalance never assumes unconfirmed sell proceeds provide buying power
  -> IbkrBroker (src/poma/broker.py) stays a thin adapter: submit/cancel/replace/query only
  -> internal lifecycle: planned -> submitted -> broker_accepted -> partially_filled
       -> filled | replace_pending -> cancel_pending -> cancelled | rejected | expired
```

`poma reconcile-orders` polls the broker for every open POMA-tagged order, matched by `orderRef`, and applies the timeout policy: replace once after `REPLACE_AFTER_SECONDS`, then cancel after `CANCEL_AFTER_SECONDS` if still unfilled. The replacement price is computed from a fresh IBKR quote fetched at reconcile time, with `REPLACE_PRICE_IMPROVEMENT_BPS` applied on top of that fresh side-of-market price. If no valid fresh quote is available, the replace is skipped for that reconcile pass rather than repricing off stale data. It is scheduled on its own cron entry (`ops/cron/poma.cron`, every 1-2 minutes) so working orders are followed up even after the rebalance process has exited; it also sends a Telegram alert on every lifecycle change.

The next scheduled rebalance checks for orders left open from a prior session, or from a different run within the same session, before planning. See `STALE_ORDER_POLICY` in [`docs/configuration.md`](configuration.md).

## Reports and auditability

Both `reports/<run_id>.json` (the CLI report) and `state/orders/<run_id>.json` (the execution journal, written before submission) carry the full capital picture for the run, not just the trades:

- `broker_account_snapshot` — the USD-only broker read (`cash_usd`, `positions_market_value_usd`, `net_liquidation_usd`, `total_value_usd`) before `MANAGED_CAP_MODE` is applied.
- `portfolio_value_usd` — the managed value the rebalance actually sized against.
- `cash_sleeve_usd` — capital assigned to the passive `cash` strategy sleeve, if configured.
- `total_allocated_usd` / `total_allocated_pct` — capital assigned to every sleeve (active strategies plus cash).
- `unallocated_capital_usd` — managed value assigned to no sleeve at all; distinct from the explicit cash sleeve.
- `target_exposure_usd` — total planned notional across every combined portfolio-level target.
- `strategy_books` / `combined_targets` — per-strategy target attribution and the netted portfolio-level target per ticker, so overlapping tickers across strategies show which sleeves contributed to the final target.
- each entry in `trades`/`planned_trades` carries both the planning reference price and, once repriced for execution, the execution-time quote's source/basis/timestamp/age/spread. See "Strategy pricing vs. execution pricing" in [`docs/configuration.md`](configuration.md).

`state/reconciliations/<run_id>.json` adds the post-trade order results and a best-effort post-trade account snapshot, for diffing what was intended against what the broker actually reports afterward.

## Runtime files

```text
state/rebalance_state.json       # last completed trading session
                                     # includes completed_with_order_issues as terminal
state/orders/<run_id>.json       # planned trades, strategy attribution, target book hash,
                                     # capital breakdown (broker/managed/cash sleeve/unallocated),
                                     # and the expected account snapshot; written before submission
state/orders/open_orders.jsonl   # durable order lifecycle ledger snapshot; one line per order
                                     # not yet in a terminal state, keyed by orderRef
state/orders/order_events.jsonl  # append-only log of every lifecycle transition ever recorded
state/reconciliations/<run_id>.json  # order results and a post-trade account snapshot;
                                     # written after submission, best-effort

data/market_snapshots/*.csv      # provider snapshots used by strategy inputs
reports/*.json                   # generated rebalance reports
.env                             # host-local secrets/config; never commit
```

## Failure modes

| Failure | Mitigation |
|---|---|
| US DST changes | Market calendar decides the rebalance window. |
| US holiday / half-day | Market calendar returns the correct session schedule. |
| Repeated cron invocations | State file allows only one rebalance attempt per session. |
| Missing strategy input data or history | The relevant strategy reports a warning or blocks according to its own documented behavior. |
| Excess turnover | Turnover guard blocks execution. |
| Broker balance unavailable | Paper/live execution blocks before order sizing can use stale configured capital. |
| Accidental live trading | `ALLOW_LIVE_TRADING=true` required for live mode. |
| Missing deploy config | CI/CD `.env` rendering and runtime validation fail before deployment. |
| Wrong IBKR account | Deploy validation requires an account id; `poma ibkr-check` verifies it appears in managed accounts. |
| Gateway not authenticated or connection lost before order acceptance | Broker readiness and per-order connection guards mark the batch `BrokerUnavailable` without emitting misleading `Created` alerts. |
| Order timeout/cancel/failure | Order lifecycle alert plus final `completed_with_order_issues` state. |
| Limit order accepted but never fills | `poma reconcile-orders` replaces once then cancels per `REPLACE_AFTER_SECONDS`/`CANCEL_AFTER_SECONDS`, independent of the rebalance process lifetime. |
| Open orders left over from a prior session, or from a different run in the same session | The next rebalance blocks (or auto-cancels, per `STALE_ORDER_POLICY`) instead of silently layering a new plan on top. |
| Process killed outright (crash/OOM/VM restart) mid-run | `poma monitor`'s next tick resumes the session left `running` with the same `run_id`; any orderRef already recorded in the ledger is not resubmitted, and `submit_plan` returns an `IdempotentReplay` result for it instead. |
| Sell proceeds assumed to fund buys | Buys are sized against broker cash refreshed *after* the sell phase, not the pre-trade cash snapshot; insufficient refreshed cash blocks the buys as `BuyingPowerBlocked` instead of submitting them. |
| Process crash mid-submission | Every order is tagged with an idempotent `orderRef` and recorded in the order ledger before submission, so a reconnect can recognize what was already sent. |
| Public SSH exposure | Terraform only allows SSH through IAP TCP forwarding. |
