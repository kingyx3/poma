# Production readiness checklist

This repo is designed to be production-ready for paper trading once CI is green and the VPS dry-run works. For live money, complete the live-trading gates below.

## Before any live trading

- [ ] Backtest against QQQ/QQQM with historical constituents and historical market caps.
- [ ] Validate the data-provider endpoints and field meanings.
- [ ] Run at least one full week in `dry_run`.
- [ ] Run at least one full week in `paper`.
- [ ] Confirm IB Gateway reconnect behavior after VPS reboot.
- [ ] Confirm order type policy and fractional-share behavior in your IBKR account.
- [ ] Confirm tax, FX, commission, and slippage assumptions.

## VPS setup

- [ ] Use one small Ubuntu VPS.
- [ ] Run IB Gateway supervised on the VPS.
- [ ] Run `docker compose run --rm poma monitor` every 5 minutes via cron.
- [ ] Keep `.env` local and readable only by the service user.
- [ ] Rotate IBKR/data-provider credentials when needed.
- [ ] Run `ops/scripts/bootstrap_vps.sh` or manually perform equivalent setup.
- [ ] Run `ops/scripts/deploy.sh` for local build and dry-run smoke test.

## Cost controls

- [ ] Do not enable cloud deploy workflows unless you deliberately reintroduce cloud infra.
- [ ] Do not push images to Artifact Registry for this personal deployment.
- [ ] Do not create recurring Secret Manager versions for this bot.
- [ ] Keep reports/logs locally and prune them periodically.
- [ ] Use one VPS and local Docker builds.

## Risk controls

- [ ] Keep `TRADING_MODE=dry_run` initially.
- [ ] Move to `paper` before `live`.
- [ ] Use `ORDER_TYPE=limit` by default.
- [ ] Keep `ALLOW_MARKET_ORDERS=false` unless explicitly intentional.
- [ ] Set `ALLOW_LIVE_TRADING=true` only when intentionally going live.
- [ ] Keep `MAX_ORDER_NOTIONAL_USD` low for early live tests.
- [ ] Keep `MAX_DAILY_TRADES` below your operational tolerance.
- [ ] Keep `MAX_POSITION_PCT` below your concentration tolerance.
- [ ] Keep `MAX_TURNOVER_PCT` low enough to avoid noisy churn.
- [ ] Keep `MIN_WEIGHT_DELTA_PCT` high enough to avoid unnecessary daily trades.
- [ ] Review every report before live mode.

## Alerts and reconciliation

- [ ] Configure Telegram credentials before any run.
- [ ] Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; the app fails fast without them.
- [ ] Send a test alert before enabling cron.
- [ ] Save every rebalance report.
- [ ] Review IBKR orders/fills after every paper/live run.
- [ ] Treat any `failed` state as manual-review only.
- [ ] Compare the report's `execution_results` against IBKR activity before scaling capital.

## Do we need Ansible?

No for this one-VPS setup. A shell bootstrap script plus Docker Compose is simpler and cheaper.

Use Ansible later only if you need repeatable provisioning across multiple VPS hosts, disaster-recovery rebuilds, or stricter configuration drift control.
