# GCP Workload Identity Federation bootstrap

This module creates the GitHub Actions deploy identity for POMA:

- A dedicated deployer service account.
- Project roles needed by the deploy workflow.
- A Workload Identity Pool and GitHub OIDC provider.
- A binding that lets only `kingyx3/poma` impersonate the deployer service account.

Workload Identity Federation avoids storing a long-lived `GCP_SERVICE_ACCOUNT_KEY` JSON secret in GitHub.

## Bootstrap

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

After these variables are set, remove the old `GCP_SERVICE_ACCOUNT_KEY` GitHub Secret.
