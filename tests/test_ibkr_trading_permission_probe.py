from __future__ import annotations

import pytest
from conftest import make_settings

from poma.broker import BROKER_UNAVAILABLE_STATUS, IbkrBroker
from poma.models import OrderSide, ProposedTrade


def test_ibkr_broker_blocks_orders_when_session_is_not_trade_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeIB:
        def __init__(self) -> None:
            self.connected = False
            self.place_order_calls = 0
            self.RequestTimeout = None

        def connect(self, *_args, **_kwargs) -> None:
            self.connected = True

        def isConnected(self) -> bool:  # noqa: N802 - mirrors ib_insync API
            return self.connected

        def managedAccounts(self) -> list[str]:  # noqa: N802 - mirrors ib_insync API
            return ["DU1234567"]

        def reqCurrentTime(self) -> str:  # noqa: N802 - mirrors ib_insync API
            return "2026-06-29T13:40:00Z"

        def reqMarketDataType(self, _data_type: int) -> None:  # noqa: N802 - mirrors ib_insync API
            return None

        def whatIfOrder(self, *_args, **_kwargs):  # noqa: N802, ANN202 - ib_insync shape
            raise RuntimeError("You are logged in without Trading/Market Data permissions")

        def placeOrder(self, *_args, **_kwargs):  # noqa: N802, ANN202 - ib_insync shape
            self.place_order_calls += 1
            raise AssertionError("placeOrder must not be called for a read-only session")

        def disconnect(self) -> None:
            self.connected = False

    instances: list[FakeIB] = []

    def fake_ib() -> FakeIB:
        instance = FakeIB()
        instances.append(instance)
        return instance

    monkeypatch.setattr("poma.broker.IB", fake_ib)
    broker = IbkrBroker(
        make_settings(
            TRADING_MODE="paper",
            IBKR_ACCOUNT="DU1234567",
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="123456",
        )
    )
    trade = ProposedTrade("AAPL", OrderSide.BUY, 1.0, 100.0, 100.0, 100.1, "rebalance")

    results = broker.submit_trades([trade])

    assert instances[0].place_order_calls == 0
    assert [result.status for result in results] == [BROKER_UNAVAILABLE_STATUS]
    assert "not trading-enabled" in str(results[0].message)
    assert "without Trading/Market Data permissions" in str(results[0].message)
