# Production readiness checklist

This repo is production-ready for **dry-run deployment** once CI is green and the deploy smoke test passes. Treat **paper trading** as the next validation stage. Do not use live money until the live gates below are complete.

## Required before paper mode

- [ ] Bootstrap WIF using only the temporary bootstrap service-account key.
- [ ] Delete the bootstrap secret and disable/delete the temporary GCP key after bootstrap.
- [ ] Add Telegram alert settings.
- [ ] Add paper Gateway login settings as GitHub Environment Secrets.
- [ ] Add the paper trading account id as a GitHub Environment Secret.
- [ ] Deploy with `TRADING_MODE=dry_run` first.
- [ ] Confirm deploy-time runtime config validation passes.
- [ ] Confirm the deploy smoke test created a new `reports/rebalance-*.json` file.
- [ ] Run **IB Gateway Ops** with `action=configure-paper`.
- [ ] Confirm `ibgateway.service` is active after reboot.
- [ ] Confirm `127.0.0.1:7497` is reachable on the VM.
- [ ] Confirm `poma ibkr-check` passes and the configured account appears in managed accounts.
- [ ] Confirm the `poma reconcile-orders` cron entry is installed alongside the rebalance cron entry (`ops/cron/poma.cron`), so accepted-but-unfilled orders are followed up independent of the rebalance process lifetime.
- [ ] Confirm the paper account cash + portfolio balance is the intended rebalance sizing base.
- [ ] Confirm `STRATEGY_ALLOCATIONS` splits no more than 100% of the broker-derived account value.
- [ ] Confirm the default allocation is intentional: `rank_velocity_size_equal_weight=0.98,cash=0.02`.

## Required before live mode

- [ ] Backtest against QQQ/QQQM or another explicit benchmark with historical constituents and historical market caps.
- [ ] Validate data-provider endpoints and field meanings.
- [ ] Run at least one full week in `dry_run`.
- [ ] Run at least one full week in `paper`.
- [ ] Review IBKR orders/fills against POMA reports.
- [ ] Confirm no unresolved `completed_with_order_issues`, `blocked`, `failed`, cancelled, timed-out, or partial-fill runs remain unexplained.
- [ ] Confirm order type policy and fractional-share behavior in the IBKR account. POMA sizes and submits fractional quantities by default; list any ticker IBKR rejects fractional orders for in `NON_FRACTIONAL_TICKERS`.
- [ ] Confirm tax, FX, commission, and slippage assumptions.
- [ ] Add live Gateway login settings before running live Gateway configuration.
- [ ] Add the live account id before switching to `TRADING_MODE=live`.
- [ ] Run **IB Gateway Ops** with `action=configure-live` and approve mobile authentication when prompted.
- [ ] Set `ALLOW_LIVE_TRADING=true` intentionally.
- [ ] Manually review the latest rebalance report before the first live run.

## Deploy-time gates

The deploy workflow now fails before Terraform/app deployment when:

- Telegram settings are missing.
- Paper mode lacks a paper account id.
- Live mode lacks a live account id.
- Live mode is requested without `ALLOW_LIVE_TRADING=true`.
- Rendered `.env` values are missing, empty, or still placeholders.
- Strategy allocations exceed 100%.
- Paper/live mode uses `DATA_PROVIDER=fixture`.
- `MAX_DAILY_TRADES` cannot support a full `MAX_HOLDINGS` bootstrap.
- `MAX_ORDER_NOTIONAL_USD` is below `MIN_TRADE_NOTIONAL_USD`.

## Cost controls

- [ ] Use exactly one VM.
- [ ] Keep the VM as `e2-micro`.
- [ ] Keep the boot disk as `pd-standard` and at or below 30 GB.
- [ ] Keep the region as `us-west1`, `us-central1`, or `us-east1`.
- [ ] Keep Terraform state in one small US-region GCS bucket.
- [ ] Keep a monthly Cloud Billing budget alert enabled.
- [ ] Review external IPv4 and outbound network charges after the first deploy.
- [ ] Do not add Artifact Registry, Secret Manager, Cloud NAT, Cloud Run, Cloud Scheduler, Pub/Sub, Redis, or managed databases unless intentionally accepting their cost.

## Trading safeguards

- [ ] Keep `ORDER_TYPE=limit` by default.
- [ ] Keep `ALLOW_MARKET_ORDERS=false` unless explicitly intentional.
- [ ] Keep `MAX_TURNOVER_PCT=1.0` for first paper bootstrap, then lower it if you want stricter ongoing churn control. Deploy-time validation prints a (non-blocking) warning if `TRADING_MODE=live` still has `MAX_TURNOVER_PCT=1.0` as a nudge to revisit it after the initial bootstrap.
- [ ] Keep `MAX_ORDER_NOTIONAL_USD`, `MAX_DAILY_TRADES`, `MAX_POSITION_PCT`, and `MAX_TURNOVER_PCT` within your operational tolerance.
- [ ] Keep cash outside active strategies by using a `cash` sleeve in `STRATEGY_ALLOCATIONS`.
- [ ] Treat broker balance-read failures as blocking infrastructure issues; do not trade until `poma ibkr-check` and a balance-backed report succeed.
- [ ] Treat any `BrokerUnavailable` report as an infrastructure issue: confirm IBKR Activity shows no accepted orders, rerun **IB Gateway Ops** configure, and require `poma ibkr-check` to pass before trading again.
- [ ] Review any `failed`, `blocked`, `completed_with_order_issues`, timed-out, cancelled, or partial execution result manually.

Built in and not configurable: `poma monitor` resumes a session a killed process (crash/OOM/VM restart) left `running`, using the *same* `run_id`, and any orderRef already recorded in the ledger for that run (open or terminal) is not resubmitted (`IdempotentReplay`); a rebalance is blocked if unresolved orders exist from a prior session *or* a different run within the same session; and buys are sized against broker cash refreshed *after* the sell phase, never against unconfirmed sell proceeds (`BuyingPowerBlocked` if that refreshed cash falls short).

## Alert expectations

- Deploy result alerts are sent by GitHub Actions.
- Dry-run and blocked runs send a summary and make no broker changes.
- Paper/live runs send an execution-start alert, broker-accepted order lifecycle/status alerts, and a final summary.
- If IBKR is unavailable before order acceptance, POMA sends one deduplicated broker-unavailable alert instead of per-ticker Created/Failed spam.
- Any non-filled order result or diagnostic message marks the run as `completed_with_order_issues` in local state.
- Normal cron checks outside the rebalance window do not send Telegram alerts to avoid noise.

No Ansible is needed for this one-host setup. Terraform startup plus Docker Compose is simpler and cheaper.
