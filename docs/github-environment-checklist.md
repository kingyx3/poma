# GitHub Environment deployment checklist

POMA uses separate GitHub **Environments** (`dev`, `stg`, `prd`). Configure the
settings below under **Settings → Environments → [environment]** before running
paper/live Deploy GCP VM or IB Gateway Ops `configure-paper`/`configure-live`.
These workflows now fail **before provisioning/remote access** if mandatory inputs
are missing. They list missing **names**, never secret values.

## Required GitHub Environment variables (not secrets)

Set these four fields under **Settings → Environments → dev → Environment variables**
(and separately under `prd` when enabling production). They are deliberately
read from GitHub Actions `vars.*` **only**. A secret with the same name is not a
substitute; an absent Environment variable fails the deploy/Gateway preflight.

| GitHub Environment variable | Example | Meaning |
|---|---|---|
| `INVESTMENT_STRATEGY_WEIGHTS` | `core_etf=1.0` | Strategy weights sum to 100% of the invested sleeve |
| `CASH_TARGET_PCT` | `0.02` | 2% of total equity held as cash |
| `MARKET_CALENDAR` | `LSE` | London Stock Exchange calendar for USMC |
| `REBALANCE_AFTER_OPEN_MINUTES` | `390` | London rebalance from 14:30 local time |

## Core ETF weights (variable or secret)

If your investment mix includes `core_etf`, also set
`CORE_ETF_WEIGHTS=USMC=1.0`. POMA accepts this setting as either an Environment
variable or an Environment secret; the variable wins if both exist. To prevent
confusing precedence, set it in only one place. Its selected allocation is still
subject to the normal strategy and instrument checks.

Invalid percentages, unregistered strategies, conflicting calendars, missing
required permissions and quote settings are rejected by runtime validation.
The legacy GitHub `STRATEGY_ALLOCATIONS` variable/secret must be unset; its
percentages were relative to total equity, whereas the new weights describe
only the invested portion. The application itself still supports legacy
`STRATEGY_ALLOCATIONS` when running outside this GitHub deployment flow.

## Required GitHub Environment secrets

| Secret | dev/stg paper | prd live |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Required | Required |
| `TELEGRAM_CHAT_ID` | Required | Required |
| `IBKR_ACCOUNT_PAPER` | Required | — |
| `IBKR_LOGIN_ID_PAPER` | Required | — |
| `IBKR_LOGIN_SECRET_PAPER` | Required | — |
| `IBKR_ACCOUNT` | — | Required |
| `IBKR_LOGIN_ID` | — | Required |
| `IBKR_LOGIN_SECRET` | — | Required |

Required GCP Workload Identity settings and VM details are generated in
`ops/deploy/environments/<environment>.env` by the bootstrap workflow; those files
must also exist and pass GCP identity validation. The deploy still requires the proper
`TRADING_MODE`, `ALLOW_LIVE_TRADING` and `DATA_PROVIDER=yahoo`. The IBKR login
secrets are needed for subsequent Gateway configuration even though the app container
uses only the account identifier at runtime.

## 98% investment example

For a $100,000 account:

```text
INVESTMENT_STRATEGY_WEIGHTS=strategy_a=60%,strategy_b=20%,strategy_c=20%
CASH_TARGET_PCT=0.02
```

Then $2,000 stays cash, while strategy A gets $58,800, B gets $19,600,
and C gets $19,600. The example strategy names are conceptual: POMA currently
registers only `core_etf` and `rank_velocity_size_equal_weight`. To use A/B/C,
implement and register them first; unknown names cause validation failure.

A working *registered-strategies* split is:

```text
INVESTMENT_STRATEGY_WEIGHTS=core_etf=60%,rank_velocity_size_equal_weight=40%
CASH_TARGET_PCT=0.02
CORE_ETF_WEIGHTS=USMC=1.0
```

This deploys 58.8% to USMC, 39.2% to rank velocity, and 2% cash. The two
strategies may need different market calendars/trading schedules if combined.
Do not enable a mixed-exchange live allocation until per-instrument market-hours
and scheduling are reviewed.

The workflow checks each environment independently: values are **not synchronized**
between `dev` and `prd`. Set the four required Environment variables for each
environment, plus `CORE_ETF_WEIGHTS` where applicable. Keep broker credentials,
account identifiers and release approvals separate.
