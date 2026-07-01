from __future__ import annotations

from dataclasses import replace as dc_replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import make_settings

from poma.execution_manager import ExecutionManager
from poma.models import (
    AccountSnapshot,
    OpenOrderSnapshot,
    OrderResult,
    OrderSide,
    ProposedTrade,
    RebalancePlan,
)
from poma.order_lifecycle import OrderLifecycleState
from poma.order_store import OrderStore


class RecordingBroker:
    """Fake broker that records submission order and returns broker-accepted fills."""

    def __init__(self) -> None:
        self.submitted_batches: list[list[ProposedTrade]] = []
        self.cancelled_order_ids: list[int] = []
        self.open_order_snapshots: list[OpenOrderSnapshot] = []
        self.next_order_id = 1

    def account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(cash_usd=10_000.0, positions=(), positions_market_value_usd=0.0)

    def submit_trades(self, trades, status_callback=None) -> list[OrderResult]:
        self.submitted_batches.append(list(trades))
        results = []
        for trade in trades:
            order_id = self.next_order_id
            self.next_order_id += 1
            result = OrderResult(
                ticker=trade.ticker,
                side=trade.side,
                quantity=trade.quantity,
                notional=trade.notional,
                order_id=order_id,
                status="Submitted",
                filled=0.0,
                average_fill_price=None,
                order_ref=trade.order_ref,
            )
            results.append(result)
            if status_callback is not None:
                status_callback(trade, result)
        return results

    def fetch_open_order_snapshots(self) -> list[OpenOrderSnapshot]:
        return self.open_order_snapshots

    def cancel_order(self, order_id: int) -> bool:
        self.cancelled_order_ids.append(order_id)
        return True

    def replace_order(self, *, order_id, ticker, side, quantity, new_limit_price, order_ref) -> OpenOrderSnapshot:
        return OpenOrderSnapshot(
            order_ref=order_ref,
            order_id=order_id + 1000,
            perm_id=None,
            ticker=ticker,
            side=side,
            raw_status="Submitted",
            filled=0.0,
            remaining=quantity,
            avg_fill_price=None,
        )


def _trade(ticker: str, side: OrderSide, limit_price: float = 100.0) -> ProposedTrade:
    return ProposedTrade(
        ticker=ticker,
        side=side,
        quantity=5.0,
        notional=500.0,
        reference_price=100.0,
        limit_price=limit_price,
        reason="rebalance_to_target_weight",
    )


def _plan(trades: list[ProposedTrade], session_date: str = "2026-07-01", run_id: str = "run-1") -> RebalancePlan:
    return RebalancePlan(
        run_id=run_id,
        session_date=session_date,
        targets=[],
        trades=trades,
        execution_results=[],
        warnings=[],
    )


def test_submit_plan_submits_sells_before_buys_in_separate_batches(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    trades = [_trade("AAPL", OrderSide.BUY), _trade("MSFT", OrderSide.SELL)]

    results = manager.submit_plan(_plan(trades))

    assert len(broker.submitted_batches) == 2
    assert broker.submitted_batches[0][0].side == OrderSide.SELL
    assert broker.submitted_batches[0][0].ticker == "MSFT"
    assert broker.submitted_batches[1][0].side == OrderSide.BUY
    assert broker.submitted_batches[1][0].ticker == "AAPL"
    # results preserve the original plan.trades order regardless of submission phase order
    assert [result.ticker for result in results] == ["AAPL", "MSFT"]


def test_submit_plan_tags_every_trade_with_a_unique_order_ref(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    trades = [_trade("AAPL", OrderSide.BUY), _trade("MSFT", OrderSide.SELL)]

    manager.submit_plan(_plan(trades))

    refs = {trade.order_ref for batch in broker.submitted_batches for trade in batch}
    assert len(refs) == 2
    assert all(ref.startswith("poma:run-1:") for ref in refs)


def test_submit_plan_records_ledger_entries_for_every_trade(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    trades = [_trade("AAPL", OrderSide.BUY)]

    manager.submit_plan(_plan(trades))

    open_orders = store.load_open_orders()
    assert len(open_orders) == 1
    assert open_orders[0].lifecycle_state == OrderLifecycleState.BROKER_ACCEPTED
    assert open_orders[0].ticker == "AAPL"


def test_check_stale_orders_blocks_on_prior_session_open_orders(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY)], session_date="2026-06-30"))

    check = manager.check_stale_orders("2026-07-01")

    assert any("block execution" in warning for warning in check.warnings)
    assert broker.cancelled_order_ids == []


