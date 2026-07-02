from __future__ import annotations

import pytest
from conftest import make_settings

from poma.broker import BROKER_UNAVAILABLE_STATUS, IbkrBroker, _probe_trading_permissions
from poma.models import OrderSide, ProposedTrade


class _WhatIfCapturingIB:
    def __init__(self) -> None:
        self.captured_orders: list[object] = []

    def whatIfOrder(self, _contract, order):  # noqa: N802, ANN202 - ib_insync shape
        self.captured_orders.append(order)

        class State:
            warningText = ""
            initMarginChange = ""

        return State()


def test_trading_permission_probe_sends_transmit_true_what_if_order() -> None:
    fake_ib = _WhatIfCapturingIB()
    settings = make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU1234567")

    ok, _message = _probe_trading_permissions(fake_ib, settings)

    assert ok is True
    # IBKR rejects what-if orders with transmit=False (Error 321); whatIf=True alone already
    # guarantees the order is never executed.
    assert fake_ib.captured_orders[0].whatIf is True
    assert fake_ib.captured_orders[0].transmit is True


def test_cash_quantity_mode_also_probes_a_cash_quantity_what_if_order() -> None:
    fake_ib = _WhatIfCapturingIB()
    settings = make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU1234567")

    ok, message = _probe_trading_permissions(fake_ib, settings)

    assert ok is True
    assert "cash-quantity what-if probe accepted" in message
    probe = fake_ib.captured_orders[1]
    assert probe.whatIf is True
    assert probe.transmit is True
    assert probe.totalQuantity == 0
    assert probe.cashQty == 25.0


def test_whole_shares_mode_skips_the_cash_quantity_probe() -> None:
    fake_ib = _WhatIfCapturingIB()
    settings = make_settings(
        TRADING_MODE="paper",
        IBKR_ACCOUNT="DU1234567",
        FRACTIONAL_ORDER_MODE="whole_shares",
    )

    ok, _message = _probe_trading_permissions(fake_ib, settings)

    assert ok is True
    assert len(fake_ib.captured_orders) == 1


def test_cash_quantity_probe_failure_reports_an_actionable_reason() -> None:
    class FakeIB:
        def __init__(self) -> None:
            self.calls = 0

        def whatIfOrder(self, _contract, order):  # noqa: N802, ANN202 - ib_insync shape
            self.calls += 1
            # The cash-quantity probe is the one that carries its sizing in cashQty instead
            # of a share count.
            if not order.totalQuantity:
                raise RuntimeError("10243: Fractional-sized order cannot be placed via API")

            class State:
                warningText = ""
                initMarginChange = ""

            return State()

    settings = make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU1234567")

    ok, message = _probe_trading_permissions(FakeIB(), settings)

    assert ok is False
    assert "fractional share trading" in message
    assert "FRACTIONAL_ORDER_MODE=whole_shares" in message
    assert "10243" in message


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
