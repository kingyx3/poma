#!/usr/bin/env bash
set -euo pipefail

: "${GCP_PROJECT_ID:?}"
: "${GCP_ZONE:?}"
: "${GCP_VM_NAME:?}"

gcloud config set project "${GCP_PROJECT_ID}"
ssh_common=(--zone "${GCP_ZONE}" --tunnel-through-iap --ssh-key-expire-after=15m --quiet)

gcloud compute ssh "${GCP_VM_NAME}" "${ssh_common[@]}" --command \
  'while [ ! -f /var/lib/cloud/instance/boot-finished ]; do sleep 5; done'
gcloud compute scp \
  ops/scripts/install_ibc_config_helper.py \
  "${GCP_VM_NAME}:/tmp/install_ibc_config_helper.py" \
  "${ssh_common[@]}"
gcloud compute scp \
  ops/scripts/ensure_ibgateway_service.sh \
  "${GCP_VM_NAME}:/tmp/ensure_ibgateway_service.sh" \
  "${ssh_common[@]}"
gcloud compute ssh "${GCP_VM_NAME}" "${ssh_common[@]}" --command '
  set -euo pipefail
  sudo python3 /tmp/install_ibc_config_helper.py
  sudo sh /tmp/ensure_ibgateway_service.sh
  rm -f /tmp/install_ibc_config_helper.py /tmp/ensure_ibgateway_service.sh
  test -s /usr/local/bin/poma-configure-ibc
  echo "Gateway helper and service refreshed."
'