def test_check_stale_orders_cancel_policy_cancels_prior_session_orders(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings(STALE_ORDER_POLICY="cancel"))
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY)], session_date="2026-06-30"))

    check = manager.check_stale_orders("2026-07-01")

    assert broker.cancelled_order_ids == [1]
    assert not any("block execution" in warning for warning in check.warnings)
    open_orders = store.load_open_orders()
    assert len(open_orders) == 1
    assert open_orders[0].lifecycle_state == OrderLifecycleState.CANCEL_PENDING


def test_check_stale_orders_does_not_block_on_same_session_open_orders(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY)], session_date="2026-07-01"))

    check = manager.check_stale_orders("2026-07-01")

    assert not any("block execution" in warning for warning in check.warnings)
    assert any("this session" in warning for warning in check.warnings)


def test_reconcile_replaces_once_after_replace_after_seconds(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    settings = make_settings(REPLACE_AFTER_SECONDS=1, CANCEL_AFTER_SECONDS=600)
    manager = ExecutionManager(broker, store, settings)
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY, limit_price=100.0)]))

    entry = store.load_open_orders()[0]
    stale_time = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    store.upsert(dc_replace(entry, submitted_at=stale_time))

    broker.open_order_snapshots = [
        OpenOrderSnapshot(
            order_ref=entry.order_ref,
            order_id=entry.order_id,
            perm_id=None,
            ticker="AAPL",
            side=OrderSide.BUY,
            raw_status="Submitted",
            filled=0.0,
            remaining=5.0,
            avg_fill_price=None,
        )
    ]

    summary = manager.reconcile()

    assert summary.checked == 1
    assert summary.updates[0].action == "replace"
    updated = store.load_open_orders()[0]
    assert updated.replace_count == 1
    assert updated.limit_price > 100.0


def test_reconcile_cancels_after_cancel_after_seconds(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    settings = make_settings(REPLACE_AFTER_SECONDS=1, CANCEL_AFTER_SECONDS=5)
    manager = ExecutionManager(broker, store, settings)
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY, limit_price=100.0)]))

    entry = store.load_open_orders()[0]
    from datetime import UTC, datetime, timedelta

    stale_time = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    from dataclasses import replace as dc_replace

    store.upsert(dc_replace(entry, submitted_at=stale_time))

    broker.open_order_snapshots = [
        OpenOrderSnapshot(
            order_ref=entry.order_ref,
            order_id=entry.order_id,
            perm_id=None,
            ticker="AAPL",
            side=OrderSide.BUY,
            raw_status="Submitted",
            filled=0.0,
            remaining=5.0,
            avg_fill_price=None,
        )
    ]

    summary = manager.reconcile()

    assert summary.updates[0].action == "cancel"
    assert broker.cancelled_order_ids == [entry.order_id]


def test_reconcile_leaves_unmatched_orders_unmodified(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY)]))
    broker.open_order_snapshots = []

    summary = manager.reconcile()

    assert summary.checked == 1
    assert summary.updates[0].matched is False
    assert summary.updates[0].action is None


def test_reconcile_with_no_open_orders_is_a_noop(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())

    summary = manager.reconcile()

    assert summary.checked == 0
    assert summary.updates == ()
