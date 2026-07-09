from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from poma.broker import (
    GROSS_POSITION_VALUE_TAGS,
    NET_LIQUIDATION_TAGS,
    USD_CASH_TAGS,
    Broker,
    BrokerUnavailable,
    IbkrBroker,
    _account_summary_queries,
    _account_values_queries,
    _find_usd_account_amount,
    _has_account_amount,
    _ignored_account_balance_warnings,
)
from poma.models import AccountSnapshot


def rebalance_account_snapshot(broker: Broker) -> AccountSnapshot:
    """Read the account snapshot used for rebalance sizing.

    Generic brokers use their own snapshot implementation. IBKR gets a rebalance-specific reader
    that does not retry the same endpoint; it walks the independent balance sources exposed by the
    already-authenticated session so a transient ``accountSummary`` timeout can still succeed from
    the local ``accountValues`` cache.
    """
    if isinstance(broker, IbkrBroker):
        return _ibkr_rebalance_account_snapshot(broker)
    return broker.account_snapshot()


def _ibkr_rebalance_account_snapshot(broker: IbkrBroker) -> AccountSnapshot:
    ib = broker._connect()
    try:
        account = broker.settings.ibkr_account
        account_rows, source_warnings = _request_account_value_rows(ib, account)
        warnings = list(source_warnings)
        warnings.extend(_ignored_account_balance_warnings(account_rows, account))
        positions, position_warnings = broker._positions_from_ib(ib)
        warnings.extend(position_warnings)
        positions_market_value = sum(position.market_value for position in positions)

        cash_usd = _find_usd_account_amount(account_rows, USD_CASH_TAGS, account)
        if cash_usd is None and not _has_account_amount(account_rows, USD_CASH_TAGS, account):
            raise BrokerUnavailable(_missing_cash_message(source_warnings))
        if cash_usd is None:
            cash_usd = 0.0
            warnings.append(
                "IBKR did not report a USD cash balance; treating available USD cash as $0.00 "
                "and ignoring non-USD cash balances"
            )

        net_liquidation_usd = None
        net_liquidation = _find_usd_account_amount(account_rows, NET_LIQUIDATION_TAGS, account)
        if net_liquidation is not None:
            net_liquidation_usd = net_liquidation

        summary_positions = _find_usd_account_amount(account_rows, GROSS_POSITION_VALUE_TAGS, account)
        if summary_positions is not None and summary_positions > 0:
            positions_market_value = summary_positions

        return AccountSnapshot(
            cash_usd=cash_usd,
            positions=tuple(positions),
            positions_market_value_usd=positions_market_value,
            net_liquidation_usd=net_liquidation_usd,
            account_id=account,
            timestamp_utc=datetime.now(UTC).isoformat(),
            warnings=tuple(warnings),
        )
    finally:
        ib.disconnect()


def _request_account_value_rows(ib: object, account: str | None) -> tuple[list[object], list[str]]:
    rows: list[object] = []
    warnings: list[str] = []

    summary_rows, summary_warnings = _request_rows_from_source(
        "accountSummary",
        lambda query_account: ib.accountSummary(query_account),
        lambda: ib.accountSummary(),
        _account_summary_queries(account),
    )
    rows.extend(summary_rows)
    warnings.extend(summary_warnings)

    account_values = getattr(ib, "accountValues", None)
    if account_values is not None:
        value_rows, value_warnings = _request_rows_from_source(
            "accountValues",
            lambda query_account: account_values(query_account),
            lambda: account_values(),
            _account_values_queries(account),
        )
        rows.extend(value_rows)
        warnings.extend(value_warnings)

    return rows, warnings


def _request_rows_from_source(
    source: str,
    account_reader: Callable[[str], Iterable[object]],
    default_reader: Callable[[], Iterable[object]],
    accounts: Iterable[str],
) -> tuple[list[object], list[str]]:
    rows: list[object] = []
    warnings: list[str] = []
    for account in accounts:
        try:
            rows.extend(account_reader(account))
        except TypeError:
            try:
                rows.extend(default_reader())
            except Exception as exc:  # noqa: BLE001 - preserve broker source diagnostics
                warnings.append(_source_failure_message(source, account, exc))
            break
        except Exception as exc:  # noqa: BLE001 - try the next independent balance source
            warnings.append(_source_failure_message(source, account, exc))
    return rows, warnings


def _source_failure_message(source: str, account: str, exc: Exception) -> str:
    account_label = account or "default"
    detail = str(exc).strip() or exc.__class__.__name__
    return f"IBKR {source}(account={account_label}) failed while building rebalance snapshot: {detail}"


def _missing_cash_message(source_warnings: list[str]) -> str:
    if not source_warnings:
        return "IBKR did not return a cash balance for the configured account"
    return (
        "IBKR did not return a cash balance for the configured account; "
        "balance source diagnostics: " + "; ".join(source_warnings)
    )


__all__ = ["rebalance_account_snapshot"]
