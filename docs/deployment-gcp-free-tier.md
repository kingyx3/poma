# GCP free-tier e2-micro deployment

This path keeps the existing one-host architecture, but lets GitHub Actions provision and update the host through Terraform.

## What gets created

Terraform creates only the minimum GCP resources needed for the bot:

- One non-preemptible `e2-micro` Compute Engine VM.
- One 30 GB `pd-standard` boot disk.
- One small dedicated VPC/subnet.
- One firewall rule that allows SSH only through IAP TCP forwarding.
- No Artifact Registry, Secret Manager, Cloud Run, Cloud Scheduler, Pub/Sub, or managed database.

Keep `GCP_REGION` set to one of the Compute Engine free-tier regions: `us-west1`, `us-central1`, or `us-east1`.

## One-time GCP setup

Enable billing on the project, then create a Terraform state bucket in a US free-tier Cloud Storage region:

```bash
gcloud services enable compute.googleapis.com iap.googleapis.com serviceusage.googleapis.com

gcloud storage buckets create gs://<unique-tf-state-bucket> \
  --project=<gcp-project-id> \
  --location=us-west1 \
  --uniform-bucket-level-access
```

Create a GitHub Actions service account and grant the minimum practical deployment roles:

```bash
gcloud iam service-accounts create poma-github-deployer \
  --project=<gcp-project-id> \
  --display-name="POMA GitHub deployer"

gcloud projects add-iam-policy-binding <gcp-project-id> \
  --member="serviceAccount:poma-github-deployer@<gcp-project-id>.iam.gserviceaccount.com" \
  --role="roles/compute.admin"

gcloud projects add-iam-policy-binding <gcp-project-id> \
  --member="serviceAccount:poma-github-deployer@<gcp-project-id>.iam.gserviceaccount.com" \
  --role="roles/iap.tunnelResourceAccessor"

gcloud projects add-iam-policy-binding <gcp-project-id> \
  --member="serviceAccount:poma-github-deployer@<gcp-project-id>.iam.gserviceaccount.com" \
  --role="roles/serviceusage.serviceUsageAdmin"

gcloud projects add-iam-policy-binding <gcp-project-id> \
  --member="serviceAccount:poma-github-deployer@<gcp-project-id>.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

Create a JSON key for that service account and store it as the `GCP_SERVICE_ACCOUNT_KEY` GitHub secret.

## Required GitHub Variables

Terraform and deployment variables:

| Variable | Example | Notes |
|---|---|---|
| `GCP_PROJECT_ID` | `my-gcp-project` | Target GCP project. |
| `GCP_REGION` | `us-west1` | Must be `us-west1`, `us-central1`, or `us-east1`. |
| `GCP_ZONE` | `us-west1-b` | Must be in `GCP_REGION`. |
| `GCP_VM_NAME` | `poma-free-tier` | Compute Engine instance name. |
| `TF_STATE_BUCKET` | `my-poma-tf-state` | Existing GCS bucket for Terraform state. |

Runtime `.env` variables from GitHub Variables:

| Variable | Recommended starting value |
|---|---|
| `APP_ENV` | `production` |
| `TRADING_MODE` | `dry_run` |
| `ALLOW_LIVE_TRADING` | `false` |
| `MARKET_CALENDAR` | `NASDAQ` |
| `REBALANCE_AFTER_OPEN_MINUTES` | `10` |
| `UNIVERSE` | `nasdaq100` |
| `RANK_LOOKBACK_DAYS` | `90` |
| `MAX_HOLDINGS` | `30` |
| `PORTFOLIO_VALUE_USD` | `10000` |
| `CASH_BUFFER_PCT` | `0.02` |
| `MAX_POSITION_PCT` | `0.10` |
| `MAX_TURNOVER_PCT` | `0.35` |
| `MIN_TRADE_NOTIONAL_USD` | `25` |
| `MIN_WEIGHT_DELTA_PCT` | `0.01` |
| `ORDER_TYPE` | `limit` |
| `ALLOW_MARKET_ORDERS` | `false` |
| `LIMIT_OFFSET_BPS` | `10` |
| `MAX_ORDER_NOTIONAL_USD` | `2000` |
| `MAX_DAILY_TRADES` | `30` |
| `DATA_PROVIDER` | `fixture` |
| `FMP_BASE_URL` | `https://financialmodelingprep.com/stable` |
| `IBKR_HOST` | `127.0.0.1` |
| `IBKR_PORT` | `7497` |
| `IBKR_CLIENT_ID` | `101` |
| `STATE_DIR` | `state` |
| `REPORT_DIR` | `reports` |

Set every runtime variable explicitly even when the value matches `.env.example`. The deploy workflow renders `.env` in strict mode and fails if a key is missing.

## Required GitHub Secrets

| Secret | Required | Notes |
|---|---:|---|
| `GCP_SERVICE_ACCOUNT_KEY` | yes | JSON key for the deployer service account. |
| `TELEGRAM_BOT_TOKEN` | yes | Mandatory alerts. |
| `TELEGRAM_CHAT_ID` | yes | Mandatory alerts. |
| `FMP_API_KEY` | when `DATA_PROVIDER=fmp` | Leave unset for fixture-only dry runs. |
| `IBKR_ACCOUNT` | paper/live | Required by the deploy renderer when `TRADING_MODE=paper` or `live`. |

## Deploy flow

1. Open **Actions** → **Deploy GCP e2-micro VM**.
2. Run with `terraform_action=plan` first.
3. If the plan is expected, rerun with `terraform_action=apply` and `deploy_app=true`.

On apply, GitHub Actions:

1. Renders `.env.deploy` from GitHub Variables and Secrets.
2. Runs Terraform against `infra/gcp-free-tier`.
3. Packages the repository without local state, reports, logs, or `.env` files.
4. Uploads the package and rendered `.env` to the VM through IAP.
5. Runs `ops/scripts/deploy.sh`.
6. Installs `ops/cron/poma.cron`.

## Manual SSH

Use the Terraform output command:

```bash
gcloud compute ssh poma-free-tier --zone us-west1-b --tunnel-through-iap
```

## Cost controls

- Keep exactly one VM.
- Keep the VM as `e2-micro`.
- Keep the boot disk at or below 30 GB and type `pd-standard`.
- Keep region in `us-west1`, `us-central1`, or `us-east1`.
- Keep Terraform state in one small US-region GCS bucket.
- Do not add Artifact Registry, Secret Manager, Cloud NAT, Cloud Scheduler, Cloud Run, or managed databases unless you intentionally accept their costs.
