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
| `YAHOO_SCREENER_LIMIT` | yahoo only | `500` | Yahoo provider limit. Used by the current rank-velocity strategy before issuer/share-class dedupe and scoring; see `docs/strategies/rank-velocity-size-equal-weight.md`. |
| `YAHOO_SCREENER_PAGE_SIZE` | yahoo only | `250` | Yahoo screen page size. yfinance/Yahoo caps this at 250. |
| `UNIVERSE` | yes | `us_top_market_cap` | Yahoo-backed US top market-cap universe. Strategy docs define how each strategy interprets this provider universe. |
| `RANK_LOOKBACK_DAYS` | yes | `90` | Rank-rising-velocity lookback window for `rank_velocity_size_equal_weight`; see `docs/strategies/rank-velocity-size-equal-weight.md`. |
| `MAX_HOLDINGS` | yes | `50` | Selection count for strategies that use a capped holdings list. For the current built-in strategy, this selects the top 50 company stocks when enough valid tickers exist. |
| `STRATEGY_ALLOCATIONS` | yes | `rank_velocity_size_equal_weight=0.98,cash=0.02` | Named portfolio sleeves. Every non-`cash` name must be registered (see `docs/strategy-contract.md`); the engine executes every positive sleeve. Total must be `<= 1.0`. |
| `DRY_RUN_PORTFOLIO_VALUE_USD` | yes | `10000` | Portfolio value used only in `dry_run` mode. Paper/live size against USD-only broker account equity instead; see `MANAGED_CAP_MODE`. |
| `MANAGED_CAP_MODE` | yes | `broker_total` | `broker_total` sizes paper/live sleeves off the USD-only broker account value. `min_of_broker_total_and_cap` sizes off `min(USD-only broker account value, MANAGED_CAP_USD)`. |
| `MANAGED_CAP_USD` | `min_of_broker_total_and_cap` only | `0` | Hard cap on managed capital when `MANAGED_CAP_MODE=min_of_broker_total_and_cap`. Must be greater than 0 in that mode; unused (and may stay `0`) under `broker_total`. |
| `MAX_POSITION_PCT` | yes | `0.10` | Portfolio/risk-layer per-position cap. |
| `MAX_TURNOVER_PCT` | yes | `1.0` | Maximum absolute trade notional divided by the allocated active strategy sleeve capital. |
| `MIN_TRADE_NOTIONAL_USD` | yes | `25` | Suppresses tiny rebalance trades. |
| `MIN_WEIGHT_DELTA_PCT` | yes | `0.0025` | Suppresses tiny target/current weight differences. |
| `ESTIMATED_TRANSACTION_COST_BPS` | yes | `0` | Optional all-in estimated transaction cost in basis points. Include expected commissions, spreads, fees, FX costs, taxes, or other known trade friction. |
| `ESTIMATED_TRANSACTION_COST_FIXED_USD` | yes | `0` | Optional fixed estimated transaction cost per trade. A trade is skipped when its notional minus estimated cost falls below `MIN_TRADE_NOTIONAL_USD`. |
| `ORDER_TYPE` | yes | `limit` | Use `limit` by default. |
| `ALLOW_MARKET_ORDERS` | live market only | `false` | Explicit opt-in for live market orders. |
| `LIMIT_OFFSET_BPS` | yes | `10` | Limit price offset applied on top of the *selected execution reference price* (see below), not the planning snapshot. |
| `MAX_ORDER_NOTIONAL_USD` | yes | `2000` | Blocks unexpectedly large orders. Must be at least `MIN_TRADE_NOTIONAL_USD`. |
| `MAX_DAILY_TRADES` | yes | `100` | Allows a full rebalance while still capping trade count. Must be at least `MAX_HOLDINGS` for full bootstrap. |
| `EXECUTION_PRICE_SOURCE` | yes | `ibkr` | Where the execution-time reference price comes from for paper/live orders. `ibkr` reads a fresh broker quote immediately before submission; `snapshot` falls back to the planning snapshot price. Live trading rejects `snapshot` unless `ALLOW_UNSAFE_EXECUTION_PRICE_SOURCE=true`. `dry_run` always uses the snapshot regardless of this setting, since there is no broker to quote from. |
| `EXECUTION_PRICE_BASIS` | yes | `side_of_market` | Which part of the broker quote a trade is priced from: `side_of_market` (BUY uses ask, SELL uses bid), `midpoint` (requires both bid and ask), or `last` (requires `ALLOW_LAST_PRICE_FALLBACK=true`). |
| `EXECUTION_QUOTE_MAX_AGE_SECONDS` | yes | `60` | Maximum age of a broker quote before it is considered stale and the trade is blocked. Deploy validation caps this at 120s for paper/live. |
| `EXECUTION_MAX_SPREAD_BPS` | yes | `50` | Maximum allowed bid/ask spread, in basis points, before a trade is blocked as too wide to price safely. |
| `ALLOW_DELAYED_EXECUTION_QUOTES` | no | mode-dependent: `true` for `dry_run`/`paper`, `false` for `live` | Whether a broker quote flagged as delayed (not live/frozen market data) may still be used. When `true`, `IbkrBroker` also automatically retries any ticker that got no live tick at all as delayed data (`reqMarketDataType`, with a doubled wait because delayed subscriptions start ticking noticeably slower) before giving up on it — no manual Gateway/TWS market-data-type step required. Unset, it defaults per trading mode, since dry-run/paper accounts commonly lack the separate IBKR "API market data" real-time opt-in even when delayed data is available. Deploy validation blocks `true` for `TRADING_MODE=live`; live requires the real-time API market data subscription instead (see `docs/production-readiness.md`). |
| `REQUIRE_LIVE_EXECUTION_QUOTES` | yes | `false` | When `true`, makes `poma ibkr-check` hard-fail unless its market data probe receives a real-time-class tick: live, or frozen (the last real-time quote) when the market is closed. Delayed-only data and market-closed silence are never soft-passed. Use it for an explicit proof run after changing IBKR market-data subscriptions or paper sharing; paper/dev deployments leave it `false` by default so the allowed delayed fallback can keep paper trading usable when IBKR Gateway classifies the available paper feed as delayed. |
| `MARKET_DATA_PROBE_WAIT_SECONDS` | yes | `5` | Per-step wait of the readiness probe's market data type ladder (see below); the delayed steps wait twice this long because delayed subscriptions start ticking noticeably slower. |
| `IBKR_MARKET_DATA_EXCHANGES` | no | mode-dependent: `IEX,SMART` for non-live, `SMART` for live | Comma-separated IBKR venues used only for quote/probe requests. Orders still route through `SMART`. Paper/dev tries `IEX` first so IBKR's US real-time non-consolidated streaming quote entitlement can satisfy live quote checks when the Gateway API exposes it before falling back to `SMART` and, if allowed, delayed data. |
| `ALLOW_LAST_PRICE_FALLBACK` | yes | `false` | Required to be `true` before `EXECUTION_PRICE_BASIS=last` is accepted. |
| `ALLOW_UNSAFE_EXECUTION_PRICE_SOURCE` | yes | `false` | Explicit override required for `TRADING_MODE=live` with `EXECUTION_PRICE_SOURCE=snapshot`. Leave `false` unless you understand the staleness risk. |
| `FRACTIONAL_SHARES` | no | `false` | Whole-share sizing is the default for every instrument because the IBKR API rejects fractional order sizes (error 10243 "Fractional-sized order cannot be placed via API"). Buys round to the nearest whole share (up or down, keeping orders centered on the target notional); sells always round down so a rounded order can never oversell the position. Buy round-ups that would push total buy limit cash requirement past available cash plus conservative planned sell proceeds are demoted back down, and the same rounding re-applies when orders are repriced off a fresh execution quote. Set `true` only for accounts confirmed to accept fractional API orders. |
| `NON_FRACTIONAL_TICKERS` | no | `` (empty) | Only meaningful with `FRACTIONAL_SHARES=true`: comma-separated tickers kept on whole-share sizing while every other instrument trades fractionally. |
| `ORDER_STATUS_TIMEOUT_SECONDS` | yes | `60` | Time to wait for broker order status before marking follow-up needed. |
| `CANCEL_STALE_ORDERS` | yes | `true` | Request cancel when an order does not reach a terminal status in time. |
| `ORDER_TIME_IN_FORCE` | yes | `DAY` | Time-in-force set explicitly on every order. Avoid `GTC`: a stale limit order surviving to the next session would be sized against a target portfolio that may have already changed. |
| `REPLACE_AFTER_SECONDS` | yes | `120` | `poma reconcile-orders` replaces a still-working (unfilled) limit order once with a more aggressive price after this many seconds. |
| `CANCEL_AFTER_SECONDS` | yes | `300` | `poma reconcile-orders` cancels a still-working limit order after this many seconds. Must be greater than `REPLACE_AFTER_SECONDS`. |
| `REPLACE_PRICE_IMPROVEMENT_BPS` | yes | `15` | Basis-point price improvement applied on the single allowed replace. |
| `STALE_ORDER_POLICY` | yes | `block` | What a new rebalance does when unresolved open orders remain from a *prior* session, or from a *different run* within the same session: `block` (default) blocks execution until they are reconciled/cancelled; `cancel` cancels them automatically before planning. Open orders from the exact *same run* are never blocking — a retry of that run relies on idempotent replay instead (see `docs/architecture.md`). |
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

