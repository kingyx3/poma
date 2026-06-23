# GCP free-tier e2-micro deployment

This is the production deployment path for the low-cost single-host setup: one GCP `e2-micro` VM runs POMA, cron, Docker, IB Gateway, and optional IBC automation. It avoids Artifact Registry, Secret Manager, Cloud Run, Cloud Scheduler, Pub/Sub, Redis, and managed databases.

## Minimal required setup

Before bootstrap, create one temporary GitHub Secret:

| Secret | Required for | Notes |
|---|---|---|
| `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` | Bootstrap only | Temporary GCP service-account JSON key. Delete it after WIF bootstrap succeeds. |

After bootstrap, add only the runtime secrets that cannot be generated:

| Secret | Required for | Notes |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | All deployed runs | Mandatory alerts. |
| `TELEGRAM_CHAT_ID` | All deployed runs | Mandatory alerts. |
| `FMP_API_KEY` | `DATA_PROVIDER=fmp` | Not needed for fixture dry-runs. |
| `IBKR_ACCOUNT` | `TRADING_MODE=paper/live` | Not needed for dry-runs. |

Do not store IBKR login credentials in GitHub. Configure them locally on the VM with [`ibkr-gateway-operations.md`](ibkr-gateway-operations.md).

## Deploy in order

1. In GitHub Actions, run **Bootstrap GCP Workload Identity Federation** with `terraform_action=plan`.
2. Rerun the same workflow with `terraform_action=apply`.
3. Delete `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` from GitHub and disable/delete the temporary key in GCP IAM.
4. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
5. In GitHub Actions, run **Deploy GCP e2-micro VM** with `terraform_action=plan`.
6. Rerun with `terraform_action=apply` and `deploy_app=true`.
7. Keep `TRADING_MODE=dry_run` until the deploy smoke test and Gateway setup are verified.

The bootstrap workflow derives the project id from the temporary key, creates the Terraform state bucket, enables required APIs, configures Workload Identity Federation, and writes safe GitHub Variables such as project, region, VM name, state bucket, trading defaults, risk limits, and local paths.

## What deploy does

On apply, the deploy workflow:

1. Authenticates to GCP through Workload Identity Federation.
2. Renders a VM-local `.env` from generated GitHub Variables and runtime secrets.
3. Runs Terraform for `infra/gcp-free-tier`.
4. Uploads the app package and `.env` through IAP SSH.
5. Runs a fixture-backed dry-run smoke test.
6. Installs the cron schedule.

The VM startup script installs Docker, cron, IB Gateway, IBC, headless GUI support, and `ibgateway.service`.

## Paper/live setup

After dry-run deploy succeeds:

1. Follow [`ibkr-gateway-operations.md`](ibkr-gateway-operations.md).
2. Verify `ibgateway.service` is active.
3. Verify `127.0.0.1:7497` is reachable on the VM.
4. Set `TRADING_MODE=paper` and redeploy.
5. Run paper mode for at least one full trading week before considering live mode.

Live mode additionally requires `ALLOW_LIVE_TRADING=true` and manual review of reports, order limits, turnover limits, and position caps.

## Cost controls

- Keep exactly one VM.
- Keep the VM as `e2-micro`.
- Keep the boot disk at or below 30 GB and type `pd-standard`.
- Keep region in `us-west1`, `us-central1`, or `us-east1`.
- Keep Terraform state in one small US-region GCS bucket.
- Keep a manual Cloud Billing budget alert enabled for the project.
- Watch external IPv4 and outbound network charges after deployment.
