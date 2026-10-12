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
| `INVESTMENT_STRATEGY_WEIGHTS` | `USMC=1.0` | Strategy weights sum to 100% of the invested sleeve |
| `CASH_TARGET_PCT` | `0.02` | 2% of total equity held as cash |
| `MARKET_CALENDAR` | `LSE` | London Stock Exchange calendar for USMC |
| `REBALANCE_AFTER_OPEN_MINUTES` | `390` | London rebalance from 14:30 local time |

## Instrument selection

Allocate directly by instrument ticker: `USMC=1.0`, `USMC=0.8,SP1=0.2`, or a mixture of a direct fund and an algorithm such as `USMC=0.8,rank_velocity_size_equal_weight=0.2`. The legacy `core_etf` strategy still targets USMC but is no longer the default. No `CORE_ETF_WEIGHTS` setting or extra instrument-weight layer exists. To change the investment mix, edit only
`INVESTMENT_STRATEGY_WEIGHTS`, which must total exactly `1.0`; the cash weight
is configured independently through `CASH_TARGET_PCT`.

Invalid percentages, unregistered strategies and conflicting market calendars
fail before deployment. The legacy `STRATEGY_ALLOCATIONS` full-account allocation
variable/secret is rejected in the GitHub workflow to avoid ambiguous allocation
models.

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
registers `USMC`, `SP1`, `core_etf` (legacy alias) and `rank_velocity_size_equal_weight`. To use A/B/C,
implement and register them first; unknown names cause validation failure.

A working *registered-strategies* split is:

```text
INVESTMENT_STRATEGY_WEIGHTS=USMC=80%,SP1=20%
CASH_TARGET_PCT=0.02
```

This deploys 78.4% to USMC, 19.6% to SP1, and 2% cash. Both funds use USD London listings. Unknown ticker IDs, including literal `XXX`, must be registered and mapped to a verified broker exchange/currency before deployment. Mixing London ETFs with US-stock algorithms still requires careful market-session alignment.

The workflow checks each environment independently: values are **not synchronized**
between `dev` and `prd`. Set the four required Environment variables for each
environment. Keep broker credentials,
account identifiers and release approvals separate.
