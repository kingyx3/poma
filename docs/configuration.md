# Configuration

## Required files on the host

- `.env` — runtime config and secrets.
- `state/` — local state volume.
- `reports/` — local rebalance reports.
- `logs/` — cron logs if using the sample crontab.

Do not commit `.env`, `.env.deploy`, `state/`, `reports`, or `logs`.

## Required external setup

- IBKR account with paper trading enabled first.
- IB Gateway running on the same host.
- Data-provider subscription that supports Nasdaq-100 constituents, market caps, and prices if using `DATA_PROVIDER=fmp`.
- Telegram bot and chat ID for mandatory run alerts.

## Environment variables

| Variable | Required | Default | Notes |
|---|---:|---|---|
| `APP_ENV` | yes | `development` | Label only. Use `production` on the deployed host. |
| `TRADING_MODE` | yes | `dry_run` | `dry_run`, `paper`, or `live`. |
| `ALLOW_LIVE_TRADING` | live only | `false` | Must be true for live trading. |
| `MARKET_CALENDAR` | yes | `NASDAQ` | Used by `pandas-market-calendars`. |
| `REBALANCE_AFTER_OPEN_MINUTES` | yes | `10` | Rebalance window after market open. |
| `DATA_PROVIDER` | yes | `fixture` | Use `fmp` only after validating endpoint output. |
| `FMP_API_KEY` | `fmp` only | empty | Store as a GitHub Secret for CI/CD deployment. |
| `FMP_BASE_URL` | no | `https://financialmodelingprep.com/stable` | Override if your plan uses different endpoints. |
| `UNIVERSE` | yes | `nasdaq100` | This scaffold is Nasdaq-100 focused. |
| `RANK_LOOKBACK_DAYS` | yes | `90` | Rolling rank-comparison window in days. |
| `MAX_HOLDINGS` | yes | `30` | Hold only the top names by rank improvement score. |
| `PORTFOLIO_VALUE_USD` | yes | `10000` | Used for target notional generation. |
| `CASH_BUFFER_PCT` | yes | `0.02` | Avoids accidental over-investment. |
| `MAX_POSITION_PCT` | yes | `0.10` | Single-name concentration cap. |
| `MAX_TURNOVER_PCT` | yes | `0.35` | Blocks excessive rebalance churn. |
| `MIN_TRADE_NOTIONAL_USD` | yes | `25` | Avoids tiny uneconomic trades. |
| `MIN_WEIGHT_DELTA_PCT` | yes | `0.01` | Avoids churn from tiny target changes. |
| `ORDER_TYPE` | yes | `limit` | Use `limit` by default. |
| `ALLOW_MARKET_ORDERS` | live market only | `false` | Explicit opt-in for live market orders. |
| `LIMIT_OFFSET_BPS` | yes | `10` | Limit price offset from reference price. |
| `MAX_ORDER_NOTIONAL_USD` | yes | `2000` | Blocks unexpectedly large orders. |
| `MAX_DAILY_TRADES` | yes | `30` | Blocks unexpectedly high trade count. |
| `ORDER_STATUS_TIMEOUT_SECONDS` | yes | `60` | Time to wait for broker order status before marking follow-up needed. |
| `CANCEL_STALE_ORDERS` | yes | `true` | Request cancel when an order does not reach a terminal status in time. |
| `IBKR_HOST` | paper/live | `127.0.0.1` | IB Gateway host on the deployed host. |
| `IBKR_PORT` | paper/live | `7497` | Paper commonly uses 7497; verify your setup. |
| `IBKR_CLIENT_ID` | paper/live | `101` | Dedicated client id for this bot. |
| `IBKR_ACCOUNT` | paper/live | empty | Store as a GitHub Secret for CI/CD deployment. |
| `STATE_DIR` | yes | `state` | Local state directory. |
| `REPORT_DIR` | yes | `reports` | Local report directory. |
| `TELEGRAM_BOT_TOKEN` | yes | none | Required for startup; store as a GitHub Secret for CI/CD deployment. |
| `TELEGRAM_CHAT_ID` | yes | none | Required for startup; store as a GitHub Secret for CI/CD deployment. |

## CI/CD `.env` rendering

The deploy workflow does not store secrets in GCP Secret Manager. Instead, it renders a VM-local `.env` file from GitHub Variables and Secrets, then uploads it to `/opt/poma/.env` over IAP SSH.

`ops/scripts/render_env.py` is the single renderer used by CI/CD. It reads `.env.example`, requires every key to be present in the workflow environment when `--strict-env` is used, rejects placeholder Telegram values, and writes the output file with `0600` permissions.

The bootstrap workflow generates the non-secret GitHub Variables required by the deploy workflow. After bootstrap, the only always-required human-provided runtime secrets are `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

Optional runtime secrets:

- `FMP_API_KEY` when `DATA_PROVIDER=fmp`.
- `IBKR_ACCOUNT` when `TRADING_MODE=paper` or `TRADING_MODE=live`.

See [`deployment-gcp-free-tier.md`](deployment-gcp-free-tier.md) for the full bootstrap and deploy sequence.

## GitHub secrets and variables

First bootstrap requires only `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY`. Delete it after WIF bootstrap succeeds.

No Artifact Registry, Secret Manager, or long-lived GCP JSON key is required for normal deploys.
