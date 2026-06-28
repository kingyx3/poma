# Configuration

POMA is configured with environment variables. Local runs read `.env`; CI/CD renders a VM-local `.env` during deploy.

Do not commit `.env`, `.env.deploy`, `state/`, `reports`, or `logs`. The `data/market_snapshots` directory may be committed only when intentionally adding fixture/history data.

## Runtime variables

| Variable | Required | Default | Notes |
|---|---:|---|---|
| `APP_ENV` | yes | `dev` | Logical environment: `dev`, `stg`, or `prd`. |
| `TRADING_MODE` | yes | `dry_run` | Supported values: `dry_run`, `paper`, `live`. |
| `ALLOW_LIVE_TRADING` | live only | `false` | Set by deploy workflow input. Must be true for live trading. |
| `MARKET_CALENDAR` | yes | `NASDAQ` | Used by `pandas-market-calendars` for the US equity trading session. |
| `REBALANCE_AFTER_OPEN_MINUTES` | yes | `10` | Rebalance window after market open. |
| `DATA_PROVIDER` | yes | `yahoo` | Supported values: `yahoo` for real runs and `fixture` for tests/PR dry-runs. Paper/live deploy validation requires `yahoo`. |
| `YAHOO_SCREENER_LIMIT` | yahoo only | `500` | Number of largest US companies to request from Yahoo before issuer/share-class dedupe and scoring. |
| `YAHOO_SCREENER_PAGE_SIZE` | yahoo only | `250` | Yahoo screen page size. yfinance/Yahoo caps this at 250. |
| `UNIVERSE` | yes | `us_top_market_cap` | Yahoo-backed US top market-cap universe. |
| `RANK_LOOKBACK_DAYS` | yes | `90` | Rolling rank-rising-velocity window. |
| `MAX_HOLDINGS` | yes | `100` | Top 100 selected stocks are held when enough valid tickers exist. |
| `ACTIVE_STRATEGY` | yes | `rank_velocity_size_equal_weight` | The active strategy sleeve currently executed by the engine. |
| `STRATEGY_ALLOCATIONS` | yes | `rank_velocity_size_equal_weight=0.98,cash=0.02` | Named portfolio sleeves. Total must be `<= 1.0`. |
| `PORTFOLIO_VALUE_USD` | yes | `10000` | Hard cap for all strategy sleeves combined. |
| `MAX_POSITION_PCT` | yes | `0.10` | Position cap inside a strategy sleeve. |
| `MAX_TURNOVER_PCT` | yes | `1.0` | Maximum absolute trade notional divided by the active strategy sleeve capital. |
| `MIN_TRADE_NOTIONAL_USD` | yes | `25` | Suppresses tiny rebalance trades. |
| `MIN_WEIGHT_DELTA_PCT` | yes | `0.0025` | Suppresses tiny target/current weight differences. |
| `ORDER_TYPE` | yes | `limit` | Use `limit` by default. |
| `ALLOW_MARKET_ORDERS` | live market only | `false` | Explicit opt-in for live market orders. |
| `LIMIT_OFFSET_BPS` | yes | `10` | Limit price offset from reference price. |
| `MAX_ORDER_NOTIONAL_USD` | yes | `2000` | Blocks unexpectedly large orders. Must be at least `MIN_TRADE_NOTIONAL_USD`. |
| `MAX_DAILY_TRADES` | yes | `100` | Allows a full rebalance while still capping trade count. Must be at least `MAX_HOLDINGS` for full bootstrap. |
| `ORDER_STATUS_TIMEOUT_SECONDS` | yes | `60` | Time to wait for broker order status before marking follow-up needed. |
| `CANCEL_STALE_ORDERS` | yes | `true` | Request cancel when an order does not reach a terminal status in time. |
| `IBKR_HOST` | paper/live | `127.0.0.1` | IB Gateway host on the deployed host. |
| `IBKR_PORT` | paper/live | `7497` | Paper commonly uses 7497; verify your setup. |
| `IBKR_CLIENT_ID` | paper/live | `101` | Dedicated client id for this bot. |
| `IBKR_ACCOUNT` | paper/live runtime `.env` | none | The app reads this value and now refuses paper/live execution when it is unset. CI renders it from `IBKR_ACCOUNT_PAPER` for paper mode and from `IBKR_ACCOUNT` for live mode. |
| `IBKR_ACCOUNT_PAPER` | paper CI secret | none | Paper trading account id used by the deploy workflow. It is not written as a separate `.env` key. |
| `STATE_DIR` | yes | `state` | Local run-state directory. |
| `DATA_DIR` | yes | `data` | Local snapshot directory; `poma refresh-market-data` writes to `DATA_DIR/market_snapshots`. |
| `REPORT_DIR` | yes | `reports` | Local report directory. |
| `TELEGRAM_BOT_TOKEN` | yes | none | Authenticates the Telegram bot. |
| `TELEGRAM_CHAT_ID` | yes | none | Destination chat/channel/user for alerts. Discover it with the **Discover Telegram chat ID** workflow. |

