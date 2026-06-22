from __future__ import annotations

from typing import Protocol

from ib_insync import IB, LimitOrder, MarketOrder, Stock

from poma.config import OrderType, Settings, TradingMode
from poma.models import CurrentPosition, OrderResult, ProposedTrade


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
                ib.sleep(1.0)
                status = submitted.orderStatus
                results.append(
                    OrderResult(
                        ticker=proposed.ticker,
                        side=proposed.side,
                        quantity=proposed.quantity,
                        notional=proposed.notional,
                        order_id=getattr(submitted.order, "orderId", None),
                        status=status.status or "submitted",
                        filled=float(status.filled or 0.0),
                        average_fill_price=_none_if_zero(status.avgFillPrice),
                        message=None,
                    )
                )
        finally:
            ib.disconnect()
        return results

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
