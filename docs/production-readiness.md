# Production readiness checklist

## Strategy and data

- [ ] Confirm Nasdaq-100 constituent endpoint and market-cap fields with your selected data plan.
- [ ] Validate historical constituent availability to avoid survivorship bias in backtests.
- [ ] Compare strategy performance against QQQ/QQQM after fees, FX, withholding tax, and slippage.
- [ ] Run dry-run reports for at least one full rebalance cycle.
- [ ] Confirm expected behavior for corporate actions, delistings, mergers, and ticker changes.

## Execution

- [ ] Configure IBKR paper account.
- [ ] Run IB Gateway on a tiny VPS with OS-level auto restart.
- [ ] Validate reconnect behavior after VPS reboot and IBKR session timeout.
- [ ] Start with `TRADING_MODE=paper`.
- [ ] Add a manual approval step before enabling `live`.
- [ ] Confirm order type policy: market-on-open, limit, or time-sliced execution.
- [ ] Confirm fractional share support for your account and order routing.

## Risk controls

- [ ] Keep `MAX_POSITION_PCT` below your concentration tolerance.
- [ ] Keep `MAX_TURNOVER_PCT` low enough to avoid noisy churn.
- [ ] Set a portfolio-level daily loss or kill-switch outside this scaffold before live trading.
- [ ] Keep `CASH_BUFFER_PCT` high enough to avoid rejected orders.
- [ ] Alert on zero targets, missing market caps, oversized orders, and order/fill mismatch.

## Security

- [ ] Use GitHub Actions Workload Identity Federation, not static GCP keys.
- [ ] Store runtime secrets only in Secret Manager.
- [ ] Restrict executor endpoint by API key and firewall/IP allowlist where possible.
- [ ] Do not expose IB Gateway directly to the internet.
- [ ] Rotate `EXECUTOR_API_KEY` periodically.

## Observability

- [ ] Store every rebalance report.
- [ ] Store submitted orders, broker responses, and fills.
- [ ] Alert on failed jobs, blocked execution, and missing data.
- [ ] Review Cloud Run logs after every production run.

## Cost controls

- [ ] Use Cloud Run Jobs rather than always-on compute for strategy decisions.
- [ ] Keep only the IBKR gateway/executor VPS always-on.
- [ ] Set Artifact Registry cleanup policy.
- [ ] Avoid creating unnecessary Secret Manager versions in deployment workflows.