`CASH_BUFFER_PCT` is intentionally not part of the current runtime contract. Cash is represented as a `cash` allocation sleeve in `STRATEGY_ALLOCATIONS`, not as a hidden buffer inside the active strategy.

## Portfolio sizing and existing account equity

POMA sizes from `PORTFOLIO_VALUE_USD`, not from total IBKR account equity. `PORTFOLIO_VALUE_USD` is the hard cap for all strategy sleeves combined. `STRATEGY_ALLOCATIONS` then splits that cap across strategies, and the total allocation must be `<= 100%`.

Example:

```text
PORTFOLIO_VALUE_USD=10000
STRATEGY_ALLOCATIONS=rank_velocity_size_equal_weight=0.60,future_strategy=0.20,cash=0.20
```

That gives the current strategy 60% of `PORTFOLIO_VALUE_USD`, gives the future strategy 20%, and leaves 20% in passive cash. The active strategy does not separately subtract a hidden cash buffer. If the account has more buying power than `PORTFOLIO_VALUE_USD`, POMA still targets only the configured cap.

Existing stock positions in the configured IBKR account are read by ticker and included in rebalance deltas. Keep unrelated/manual positions in a separate account or avoid overlapping tickers if you do not want them to affect POMA's calculations.

## Required GitHub secret shapes

| Secret | Shape / example | Notes |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `1234567890:AA...` | Token from BotFather. Required for deploy and runtime alerts. |
| `TELEGRAM_CHAT_ID` | `123456789`, `-1001234567890` | User, group, or channel id. For groups/channels this is often negative. |
| `IBKR_ACCOUNT_PAPER` | `DU1234567` | Paper account id. Required when deploying `TRADING_MODE=paper`. |
| `IBKR_ACCOUNT` | `U1234567` or broker-provided account id | Live account id. Required when deploying `TRADING_MODE=live`. |
| `IBKR_LOGIN_ID` | broker login id | Used only by IB Gateway Ops configure actions. |
| `IBKR_LOGIN_SECRET` | broker login password | Used only by IB Gateway Ops configure actions. |
| `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` | one temporary JSON key | Bootstrap only. Delete after WIF bootstrap succeeds. |

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

## CI/CD `.env` rendering and validation

The deploy workflow does not store secrets in GCP Secret Manager. Instead, it renders a VM-local `.env` file from CI defaults plus the selected GitHub Environment's required secrets, then uploads it to `/opt/poma/.env` over IAP SSH.

`ops/scripts/render_env.py` is the single renderer used by CI/CD. It reads `.env.example`, requires every key to be present in the workflow environment when `--strict-env` is used, rejects empty/placeholder values, and writes the output file with `0600` permissions. The deploy workflow resolves runtime `IBKR_ACCOUNT` before rendering: paper uses `IBKR_ACCOUNT_PAPER`, live uses `IBKR_ACCOUNT`, and dry-run prefers `IBKR_ACCOUNT_PAPER` before falling back to `IBKR_ACCOUNT`.

After rendering, `ops/scripts/validate_runtime_config.py` loads `.env.deploy` and fails before Terraform/app deploy when runtime invariants are unsafe: paper/live missing `IBKR_ACCOUNT`, live missing `ALLOW_LIVE_TRADING=true`, strategy allocations above 100%, paper/live using `fixture`, or order guard values that cannot support the configured bootstrap.

The deploy workflow supplies deterministic defaults for every non-secret `.env.example` key. Do not create GitHub Environment Variables for the normal production path.

## Generated deploy config

Bootstrap apply writes generated, non-secret GCP deployment identifiers to `ops/deploy/environments/<env>.env`. Commit that generated file; do not convert its keys into GitHub Environment Variables.

## Safety notes

- `TRADING_MODE=live` is blocked unless `ALLOW_LIVE_TRADING=true`.
- `ORDER_TYPE=market` in live mode is blocked unless `ALLOW_MARKET_ORDERS=true`.
- `MAX_POSITION_PCT`, `MAX_TURNOVER_PCT`, `STRATEGY_ALLOCATIONS`, and min-trade thresholds are validated at startup.
- The deploy workflow renders paper runtime `IBKR_ACCOUNT` from `IBKR_ACCOUNT_PAPER`. Live mode uses `IBKR_ACCOUNT`.
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are required so every deployed run has alerting configured.
