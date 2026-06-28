# GCP Workload Identity Federation bootstrap

This module creates the GitHub Actions deploy identity for POMA:

- A dedicated deployer service account.
- Project roles needed by the deploy workflow.
- A Workload Identity Pool and GitHub OIDC provider.
- A binding that lets only `kingyx3/poma` impersonate the deployer service account.

Workload Identity Federation avoids storing a long-lived `GCP_SERVICE_ACCOUNT_KEY` JSON secret in GitHub.
Only the first bootstrap uses a temporary `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` GitHub Environment
secret; delete that key immediately after the selected environment is bootstrapped.

## Idempotency model

The GitHub Actions bootstrap workflow uses a GCS Terraform backend with an environment-specific prefix such as `poma/dev/gcp-wif-bootstrap`. Re-running the workflow with the same `deploy_environment`, `project_id`, `tf_state_bucket`, and `github_repository` should converge against the same Terraform state instead of trying to recreate existing WIF resources.

Use the same state bucket as the VM deployment module, but keep the bootstrap and VM states under different prefixes:

- `poma/<environment>/gcp-wif-bootstrap`
- `poma/<environment>/gcp-free-tier`

## Recommended bootstrap through GitHub Actions

The first bootstrap cannot be fully keyless because GitHub needs WIF to already exist before it can authenticate keylessly. Use a temporary bootstrap key once, then delete it immediately after the workflow succeeds.

Terraform should manage the durable bootstrap resources, including the deployer service account, IAM bindings, Workload Identity Pool, provider, and required project services. The workflow still performs two small pre-Terraform actions because Terraform cannot use a GCS backend before the state bucket exists, and the Google provider/data sources cannot read the project until prerequisite APIs such as Cloud Resource Manager are enabled.

1. Create a temporary GCP bootstrap service account key with the permissions listed below.
2. Store that JSON key temporarily as the `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` GitHub Environment Secret for only the target `dev`, `stg`, or `prd` environment.
3. Open **Actions** -> **Bootstrap GCP Workload Identity Federation**.
4. Run with `terraform_action=plan` first, passing `deploy_environment`, `project_id`, `tf_state_bucket`, and `github_repository`.
5. Rerun with `terraform_action=apply` after reviewing the plan.
6. The workflow commits a generated, non-secret deploy config file for the selected environment:

   ```text
   ops/deploy/environments/<deploy_environment>.env
   ```

   The file contains:

   ```text
   GCP_WORKLOAD_IDENTITY_PROVIDER=<terraform output>
   GCP_SERVICE_ACCOUNT_EMAIL=<terraform output>
   TF_STATE_BUCKET=<resolved state bucket>
   ```

   Deploy reads this file directly and derives `GCP_PROJECT_ID` from `GCP_SERVICE_ACCOUNT_EMAIL`. Bootstrap does not write GitHub Variables.
7. Delete the `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` GitHub Environment Secret.
8. Delete or disable the temporary GCP bootstrap key in IAM.
9. Remove any old GitHub Environment Variables from earlier bootstrap designs; current deploy and Gateway Ops workflows do not read them.

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

## Generated deploy config contract

`ops/deploy/environments/<env>.env` is safe to commit because it contains only deployment identifiers, not secrets. Runtime secrets still belong in GitHub Environment Secrets. The deploy and IB Gateway Ops workflows read the generated file directly, so do not recreate the old GitHub Variable path.

Expected generated keys:

| Key | Consumer | Notes |
|---|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | Deploy and IB Gateway Ops | Used by `google-github-actions/auth` for WIF. |
| `GCP_SERVICE_ACCOUNT_EMAIL` | Deploy and IB Gateway Ops | Used for service-account impersonation; workflows derive `GCP_PROJECT_ID` from this email. |
| `TF_STATE_BUCKET` | Deploy | Used as the shared GCS Terraform backend bucket. |

## Local / Cloud Shell bootstrap alternative

Run this once from Cloud Shell or a local terminal authenticated as a user that has the same permissions listed above. Keep the environment-specific naming aligned with the GitHub workflow.

```bash
export DEPLOY_ENVIRONMENT=dev
export GCP_PROJECT_ID=<gcp-project-id>
export TF_STATE_BUCKET=<unique-tf-state-bucket>

gcloud services enable \
  serviceusage.googleapis.com \
  cloudresourcemanager.googleapis.com \
  compute.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  iap.googleapis.com \
  storage.googleapis.com \
  sts.googleapis.com \
  --project="${GCP_PROJECT_ID}"

gcloud storage buckets describe "gs://${TF_STATE_BUCKET}" --project="${GCP_PROJECT_ID}" || \
  gcloud storage buckets create "gs://${TF_STATE_BUCKET}" \
    --project="${GCP_PROJECT_ID}" \
    --location=us-west1 \
    --uniform-bucket-level-access

gcloud storage buckets update "gs://${TF_STATE_BUCKET}" --versioning --project="${GCP_PROJECT_ID}"

terraform -chdir=infra/gcp-wif-bootstrap init \
  -backend-config="bucket=${TF_STATE_BUCKET}" \
  -backend-config="prefix=poma/${DEPLOY_ENVIRONMENT}/gcp-wif-bootstrap"

terraform -chdir=infra/gcp-wif-bootstrap apply \
  -var="project_id=${GCP_PROJECT_ID}" \
  -var="github_repository=kingyx3/poma" \
  -var="pool_id=poma-${DEPLOY_ENVIRONMENT}-github" \
  -var="provider_id=github" \
  -var="service_account_id=poma-${DEPLOY_ENVIRONMENT}-github-deployer"
```

Then write and commit the same generated config shape that the workflow would create:

```bash
mkdir -p ops/deploy/environments
cat > "ops/deploy/environments/${DEPLOY_ENVIRONMENT}.env" <<EOF
# Generated by manual WIF bootstrap.
# Safe to commit: contains non-secret deployment identifiers only.
GCP_WORKLOAD_IDENTITY_PROVIDER=$(terraform -chdir=infra/gcp-wif-bootstrap output -raw workload_identity_provider)
GCP_SERVICE_ACCOUNT_EMAIL=$(terraform -chdir=infra/gcp-wif-bootstrap output -raw service_account_email)
TF_STATE_BUCKET=${TF_STATE_BUCKET}
EOF
```

Commit `ops/deploy/environments/${DEPLOY_ENVIRONMENT}.env`, then remove any old GCP JSON-key secrets and keep runtime secrets only in GitHub Environments.