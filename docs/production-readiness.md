# Production readiness checklist

This repo is production-ready for **dry-run deployment** once CI is green and the deploy smoke test passes. Treat **paper trading** as the next validation stage. Do not use live money until the live gates below are complete.

## Required before paper mode

- [ ] Bootstrap WIF using only the temporary `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` secret.
- [ ] Delete the bootstrap secret and disable/delete the temporary GCP key after bootstrap.
- [ ] Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- [ ] Add `IBKR_LOGIN_ID_PAPER` and `IBKR_LOGIN_SECRET_PAPER` as GitHub Environment Secrets.
- [ ] Add `IBKR_ACCOUNT_PAPER` as a GitHub Environment Secret.
- [ ] Deploy with `TRADING_MODE=dry_run` first.
- [ ] Confirm deploy-time runtime config validation passes.
- [ ] Confirm the deploy smoke test created a new `reports/rebalance-*.json` file.
- [ ] Run **IB Gateway Ops** with `action=configure-paper`.
- [ ] Confirm `ibgateway.service` is active after reboot.
- [ ] Confirm `127.0.0.1:7497` is reachable on the VM.
- [ ] Confirm `poma ibkr-check` passes and the configured account appears in managed accounts.
- [ ] Confirm `PORTFOLIO_VALUE_USD` is the intended total managed cap and `STRATEGY_ALLOCATIONS` splits no more than 100% of it.
- [ ] Confirm the default allocation is intentional: `rank_velocity_size_equal_weight=0.98,cash=0.02`.

## Required before live mode

- [ ] Backtest against QQQ/QQQM or another explicit benchmark with historical constituents and historical market caps.
- [ ] Validate data-provider endpoints and field meanings.
- [ ] Run at least one full week in `dry_run`.
- [ ] Run at least one full week in `paper`.
- [ ] Review IBKR orders/fills against POMA reports.
- [ ] Confirm no unresolved `completed_with_order_issues`, `blocked`, `failed`, cancelled, timed-out, or partial-fill runs remain unexplained.
- [ ] Confirm order type policy and fractional-share behavior in the IBKR account.
- [ ] Confirm tax, FX, commission, and slippage assumptions.
- [ ] Add `IBKR_LOGIN_ID` and `IBKR_LOGIN_SECRET` before running live Gateway configuration.
- [ ] Add `IBKR_ACCOUNT` before switching to `TRADING_MODE=live`.
- [ ] Run **IB Gateway Ops** with `action=configure-live` and approve mobile authentication when prompted.
- [ ] Set `ALLOW_LIVE_TRADING=true` intentionally.
- [ ] Manually review the latest rebalance report before the first live run.

## Deploy-time gates

The deploy workflow now fails before Terraform/app deployment when:

- Telegram secrets are missing.
- Paper mode lacks `IBKR_ACCOUNT_PAPER`.
- Live mode lacks `IBKR_ACCOUNT`.
- Live mode is requested without `ALLOW_LIVE_TRADING=true`.
- Rendered `.env` values are missing, empty, or still placeholders.
- Strategy allocations exceed 100% of `PORTFOLIO_VALUE_USD`.
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
- [ ] Keep `MAX_TURNOVER_PCT=1.0` for first paper bootstrap, then lower it if you want stricter ongoing churn control.
- [ ] Keep `MAX_ORDER_NOTIONAL_USD`, `MAX_DAILY_TRADES`, `MAX_POSITION_PCT`, and `MAX_TURNOVER_PCT` within your operational tolerance.
- [ ] Keep cash outside active strategies by using a `cash` sleeve in `STRATEGY_ALLOCATIONS`.
- [ ] Treat any `BrokerUnavailable` report as an infrastructure issue: confirm IBKR Activity shows no accepted orders, rerun **IB Gateway Ops** configure, and require `poma ibkr-check` to pass before trading again.
- [ ] Review any `failed`, `blocked`, `completed_with_order_issues`, timed-out, cancelled, or partial execution result manually.

## Alert expectations

- Deploy result alerts are sent by GitHub Actions.
- Dry-run and blocked runs send a summary and make no broker changes.
- Paper/live runs send an execution-start alert, broker-accepted order lifecycle/status alerts, and a final summary.
- If IBKR is unavailable before order acceptance, POMA sends one deduplicated broker-unavailable alert instead of per-ticker Created/Failed spam.
- Any non-filled order result or diagnostic message marks the run as `completed_with_order_issues` in local state.
- Normal cron checks outside the rebalance window do not send Telegram alerts to avoid noise.

No Ansible is needed for this one-host setup. Terraform startup plus Docker Compose is simpler and cheaper.
