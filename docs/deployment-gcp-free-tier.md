# GCP free-tier e2-micro deployment

This path keeps the existing one-host architecture, but lets GitHub Actions provision and update the host through Terraform.

## What gets created

Terraform creates only the minimum GCP resources needed for the bot:

- One non-preemptible `e2-micro` Compute Engine VM.
- One 30 GB `pd-standard` boot disk.
- One small dedicated VPC/subnet.
- One firewall rule that allows SSH only through IAP TCP forwarding.
- A generated GCS Terraform state bucket during bootstrap.
- No Artifact Registry, Secret Manager, Cloud Run, Cloud Scheduler, Pub/Sub, or managed database.

Keep `GCP_REGION` set to one of the Compute Engine free-tier regions: `us-west1`, `us-central1`, or `us-east1`.

> Cost note: the VM is free-tier-aligned, not guaranteed zero-cost. External IPv4 addresses, outbound network usage, and the small Terraform state bucket can still create small charges. Keep a budget alert enabled and review the bill after the first deploy.

## Minimal setup model

For a new GCP project with billing already enabled, the first GitHub Actions bootstrap run only needs one temporary GitHub Secret:

| Secret | When needed | Notes |
|---|---|---|
| `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` | Bootstrap only | Temporary JSON key for the bootstrap service account. Delete after WIF bootstrap succeeds. |

After bootstrap succeeds, add only the runtime secrets that cannot be generated:

| Secret | When needed | Notes |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Always | Mandatory alerts. |
| `TELEGRAM_CHAT_ID` | Always | Mandatory alerts. |
| `FMP_API_KEY` | When `DATA_PROVIDER=fmp` | Leave unset for fixture-only dry runs. |
| `IBKR_ACCOUNT` | When `TRADING_MODE=paper` or `live` | Required by the deploy renderer for broker modes. |

## One-time WIF bootstrap

The recommended path is the manual GitHub Actions workflow:

1. Create a temporary GCP bootstrap service account key with the permissions listed in [`../infra/gcp-wif-bootstrap/README.md`](../infra/gcp-wif-bootstrap/README.md).
2. Add it as `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY`.
3. Open **Actions** → **Bootstrap GCP Workload Identity Federation**.
4. Run with `terraform_action=plan` first. `project_id` and `tf_state_bucket` are optional overrides; by default the workflow derives the project id from the key and creates `poma-tf-state-<project-number>`.
5. Rerun with `terraform_action=apply` after reviewing the plan.
6. Delete `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` and disable or delete the temporary bootstrap key in GCP IAM.
7. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` before the first deploy.

The bootstrap workflow generates the Terraform state bucket, enables required APIs, creates the deployer service account, configures WIF, and writes the GitHub Variables needed by the deploy workflow.

Generated GitHub Variables include:

- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_ZONE`
- `GCP_VM_NAME`
- `TF_STATE_BUCKET`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT_EMAIL`
- Safe dry-run runtime defaults such as `TRADING_MODE=dry_run`, `DATA_PROVIDER=fixture`, order limits, calendar settings, and local paths.

## Idempotency expectations

- Re-running **Bootstrap GCP Workload Identity Federation** with the same project, state bucket, and GitHub repository should converge against the same Terraform state.
- Re-running **Deploy GCP e2-micro VM** should converge infrastructure through Terraform, then overwrite `/opt/poma` with the latest package, replace `/opt/poma/.env`, run the fixture dry-run smoke test, and reinstall the same crontab.
- Changing bootstrap identifiers such as pool id, provider id, service account id, project id, or state bucket intentionally creates or targets different infrastructure and should be treated as a migration.

## Manual budget alert setup

Create a monthly Cloud Billing budget alert before the first deploy. Keep the threshold low, such as USD 5, and scope it to the GCP project used for POMA. This remains manual because billing-account IAM differs by account and organization.

## Deploy flow

1. Open **Actions** → **Deploy GCP e2-micro VM**.
2. Run with `terraform_action=plan` first.
3. If the plan is expected, rerun with `terraform_action=apply` and `deploy_app=true`.

On apply, GitHub Actions:

1. Authenticates to Google Cloud through Workload Identity Federation.
2. Renders `.env.deploy` from generated GitHub Variables and runtime secrets.
3. Validates FMP output when `DATA_PROVIDER=fmp`.
4. Runs Terraform against `infra/gcp-free-tier` using the generated state bucket.
5. Packages the repository without local state, reports, logs, or `.env` files.
6. Uploads the package and rendered `.env` to the VM through IAP.
7. Runs `ops/scripts/deploy.sh`, which forces a fixture-backed dry-run rebalance and verifies that a report was created.
8. Installs `ops/cron/poma.cron`.

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
- Do not add Artifact Registry, Secret Manager, Cloud NAT, Cloud Scheduler, Cloud Run, Pub/Sub, Redis, or managed databases unless you intentionally accept their costs.