`CASH_BUFFER_PCT` is intentionally not part of the current runtime contract. Cash is represented as a `cash` allocation sleeve in `STRATEGY_ALLOCATIONS`, not as a hidden buffer inside an active strategy.

## Strategy pricing vs. execution pricing

The planning snapshot price and the paper/live execution price answer two different questions and are never the same read:

- **Strategy market data** (provider snapshot, via `DATA_PROVIDER`/`UNIVERSE`): drives strategy target selection and planning target-weight/notional sizing. It also sizes *planning* trade quantities (`poma.risk.generate_trades`), and it is what `dry_run` mode uses end to end since there is no broker connection to quote from. The current built-in strategy's interpretation of the Yahoo snapshot is documented in [`docs/strategies/rank-velocity-size-equal-weight.md`](strategies/rank-velocity-size-equal-weight.md).
- **Execution market data** (`EXECUTION_PRICE_SOURCE=ibkr` by default): a fresh IBKR bid/ask/last quote fetched immediately before a paper/live order is submitted (and again immediately before a reconciliation replace). This — not the planning snapshot — is what `LIMIT_OFFSET_BPS` is offset from, and what the final order quantity/limit price are computed against.

Paper/live execution blocks the affected order (with a `block execution` warning, the same marker used elsewhere in the risk engine) instead of silently falling back to the planning snapshot price when the IBKR quote is missing, older than `EXECUTION_QUOTE_MAX_AGE_SECONDS`, wider than `EXECUTION_MAX_SPREAD_BPS`, or flagged delayed without `ALLOW_DELAYED_EXECUTION_QUOTES=true`. Every submitted order's ledger entry (`poma.order_lifecycle.OrderLedgerEntry`) records the quote source, basis, timestamp, age, and spread it was priced from, and Telegram order-status alerts include the same summary.

