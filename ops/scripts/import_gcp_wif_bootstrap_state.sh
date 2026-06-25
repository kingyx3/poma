#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: import_gcp_wif_bootstrap_state.sh --project-id PROJECT --pool-id POOL --provider-id PROVIDER --service-account-id SA_ID [--terraform-dir DIR]

Imports existing GCP WIF bootstrap resources into the currently configured Terraform state.
This is safe to run before every bootstrap plan/apply; resources already in state are skipped.
EOF
}

project_id=""
pool_id=""
provider_id=""
service_account_id=""
terraform_dir="infra/gcp-wif-bootstrap"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --project-id)
      project_id="${2:-}"
      shift 2
      ;;
    --pool-id)
      pool_id="${2:-}"
      shift 2
      ;;
    --provider-id)
      provider_id="${2:-}"
      shift 2
      ;;
    --service-account-id)
      service_account_id="${2:-}"
      shift 2
      ;;
    --terraform-dir)
      terraform_dir="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

require_non_empty() {
  local name="$1"
  local value="$2"
  if [ -z "${value}" ]; then
    echo "Missing required argument: ${name}" >&2
    usage >&2
    exit 2
  fi
}

require_non_empty --project-id "${project_id}"
require_non_empty --pool-id "${pool_id}"
require_non_empty --provider-id "${provider_id}"
require_non_empty --service-account-id "${service_account_id}"

state_has() {
  local address="$1"
  terraform -chdir="${terraform_dir}" state list 2>/dev/null | grep -Fxq "${address}"
}

import_if_missing() {
  local address="$1"
  local import_id="$2"

  if state_has "${address}"; then
    echo "Terraform state already contains ${address}"
    return 0
  fi

  echo "Importing existing ${address}: ${import_id}"
  terraform -chdir="${terraform_dir}" import -input=false \
    -var="project_id=${project_id}" \
    -var="pool_id=${pool_id}" \
    -var="provider_id=${provider_id}" \
    -var="service_account_id=${service_account_id}" \
    "${address}" "${import_id}"
}

import_project_service_if_enabled() {
  local service="$1"
  local address="google_project_service.required[\"${service}\"]"

  if state_has "${address}"; then
    echo "Terraform state already contains ${address}"
    return 0
  fi

  if gcloud services list --enabled \
    --project="${project_id}" \
    --filter="config.name:${service}" \
    --format="value(config.name)" | grep -Fxq "${service}"; then
    import_if_missing "${address}" "${project_id}/${service}"
  else
    echo "GCP service not enabled yet; Terraform will enable it: ${service}"
  fi
}

service_account_email="${service_account_id}@${project_id}.iam.gserviceaccount.com"
service_account_name="$(gcloud iam service-accounts describe "${service_account_email}" \
  --project="${project_id}" \
  --format="value(name)" 2>/dev/null || true)"
if [ -n "${service_account_name}" ]; then
  import_if_missing google_service_account.github_deployer "${service_account_name}"
else
  echo "Service account does not exist yet; Terraform will create it: ${service_account_email}"
fi

pool_name="$(gcloud iam workload-identity-pools describe "${pool_id}" \
  --project="${project_id}" \
  --location="global" \
  --format="value(name)" 2>/dev/null || true)"
if [ -n "${pool_name}" ]; then
  import_if_missing google_iam_workload_identity_pool.github "${pool_name}"
else
  echo "Workload Identity Pool does not exist yet; Terraform will create it: ${pool_id}"
fi

provider_name="$(gcloud iam workload-identity-pools providers describe "${provider_id}" \
  --project="${project_id}" \
  --location="global" \
  --workload-identity-pool="${pool_id}" \
  --format="value(name)" 2>/dev/null || true)"
if [ -n "${provider_name}" ]; then
  import_if_missing google_iam_workload_identity_pool_provider.github "${provider_name}"
else
  echo "Workload Identity Pool Provider does not exist yet; Terraform will create it: ${provider_id}"
fi

import_project_service_if_enabled iam.googleapis.com
import_project_service_if_enabled iamcredentials.googleapis.com
import_project_service_if_enabled sts.googleapis.com
