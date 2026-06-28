from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from ib_insync import IB, LimitOrder, MarketOrder, Stock, Trade

from poma.config import OrderType, Settings, TradingMode
from poma.models import CurrentPosition, OrderResult, ProposedTrade

DONE_STATUSES = {"Filled", "Cancelled", "ApiCancelled", "Inactive"}

# Health probes use a dedicated client id offset so they never collide with the client id the
# scheduled trader connects with (a duplicate client id is rejected by the gateway).
HEALTH_CLIENT_ID_OFFSET = 90


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
    # Apply the same timeout to post-connect API requests.  ib_insync's default
    # RequestTimeout is 0 (falsy → no timeout), so reqCurrentTime() and portfolio()
    # can hang indefinitely when the gateway accepts the TCP handshake but stops
    # responding to subsequent API messages (e.g. during early-startup).
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

    def submit_trades(self, trades: list[ProposedTrade]) -> list[OrderResult]:
        ...


class DryRunBroker:
    def positions(self) -> list[CurrentPosition]:
        return []

    def submit_trades(self, trades: list[ProposedTrade]) -> list[OrderResult]:
        return [
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


class IbkrBroker:
    def __init__(self, settings: Settings) -> None:
        settings.assert_safe_for_execution()
        self.settings = settings

    def _connect(self) -> IB:
        ib = IB()
        ib.connect(
            self.settings.ibkr_host,
            self.settings.ibkr_port,
            clientId=self.settings.ibkr_client_id,
            account=self.settings.ibkr_account or "",
        )
        return ib

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

    def submit_trades(self, trades: list[ProposedTrade]) -> list[OrderResult]:
        if not trades:
            return []
        ib = self._connect()
        results: list[OrderResult] = []
        try:
            for proposed in trades:
                contract = Stock(proposed.ticker, "SMART", "USD")
                order = self._build_order(proposed)
                if self.settings.ibkr_account:
                    order.account = self.settings.ibkr_account
                submitted = ib.placeOrder(contract, order)
                self._wait_for_terminal_status(ib, submitted)
                results.append(self._order_result(proposed, submitted))
        finally:
            ib.disconnect()
        return results

    def _wait_for_terminal_status(self, ib: IB, trade: Trade) -> None:
        deadline = time.monotonic() + self.settings.order_status_timeout_seconds
        while time.monotonic() < deadline:
            status = trade.orderStatus.status
            if trade.isDone() or status in DONE_STATUSES:
                return
            ib.sleep(1.0)

        if self.settings.cancel_stale_orders:
            ib.cancelOrder(trade.order)
            cancel_deadline = time.monotonic() + min(10, self.settings.order_status_timeout_seconds)
            while time.monotonic() < cancel_deadline:
                status = trade.orderStatus.status
                if trade.isDone() or status in DONE_STATUSES:
                    return
                ib.sleep(1.0)

    def _order_result(self, proposed: ProposedTrade, trade: Trade) -> OrderResult:
        status = trade.orderStatus
        final_status = status.status or "submitted"
        message = None
        if final_status not in DONE_STATUSES:
            message = "order did not reach terminal status before timeout"
            if self.settings.cancel_stale_orders:
                message += "; cancel requested"
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


def _none_if_zero(value: float | None) -> float | None:
    if value is None or value == 0:
        return None
    return float(value)


def build_broker(settings: Settings) -> Broker:
    if settings.trading_mode == TradingMode.DRY_RUN:
        return DryRunBroker()
    return IbkrBroker(settings)
