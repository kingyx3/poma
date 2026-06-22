# Production configuration

## GitHub environments

Create these GitHub environments:

- `development`
- `staging`
- `production`

For `production`, require manual approval before deployment.

## GitHub Actions secrets

| Secret | Environment | Purpose |
|---|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | all | Workload Identity Provider resource name used by GitHub Actions. |
| `GCP_DEPLOYER_SERVICE_ACCOUNT` | all | Deployer service account email used by GitHub Actions. |

Broker/data credentials should **not** be stored directly as GitHub Actions secrets for runtime use. Store runtime credentials in GCP Secret Manager and grant only the Cloud Run runtime service account access.

## GitHub Actions variables

| Variable | Example | Purpose |
|---|---:|---|
| `GCP_PROJECT_ID` | `my-prod-project` | GCP project for deployment. |
| `GCP_REGION` | `asia-southeast1` | Region for Artifact Registry and Cloud Run Job. |
| `GAR_REPOSITORY` | `poma` | Artifact Registry Docker repository. |
| `CLOUD_RUN_JOB_NAME` | `poma-rebalance` | Cloud Run Job name provisioned by Terraform. |

## GCP Secret Manager runtime secrets

| Secret | Purpose |
|---|---|
| `poma-fmp-api-key` | Financial Modeling Prep API key. |
| `poma-executor-api-key` | Shared secret used by Cloud Run Job to call the VPS executor. |

## Runtime environment variables

| Variable | Required | Default | Notes |
|---|---:|---|---|
| `APP_ENV` | yes | `development` | `development`, `staging`, or `production`. |
| `TRADING_MODE` | yes | `dry_run` | `dry_run`, `paper`, or `live`. |
| `ALLOW_LIVE_TRADING` | live only | `false` | Must be true for live trading. |
| `DATA_PROVIDER` | yes | `fixture` | Use `fmp` in production. |
| `FMP_API_KEY` | production | empty | Inject from Secret Manager. |
| `FMP_BASE_URL` | no | `https://financialmodelingprep.com/stable` | Override if your plan uses different endpoints. |
| `UNIVERSE` | yes | `nasdaq100` | This scaffold is Nasdaq-100 focused. |
| `RANK_LOOKBACK_PERIODS` | yes | `21` | Approx. one trading month. |
| `REBALANCE_FREQUENCY` | yes | `monthly` | Operational schedule label. |
| `PORTFOLIO_VALUE_USD` | yes | `10000` | Used for target notional generation. |
| `CASH_BUFFER_PCT` | yes | `0.02` | Avoids accidental over-investment. |
| `MAX_POSITION_PCT` | yes | `0.10` | Single-name concentration cap. |
| `MAX_TURNOVER_PCT` | yes | `0.35` | Blocks excessive rebalance churn. |
| `MIN_TRADE_NOTIONAL_USD` | yes | `25` | Avoids tiny uneconomic trades. |
| `EXECUTOR_ENDPOINT` | paper/live | empty | VPS executor HTTPS endpoint. |
| `EXECUTOR_API_KEY` | paper/live | empty | Inject from Secret Manager. |
| `IBKR_HOST` | executor | `127.0.0.1` | Used on the VPS, not Cloud Run. |
| `IBKR_PORT` | executor | `7497` | IBKR paper default is usually 7497; live often differs. |
| `IBKR_CLIENT_ID` | executor | `101` | Dedicated client id for this bot. |
| `IBKR_ACCOUNT` | executor | empty | Set to intended IBKR account id. |

## Required external accounts

- IBKR account with paper trading enabled first.
- IB Gateway or Client Portal Gateway running on the executor host.
- Data-provider subscription that covers Nasdaq-100 constituents and market-cap snapshots.
- GCP project with billing enabled.

## Deployment gates

Production should require:

1. CI green.
2. Manual environment approval.
3. `TRADING_MODE=paper` for an observation period.
4. Explicit `TRADING_MODE=live` + `ALLOW_LIVE_TRADING=true` change reviewed separately.
