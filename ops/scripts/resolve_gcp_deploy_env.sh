#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_ENVIRONMENT:?}"
: "${GITHUB_ENV:?}"

config_path="ops/deploy/environments/${DEPLOY_ENVIRONMENT}.env"
if [ ! -f "${config_path}" ]; then
  echo "::error::Missing generated deployment config: ${config_path}. Run Bootstrap GCP Workload Identity Federation with terraform_action=apply first."
  exit 1
fi

set -a
# shellcheck source=/dev/null
. "${config_path}"
set +a

require_non_empty() {
  local key="$1"
  if [ -z "${!key:-}" ]; then
    echo "::error::Missing required generated deployment setting for ${DEPLOY_ENVIRONMENT}: ${key}"
    exit 1
  fi
}

set_env() {
  local key="$1"
  local value="$2"
  echo "${key}=${value}" >> "${GITHUB_ENV}"
  export "${key}=${value}"
}

set_default() {
  local key="$1"
  local default_value="$2"
  local value="${!key:-}"
  if [ -z "${value}" ]; then
    value="${default_value}"
  fi
  set_env "${key}" "${value}"
}

require_non_empty GCP_WORKLOAD_IDENTITY_PROVIDER
require_non_empty GCP_SERVICE_ACCOUNT_EMAIL

if [[ ! "${GCP_WORKLOAD_IDENTITY_PROVIDER}" =~ ^projects/([0-9]+)/locations/global/workloadIdentityPools/.+/providers/.+$ ]]; then
  echo "::error::GCP_WORKLOAD_IDENTITY_PROVIDER in ${config_path} must use the projects/<number>/locations/global/workloadIdentityPools/<pool>/providers/<provider> format"
  exit 1
fi
project_number="${BASH_REMATCH[1]}"

if [[ ! "${GCP_SERVICE_ACCOUNT_EMAIL}" =~ @([a-z0-9-]+)\.iam\.gserviceaccount\.com$ ]]; then
  echo "::error::GCP_SERVICE_ACCOUNT_EMAIL in ${config_path} must be a Google service account email"
  exit 1
fi
derived_project_id="${BASH_REMATCH[1]}"

set_env GCP_WORKLOAD_IDENTITY_PROVIDER "${GCP_WORKLOAD_IDENTITY_PROVIDER}"
set_env GCP_SERVICE_ACCOUNT_EMAIL "${GCP_SERVICE_ACCOUNT_EMAIL}"
set_env GCP_PROJECT_ID "${derived_project_id}"
set_env GCP_PROJECT_NUMBER "${project_number}"
set_default GCP_REGION "us-west1"
set_default GCP_ZONE "us-west1-b"
set_default GCP_VM_NAME "poma-${DEPLOY_ENVIRONMENT}-free-tier"
set_default TF_STATE_BUCKET "poma-tf-state-${project_number}"
