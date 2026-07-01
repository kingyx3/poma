from __future__ import annotations

from datetime import UTC, datetime, timedelta

from poma.models import OpenOrderSnapshot, OrderResult, OrderSide
from poma.order_lifecycle import (
    TERMINAL_LIFECYCLE_STATES,
    OrderLedgerEntry,
    OrderLifecycleState,
    build_order_ref,
    classify_lifecycle,
    more_aggressive_limit_price,
    seconds_since,
)


def test_build_order_ref_is_deterministic_and_namespaced() -> None:
    ref = build_order_ref("run-1", 0, "AAPL", OrderSide.BUY)
    assert ref == "poma:run-1:0:AAPL:BUY"


def test_classify_lifecycle_working_vs_filled() -> None:
    assert classify_lifecycle("Submitted", filled=0.0, remaining=5.0) == OrderLifecycleState.BROKER_ACCEPTED
    assert classify_lifecycle("Submitted", filled=2.0, remaining=3.0) == OrderLifecycleState.PARTIALLY_FILLED
    assert classify_lifecycle("Filled", filled=5.0, remaining=0.0) == OrderLifecycleState.FILLED
    assert classify_lifecycle("Cancelled", filled=0.0, remaining=5.0) == OrderLifecycleState.CANCELLED
    assert classify_lifecycle("Inactive", filled=0.0, remaining=5.0) == OrderLifecycleState.REJECTED
    assert classify_lifecycle("PendingSubmit", filled=0.0, remaining=5.0) == OrderLifecycleState.SUBMITTED
    assert classify_lifecycle("PendingCancel", filled=0.0, remaining=5.0) == OrderLifecycleState.CANCEL_PENDING
    assert classify_lifecycle("BrokerUnavailable", filled=0.0, remaining=5.0) == OrderLifecycleState.REJECTED
    assert classify_lifecycle("SomethingUnknown", filled=0.0, remaining=5.0) == OrderLifecycleState.UNKNOWN


def test_terminal_states_are_exactly_the_documented_set() -> None:
    expected = {
        OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCELLED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.EXPIRED,
    }
    assert expected == TERMINAL_LIFECYCLE_STATES


def test_more_aggressive_limit_price_moves_toward_the_market_for_each_side() -> None:
    buy_price = more_aggressive_limit_price(OrderSide.BUY, 100.0, 15.0)
    sell_price = more_aggressive_limit_price(OrderSide.SELL, 100.0, 15.0)
    assert buy_price > 100.0
    assert sell_price < 100.0


def test_seconds_since_handles_missing_and_invalid_timestamps() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert seconds_since(None, now) is None
    assert seconds_since("not-a-timestamp", now) is None
    earlier = (now - timedelta(seconds=90)).isoformat()
    assert seconds_since(earlier, now) == 90.0


def _base_entry() -> OrderLedgerEntry:
    return OrderLedgerEntry(
        ledger_key="poma:run-1:0:AAPL:BUY",
        order_ref="poma:run-1:0:AAPL:BUY",
        run_id="run-1",
        session_date="2026-07-01",
        ticker="AAPL",
        side=OrderSide.BUY,
        quantity=5.0,
        limit_price=196.20,
    )


def test_with_order_result_updates_lifecycle_and_marks_terminal_reason_on_rejection() -> None:
    entry = _base_entry()
    result = OrderResult(
        ticker="AAPL",
        side=OrderSide.BUY,
        quantity=5.0,
        notional=980.0,
        order_id=42,
        status="Cancelled",
        filled=0.0,
        average_fill_price=None,
        message="rejected by broker",
    )

    updated = entry.with_order_result(result)

    assert updated.lifecycle_state == OrderLifecycleState.CANCELLED
    assert updated.is_terminal
    assert updated.order_id == 42
    assert updated.terminal_reason == "rejected by broker"
    assert updated.submitted_at is not None


def test_with_order_result_keeps_working_orders_non_terminal() -> None:
    entry = _base_entry()
    result = OrderResult(
        ticker="AAPL",
        side=OrderSide.BUY,
        quantity=5.0,
        notional=980.0,
        order_id=42,
        status="Submitted",
        filled=0.0,
        average_fill_price=None,
    )

    updated = entry.with_order_result(result)

    assert updated.lifecycle_state == OrderLifecycleState.BROKER_ACCEPTED
    assert not updated.is_terminal
    assert updated.terminal_reason is None


def test_with_snapshot_updates_fill_and_ids() -> None:
    entry = _base_entry().with_order_result(
        OrderResult(
            ticker="AAPL",
            side=OrderSide.BUY,
            quantity=5.0,
            notional=980.0,
            order_id=42,
            status="Submitted",
            filled=0.0,
            average_fill_price=None,
        )
    )
    snapshot = OpenOrderSnapshot(
        order_ref=entry.order_ref,
        order_id=42,
        perm_id=99,
        ticker="AAPL",
        side=OrderSide.BUY,
        raw_status="Filled",
        filled=5.0,
        remaining=0.0,
        avg_fill_price=196.5,
    )

    updated = entry.with_snapshot(snapshot)

    assert updated.lifecycle_state == OrderLifecycleState.FILLED
    assert updated.is_terminal
    assert updated.perm_id == 99
    assert updated.avg_fill_price == 196.5
    assert updated.terminal_reason == "broker reported Filled"


def test_ledger_entry_json_round_trip() -> None:
    entry = _base_entry()
    restored = OrderLedgerEntry.from_json(entry.to_json())
    assert restored == entry
