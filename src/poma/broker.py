from __future__ import annotations

from typing import Protocol

from ib_insync import IB, MarketOrder, Stock

from poma.config import Settings, TradingMode
from poma.models import CurrentPosition, ProposedTrade


class Broker(Protocol):
    def positions(self) -> list[CurrentPosition]:
        ...

    def submit_trades(self, trades: list[ProposedTrade]) -> None:
        ...


class DryRunBroker:
    def positions(self) -> list[CurrentPosition]:
        return []

    def submit_trades(self, trades: list[ProposedTrade]) -> None:
        _ = trades


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

    def submit_trades(self, trades: list[ProposedTrade]) -> None:
        if not trades:
            return
        ib = self._connect()
        try:
            for trade in trades:
                contract = Stock(trade.ticker, "SMART", "USD")
                order = MarketOrder(trade.side.value, abs(trade.quantity))
                if self.settings.ibkr_account:
                    order.account = self.settings.ibkr_account
                ib.placeOrder(contract, order)
                ib.sleep(0.2)
        finally:
            ib.disconnect()


def build_broker(settings: Settings) -> Broker:
    if settings.trading_mode == TradingMode.DRY_RUN:
        return DryRunBroker()
    return IbkrBroker(settings)
