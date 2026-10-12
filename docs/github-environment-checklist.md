# GitHub Environment deployment checklist

POMA uses separate GitHub **Environments** (`dev`, `stg`, `prd`). Configure the
settings below under **Settings → Environments → [environment]** before running
paper/live Deploy GCP VM or IB Gateway Ops `configure-paper`/`configure-live`.
These workflows now fail **before provisioning/remote access** if mandatory inputs
are missing. They list missing **names**, never secret values.

## Required GitHub Environment variables (non-secret)

| Variable | Example | Meaning |
|---|---|---|
| `INVESTMENT_STRATEGY_WEIGHTS` | `core_etf=1.0` | Registered strategy weights, summing to 100% of the **invested** sleeve |
| `CASH_TARGET_PCT` | `0.02` | 2% of **total** portfolio remains cash |
| `CORE_ETF_WEIGHTS` | `USMC=1.0` | Mandatory if `core_etf` is in the investment mix; otherwise optional |
| `MARKET_CALENDAR` | `LSE` | USMC is traded in London |
| `REBALANCE_AFTER_OPEN_MINUTES` | `390` | LSE rebalance after 14:30 London time, overlapping the US session |

The four general settings must be nonempty for paper/live. `CORE_ETF_WEIGHTS` must also be nonempty if the strategy mix includes `core_etf`. Invalid percentages, unregistered strategies,
conflicting calendars, missing required live permissions and quote settings are rejected by
the runtime-config validation step. The legacy GH variable
`STRATEGY_ALLOCATIONS` must be **unset**; its semantics were total-portfolio weights
and it is intentionally rejected by the new deploy workflow to prevent mixing models.
The Python app continues to accept legacy `STRATEGY_ALLOCATIONS` when configured
directly and the new investment-weight variable is unset.

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

The workflow checks each environment independently: identical strategy variables
are **not automatically synchronized** between dev and prd. To keep paper and live
aligned, set the same five non-secret values in both GitHub Environments and keep
broker credentials, account identifiers and release approvals separate.
