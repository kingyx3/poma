from __future__ import annotations

from poma.models import OrderSide
from poma.order_lifecycle import OrderLedgerEntry, OrderLifecycleState
from poma.order_status_alerts import lifecycle_status_alert


def _entry(state: OrderLifecycleState, filled: float) -> OrderLedgerEntry:
    return OrderLedgerEntry(
        ledger_key="poma:run-1:0:SP1:BUY",
        order_ref="poma:run-1:0:SP1:BUY:r1",
        run_id="run-1",
        session_date="2026-10-13",
        ticker="SP1",
        side=OrderSide.BUY,
        quantity=85.0,
        limit_price=12.02,
        order_id=626,
        lifecycle_state=state,
        filled_qty=filled,
    )


def test_replace_alert_says_when_the_replacement_already_filled() -> None:
    message = lifecycle_status_alert(_entry(OrderLifecycleState.FILLED, 85.0), "replace")

    assert "Action: replaced with a more aggressive limit ($12.02); the replacement order then filled" in message


def test_replace_alert_for_a_working_replacement_is_unchanged() -> None:
    message = lifecycle_status_alert(_entry(OrderLifecycleState.BROKER_ACCEPTED, 0.0), "replace")

    assert "Action: replaced with a more aggressive limit ($12.02)\n" in message
    assert "then filled" not in message
