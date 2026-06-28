# Production readiness checklist

This repo is production-ready for **dry-run deployment** once CI is green and the deploy smoke test passes. Treat **paper trading** as the next validation stage. Do not use live money until the live gates below are complete.

## Required before paper mode

- [ ] Bootstrap WIF using only the temporary `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` secret.
- [ ] Delete the bootstrap secret and disable/delete the temporary GCP key after bootstrap.
- [ ] Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- [ ] Add `IBKR_LOGIN_ID` and `IBKR_LOGIN_SECRET` as GitHub Environment Secrets.
- [ ] Deploy with `TRADING_MODE=dry_run` first.
- [ ] Confirm the deploy smoke test created a new `reports/rebalance-*.json` file.
- [ ] Run **IB Gateway Ops** with `action=configure-paper`.
- [ ] Approve IBKR mobile authentication if prompted.
- [ ] Confirm `ibgateway.service` is active after reboot.
- [ ] Confirm `127.0.0.1:7497` is reachable on the VM.
- [ ] Add `IBKR_ACCOUNT_PAPER` before switching to `TRADING_MODE=paper`.
- [ ] Confirm `PORTFOLIO_VALUE_USD` matches the intended paper strategy sleeve size, not necessarily total IBKR account equity.

## Required before live mode

- [ ] Backtest against QQQ/QQQM or another explicit benchmark with historical constituents and historical market caps.
- [ ] Validate data-provider endpoints and field meanings.
- [ ] Run at least one full week in `dry_run`.
- [ ] Run at least one full week in `paper`.
- [ ] Review IBKR orders/fills against POMA reports.
- [ ] Confirm order type policy and fractional-share behavior in the IBKR account.
- [ ] Confirm tax, FX, commission, and slippage assumptions.
- [ ] Add `IBKR_ACCOUNT` before switching to `TRADING_MODE=live`.
- [ ] Run **IB Gateway Ops** with `action=configure-live`.
- [ ] Set `ALLOW_LIVE_TRADING=true` intentionally.
- [ ] Manually review the latest rebalance report before the first live run.

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
- [ ] Review any `failed`, blocked, timed-out, cancelled, or partial execution result manually.

No Ansible is needed for this one-host setup. Terraform startup plus Docker Compose is simpler and cheaper.
