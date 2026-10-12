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

# Paper and live trades must carry an explicit, reviewed investment mandate.
# A missing var must not silently deploy a different investment allocation.
if [ "${mode}" != "dry_run" ]; then
  require_var INVESTMENT_STRATEGY_WEIGHTS
  require_var CASH_TARGET_PCT
  require_var CORE_ETF_WEIGHTS
  require_var MARKET_CALENDAR
  require_var REBALANCE_AFTER_OPEN_MINUTES
fi
if [ -n "${STRATEGY_ALLOCATIONS:-}" ] && [ -n "${INVESTMENT_STRATEGY_WEIGHTS:-}" ]; then
  echo "::error::Set INVESTMENT_STRATEGY_WEIGHTS or legacy STRATEGY_ALLOCATIONS, not both" >&2
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