Buying-power checks use the cash the submitted limit order can actually consume, not just the
reference-price notional. For paper/live buys, `ExecutionManager` reprices against the fresh
execution quote first, then refreshes broker cash after the sell phase and blocks buys as
`BuyingPowerBlocked` if that cash cannot cover the buy limit cash requirement.

`IbkrBroker` explicitly requests live market data (`reqMarketDataType(1)`) on every connection
rather than relying on Gateway remembering a data type from a prior session or client id, since a
fresh connection can otherwise silently return no ticks at all even with live entitlements in
place. Quote requests use `IBKR_MARKET_DATA_EXCHANGES` in order. This affects only quote/probe
contracts, not order routing: submitted orders still use `SMART`. In paper/dev, trying `IEX`
before `SMART` lets accounts with IBKR's US real-time non-consolidated streaming quotes use a
real-time direct-venue feed when the Gateway API exposes one, instead of immediately failing on
`SMART`/NASDAQ consolidated market-data entitlement. A ticker with no live tick on any configured venue is retried as
delayed data automatically when `ALLOW_DELAYED_EXECUTION_QUOTES=true`; tickers that already
received a live tick are left alone. A symbol with no data under either mode still blocks
execution.

The readiness probe behind `poma ibkr-check` additionally walks the full market data type ladder (live → frozen → delayed → delayed-frozen) across each configured `IBKR_MARKET_DATA_EXCHANGES` venue, so its verdict is conclusive even outside market hours: frozen data serves the last real-time quote of a closed session and needs the same entitlement as live, so a frozen tick off-hours proves real-time entitlement, while a delayed-only tick proves that entitlement is missing. The ladder is probe-only — frozen data is stale by definition and never feeds execution pricing. `ibkr-check` output reports the verdict directly as `market_data_type=…`, `market_data_exchange=…`, and `realtime_entitlement=yes|no`.

## Portfolio sizing and existing account equity

