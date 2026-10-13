#!/usr/bin/env bash
# Fail a GitHub deployment before provisioning or touching a VM when required
# per-environment Vars/Secrets are absent. Never print secret values.
set -euo pipefail

mode="${1:?usage: validate_github_environment.sh dry_run|paper|live}"
case "${mode}" in
  dry_run|paper|live) ;;
  *) echo "::error::invalid deployment trading mode: ${mode}" >&2; exit 1 ;;
esac

missing=()
require_var() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    missing+=("GitHub Environment Variable ${name}")
  fi
}
require_secret() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    missing+=("GitHub Environment Secret ${name}")
  fi
}

require_secret TELEGRAM_BOT_TOKEN
require_secret TELEGRAM_CHAT_ID

# Paper and live trades must carry explicit, reviewed investment and cash allocations.
# Calendar and rebalance time are optional: runtime defaults safely select London
# and the 14:30 local trading window for USD-listed LSE UCITS ETFs.
if [ "${mode}" != "dry_run" ]; then
  require_var INVESTMENT_STRATEGY_WEIGHTS
  require_var CASH_TARGET_PCT
fi
if [ -n "${LEGACY_STRATEGY_ALLOCATIONS:-}" ]; then
  echo "::error::Legacy STRATEGY_ALLOCATIONS GitHub variable/secret is not supported by the new deploy flow; migrate it to INVESTMENT_STRATEGY_WEIGHTS + CASH_TARGET_PCT" >&2
  exit 1
fi

case "${mode}" in
  paper)
    require_secret IBKR_ACCOUNT_PAPER
    require_secret IBKR_LOGIN_ID_PAPER
    require_secret IBKR_LOGIN_SECRET_PAPER
    ;;
  live)
    require_secret IBKR_ACCOUNT
    require_secret IBKR_LOGIN_ID
    require_secret IBKR_LOGIN_SECRET
    ;;
  dry_run)
    if [ -z "${IBKR_ACCOUNT_PAPER:-}" ] && [ -z "${IBKR_ACCOUNT:-}" ]; then
      missing+=("GitHub Environment Secret IBKR_ACCOUNT_PAPER or IBKR_ACCOUNT")
    fi
    ;;
esac

if [ "${#missing[@]}" -ne 0 ]; then
  echo "::error::POMA ${mode} deployment blocked: ${#missing[@]} missing mandatory settings" >&2
  printf '::error::Missing %s\n' "${missing[@]}" >&2
  exit 1
fi
echo "POMA ${mode} GitHub Environment inputs present (secret values are never logged)"
