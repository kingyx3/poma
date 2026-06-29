from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from ib_insync import IB, LimitOrder, MarketOrder, Stock, Trade

from poma.config import OrderType, Settings, TradingMode
from poma.models import CurrentPosition, OrderResult, ProposedTrade

DONE_STATUSES = {"Filled", "Cancelled", "ApiCancelled", "Inactive"}
SUCCESS_STATUSES = {"Filled"}
BROKER_UNAVAILABLE_STATUS = "BrokerUnavailable"

# Health probes use a dedicated client id offset so they never collide with the client id the
# scheduled trader connects with (a duplicate client id is rejected by the gateway).
HEALTH_CLIENT_ID_OFFSET = 90
IBKR_CONNECT_TIMEOUT_SECONDS = 20.0
OrderStatusCallback = Callable[[ProposedTrade, OrderResult], None]


class BrokerUnavailable(RuntimeError):
    """Raised when the IBKR API is not ready enough to safely submit orders."""


@dataclass(frozen=True)
class IbkrHealth:
    connected: bool
    accounts: list[str]
    server_time: str
    stock_positions: int


def probe_ibkr(settings: Settings, *, timeout: float = 20.0) -> IbkrHealth:
    """Open a short-lived IBKR connection to confirm the API is reachable and authenticated.

    Places no orders. Used by the ``doctor`` command and ops verification.
    """
    ib = IB()
    ib.connect(
        settings.ibkr_host,
        settings.ibkr_port,
        clientId=settings.ibkr_client_id + HEALTH_CLIENT_ID_OFFSET,
        account=settings.ibkr_account or "",
        timeout=timeout,
    )
    ib.RequestTimeout = timeout
    try:
        accounts = [account for account in ib.managedAccounts() if account]
        server_time = str(ib.reqCurrentTime())
        stock_positions = sum(1 for item in ib.portfolio() if item.contract.secType == "STK")
        return IbkrHealth(
            connected=ib.isConnected(),
            accounts=accounts,
            server_time=server_time,
            stock_positions=stock_positions,
        )
    finally:
        ib.disconnect()


class Broker(Protocol):
    def positions(self) -> list[CurrentPosition]:
        ...

    def submit_trades(
        self,
        trades: list[ProposedTrade],
        status_callback: OrderStatusCallback | None = None,
    ) -> list[OrderResult]:
        ...


class DryRunBroker:
    def positions(self) -> list[CurrentPosition]:
        return []

    def submit_trades(
        self,
        trades: list[ProposedTrade],
        status_callback: OrderStatusCallback | None = None,
    ) -> list[OrderResult]:
        results = [
            OrderResult(
                ticker=trade.ticker,
                side=trade.side,
                quantity=trade.quantity,
                notional=trade.notional,
                order_id=None,
                status="dry_run",
                filled=0.0,
                average_fill_price=None,
                message="order not submitted in dry_run mode",
            )
            for trade in trades
        ]
        if status_callback is not None:
            for trade, result in zip(trades, results, strict=True):
                status_callback(trade, result)
        return results


