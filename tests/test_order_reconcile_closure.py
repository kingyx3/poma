from __future__ import annotations

from datetime import UTC, datetime

from poma.execution_manager import ExecutionManager
from poma.models import OrderSide
from poma.order_lifecycle import OrderLedgerEntry, OrderLifecycleState


def test_close_unreported_cancel_pending_order_as_cancelled() -> None:
    entry = OrderLedgerEntry(
        ledger_key="poma:run-1:0:AAPL:BUY",
        order_ref="poma:run-1:0:AAPL:BUY",
        run_id="run-1",
        session_date="2026-07-06",
        ticker="AAPL",
        side=OrderSide.BUY,
        quantity=1.0,
        limit_price=100.0,
        order_id=123,
        lifecycle_state=OrderLifecycleState.CANCEL_PENDING,
        raw_status="PendingCancel",
        filled_qty=0.0,
        remaining_qty=1.0,
        terminal_reason="cancelled after 300s unfilled",
    )

    updated = ExecutionManager._close_unreported_open_entry(entry, datetime.now(UTC))

    assert updated.lifecycle_state == OrderLifecycleState.CANCELLED
    assert updated.raw_status == "Cancelled"
    assert updated.remaining_qty == 0.0
    assert updated.terminal_reason == "cancelled after 300s unfilled"


def test_close_unreported_non_cancel_order_as_externally_resolved() -> None:
    entry = OrderLedgerEntry(
        ledger_key="poma:run-1:0:AAPL:BUY",
        order_ref="poma:run-1:0:AAPL:BUY",
        run_id="run-1",
        session_date="2026-07-06",
        ticker="AAPL",
        side=OrderSide.BUY,
        quantity=1.0,
        limit_price=100.0,
        order_id=123,
        lifecycle_state=OrderLifecycleState.BROKER_ACCEPTED,
        raw_status="Submitted",
        filled_qty=0.0,
        remaining_qty=1.0,
    )

    updated = ExecutionManager._close_unreported_open_entry(entry, datetime.now(UTC))

    assert updated.lifecycle_state == OrderLifecycleState.EXPIRED
    assert updated.raw_status == "NotOpen"
    assert updated.remaining_qty == 0.0
    assert "no longer reports" in updated.terminal_reason
