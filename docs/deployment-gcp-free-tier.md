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

> Cost note: the VM is free-tier-aligned, not guaranteed zero-cost. External IPv4 addresses and outbound network usage can still create small charges. Keep a budget alert enabled and review the bill after the first deploy.

## One-time GCP setup

Enable billing on the project, then create a Terraform state bucket in a US free-tier Cloud Storage region:

```bash
gcloud services enable \
  compute.googleapis.com \
  iap.googleapis.com \
  serviceusage.googleapis.com

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

> Production hardening: replace the JSON key with Workload Identity Federation when you are ready. This avoids long-lived cloud keys in GitHub Secrets.

## Manual budget alert setup

Create a monthly Cloud Billing budget alert before the first deploy. Keep the threshold low, such as USD 5, and scope it to the GCP project used for POMA. This is intentionally manual in this template because billing-account IAM differs by account and organization.

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
| `GCP_SERVICE_ACCOUNT_KEY` | yes | JSON key for the deployer service account. Replace with WIF later. |
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
2. Validates FMP output when `DATA_PROVIDER=fmp`.
3. Runs Terraform against `infra/gcp-free-tier`.
4. Packages the repository without local state, reports, logs, or `.env` files.
5. Uploads the package and rendered `.env` to the VM through IAP.
6. Runs `ops/scripts/deploy.sh`, which forces a fixture-backed dry-run rebalance and verifies that a report was created.
7. Installs `ops/cron/poma.cron`.

## IB Gateway supervision

This Terraform path does not install IB Gateway because setup varies by account, license flow, and whether you use IB Gateway or Trader Workstation. Before paper or live trading:

- Install IB Gateway on the same host.
- Run it under a supervised service such as `systemd`.
- Confirm it auto-starts after reboot.
- Confirm POMA can connect to the configured `IBKR_HOST`, `IBKR_PORT`, and `IBKR_CLIENT_ID`.
- Confirm paper mode works for at least one full trading week before considering live mode.

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
- Keep a manual budget alert enabled for the project.
- Watch external IPv4 and outbound network charges after deployment.
- Do not add Artifact Registry, Secret Manager, Cloud NAT, Cloud Scheduler, Cloud Run, or managed databases unless you intentionally accept their costs.
