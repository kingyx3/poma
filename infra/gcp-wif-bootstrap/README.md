# GCP Workload Identity Federation bootstrap

This module creates the GitHub Actions deploy identity for POMA:

- A dedicated deployer service account.
- Project roles needed by the deploy workflow.
- A Workload Identity Pool and GitHub OIDC provider.
- A binding that lets only `kingyx3/poma` impersonate the deployer service account.

Workload Identity Federation avoids storing a long-lived `GCP_SERVICE_ACCOUNT_KEY` JSON secret in GitHub.

## Idempotency model

The GitHub Actions bootstrap workflow uses a GCS Terraform backend with an environment-specific prefix such as `poma/dev/gcp-wif-bootstrap`. Re-running the workflow with the same `deploy_environment`, `project_id`, `tf_state_bucket`, and `github_repository` should converge against the same Terraform state instead of trying to recreate existing WIF resources.

Use the same state bucket as the VM deployment module, but keep the bootstrap and VM states under different prefixes:

- `poma/<environment>/gcp-wif-bootstrap`
- `poma/<environment>/gcp-free-tier`

## Recommended bootstrap through GitHub Actions

The first bootstrap cannot be fully keyless because GitHub needs WIF to already exist before it can authenticate keylessly. Use a temporary bootstrap key once, then delete it immediately after the workflow succeeds.

Terraform should manage the durable bootstrap resources, including the deployer service account, IAM bindings, Workload Identity Pool, provider, and required project services. The workflow still performs two small pre-Terraform actions because Terraform cannot use a GCS backend before the state bucket exists, and the Google provider/data sources cannot read the project until prerequisite APIs such as Cloud Resource Manager are enabled.

1. Create a temporary GCP bootstrap service account key with the permissions listed below.
2. Store that JSON key temporarily as the `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` GitHub Secret.
3. Open **Actions** → **Bootstrap GCP Workload Identity Federation**.
4. Run with `terraform_action=plan` first, passing `project_id`, `tf_state_bucket`, and `github_repository`.
5. Rerun with `terraform_action=apply` after reviewing the plan.
6. The workflow writes these GitHub Variables automatically:
   - `GCP_PROJECT_ID`
   - `TF_STATE_BUCKET`
   - `GCP_WORKLOAD_IDENTITY_PROVIDER`
   - `GCP_SERVICE_ACCOUNT_EMAIL`
7. Delete the `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` GitHub Secret.
8. Delete or disable the temporary GCP bootstrap key in IAM.

## Temporary bootstrap service account permissions

Grant the temporary bootstrap service account these roles on the target project:

| Role | Why it is needed |
|---|---|
| `roles/serviceusage.serviceUsageAdmin` | Enable Service Usage, Cloud Resource Manager, IAM, Service Account Credentials, STS, Compute, IAP, and Storage APIs. |
| `roles/iam.workloadIdentityPoolAdmin` | Create and manage the WIF pool and GitHub OIDC provider. |
| `roles/iam.serviceAccountAdmin` | Create and manage the GitHub deployer service account. |
| `roles/iam.serviceAccountIamAdmin` | Grant `roles/iam.workloadIdentityUser` on the deployer service account to the GitHub repository principal. |
| `roles/resourcemanager.projectIamAdmin` | Grant the deployer service account the project roles used by the deployment workflow. |

Grant this role on the Terraform state bucket:

| Role | Why it is needed |
|---|---|
| `roles/storage.objectAdmin` | Read, write, and lock Terraform state in the GCS backend bucket. |

After WIF bootstrap succeeds, remove the temporary key and disable or delete the temporary bootstrap service account. Do not keep this bootstrap identity around for normal deployment.

## Local / Cloud Shell bootstrap alternative

Run this once from Cloud Shell or a local terminal authenticated as a user that has the same permissions listed above.

```bash
gcloud services enable \
  serviceusage.googleapis.com \
  cloudresourcemanager.googleapis.com \
  compute.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  iap.googleapis.com \
  storage.googleapis.com \
  sts.googleapis.com

terraform -chdir=infra/gcp-wif-bootstrap init \
  -backend-config="bucket=<unique-tf-state-bucket>" \
  -backend-config="prefix=poma/gcp-wif-bootstrap"

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