class IbkrBroker:
    def __init__(self, settings: Settings) -> None:
        settings.assert_safe_for_execution()
        self.settings = settings

    def _connect(self) -> IB:
        ib = IB()
        try:
            ib.connect(
                self.settings.ibkr_host,
                self.settings.ibkr_port,
                clientId=self.settings.ibkr_client_id,
                account=self.settings.ibkr_account or "",
                timeout=IBKR_CONNECT_TIMEOUT_SECONDS,
            )
            ib.RequestTimeout = IBKR_CONNECT_TIMEOUT_SECONDS
            self._assert_connected(ib)
        except Exception:
            ib.disconnect()
            raise
        return ib

    def _assert_connected(self, ib: IB) -> None:
        if not ib.isConnected():
            raise BrokerUnavailable(
                f"IBKR API is not connected at {self.settings.ibkr_host}:{self.settings.ibkr_port}"
            )

    def _assert_ready_for_orders(self, ib: IB) -> None:
        """Validate the API handshake before creating any order objects.

        A listening socket is not enough; Gateway must be authenticated, expose the configured
        account, and answer a lightweight server-time request. Failing here is safe because no
        order has been accepted by IBKR yet.
        """
        self._assert_connected(ib)
        accounts = [account for account in ib.managedAccounts() if account]
        if self.settings.ibkr_account and self.settings.ibkr_account not in accounts:
            raise BrokerUnavailable(
                f"configured IBKR_ACCOUNT={self.settings.ibkr_account} not in {accounts}"
            )
        ib.reqCurrentTime()
        self._assert_connected(ib)

    def positions(self) -> list[CurrentPosition]:
        ib = self._connect()
        try:
            rows: list[CurrentPosition] = []
            for item in ib.portfolio():
                if item.contract.secType != "STK":
                    continue
                account_mismatch = (
                    self.settings.ibkr_account
                    and item.account != self.settings.ibkr_account
                )
                if account_mismatch:
                    continue
                rows.append(
                    CurrentPosition(
                        ticker=item.contract.symbol.upper(),
                        quantity=float(item.position),
                        market_value=float(item.marketValue),
                    )
                )
            return rows
        finally:
            ib.disconnect()

    def submit_trades(
        self,
        trades: list[ProposedTrade],
        status_callback: OrderStatusCallback | None = None,
    ) -> list[OrderResult]:
        if not trades:
            return []
        ib: IB | None = None
        try:
            ib = self._connect()
            self._assert_ready_for_orders(ib)
        except Exception as exc:  # noqa: BLE001 - normalize connection/auth failures in reports
            if ib is not None:
                ib.disconnect()
            return self._unsubmitted_results(
                trades,
                status_callback,
                f"broker unavailable before submitting orders; no orders submitted: {exc}",
            )

        results: list[OrderResult] = []
        try:
            for index, proposed in enumerate(trades):
                try:
                    self._assert_connected(ib)
                    contract = Stock(proposed.ticker, "SMART", "USD")
                    order = self._build_order(proposed)
                    if self.settings.ibkr_account:
                        order.account = self.settings.ibkr_account
                    submitted = ib.placeOrder(contract, order)
                    self._emit_status(
                        status_callback,
                        proposed,
                        self._order_result(
                            proposed,
                            submitted,
                            fallback_status="Submitted",
                            message="order submitted",
                        ),
                    )
                    last_status, timed_out = self._wait_for_terminal_status(
                        ib,
                        submitted,
                        proposed,
                        status_callback=status_callback,
                    )
                    final_message = None
                    if timed_out:
                        final_message = "order did not reach terminal status before timeout"
                        if self.settings.cancel_stale_orders:
                            final_message += "; cancel requested"
                    result = self._order_result(proposed, submitted, message=final_message)
                    if result.status != last_status or final_message:
                        self._emit_status(status_callback, proposed, result)
                    results.append(result)
                except Exception as exc:  # noqa: BLE001 - report per-order failures without hiding context
                    if _looks_like_connection_failure(ib, exc):
                        results.extend(
                            self._unsubmitted_results(
                                trades[index:],
                                status_callback,
                                f"broker connection lost before order acceptance; no further "
                                f"orders submitted: {exc}",
                            )
                        )
                        break
                    result = _manual_result(proposed, "Failed", f"order not accepted by broker: {exc}")
                    self._emit_status(status_callback, proposed, result)
                    results.append(result)
        finally:
            ib.disconnect()
        return results

    def _unsubmitted_results(
        self,
        trades: Sequence[ProposedTrade],
        status_callback: OrderStatusCallback | None,
        message: str,
    ) -> list[OrderResult]:
        results = [_manual_result(trade, BROKER_UNAVAILABLE_STATUS, message) for trade in trades]
        if status_callback is not None:
            for trade, result in zip(trades, results, strict=True):
                status_callback(trade, result)
        return results

    @staticmethod
    def _emit_status(
        callback: OrderStatusCallback | None,
        proposed: ProposedTrade,
        result: OrderResult,
    ) -> None:
        if callback is not None:
            callback(proposed, result)

    def _wait_for_terminal_status(
        self,
        ib: IB,
        trade: Trade,
        proposed: ProposedTrade,
        *,
        status_callback: OrderStatusCallback | None = None,
    ) -> tuple[str, bool]:
        deadline = time.monotonic() + self.settings.order_status_timeout_seconds
        last_status = trade.orderStatus.status or "Submitted"
        while time.monotonic() < deadline:
            status = trade.orderStatus.status or last_status
            if status != last_status:
                last_status = status
                self._emit_status(
                    status_callback,
                    proposed,
                    self._order_result(proposed, trade, fallback_status=status),
                )
            if trade.isDone() or status in DONE_STATUSES:
                return status, False
            ib.sleep(1.0)

        timeout_message = "order did not reach terminal status before timeout"
        if self.settings.cancel_stale_orders:
            ib.cancelOrder(trade.order)
            cancel_message = f"{timeout_message}; cancel requested"
            self._emit_status(
                status_callback,
                proposed,
                self._order_result(
                    proposed,
                    trade,
                    fallback_status="PendingCancel",
                    message=cancel_message,
                ),
            )
            last_status = "PendingCancel"
            cancel_deadline = time.monotonic() + min(10, self.settings.order_status_timeout_seconds)
            while time.monotonic() < cancel_deadline:
                status = trade.orderStatus.status or last_status
                if status != last_status:
                    last_status = status
                    self._emit_status(
                        status_callback,
                        proposed,
                        self._order_result(proposed, trade, fallback_status=status),
                    )
                if trade.isDone() or status in DONE_STATUSES:
                    return status, False
                ib.sleep(1.0)
        return last_status, True

    def _order_result(
        self,
        proposed: ProposedTrade,
        trade: Trade,
        *,
        fallback_status: str = "submitted",
        message: str | None = None,
    ) -> OrderResult:
        status = trade.orderStatus
        final_status = status.status or fallback_status
        return OrderResult(
            ticker=proposed.ticker,
            side=proposed.side,
            quantity=proposed.quantity,
            notional=proposed.notional,
            order_id=getattr(trade.order, "orderId", None),
            status=final_status,
            filled=float(status.filled or 0.0),
            average_fill_price=_none_if_zero(status.avgFillPrice),
            message=message,
        )

    def _build_order(self, trade: ProposedTrade) -> MarketOrder | LimitOrder:
        if self.settings.order_type == OrderType.MARKET:
            return MarketOrder(trade.side.value, abs(trade.quantity))
        if trade.limit_price is None:
            raise ValueError(f"missing limit price for {trade.ticker}")
        return LimitOrder(trade.side.value, abs(trade.quantity), trade.limit_price)


def _looks_like_connection_failure(ib: IB, exc: Exception) -> bool:
    if isinstance(exc, BrokerUnavailable):
        return True
    try:
        if not ib.isConnected():
            return True
    except Exception:  # noqa: BLE001 - an unreadable connection state is also unsafe
        return True
    message = str(exc).lower()
    return "not connected" in message or "connection" in message


def _manual_result(proposed: ProposedTrade, status: str, message: str | None = None) -> OrderResult:
    return OrderResult(
        ticker=proposed.ticker,
        side=proposed.side,
        quantity=proposed.quantity,
        notional=proposed.notional,
        order_id=None,
        status=status,
        filled=0.0,
        average_fill_price=None,
        message=message,
    )


def _none_if_zero(value: float | None) -> float | None:
    if value is None or value == 0:
        return None
    return float(value)


def order_results_have_issues(results: list[OrderResult]) -> bool:
    return any(result.status not in SUCCESS_STATUSES or result.message for result in results)


def build_broker(settings: Settings) -> Broker:
    if settings.trading_mode == TradingMode.DRY_RUN:
        return DryRunBroker()
    return IbkrBroker(settings)
