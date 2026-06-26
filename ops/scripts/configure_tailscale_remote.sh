#!/usr/bin/env bash
set -euo pipefail

: "${POMA_TAILSCALE_AUTHKEY:?POMA_TAILSCALE_AUTHKEY is required}"
: "${POMA_TAILSCALE_HOSTNAME:?POMA_TAILSCALE_HOSTNAME is required}"

run_with_timeout() {
  local seconds="$1"
  shift
  timeout --kill-after=30s "${seconds}" "$@"
}

apt_locks_busy() {
  if command -v fuser >/dev/null 2>&1; then
    fuser /var/lib/dpkg/lock-frontend \
      /var/lib/dpkg/lock \
      /var/lib/apt/lists/lock \
      /var/cache/apt/archives/lock >/dev/null 2>&1
    return $?
  fi

  pgrep -f '(apt-get|apt.systemd.daily|dpkg|unattended-upgrade)' >/dev/null 2>&1
}

wait_for_apt() {
  local waited_seconds=0
  local max_wait_seconds=120

  while apt_locks_busy; do
    if [ "${waited_seconds}" -ge "${max_wait_seconds}" ]; then
      echo "Timed out waiting for apt/dpkg locks to be released." >&2
      exit 1
    fi

    echo "Waiting for apt/dpkg locks to be released before installing Tailscale..."
    sleep 5
    waited_seconds=$((waited_seconds + 5))
  done
}

install_tailscale_if_missing() {
  if command -v tailscale >/dev/null 2>&1; then
    return 0
  fi

  wait_for_apt
  install -d -m 0755 /usr/share/keyrings
  curl --connect-timeout 5 --max-time 30 -fsSL \
    https://pkgs.tailscale.com/stable/ubuntu/jammy.noarmor.gpg \
    -o /usr/share/keyrings/tailscale-archive-keyring.gpg
  chmod 0644 /usr/share/keyrings/tailscale-archive-keyring.gpg
  curl --connect-timeout 5 --max-time 30 -fsSL \
    https://pkgs.tailscale.com/stable/ubuntu/jammy.tailscale-keyring.list \
    -o /etc/apt/sources.list.d/tailscale.list
  chmod 0644 /etc/apt/sources.list.d/tailscale.list
  run_with_timeout 3m apt-get update
  run_with_timeout 3m apt-get install -y tailscale
}

is_tailscale_connected() {
  command -v tailscale >/dev/null 2>&1 \
    && tailscale status --json 2>/dev/null \
    | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("BackendState") == "Running" else 1)'
}

install_tailscale_if_missing
run_with_timeout 45s systemctl enable --now tailscaled

if is_tailscale_connected; then
  echo "Tailscale is already connected; skipping tailscale up."
else
  auth_key_file="$(mktemp)"
  cleanup() {
    rm -f "${auth_key_file}"
  }
  trap cleanup EXIT
  printf '%s' "${POMA_TAILSCALE_AUTHKEY}" > "${auth_key_file}"
  run_with_timeout 90s tailscale up \
    --auth-key="file:${auth_key_file}" \
    --hostname="${POMA_TAILSCALE_HOSTNAME}" \
    --accept-dns=false
fi

run_with_timeout 20s tailscale status
