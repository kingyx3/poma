"""Read-only authentication probe, independent of trading and market-data entitlements."""
from __future__ import annotations

from enum import StrEnum

from poma.broker import _connect_ib
from poma.config import Settings, TradingMode
from poma.health import HEALTH_CONNECT_TIMEOUT_SECONDS

AUTH_CLIENT_ID_OFFSET = 190


class AuthStatus(StrEnum):
    AUTHENTICATED = 'authenticated'
    UNAVAILABLE = 'unavailable'
    CONFIGURATION_ERROR = 'configuration_error'
    DISABLED = 'disabled'


class RecoveryEvent(StrEnum):
    RESTART = 'restart'
    WAITING = 'waiting'
    RECOVERED = 'recovered'
    CONFIGURATION = 'configuration'
    RESTART_FAILED = 'restart_failed'


RECOVERY_MESSAGES = {
    RecoveryEvent.RESTART: (
        '🔐 Gateway authentication unavailable after repeated checks. Restarting Gateway to retry login. '
        'Approve IBKR Mobile if prompted; scheduled orders remain subject to broker readiness checks.'
    ),
    RecoveryEvent.WAITING: (
        '🔐 Gateway authentication is still unavailable. Approve IBKR Mobile if prompted, or inspect '
        'Gateway Ops for expired credentials, account/session restrictions or connectivity problems. '
        'Automatic restart attempts are rate limited.'
    ),
    RecoveryEvent.RECOVERED: (
        '✅ Gateway authenticated account access recovered. No orders were placed by the watchdog.'
    ),
    RecoveryEvent.CONFIGURATION: (
        '⚠️ Gateway watchdog detected an account/configuration mismatch. No restart attempted. '
        'Check IBKR_ACCOUNT, TRADING_MODE and the shared STATE_DIR in the deployed app.'
    ),
    RecoveryEvent.RESTART_FAILED: (
        '⚠️ Gateway watchdog could not restart the service. Inspect systemctl status ibgateway.'
    ),
}


def probe_authentication(settings: Settings) -> AuthStatus:
    if settings.trading_mode == TradingMode.DRY_RUN:
        return AuthStatus.DISABLED
    if not settings.ibkr_account:
        return AuthStatus.CONFIGURATION_ERROR
    # One bounded attempt per timer tick; repeated checks are the host policy's job.
    ib = None
    try:
        ib = _connect_ib(
            settings.model_copy(update={'ibkr_connect_attempts': 1}),
            client_id=settings.ibkr_client_id + AUTH_CLIENT_ID_OFFSET,
            timeout=HEALTH_CONNECT_TIMEOUT_SECONDS,
        )
        accounts = [account for account in ib.managedAccounts() if account]
        if not accounts:
            return AuthStatus.UNAVAILABLE
        if settings.ibkr_account not in accounts:
            return AuthStatus.CONFIGURATION_ERROR
        if not ib.isConnected() or ib.reqCurrentTime() is None:
            return AuthStatus.UNAVAILABLE
        return AuthStatus.AUTHENTICATED
    except Exception:  # noqa: BLE001 - report generic status without leaking broker/config values
        return AuthStatus.UNAVAILABLE
    finally:
        if ib is not None:
            ib.disconnect()
