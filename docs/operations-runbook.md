# Operations runbook

This runbook is the day-to-day checklist for operating POMA in `dry_run`, `paper`, and `live`.

## Order lifecycle follow-up

Reaching `PreSubmitted` or `Submitted` only means the broker accepted the order; a working limit order can sit unfilled. `ops/cron/poma.cron` schedules `poma reconcile-orders` every 2 minutes so working orders are followed up after the rebalance process exits.

The app replaces a still-unfilled order once after `REPLACE_AFTER_SECONDS`, requests cancel after `CANCEL_AFTER_SECONDS` if it remains unfilled, and sends a Telegram alert on lifecycle changes.

The next scheduled rebalance also checks the order ledger for anything still open from a prior session, or from a different run within the same session, before planning. That guard refreshes broker open-order state before deciding whether to block.

If POMA had already requested a cancel and the broker no longer reports the order open, the local ledger is marked terminal `cancelled`. If the broker no longer reports an order open without a known POMA cancel request, it is marked terminal as externally resolved.

The manual Reconcile Orders GitHub workflow has been removed. Normal timeout, expiry, and cancel state should be handled by the scheduled lifecycle job and by the monitor pre-check.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Limit order accepted but never fills | Working limit price never crossed the market | Confirm the scheduled lifecycle job is installed and running; it replaces once then requests cancel per timeout settings. |
| New rebalance blocked by open orders from another run/session | The broker still reports those orders open after POMA refreshed state | Review the blocked report and broker activity before retrying. |
| Buy orders show `BuyingPowerBlocked` | Refreshed broker cash did not cover the repriced buy limit cash requirement | Review sell fills and rerun once cash confirms, rather than assuming sell proceeds. |
