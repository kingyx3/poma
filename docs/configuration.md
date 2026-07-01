# Configuration

POMA is configured with environment variables. Local runs read `.env`; CI/CD renders a VM-local `.env` during deploy.

Do not commit `.env`, `.env.deploy`, `state/`, `reports`, or `logs`. The `data/market_snapshots` directory may be committed only when intentionally adding fixture/history data.

## Runtime variables

| Variable | Required | Default | Notes |
|---|---:|---|---|
| `APP_ENV` | yes | `development` | Local/runtime label. CI/CD deploys render `dev`, `stg`, or `prd` from the selected GitHub Environment. |
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
| `STRATEGY_ALLOCATIONS` | yes | `rank_velocity_size_equal_weight=0.98,cash=0.02` | Named portfolio sleeves. Every non-`cash` name must be a registered strategy (see `docs/strategy-contract.md`); the engine executes every sleeve with a positive allocation, not just one. Total must be `<= 1.0`. |
| `DRY_RUN_PORTFOLIO_VALUE_USD` | yes | `10000` | Portfolio value used only in `dry_run` mode. Paper/live size against broker account equity instead; see `MANAGED_CAP_MODE`. |
| `MANAGED_CAP_MODE` | yes | `broker_total` | `broker_total` sizes paper/live sleeves off the full broker account value. `min_of_broker_total_and_cap` sizes off `min(broker account value, MANAGED_CAP_USD)`. |
| `MANAGED_CAP_USD` | `min_of_broker_total_and_cap` only | `0` | Hard cap on managed capital when `MANAGED_CAP_MODE=min_of_broker_total_and_cap`. Must be greater than 0 in that mode; unused (and may stay `0`) under `broker_total`. |
| `MAX_POSITION_PCT` | yes | `0.10` | Position cap inside a strategy sleeve. |
| `MAX_TURNOVER_PCT` | yes | `1.0` | Maximum absolute trade notional divided by the active strategy sleeve capital. |
| `MIN_TRADE_NOTIONAL_USD` | yes | `25` | Suppresses tiny rebalance trades. |
| `MIN_WEIGHT_DELTA_PCT` | yes | `0.0025` | Suppresses tiny target/current weight differences. |
| `ORDER_TYPE` | yes | `limit` | Use `limit` by default. |
| `ALLOW_MARKET_ORDERS` | live market only | `false` | Explicit opt-in for live market orders. |
| `LIMIT_OFFSET_BPS` | yes | `10` | Limit price offset from reference price. |
| `MAX_ORDER_NOTIONAL_USD` | yes | `2000` | Blocks unexpectedly large orders. Must be at least `MIN_TRADE_NOTIONAL_USD`. |
| `MAX_DAILY_TRADES` | yes | `100` | Allows a full rebalance while still capping trade count. Must be at least `MAX_HOLDINGS` for full bootstrap. |
| `NON_FRACTIONAL_TICKERS` | no | `` (empty) | Comma-separated tickers to round down to whole shares instead of sending fractional orders. Every other ticker defaults to fractional-friendly sizing, since a small managed cap depends on fractional quantities to hit target weights. Only list tickers confirmed to reject fractional orders at the broker. |
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

Paper/live rebalances size from the configured IBKR account's actual equity (USD cash + current portfolio value), read fresh before every rebalance, not from a static config number. `MANAGED_CAP_MODE=broker_total` (the default) uses that full broker equity. `MANAGED_CAP_MODE=min_of_broker_total_and_cap` instead uses `min(broker equity, MANAGED_CAP_USD)`, which is useful when the IBKR account holds more than POMA should manage. `DRY_RUN_PORTFOLIO_VALUE_USD` is used only in `dry_run` mode, where there is no broker to read from.

`STRATEGY_ALLOCATIONS` splits whichever value is resolved above across named sleeves, and the total allocation must be `<= 100%`. Every allocated non-`cash` sleeve is executed by the engine, not just one:

```text
STRATEGY_ALLOCATIONS=rank_velocity_size_equal_weight=0.60,future_strategy=0.20,cash=0.20
```

That runs `rank_velocity_size_equal_weight` on 60% of the resolved portfolio value, runs `future_strategy` on 20%, and leaves 20% in passive cash. No sleeve separately subtracts a hidden cash buffer. If two sleeves both target the same ticker, POMA combines their targets into a single portfolio-level order rather than trading them independently.

Existing stock positions in the configured IBKR account are read by ticker and included in rebalance deltas. Keep unrelated/manual positions in a separate account or avoid overlapping tickers if you do not want them to affect POMA's calculations.

## Required GitHub secret shapes

| Secret | Shape / example | Notes |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `1234567890:AA...` | Token from BotFather. Required for deploy and runtime alerts. |
| `TELEGRAM_CHAT_ID` | `123456789`, `-1001234567890` | User, group, or channel id. For groups/channels this is often negative. |
| `IBKR_ACCOUNT_PAPER` | `DU1234567` | Paper account id. Required when deploying `TRADING_MODE=paper`. |
| `IBKR_ACCOUNT` | `U1234567` or broker-provided account id | Live account id. Required when deploying `TRADING_MODE=live`. |
| `IBKR_LOGIN_ID_PAPER` | broker paper login id | Used only by IB Gateway Ops `configure-paper`. |
| `IBKR_LOGIN_SECRET_PAPER` | broker paper login password | Used only by IB Gateway Ops `configure-paper`. |
| `IBKR_LOGIN_ID` | broker live login id | Used only by IB Gateway Ops `configure-live`. |
| `IBKR_LOGIN_SECRET` | broker live login password | Used only by IB Gateway Ops `configure-live`. |
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

`IBKR_LOGIN_ID_PAPER` and `IBKR_LOGIN_SECRET_PAPER` are GitHub Environment Secrets consumed only by the **IB Gateway Ops** workflow for `configure-paper`. `IBKR_LOGIN_ID` and `IBKR_LOGIN_SECRET` are consumed only for `configure-live`. They are broker login credentials, not app `.env` keys.

The workflow sends the selected pair to `sudo poma-configure-ibc` over IAP SSH stdin so IBC can create VM-local Gateway config. Do not add broker login credentials to Terraform, VM metadata, `.env`, or repository files. See [`adr/0001-ibkr-credentials-in-github-secrets.md`](adr/0001-ibkr-credentials-in-github-secrets.md).

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
- Paper Gateway login secrets are separate from the paper account id: `IBKR_LOGIN_ID_PAPER` / `IBKR_LOGIN_SECRET_PAPER` authenticate Gateway, while `IBKR_ACCOUNT_PAPER` selects the account the app may trade.
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are required so every deployed run has alerting configured.
