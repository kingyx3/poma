from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from conftest import make_settings

from poma.execution_manager import ExecutionManager
from poma.ibkr_order_history import fetch_completed_order_snapshots
from poma.models import OpenOrderSnapshot, OrderSide
from poma.order_lifecycle import OrderLedgerEntry, OrderLifecycleState
from poma.order_store import OrderStore


class HistoryBroker:
    def __init__(
        self,
        completed: list[OpenOrderSnapshot] | None = None,
        *,
        history_error: Exception | None = None,
    ) -> None:
        self.completed = completed or []
        self.history_error = history_error

    def fetch_open_order_snapshots(self) -> list[OpenOrderSnapshot]:
        return []

    def fetch_completed_order_snapshots(self) -> list[OpenOrderSnapshot]:
        if self.history_error is not None:
            raise self.history_error
        return list(self.completed)


def _accepted_entry(*, state: OrderLifecycleState = OrderLifecycleState.BROKER_ACCEPTED) -> OrderLedgerEntry:
    return OrderLedgerEntry(
        ledger_key="poma:run-1:0:RY:SELL",
        order_ref="poma:run-1:0:RY:SELL",
        run_id="run-1",
        session_date="2026-08-28",
        ticker="RY",
        side=OrderSide.SELL,
        quantity=1.0,
        limit_price=204.0,
        order_id=207,
        perm_id=9007,
        lifecycle_state=state,
        raw_status="NotOpenUnverified" if state == OrderLifecycleState.UNKNOWN else "PreSubmitted",
        filled_qty=0.0,
        remaining_qty=1.0,
    )


def _filled_ry_snapshot() -> OpenOrderSnapshot:
    return OpenOrderSnapshot(
        order_ref="poma:run-1:0:RY:SELL",
        order_id=207,
        perm_id=9007,
        ticker="RY",
        side=OrderSide.SELL,
        raw_status="Filled",
        filled=1.0,
        remaining=0.0,
        avg_fill_price=204.25,
    )


def test_reconcile_resolves_disappeared_open_order_from_completed_history(tmp_path) -> None:
    store = OrderStore(tmp_path)
    store.upsert(_accepted_entry())
    manager = ExecutionManager(HistoryBroker([_filled_ry_snapshot()]), store, make_settings())

    summary = manager.reconcile()

    assert summary.checked == 1
    assert len(summary.updates) == 1
    update = summary.updates[0]
    assert update.action == "closed"
    assert update.matched is True
    assert update.entry.lifecycle_state == OrderLifecycleState.FILLED
    assert update.entry.filled_qty == 1.0
    assert store.load_open_orders() == []
    latest = store.get_latest_many([update.entry.ledger_key])[update.entry.ledger_key]
    assert latest.lifecycle_state == OrderLifecycleState.FILLED


def test_reconcile_can_recover_existing_unknown_when_completed_history_appears(tmp_path) -> None:
    store = OrderStore(tmp_path)
    store.upsert(_accepted_entry(state=OrderLifecycleState.UNKNOWN))
    manager = ExecutionManager(HistoryBroker([_filled_ry_snapshot()]), store, make_settings())

    summary = manager.reconcile()

    assert summary.updates[0].entry.lifecycle_state == OrderLifecycleState.FILLED
    assert summary.updates[0].action == "closed"
    assert store.load_open_orders() == []


def test_completed_history_failure_keeps_order_fail_closed_without_crashing_reconcile(tmp_path) -> None:
    store = OrderStore(tmp_path)
    store.upsert(_accepted_entry())
    manager = ExecutionManager(
        HistoryBroker(history_error=TimeoutError("completed history unavailable")),
        store,
        make_settings(),
    )

    summary = manager.reconcile()

    update = summary.updates[0]
    assert update.entry.lifecycle_state == OrderLifecycleState.UNKNOWN
    assert update.entry.raw_status == "NotOpenUnverified"
    assert update.action == "unverified"
    assert update.matched is False


@dataclass
class FakeExecution:
    shares: float
    price: float


@dataclass
class FakeFill:
    execution: FakeExecution


@dataclass
class FakeOrder:
    orderId: int
    permId: int
    orderRef: str
    account: str
    action: str
    totalQuantity: float


@dataclass
class FakeOrderStatus:
    status: str


