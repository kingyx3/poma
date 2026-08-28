from __future__ import annotations

from conftest import make_settings

from poma.execution_manager import ExecutionManager
from poma.models import OrderSide, ProposedTrade
from poma.order_lifecycle import IDEMPOTENT_REPLAY_STATUS, OrderLedgerEntry, OrderLifecycleState
from poma.order_store import OrderStore


def _sell(ticker: str) -> ProposedTrade:
    return ProposedTrade(
        ticker=ticker,
        side=OrderSide.SELL,
        quantity=1.0,
        notional=100.0,
        reference_price=100.0,
        limit_price=99.9,
        reason="test",
    )


def test_same_run_residual_plan_reuses_original_order_ref_when_sequence_shifts(tmp_path) -> None:
    store = OrderStore(tmp_path)
    original = OrderLedgerEntry(
        ledger_key="poma:run-1:1:MSFT:SELL",
        order_ref="poma:run-1:1:MSFT:SELL",
        run_id="run-1",
        session_date="2026-08-28",
        ticker="MSFT",
        side=OrderSide.SELL,
        quantity=1.0,
        limit_price=99.9,
        order_id=42,
        lifecycle_state=OrderLifecycleState.BROKER_ACCEPTED,
        raw_status="Submitted",
        filled_qty=0.0,
        remaining_qty=1.0,
    )
    store.upsert(original)
    manager = ExecutionManager(object(), store, make_settings())  # type: ignore[arg-type]

    # A prior ticker filled and disappeared, so MSFT moved from sequence slot 1 to slot 0.
    tagged = manager._tag("run-1", [_sell("MSFT")], offset=0)

    assert tagged[0].order_ref == original.ledger_key
    latest = store.get_latest_many([tagged[0].order_ref])
    replay = manager._idempotent_replay(tagged[0], latest)
    assert replay is not None
    assert replay.status == IDEMPOTENT_REPLAY_STATUS
    assert replay.order_id == 42


def test_latest_run_trade_lookup_includes_terminal_event_history(tmp_path) -> None:
    store = OrderStore(tmp_path)
    filled = OrderLedgerEntry(
        ledger_key="poma:run-1:1:MSFT:SELL",
        order_ref="poma:run-1:1:MSFT:SELL",
        run_id="run-1",
        session_date="2026-08-28",
        ticker="MSFT",
        side=OrderSide.SELL,
        quantity=1.0,
        limit_price=99.9,
        order_id=42,
        lifecycle_state=OrderLifecycleState.FILLED,
        raw_status="Filled",
        filled_qty=1.0,
        remaining_qty=0.0,
    )
    store.upsert(filled)
    assert store.load_open_orders() == []

    latest = store.get_latest_run_trades("run-1")

    assert latest[("MSFT", "SELL")].ledger_key == filled.ledger_key
    assert latest[("MSFT", "SELL")].lifecycle_state == OrderLifecycleState.FILLED
