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
- GitHub Environment Secrets for IB Gateway login configuration: `IBKR_LOGIN_ID` and `IBKR_LOGIN_SECRET`.
- FMP API key that supports market caps and prices when using `data_provider=fmp` in the deploy workflow.
- Telegram bot token and chat ID for mandatory run alerts.
- Tailscale tailnet and reusable or ephemeral auth key for secure VPS access.

## Environment variables

| Variable | Required | Default | Notes |
|---|---:|---|---|
| `APP_ENV` | yes | selected GitHub Environment in CI | Label only. CI sets this to `dev`, `stg`, or `prd`. |
| `TRADING_MODE` | yes | `dry_run` | Set by deploy workflow input: `dry_run`, `paper`, or `live`. |
| `ALLOW_LIVE_TRADING` | live only | `false` | Set by deploy workflow input. Must be true for live trading. |
| `MARKET_CALENDAR` | yes | `NASDAQ` | Used by `pandas-market-calendars`. |
| `REBALANCE_AFTER_OPEN_MINUTES` | yes | `10` | Rebalance window after market open. |
| `DATA_PROVIDER` | yes | `fixture` | Set by deploy workflow input. Use `fmp` only after validating endpoint output. |
| `FMP_API_KEY` | yes | none | Mandatory deploy secret. Required even for fixture deploys so production can switch safely. |
| `FMP_BASE_URL` | no | `https://financialmodelingprep.com/stable` | Stable FMP base for market-cap and quote endpoints. Constituent lookup falls back to FMP legacy v3 when stable constituent endpoints are plan-gated. |
| `UNIVERSE` | yes | `sp500` | Default strategy ranks S&P 500 constituents. Supported FMP universes: `sp500`, `nasdaq100`. |
| `RANK_LOOKBACK_DAYS` | yes | `90` | Rolling rank-comparison window in days. |
| `MAX_HOLDINGS` | yes | `100` | Hold only the top names by rank improvement score. |
| `PORTFOLIO_VALUE_USD` | yes | `10000` | Used for target notional generation. |
| `CASH_BUFFER_PCT` | yes | `0.02` | Avoids accidental over-investment. |
| `MAX_POSITION_PCT` | yes | `0.10` | Single-name concentration cap. |
| `MAX_TURNOVER_PCT` | yes | `0.35` | Blocks excessive rebalance churn. |
| `MIN_TRADE_NOTIONAL_USD` | yes | `25` | Avoids tiny uneconomic trades. |
| `MIN_WEIGHT_DELTA_PCT` | yes | `0.0025` | Avoids churn from tiny target changes while allowing smaller top-100 positions. |
| `ORDER_TYPE` | yes | `limit` | Use `limit` by default. |
| `ALLOW_MARKET_ORDERS` | live market only | `false` | Explicit opt-in for live market orders. |
| `LIMIT_OFFSET_BPS` | yes | `10` | Limit price offset from reference price. |
| `MAX_ORDER_NOTIONAL_USD` | yes | `2000` | Blocks unexpectedly large orders. |
| `MAX_DAILY_TRADES` | yes | `100` | Allows a full top-100 rebalance while still capping trade count. |
| `ORDER_STATUS_TIMEOUT_SECONDS` | yes | `60` | Time to wait for broker order status before marking follow-up needed. |
| `CANCEL_STALE_ORDERS` | yes | `true` | Request cancel when an order does not reach a terminal status in time. |
| `IBKR_HOST` | paper/live | `127.0.0.1` | IB Gateway host on the deployed host. |
| `IBKR_PORT` | paper/live | `7497` | Paper commonly uses 7497; verify your setup. |
| `IBKR_CLIENT_ID` | paper/live | `101` | Dedicated client id for this bot. |
| `IBKR_ACCOUNT` | runtime `.env` | none | The app reads this value. CI renders it from `IBKR_ACCOUNT_PAPER` for paper mode and from `IBKR_ACCOUNT` for live mode. |
| `IBKR_ACCOUNT_PAPER` | paper CI secret | none | Paper trading account id used by the deploy workflow. It is not written as a separate `.env` key. |
| `STATE_DIR` | yes | `state` | Local state directory. |
| `REPORT_DIR` | yes | `reports` | Local report directory. |
| `TELEGRAM_BOT_TOKEN` | yes | none | Authenticates the Telegram bot. |
| `TELEGRAM_CHAT_ID` | yes | none | Destination chat/channel/user for alerts. Discover it with the **Discover Telegram chat ID** workflow. |

