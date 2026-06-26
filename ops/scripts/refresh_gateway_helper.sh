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
gcloud compute ssh "${GCP_VM_NAME}" "${ssh_common[@]}" --command '
  set -euo pipefail
  sudo python3 /tmp/install_ibc_config_helper.py
  rm -f /tmp/install_ibc_config_helper.py
  test -s /usr/local/bin/poma-configure-ibc
  echo "Gateway helper refreshed."
'
