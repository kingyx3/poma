# Production readiness checklist

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

## Cost controls

- [ ] Do not enable cloud deploy workflows unless you deliberately reintroduce cloud infra.
- [ ] Do not push images to Artifact Registry for this personal deployment.
- [ ] Do not create recurring Secret Manager versions for this bot.
- [ ] Keep reports/logs locally and prune them periodically.
- [ ] Use one VPS and local Docker builds.

## Risk controls

- [ ] Keep `TRADING_MODE=dry_run` initially.
- [ ] Move to `paper` before `live`.
- [ ] Set `ALLOW_LIVE_TRADING=true` only when intentionally going live.
- [ ] Keep `MAX_POSITION_PCT` below your concentration tolerance.
- [ ] Keep `MAX_TURNOVER_PCT` low enough to avoid noisy churn.
- [ ] Keep `MIN_WEIGHT_DELTA_PCT` high enough to avoid unnecessary daily trades.
- [ ] Review every report before live mode.

## Alerts and reconciliation

- [ ] Configure Telegram alerts.
- [ ] Save every rebalance report.
- [ ] Review IBKR orders/fills after every paper/live run.
- [ ] Add a fill reconciliation module before scaling capital materially.