## Telegram alert config

`TELEGRAM_BOT_TOKEN` is necessary but not sufficient for outbound alerts. The bot token authenticates the bot to Telegram. `TELEGRAM_CHAT_ID` tells Telegram where to send the message. The current implementation calls Telegram `sendMessage` with both values, so both are required for reliable deploy-time and runtime alerts.

Use **Discover Telegram chat ID** in GitHub Actions to read the chat ID. Start the workflow first, then send a fresh `/start` or message to the bot or target group/channel while the workflow is polling. Messages sent before the helper starts may already have been consumed by Telegram and may not appear in `getUpdates`.

## IBKR Gateway login config

`IBKR_LOGIN_ID` and `IBKR_LOGIN_SECRET` are GitHub Environment Secrets consumed only by the **IB Gateway Ops** workflow for `configure-paper` and `configure-live`. They are broker login credentials, not app `.env` keys.

The workflow sends these values to `sudo poma-configure-ibc` over IAP SSH stdin so IBC can create VM-local Gateway config. Do not add the broker login credentials to Terraform, VM metadata, `.env`, or repository files. See [`adr/0001-ibkr-credentials-in-github-secrets.md`](adr/0001-ibkr-credentials-in-github-secrets.md).

## CI/CD `.env` rendering

The deploy workflow does not store secrets in GCP Secret Manager. Instead, it renders a VM-local `.env` file from CI defaults plus the selected GitHub Environment's required secrets, then uploads it to `/opt/poma/.env` over IAP SSH.

`ops/scripts/render_env.py` is the single renderer used by CI/CD. It reads `.env.example`, requires every key to be present in the workflow environment when `--strict-env` is used, rejects empty/placeholder values, and writes the output file with `0600` permissions. The deploy workflow resolves runtime `IBKR_ACCOUNT` before rendering: paper uses `IBKR_ACCOUNT_PAPER`, live uses `IBKR_ACCOUNT`, and dry-run prefers `IBKR_ACCOUNT_PAPER` before falling back to `IBKR_ACCOUNT`.

The deploy workflow supplies deterministic defaults for every non-secret `.env.example` key. Do not create GitHub Environment Variables for the normal production path.

## Generated deploy config

Bootstrap apply commits non-secret GCP deploy identifiers to:

```text
ops/deploy/environments/<env>.env
```

The generated file contains:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT_EMAIL`
- `TF_STATE_BUCKET`

Deploy reads this file and derives `GCP_PROJECT_ID` from `GCP_SERVICE_ACCOUNT_EMAIL`. These values are not secrets and should not be duplicated as GitHub Environment Variables.

## Minimal GitHub secrets and variables

GitHub Environment Variables required for normal production deploys: **none**.

First bootstrap requires only the temporary `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` GitHub Environment secret. Delete it after WIF bootstrap succeeds.

Runtime/deploy secrets:

- `TAILSCALE_AUTHKEY` when deploy input `tailscale_enabled=true`.
- `FMP_API_KEY`.
- `IBKR_ACCOUNT_PAPER` for paper deploys, and preferred for dry-run deploy rendering.
- `IBKR_ACCOUNT` for live deploys.
- `TELEGRAM_BOT_TOKEN`.
- `TELEGRAM_CHAT_ID`.

Gateway operation secrets:

- `IBKR_LOGIN_ID` for `configure-paper` and `configure-live`.
- `IBKR_LOGIN_SECRET` for `configure-paper` and `configure-live`.

`TAILSCALE_AUTHKEY` is used only during deploy apply. It is copied to the VM over IAP, consumed by `tailscale up`, then deleted from both the runner and VM. It is not written to Terraform state, VM metadata, or the app `.env`.

No Artifact Registry, Secret Manager, or long-lived GCP JSON key is required for normal deploys. Manually delete any old GitHub Environment Variables left over from earlier bootstrap runs; the current workflows no longer read or manage them.
