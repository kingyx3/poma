from __future__ import annotations

import json
from types import SimpleNamespace

from ops.scripts.resolve_unresolved_order import (
    _expected_position_delta,
    _operator_resolved_entry,
    _position_change_proves_fill,
    _position_quantity,
    _retained_post_submission_quantity,
)
from poma.models import OrderSide
from poma.order_lifecycle import OrderLedgerEntry, OrderLifecycleState


def _entry(*, side: OrderSide = OrderSide.SELL, quantity: float = 1.0) -> OrderLedgerEntry:
    return OrderLedgerEntry(
        ledger_key=f"poma:run-1:0:RY:{side.value}",
        order_ref=f"poma:run-1:0:RY:{side.value}",
        run_id="run-1",
        session_date="2026-08-28",
        ticker="RY",
        side=side,
        quantity=quantity,
        limit_price=203.66,
        order_id=207,
        perm_id=1439585329,
        lifecycle_state=OrderLifecycleState.UNKNOWN,
        raw_status="NotOpenUnverified",
        filled_qty=0.0,
        remaining_qty=quantity,
    )


def test_position_quantity_treats_missing_ticker_as_zero() -> None:
    snapshot = SimpleNamespace(positions=[SimpleNamespace(ticker="AAPL", quantity=2.0)])
    assert _position_quantity(snapshot, "RY") == 0.0


def test_retained_post_submission_quantity_reads_original_reconciliation(tmp_path) -> None:
    reconciliations = tmp_path / "reconciliations"
    reconciliations.mkdir()
    (reconciliations / "run-1.json").write_text(
        json.dumps(
            {
                "post_trade_account_snapshot": {
                    "positions": [
                        {"ticker": "AAPL", "quantity": 1.0},
                        {"ticker": "RY", "quantity": 1.0},
                    ]
                }
            }
        )
    )

    assert _retained_post_submission_quantity(tmp_path, _entry()) == 1.0


def test_expected_position_delta_handles_buy_and_sell() -> None:
    assert _expected_position_delta(_entry(side=OrderSide.BUY, quantity=2.0)) == 2.0
    assert _expected_position_delta(_entry(side=OrderSide.SELL, quantity=2.0)) == -2.0


def test_position_change_proves_exact_sell_fill() -> None:
    assert _position_change_proves_fill(_entry(side=OrderSide.SELL), 1.0, 0.0)
    assert not _position_change_proves_fill(_entry(side=OrderSide.SELL), 2.0, 0.0)


def test_position_change_proves_exact_buy_fill() -> None:
    assert _position_change_proves_fill(_entry(side=OrderSide.BUY), 0.0, 1.0)
    assert not _position_change_proves_fill(_entry(side=OrderSide.BUY), 0.0, 2.0)


def test_operator_resolved_entry_is_terminal_and_preserves_broker_identity() -> None:
    entry = _entry()
    resolved = _operator_resolved_entry(
        entry,
        reason="operator evidence",
        resolved_at="2026-08-31T17:00:00+00:00",
    )

    assert resolved.lifecycle_state == OrderLifecycleState.FILLED
    assert resolved.raw_status == "OperatorResolvedFilled"
    assert resolved.filled_qty == 1.0
    assert resolved.remaining_qty == 0.0
    assert resolved.order_id == 207
    assert resolved.perm_id == 1439585329
    assert resolved.order_ref == entry.order_ref
    assert resolved.avg_fill_price is None
    assert resolved.terminal_reason == "operator evidence"
