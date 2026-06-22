# GCP Terraform

This module provisions the minimum production GCP infrastructure:

- Artifact Registry repository for Docker images.
- Cloud Run Job for scheduled strategy execution.
- Cloud Scheduler trigger.
- Runtime service account.
- Secret Manager secrets for data-provider and executor credentials.

## Bootstrap flow

1. Enable required APIs:
   - Artifact Registry API
   - Cloud Run API
   - Cloud Scheduler API
   - Secret Manager API
   - IAM Credentials API
2. Create or choose a deployer service account for GitHub Actions Workload Identity Federation.
3. Run Terraform with an initial image that exists, for example a temporary hello-world image or a previously pushed POMA image.
4. Add secret versions:

```bash
echo -n "$FMP_API_KEY" | gcloud secrets versions add poma-fmp-api-key --data-file=-
echo -n "$EXECUTOR_API_KEY" | gcloud secrets versions add poma-executor-api-key --data-file=-
```

5. Configure GitHub variables/secrets from `docs/configuration.md`.
6. Run the deploy workflow.

## Important

Start with `trading_mode = "dry_run"`. Move to `paper`, then `live` only after paper fills, reports, and alerting have been verified.
