# Configuration

## Required files on the host

- `.env` — runtime config and secrets.
- `data/` — repo-local market data snapshots used for rank-history comparisons.
- `state/` — local run state volume.
- `reports/` — local rebalance reports.
- `logs/` — cron logs if using the sample crontab.

Do not commit `.env`, `.env.deploy`, `state/`, `reports`, or `logs`. The `data/market_snapshots/` directory is intentionally repo-local so you can inspect, back up, or commit point-in-time snapshots when desired.

## Required external setup

- IBKR account with paper trading enabled first.
- IB Gateway running on the same host.
- GitHub Environment Secrets for IB Gateway login configuration: `IBKR_LOGIN_ID` and `IBKR_LOGIN_SECRET`.
- Market-data provider: `DATA_PROVIDER=yahoo` uses yfinance and requires no API key.
- Telegram bot token and chat ID for mandatory run alerts.
- Operator access to the VM is via Google Cloud IAP SSH (`gcloud compute ssh --tunnel-through-iap`); no extra VPN/secret is required.

## Environment variables

| Variable | Required | Default | Notes |
|---|---:|---|---|
| `APP_ENV` | yes | selected GitHub Environment in CI | Label only. CI sets this to `dev`, `stg`, or `prd`. |
| `TRADING_MODE` | yes | `dry_run` | Set by deploy workflow input: `dry_run`, `paper`, or `live`. |
| `ALLOW_LIVE_TRADING` | live only | `false` | Set by deploy workflow input. Must be true for live trading. |
| `MARKET_CALENDAR` | yes | `NASDAQ` | Used by `pandas-market-calendars` for the US equity trading session. |
| `REBALANCE_AFTER_OPEN_MINUTES` | yes | `10` | Rebalance window after market open. |
| `DATA_PROVIDER` | yes | `yahoo` | Supported values: `yahoo` for real runs and `fixture` for tests/PR dry-runs. |
| `YAHOO_SCREENER_LIMIT` | yahoo only | `500` | Number of largest US companies to request from Yahoo before issuer/share-class dedupe and scoring. |
| `YAHOO_SCREENER_PAGE_SIZE` | yahoo only | `250` | Yahoo screen page size. yfinance/Yahoo caps this at 250. |
| `UNIVERSE` | yes | `us_top_market_cap` | Yahoo-backed US top market-cap universe. |
| `RANK_LOOKBACK_DAYS` | yes | `90` | Rolling rank-comparison window in days. |
| `MAX_HOLDINGS` | yes | `100` | Hold the top company stocks by combined market-cap-size + rank-rising-velocity score, equal-weighted. |
| `ACTIVE_STRATEGY` | yes | `rank_velocity_size_equal_weight` | Trading strategy sleeve to run. This must exist in `STRATEGY_ALLOCATIONS`. |
| `STRATEGY_ALLOCATIONS` | yes | `rank_velocity_size_equal_weight=0.98,cash=0.02` | Comma-separated strategy capital sleeves. Values are percentages of `PORTFOLIO_VALUE_USD`; `0.98` means 98%. The passive `cash` sleeve counts toward the portfolio cap but does not generate trades. The total must be `<= 1.0`. |
| `PORTFOLIO_VALUE_USD` | yes | `10000` | Hard cap for total capital managed across all strategy sleeves. POMA does not auto-size to total IBKR account equity. |
| `MAX_POSITION_PCT` | yes | `0.10` | Single-name concentration cap within the active strategy sleeve. |
| `MAX_TURNOVER_PCT` | yes | `1.0` | Allows the first paper/live bootstrap allocation while still blocking impossible >100% sleeve turnover. Lower it after the initial portfolio is established if desired. |
| `MIN_TRADE_NOTIONAL_USD` | yes | `25` | Avoids tiny uneconomic trades. |
| `MIN_WEIGHT_DELTA_PCT` | yes | `0.0025` | Avoids churn from tiny target changes while allowing 1% top-100 target weights. |
| `ORDER_TYPE` | yes | `limit` | Use `limit` by default. |
| `ALLOW_MARKET_ORDERS` | live market only | `false` | Explicit opt-in for live market orders. |
| `LIMIT_OFFSET_BPS` | yes | `10` | Limit price offset from reference price. |
| `MAX_ORDER_NOTIONAL_USD` | yes | `2000` | Blocks unexpectedly large orders. |
| `MAX_DAILY_TRADES` | yes | `100` | Allows a full rebalance while still capping trade count. |
| `ORDER_STATUS_TIMEOUT_SECONDS` | yes | `60` | Time to wait for broker order status before marking follow-up needed. |
| `CANCEL_STALE_ORDERS` | yes | `true` | Request cancel when an order does not reach a terminal status in time. |
| `IBKR_HOST` | paper/live | `127.0.0.1` | IB Gateway host on the deployed host. |
| `IBKR_PORT` | paper/live | `7497` | Paper commonly uses 7497; verify your setup. |
| `IBKR_CLIENT_ID` | paper/live | `101` | Dedicated client id for this bot. |
| `IBKR_ACCOUNT` | runtime `.env` | none | The app reads this value. CI renders it from `IBKR_ACCOUNT_PAPER` for paper mode and from `IBKR_ACCOUNT` for live mode. |
| `IBKR_ACCOUNT_PAPER` | paper CI secret | none | Paper trading account id used by the deploy workflow. It is not written as a separate `.env` key. |
| `STATE_DIR` | yes | `state` | Local run-state directory. |
| `DATA_DIR` | yes | `data` | Local snapshot directory; `poma refresh-market-data` writes to `DATA_DIR/market_snapshots`. |
| `REPORT_DIR` | yes | `reports` | Local report directory. |
| `TELEGRAM_BOT_TOKEN` | yes | none | Authenticates the Telegram bot. |
| `TELEGRAM_CHAT_ID` | yes | none | Destination chat/channel/user for alerts. Discover it with the **Discover Telegram chat ID** workflow. |

