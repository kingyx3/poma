#!/usr/bin/env bash
# POMA VM disk housekeeping. Never delete app state, data, reports, credentials
# or Docker volumes. Run as root from /etc/cron.d/poma-housekeeping (daily) or
# from the deploy's IAP SSH channel BEFORE uploading a bundle (predeploy).
set -euo pipefail

mode="${1:-daily}"
environment="${2:-unknown}"
case "${mode}" in daily|predeploy) ;; *) echo "Invalid housekeeping mode" >&2; exit 2;; esac
case "${environment}" in dev|stg|prd) ;; *) echo "Invalid POMA environment" >&2; exit 2;; esac

min_free_kib=4194304  # 4 GiB: leave room to unpack the scientific Python image.
app_dir="${POMA_APP_DIR:-/opt/poma}"
free_kib() { df -Pk / | awk 'NR==2 {print $4}'; }
say() { printf '[poma-housekeeping] %s\n' "$*"; }
disk_status() {
  say "Root available: $(free_kib) KiB (target: ${min_free_kib} KiB)"
  df -h /
}
run_bounded() {
  # Cleanup is best effort; persistent data is never a cleanup target.
  local label="$1"
  shift
  say "$label"
  timeout --kill-after=10s 180s "$@" || say "$label failed or timed out"
}

disk_status
initial_free="$(free_kib)"
if [[ "${mode}" == "predeploy" && "${initial_free}" -ge "${min_free_kib}" ]]; then
  say "Enough headroom for deployment bundle and image; no forced pruning"
  exit 0
fi

# A small VM sometimes fills with downloaded package metadata and persistent
# journal segments. Safe cleanup even if Docker has stopped responding.
if command -v apt-get >/dev/null 2>&1; then
  run_bounded "Clear apt package cache" apt-get clean
fi
if command -v journalctl >/dev/null 2>&1; then
  run_bounded "Rotate system journal" journalctl --rotate
  run_bounded "Bound archived system journal" journalctl --vacuum-size=100M
fi

if command -v docker >/dev/null 2>&1 && timeout 20s docker info >/dev/null 2>&1; then
  # Build cache, old stopped containers, and untagged images are disposable.
  # NEVER pass --volumes to docker prune.
  run_bounded "Remove old stopped containers" docker container prune -f --filter until=168h
  run_bounded "Remove old Docker build cache" docker builder prune -af --filter until=168h
  run_bounded "Remove dangling image layers" docker image prune -f

  # Only the dedicated dev VM may discard historical POMA SHA-tagged releases.
  # Preserve the image pinned by the current .compose.env even if no container
  # is running (POMA's cron runs use short-lived containers).
  if [[ "${environment}" == "dev" ]]; then
    current_image=""
    if [[ -r "${app_dir}/.compose.env" ]]; then
      current_image="$(sed -n 's/^POMA_IMAGE=//p' "${app_dir}/.compose.env" | head -n 1)"
    fi
    if [[ -z "${current_image}" ]]; then
      say "No pinned POMA_IMAGE found; retaining tagged images"
    else
      # Retain the newest older image as a rollback candidate. A failed prior
      # pull may have updated .compose.env to a tag that does not yet exist.
      fallback_image=""
      while IFS= read -r image; do
        [[ "${image}" =~ ^ghcr[.]io/kingyx3/poma:[0-9a-fA-F]{40}$ ]] || continue
        if [[ "${image}" != "${current_image}" ]]; then
          fallback_image="${image}"
          break
        fi
      done < <(docker image ls --format '{{.Repository}}:{{.Tag}}')
      while IFS= read -r image; do
        [[ "${image}" =~ ^ghcr[.]io/kingyx3/poma:[0-9a-fA-F]{40}$ ]] || continue
        if [[ "${image}" == "${current_image}" || "${image}" == "${fallback_image}" ]]; then
          continue
        fi
        say "Reclaim unused historical dev app image: ${image}"
        timeout --kill-after=10s 90s docker image rm "${image}" ||
          say "Image retained because Docker still references it: ${image}"
      done < <(docker image ls --format '{{.Repository}}:{{.Tag}}')
    fi

    # Under acute pressure, reclaim build cache regardless of age and
    # stopped containers (never live containers, images in use, or volumes).
    if [[ "$(free_kib)" -lt "${min_free_kib}" ]]; then
      run_bounded "Pressure: clear all unused builder cache" docker builder prune -af
      run_bounded "Pressure: clear stopped containers" docker container prune -f
      run_bounded "Pressure: remove dangling images" docker image prune -f
    fi
  fi
else
  say "Docker daemon unavailable; skipped Docker cleanup"
fi

# The dedicated POMA logs are rotated via /etc/logrotate.d/poma, preserving
# recently rotated diagnostics. Never touch state/, data/ or reports/.
if command -v logrotate >/dev/null 2>&1 && [[ -f /etc/logrotate.d/poma ]]; then
  run_bounded "Rotate bounded POMA cron logs" logrotate /etc/logrotate.d/poma
fi

disk_status
if [[ "${mode}" == "predeploy" && "$(free_kib)" -lt "${min_free_kib}" ]]; then
  say "Disk headroom still below 4 GiB; refusing deployment to avoid partial image extraction"
  exit 1
fi
