# GCP Workload Identity Federation bootstrap

This module creates the GitHub Actions deploy identity for POMA:

- A dedicated deployer service account.
- Project roles needed by the deploy workflow.
- A Workload Identity Pool and GitHub OIDC provider.
- A binding that lets only `kingyx3/poma` impersonate the deployer service account.

Workload Identity Federation avoids storing a long-lived `GCP_SERVICE_ACCOUNT_KEY` JSON secret in GitHub.

## Recommended bootstrap through GitHub Actions

The first bootstrap cannot be fully keyless because GitHub needs WIF to already exist before it can authenticate keylessly. Use a temporary bootstrap key once, then delete it immediately after the workflow succeeds.

1. Create a temporary GCP bootstrap service account key with permissions to manage IAM, service accounts, Workload Identity Federation, Service Usage, and project IAM bindings.
2. Store that JSON key temporarily as the `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` GitHub Secret.
3. Open **Actions** → **Bootstrap GCP Workload Identity Federation**.
4. Run with `terraform_action=plan` first.
5. Rerun with `terraform_action=apply` after reviewing the plan.
6. The workflow writes these GitHub Variables automatically:
   - `GCP_PROJECT_ID`
   - `GCP_WORKLOAD_IDENTITY_PROVIDER`
   - `GCP_SERVICE_ACCOUNT_EMAIL`
7. Delete the `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` GitHub Secret.
8. Delete or disable the temporary GCP bootstrap key in IAM.

## Local / Cloud Shell bootstrap alternative

Run this once from Cloud Shell or a local terminal authenticated as a user that can manage IAM, service accounts, and workload identity pools.

```bash
gcloud services enable \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  serviceusage.googleapis.com

terraform -chdir=infra/gcp-wif-bootstrap init
terraform -chdir=infra/gcp-wif-bootstrap apply \
  -var="project_id=<gcp-project-id>" \
  -var="github_repository=kingyx3/poma"
```

Then copy the outputs into GitHub Variables:

| Terraform output | GitHub Variable |
|---|---|
| `workload_identity_provider` | `GCP_WORKLOAD_IDENTITY_PROVIDER` |
| `service_account_email` | `GCP_SERVICE_ACCOUNT_EMAIL` |

After these variables are set, remove any old GCP JSON-key secrets.
