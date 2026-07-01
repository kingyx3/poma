from __future__ import annotations

from pathlib import Path

from poma.models import OrderResult, OrderSide
from poma.order_lifecycle import OrderLedgerEntry, OrderLifecycleState
from poma.order_store import OrderStore


def _entry(**overrides: object) -> OrderLedgerEntry:
    values: dict[str, object] = {
        "ledger_key": "poma:run-1:0:AAPL:BUY",
        "order_ref": "poma:run-1:0:AAPL:BUY",
        "run_id": "run-1",
        "session_date": "2026-07-01",
        "ticker": "AAPL",
        "side": OrderSide.BUY,
        "quantity": 5.0,
        "limit_price": 196.20,
    }
    values.update(overrides)
    return OrderLedgerEntry(**values)


def test_upsert_then_load_round_trips_a_working_order(tmp_path: Path) -> None:
    store = OrderStore(tmp_path)
    store.upsert(_entry())

    open_orders = store.load_open_orders()

    assert len(open_orders) == 1
    assert open_orders[0].ticker == "AAPL"
    assert store.get("poma:run-1:0:AAPL:BUY") is not None


def test_terminal_orders_are_removed_from_the_open_snapshot(tmp_path: Path) -> None:
    store = OrderStore(tmp_path)
    store.upsert(_entry())

    filled = _entry().with_order_result(
        OrderResult(
            ticker="AAPL",
            side=OrderSide.BUY,
            quantity=5.0,
            notional=980.0,
            order_id=1,
            status="Filled",
            filled=5.0,
            average_fill_price=196.4,
        )
    )
    store.upsert(filled)

    assert store.load_open_orders() == []
    assert store.get("poma:run-1:0:AAPL:BUY") is None


def test_events_log_keeps_every_transition_even_after_terminal(tmp_path: Path) -> None:
    store = OrderStore(tmp_path)
    store.upsert(_entry(lifecycle_state=OrderLifecycleState.PLANNED))
    store.upsert(_entry(lifecycle_state=OrderLifecycleState.BROKER_ACCEPTED))
    store.upsert(_entry(lifecycle_state=OrderLifecycleState.CANCELLED))

    events = store.events_path.read_text().splitlines()

    assert len(events) == 3
    assert store.load_open_orders() == []


def test_upsert_only_touches_the_matching_ledger_key(tmp_path: Path) -> None:
    store = OrderStore(tmp_path)
    store.upsert(_entry(ledger_key="a", order_ref="a", ticker="AAPL"))
    store.upsert(_entry(ledger_key="b", order_ref="b", ticker="MSFT"))

    store.upsert(
        _entry(ledger_key="a", order_ref="a", ticker="AAPL", lifecycle_state=OrderLifecycleState.CANCELLED)
    )

    remaining = {entry.ledger_key: entry for entry in store.load_open_orders()}
    assert set(remaining) == {"b"}