Paper/live rebalances size from the configured IBKR account's actual USD cash plus USD-denominated portfolio value, read fresh before every rebalance, not from a static config number. POMA does not convert non-USD cash, `BASE` totals, or non-USD position values into USD buying power; those rows are ignored for allocation gaps and trade recommendations, with a plan warning so the report explains the exclusion. If an account has non-USD cash that should fund US-stock rebalancing, convert it to USD outside POMA before the run. `MANAGED_CAP_MODE=broker_total` (the default) uses that USD-only broker equity. `MANAGED_CAP_MODE=min_of_broker_total_and_cap` instead uses `min(USD-only broker equity, MANAGED_CAP_USD)`, which is useful when the IBKR account holds more USD value than POMA should manage. `DRY_RUN_PORTFOLIO_VALUE_USD` is used only in `dry_run` mode, where there is no broker to read from.

Transaction costs are operator estimates, not broker guarantees. Set `ESTIMATED_TRANSACTION_COST_BPS` and/or `ESTIMATED_TRANSACTION_COST_FIXED_USD` to account for commissions, spreads, platform fees, FX costs, taxes, or other known trade friction. After normal trade generation, POMA skips any trade whose notional minus estimated cost falls below `MIN_TRADE_NOTIONAL_USD`. The execution-time spread guard (`EXECUTION_MAX_SPREAD_BPS`) remains separate and can still block an otherwise cost-acceptable order if the live quote is too wide.

`STRATEGY_ALLOCATIONS` splits whichever value is resolved above across named sleeves, and the total allocation must be `<= 100%`. Every allocated non-`cash` sleeve is executed by the engine, not just one:

```text
STRATEGY_ALLOCATIONS=rank_velocity_size_equal_weight=0.60,future_strategy=0.20,cash=0.20
```

That runs `rank_velocity_size_equal_weight` on 60% of the resolved portfolio value, runs `future_strategy` on 20%, and leaves 20% in passive cash. No sleeve separately subtracts a hidden cash buffer. If two sleeves both target the same ticker, POMA combines their targets into a single portfolio-level order rather than trading them independently. See [`docs/portfolio-management.md`](portfolio-management.md) for the strategy-neutral model.

Existing USD-denominated stock positions in the configured IBKR account are read by ticker and included in rebalance deltas. Keep unrelated/manual positions in a separate account or avoid overlapping tickers if you do not want them to affect POMA's calculations.

If a held ticker is missing from the Yahoo planning snapshot and the combined target would reduce
or exit that position, POMA may use the broker-reported position market value per share to size the
risk-reducing sell. This fallback is not used for buys, and it does not bypass the fresh IBKR
execution quote check before submission.

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
| `IBKR_LOGIN_SECRET` | broker live login password | Used only for IB Gateway Ops `configure-live`. |
| `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` | one temporary JSON key | Bootstrap only. Delete after WIF bootstrap succeeds. |

## Market data snapshots and strategy inputs

Run this before the first data-dependent rebalance, or let the first rebalance save only the current snapshot and follow the active strategy's documented missing-history behavior:

```bash
poma refresh-market-data
```

`poma refresh-market-data` writes provider snapshots under `DATA_DIR/market_snapshots/`. The provider layer stores normalized market data; each strategy decides how to interpret those snapshots for selection, scoring, ranking, or other target-building logic. The current rank-velocity strategy's Yahoo top-market-cap ranking, share-class deduplication, and historical market-cap estimation behavior are documented in [`docs/strategies/rank-velocity-size-equal-weight.md`](strategies/rank-velocity-size-equal-weight.md).

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
- `TRADING_MODE=live` with `EXECUTION_PRICE_SOURCE=snapshot` is blocked unless `ALLOW_UNSAFE_EXECUTION_PRICE_SOURCE=true`.
- Paper/live orders block (not silently reprice from a stale/fallback source) on a missing, stale, too-wide, or delayed execution quote. See "Strategy pricing vs. execution pricing" above.
- `MAX_POSITION_PCT`, `MAX_TURNOVER_PCT`, `STRATEGY_ALLOCATIONS`, and min-trade thresholds are validated at startup.
- The deploy workflow renders paper runtime `IBKR_ACCOUNT` from `IBKR_ACCOUNT_PAPER`. Live mode uses `IBKR_ACCOUNT`.
- Paper Gateway login secrets are separate from the paper account id: `IBKR_LOGIN_ID_PAPER` / `IBKR_LOGIN_SECRET_PAPER` authenticate Gateway, while `IBKR_ACCOUNT_PAPER` selects the account the app may trade.
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are required so every deployed run has alerting configured.