@dataclass
class FakeContract:
    symbol: str


@dataclass
class FakeTrade:
    order: FakeOrder
    orderStatus: FakeOrderStatus
    contract: FakeContract
    fills: list[FakeFill] = field(default_factory=list)

    def filled(self) -> float:
        return sum(fill.execution.shares for fill in self.fills)


@dataclass
class FakeHistoryIB:
    completed: list[FakeTrade]
    hydrated: list[FakeTrade]
    executions_error: Exception | None = None
    disconnected: bool = False

    def reqCompletedOrders(self, apiOnly: bool) -> list[FakeTrade]:  # noqa: N802
        assert apiOnly is True
        return self.completed

    def reqExecutions(self) -> list[FakeFill]:  # noqa: N802
        if self.executions_error is not None:
            raise self.executions_error
        return []

    def trades(self) -> list[FakeTrade]:
        return self.hydrated

    def disconnect(self) -> None:
        self.disconnected = True


def _fake_trade(
    *,
    status: str,
    order_ref: str = "poma:run-1:0:RY:SELL",
    account: str = "DU1234567",
    action: str = "SELL",
    quantity: float = 1.0,
    perm_id: int = 9007,
    fills: list[FakeFill] | None = None,
) -> FakeTrade:
    return FakeTrade(
        order=FakeOrder(
            orderId=207,
            permId=perm_id,
            orderRef=order_ref,
            account=account,
            action=action,
            totalQuantity=quantity,
        ),
        orderStatus=FakeOrderStatus(status=status),
        contract=FakeContract(symbol="RY"),
        fills=list(fills or []),
    )


def test_ibkr_completed_filled_order_infers_full_quantity_when_execution_history_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = _fake_trade(status="Filled")
    fake_ib = FakeHistoryIB(
        completed=[completed],
        hydrated=[completed],
        executions_error=TimeoutError("execution history unavailable"),
    )
    monkeypatch.setattr("poma.ibkr_order_history._connect_ib", lambda *_args, **_kwargs: fake_ib)
    settings = make_settings(IBKR_ACCOUNT="DU1234567")

    snapshots = fetch_completed_order_snapshots(settings, {"poma:run-1:0:RY:SELL"})

    assert len(snapshots) == 1
    assert snapshots[0].raw_status == "Filled"
    assert snapshots[0].filled == 1.0
    assert snapshots[0].remaining == 0.0
    assert snapshots[0].side == OrderSide.SELL
    assert fake_ib.disconnected is True


def test_ibkr_completed_cancelled_order_uses_execution_fills_for_partial_fill_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = _fake_trade(status="Cancelled")
    hydrated = _fake_trade(
        status="Cancelled",
        fills=[FakeFill(FakeExecution(0.4, 100.0)), FakeFill(FakeExecution(0.1, 101.0))],
    )
    fake_ib = FakeHistoryIB(completed=[completed], hydrated=[hydrated])
    monkeypatch.setattr("poma.ibkr_order_history._connect_ib", lambda *_args, **_kwargs: fake_ib)
    settings = make_settings(IBKR_ACCOUNT="DU1234567")

    snapshots = fetch_completed_order_snapshots(settings)

    assert len(snapshots) == 1
    assert snapshots[0].raw_status == "Cancelled"
    assert snapshots[0].filled == pytest.approx(0.5)
    assert snapshots[0].remaining == pytest.approx(0.5)
    assert snapshots[0].avg_fill_price == pytest.approx(100.2)


def test_ibkr_completed_history_filters_non_poma_and_other_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    wanted = _fake_trade(status="Filled")
    manual = _fake_trade(status="Filled", order_ref="manual-order", perm_id=9008)
    other_account = _fake_trade(status="Filled", account="DU9999999", perm_id=9009)
    fake_ib = FakeHistoryIB(
        completed=[wanted, manual, other_account],
        hydrated=[wanted, manual, other_account],
    )
    monkeypatch.setattr("poma.ibkr_order_history._connect_ib", lambda *_args, **_kwargs: fake_ib)
    settings = make_settings(IBKR_ACCOUNT="DU1234567")

    snapshots = fetch_completed_order_snapshots(settings)

    assert [snapshot.order_ref for snapshot in snapshots] == ["poma:run-1:0:RY:SELL"]
