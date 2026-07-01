from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from conftest import make_settings

from poma.broker import IbkrBroker
from poma.models import OrderSide


@dataclass
class FakeOrder:
    orderId: int
    action: str
    orderRef: str = ""
    account: str = ""
    permId: int | None = None


@dataclass
class FakeOrderStatus:
    status: str = "Submitted"
    filled: float = 0.0
    remaining: float = 5.0
    avgFillPrice: float = 0.0


@dataclass
class FakeContract:
    symbol: str


@dataclass
class FakeTrade:
    order: FakeOrder
    orderStatus: FakeOrderStatus
    contract: FakeContract


@dataclass
class FakeIB:
    open_trades: list[FakeTrade] = field(default_factory=list)
    connected: bool = False
    RequestTimeout: float | None = None
    cancelled_orders: list[int] = field(default_factory=list)
    placed_orders: list[tuple[object, object]] = field(default_factory=list)
    next_order_id: int = 100

    def connect(self, *_args, **_kwargs) -> None:
        self.connected = True

    def isConnected(self) -> bool:  # noqa: N802 - mirrors ib_insync API
        return self.connected

    def managedAccounts(self) -> list[str]:  # noqa: N802
        return ["DU1234567"]

    def reqCurrentTime(self) -> str:  # noqa: N802
        return "2026-07-01T13:40:00Z"

    def reqAllOpenOrders(self) -> None:  # noqa: N802
        return None

    def openTrades(self) -> list[FakeTrade]:  # noqa: N802
        return self.open_trades

    def cancelOrder(self, order: FakeOrder) -> None:  # noqa: N802
        self.cancelled_orders.append(order.orderId)
        for trade in self.open_trades:
            if trade.order.orderId == order.orderId:
                trade.orderStatus.status = "Cancelled"

    def placeOrder(self, contract, order):  # noqa: N802, ANN201
        order.orderId = self.next_order_id
        self.next_order_id += 1
        self.placed_orders.append((contract, order))
        status = FakeOrderStatus(status="Submitted", filled=0.0, remaining=order.totalQuantity)
        trade = FakeTrade(order=order, orderStatus=status, contract=FakeContract(symbol=contract.symbol))
        self.open_trades.append(trade)
        return trade

    def sleep(self, _seconds: float) -> None:
        return None

    def disconnect(self) -> None:
        self.connected = False


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: str):
    env = {
        "APP_ENV": "test",
        "TRADING_MODE": "paper",
        "ALLOW_LIVE_TRADING": "false",
        "DATA_PROVIDER": "fixture",
        "IBKR_ACCOUNT": "DU1234567",
        "TELEGRAM_BOT_TOKEN": "token",
        "TELEGRAM_CHAT_ID": "123456",
    }
    env.update(overrides)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return make_settings(**{k: v for k, v in env.items()})


def test_fetch_open_order_snapshots_only_returns_poma_tagged_orders_for_the_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ib = FakeIB(
        open_trades=[
            FakeTrade(
                order=FakeOrder(orderId=1, action="BUY", orderRef="poma:run-1:0:AAPL:BUY", account="DU1234567"),
                orderStatus=FakeOrderStatus(status="Submitted", filled=0.0, remaining=5.0),
                contract=FakeContract(symbol="AAPL"),
            ),
            FakeTrade(
                order=FakeOrder(orderId=2, action="SELL", orderRef="", account="DU1234567"),
                orderStatus=FakeOrderStatus(status="Submitted", filled=0.0, remaining=3.0),
                contract=FakeContract(symbol="MSFT"),
            ),
            FakeTrade(
                order=FakeOrder(orderId=3, action="BUY", orderRef="poma:run-1:1:NVDA:BUY", account="DU7654321"),
                orderStatus=FakeOrderStatus(status="Submitted", filled=0.0, remaining=1.0),
                contract=FakeContract(symbol="NVDA"),
            ),
        ]
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    snapshots = broker.fetch_open_order_snapshots()

    assert len(snapshots) == 1
    assert snapshots[0].order_ref == "poma:run-1:0:AAPL:BUY"
    assert snapshots[0].ticker == "AAPL"
    assert snapshots[0].side == OrderSide.BUY


def test_cancel_order_cancels_matching_open_order(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = FakeIB(
        open_trades=[
            FakeTrade(
                order=FakeOrder(orderId=7, action="BUY", orderRef="poma:run-1:0:AAPL:BUY", account="DU1234567"),
                orderStatus=FakeOrderStatus(status="Submitted"),
                contract=FakeContract(symbol="AAPL"),
            ),
        ]
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    cancelled = broker.cancel_order(7)

    assert cancelled is True
    assert fake_ib.cancelled_orders == [7]


def test_cancel_order_returns_false_when_order_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = FakeIB(open_trades=[])
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    assert broker.cancel_order(999) is False


def test_replace_order_cancels_old_order_and_places_a_new_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = FakeIB(
        open_trades=[
            FakeTrade(
                order=FakeOrder(orderId=7, action="BUY", orderRef="poma:run-1:0:AAPL:BUY", account="DU1234567"),
                orderStatus=FakeOrderStatus(status="Submitted"),
                contract=FakeContract(symbol="AAPL"),
            ),
        ]
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    snapshot = broker.replace_order(
        order_id=7,
        ticker="AAPL",
        side=OrderSide.BUY,
        quantity=5.0,
        new_limit_price=101.5,
        order_ref="poma:run-1:0:AAPL:BUY:r1",
    )

    assert fake_ib.cancelled_orders == [7]
    assert snapshot.order_ref == "poma:run-1:0:AAPL:BUY:r1"
    assert snapshot.ticker == "AAPL"
    assert len(fake_ib.placed_orders) == 1
