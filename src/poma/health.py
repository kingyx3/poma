from __future__ import annotations

from dataclasses import dataclass

from poma.config import Settings, TradingMode
from poma.data import build_data_client


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def check_data_provider(settings: Settings) -> Check:
    """Confirm the configured market-data provider returns a usable snapshot."""
    try:
        client = build_data_client(settings)
        snapshot = client.current_universe_snapshot()
    except Exception as exc:  # noqa: BLE001 - report any provider failure as a failed check
        return Check("data_provider", False, f"{settings.data_provider}: {exc}")
    rows = len(snapshot)
    return Check(
        "data_provider",
        rows > 0,
        f"{settings.data_provider}: {rows} rows"
        + ("" if rows else " (provider returned no rows)"),
    )


def check_ibkr(settings: Settings) -> Check:
    """Confirm the IBKR API is reachable and authenticated for the active account."""
    # Imported lazily so health checks that do not touch IBKR (dry-run) avoid the dependency.
    from poma.broker import probe_ibkr

    if settings.trading_mode == TradingMode.DRY_RUN:
        return Check("ibkr", True, "skipped (dry_run mode does not use IBKR)")

    try:
        result = probe_ibkr(settings)
    except Exception as exc:  # noqa: BLE001 - any connection/auth failure is a failed check
        return Check("ibkr", False, f"{settings.ibkr_host}:{settings.ibkr_port} unreachable: {exc}")

    detail = (
        f"connected to {settings.ibkr_host}:{settings.ibkr_port}, "
        f"accounts={result.accounts or ['none']}, "
        f"server_time={result.server_time}, stock_positions={result.stock_positions}"
    )
    account_ok = (
        settings.ibkr_account is None or settings.ibkr_account in result.accounts
    )
    if not account_ok:
        return Check(
            "ibkr",
            False,
            f"configured IBKR_ACCOUNT={settings.ibkr_account} not in {result.accounts}",
        )
    return Check("ibkr", result.connected, detail)


def run_checks(settings: Settings) -> list[Check]:
    return [check_data_provider(settings), check_ibkr(settings)]
