from __future__ import annotations

from dataclasses import dataclass

import pytest
from conftest import make_settings

from poma.account_snapshot import rebalance_account_snapshot
from poma.broker import BrokerUnavailable, IbkrBroker


@dataclass(frozen=True)
class AccountSummaryRow:
    tag: str
    value: str
    currency: str = "USD"
    account: str = "DU1234567"


class FakeContract:
    secType = "STK"
    symbol = "AAPL"


class FakePortfolioItem:
    contract = FakeContract()
    account = "DU1234567"
    position = 5
    marketValue = 5_000


class BaseFakeIB:
    def __init__(self) -> None:
        self.connected = False
        self.RequestTimeout = None

    def connect(self, *_args, **_kwargs) -> None:
        self.connected = True

    def isConnected(self) -> bool:  # noqa: N802 - mirrors ib_insync API
        return self.connected

    def reqMarketDataType(self, _data_type: int) -> None:  # noqa: N802 - mirrors ib_insync API
        return None

    def portfolio(self) -> list[FakePortfolioItem]:
        return [FakePortfolioItem()]

    def disconnect(self) -> None:
        self.connected = False


def _settings() -> object:
    return make_settings(
        TRADING_MODE="paper",
        IBKR_ACCOUNT="DU1234567",
        MAX_POSITION_PCT=1.0,
        MAX_TURNOVER_PCT=1.0,
        MAX_ORDER_NOTIONAL_USD=100_000.0,
    )


def test_ibkr_rebalance_snapshot_uses_account_values_when_account_summary_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeIB(BaseFakeIB):
        def __init__(self) -> None:
            super().__init__()
            self.account_summary_queries: list[str] = []
            self.account_value_queries: list[str] = []

        def accountSummary(self, account: str = "") -> list[AccountSummaryRow]:  # noqa: N802
            self.account_summary_queries.append(account)
            raise TimeoutError()

        def accountValues(self, account: str = "") -> list[AccountSummaryRow]:  # noqa: N802
            self.account_value_queries.append(account)
            return [
                AccountSummaryRow("TotalCashValue", "15000"),
                AccountSummaryRow("NetLiquidation", "20000"),
                AccountSummaryRow("GrossPositionValue", "5000"),
            ]

    fake_ib = FakeIB()
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    snapshot = rebalance_account_snapshot(IbkrBroker(_settings()))

    assert fake_ib.account_summary_queries == ["DU1234567", ""]
    assert fake_ib.account_value_queries == ["DU1234567", ""]
    assert snapshot.cash_usd == 15_000
    assert snapshot.positions_market_value_usd == 5_000
    assert snapshot.total_value_usd == 20_000
    assert snapshot.net_liquidation_usd == 20_000
    assert any(
        "IBKR accountSummary(account=DU1234567) failed while building rebalance snapshot: TimeoutError" in warning
        for warning in snapshot.warnings
    )


def test_ibkr_rebalance_snapshot_fails_closed_when_no_balance_source_returns_cash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeIB(BaseFakeIB):
        def accountSummary(self, _account: str = "") -> list[AccountSummaryRow]:  # noqa: N802
            raise TimeoutError()

        def accountValues(self, _account: str = "") -> list[AccountSummaryRow]:  # noqa: N802
            raise RuntimeError("account value cache empty")

    monkeypatch.setattr("poma.broker.IB", FakeIB)

    with pytest.raises(BrokerUnavailable) as exc_info:
        rebalance_account_snapshot(IbkrBroker(_settings()))

    message = str(exc_info.value)
    assert "IBKR did not return a cash balance for the configured account" in message
    assert "accountSummary(account=DU1234567) failed" in message
    assert "accountValues(account=DU1234567) failed" in message
