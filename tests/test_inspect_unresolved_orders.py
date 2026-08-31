from __future__ import annotations

from poma.models import OpenOrderSnapshot, OrderSide
from poma.order_lifecycle import OrderLedgerEntry
from ops.scripts.inspect_unresolved_orders import _identity_match


def _entry(order_ref: str) -> OrderLedgerEntry:
    return OrderLedgerEntry(
        ledger_key="ledger-1",
        order_ref=order_ref,
        run_id="run-1",
        session_date="2026-08-31",
        ticker="RY",
        side=OrderSide.SELL,
        quantity=1,
        limit_price=200,
    )


def _snapshot(order_ref: str, *, order_id: int = 10, perm_id: int = 99) -> OpenOrderSnapshot:
    return OpenOrderSnapshot(
        order_ref=order_ref,
        order_id=order_id,
        perm_id=perm_id,
        ticker="RY",
        side=OrderSide.SELL,
        raw_status="Filled",
        filled=1,
        remaining=0,
        avg_fill_price=200,
    )


def test_identity_match_requires_exact_order_ref() -> None:
    assert _identity_match(_entry("poma:run-1:0:RY:SELL"), _snapshot("poma:run-1:0:RY:SELL"))


def test_identity_match_does_not_adopt_same_broker_ids_with_different_order_ref() -> None:
    entry = _entry("poma:run-1:0:RY:SELL")
    snapshot = _snapshot("poma:other:0:RY:SELL", order_id=10, perm_id=99)
    assert not _identity_match(entry, snapshot)