## Portfolio sizing and existing account equity

POMA sizes from `PORTFOLIO_VALUE_USD`, not from total IBKR account equity. `PORTFOLIO_VALUE_USD` is the hard cap for all strategy sleeves combined. `STRATEGY_ALLOCATIONS` then splits that cap across strategies, and the total allocation must be `<= 100%`.

With the defaults, the active trading strategy is `rank_velocity_size_equal_weight` and it receives `98%` of `PORTFOLIO_VALUE_USD`. The passive `cash` sleeve receives the remaining `2%` and does not create orders. A paper or live account with more than $10,000 therefore targets a $9,800 rank-strategy sleeve and a $200 cash sleeve by default. Raise `PORTFOLIO_VALUE_USD` intentionally if you want POMA to manage a larger total sleeve.

For future strategies, add them to `STRATEGY_ALLOCATIONS`, for example:

```text
STRATEGY_ALLOCATIONS=rank_velocity_size_equal_weight=0.60,future_strategy=0.38,cash=0.02
```

That gives the current strategy 60% of `PORTFOLIO_VALUE_USD`, gives the future strategy 38%, reserves 2% as cash, and still caps total managed capital at `PORTFOLIO_VALUE_USD`.

Existing stock positions in the configured IBKR account are read by ticker and included in rebalance deltas. Keep unrelated/manual positions in a separate account or avoid overlapping tickers if you do not want them to affect POMA's calculations.

## Market data snapshots and ranking

Run this before the first rank-rising-velocity rebalance, or let the first rebalance save only the current snapshot and fall back to current market-cap selection until lookback history exists:

```bash
poma refresh-market-data
```

Current top-500 membership comes from `yfinance.screen()` sorted by `intradaymarketcap`. The strategy deduplicates share classes before ranking companies: issuer/name metadata is preferred when available, and exact duplicate market-cap buckets are used as a fallback when issuer metadata is unavailable. Historical market caps are estimated from Yahoo close prices multiplied by the current share count from the latest snapshot.

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

- `IBKR_ACCOUNT_PAPER` for paper deploys, and preferred for dry-run deploy rendering.
- `IBKR_ACCOUNT` for live deploys.
- `TELEGRAM_BOT_TOKEN`.
- `TELEGRAM_CHAT_ID`.

Gateway operation secrets:

- `IBKR_LOGIN_ID` for `configure-paper` and `configure-live`.
- `IBKR_LOGIN_SECRET` for `configure-paper` and `configure-live`.

Operator access to the VM uses Google Cloud IAP SSH and needs no stored access secret. The VM's only ingress is IAP SSH (TCP 22 from `35.235.240.0/20`); reach a shell or tunnel the IB Gateway VNC port with `gcloud compute ssh <vm> --zone <zone> --tunnel-through-iap -- -L 5900:127.0.0.1:5900`.

No Artifact Registry, Secret Manager, or long-lived GCP JSON key is required for normal deploys. Manually delete any old GitHub Environment Variables left over from earlier bootstrap runs; the current workflows no longer read or manage them.
