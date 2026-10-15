"""Fail-closed, self-healing read-only IBKR order discovery and connectivity regressions."""

from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import make_settings

from poma.broker import BrokerUnavailable, IbkrBroker
from poma.execution_manager import ExecutionManager
from poma.models import OpenOrderSnapshot, OrderSide
from poma.order_lifecycle import OrderLedgerEntry, OrderLifecycleState
from poma.order_store import OrderStore


def _settings(**overrides):
    return make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU1234567", **overrides)


def _broker_order(order_ref="poma:run-1:0:USMC:BUY", *, status="Submitted", symbol="USMC"):
    return OpenOrderSnapshot(
        order_ref=order_ref,
        order_id=615,
        perm_id=995615,
        ticker=symbol,
        side=OrderSide.BUY,
        raw_status=status,
        filled=0.0,
        remaining=251.0,
        avg_fill_price=None,
    )


def _broker(settings, monkeypatch, snapshots):
    broker = IbkrBroker(settings)
    monkeypatch.setattr(broker, "fetch_open_order_snapshots", lambda: list(snapshots))
    return broker


def test_empty_ledger_rediscovers_broker_working_order_and_persists_identity(tmp_path, monkeypatch):
    settings = _settings()
    store = OrderStore(tmp_path)
    broker = _broker(settings, monkeypatch, [_broker_order()])
    manager = ExecutionManager(broker, store, settings)

    summary = manager.reconcile()

    assert summary.checked == 1
    assert summary.updates[0].action == "recovered"
    recovered = store.load_open_orders()
    assert len(recovered) == 1
    assert recovered[0].order_id == 615
    assert recovered[0].perm_id == 995615
    assert recovered[0].quantity == 251.0
    assert recovered[0].lifecycle_state == OrderLifecycleState.BROKER_ACCEPTED
    assert "Recovered from IBKR" in (recovered[0].terminal_reason or "")
    assert OrderStore(tmp_path).load_open_orders()[0].perm_id == 995615


def test_recovered_order_blocks_new_rebalance_even_when_initial_ledger_was_empty(tmp_path, monkeypatch):
    settings = _settings(STALE_ORDER_POLICY="cancel")
    store = OrderStore(tmp_path)
    broker = _broker(settings, monkeypatch, [_broker_order()])
    monkeypatch.setattr(broker, "cancel_order", lambda _id: pytest.fail("must not auto-cancel recovered order"))
    manager = ExecutionManager(broker, store, settings)

    check = manager.check_stale_orders("2026-10-15", "run-today")

    assert any("block execution" in warning for warning in check.warnings)
    assert store.load_open_orders()[0].order_ref == "poma:run-1:0:USMC:BUY"
    assert store.load_open_orders()[0].lifecycle_state == OrderLifecycleState.BROKER_ACCEPTED


def test_disconnected_api_blocks_new_rebalance_even_with_empty_ledger(tmp_path, monkeypatch):
    settings = _settings()
    broker = IbkrBroker(settings)
    monkeypatch.setattr(
        broker, "fetch_open_order_snapshots",
        lambda: (_ for _ in ()).throw(BrokerUnavailable("Gateway disconnected")),
    )
    manager = ExecutionManager(broker, OrderStore(tmp_path), settings)

    check = manager.check_stale_orders("2026-10-15", "run-today")

    assert any("block execution" in warning and "Gateway disconnected" in warning for warning in check.warnings)


def test_unparseable_poma_broker_ref_blocks_execution_instead_of_dropping_order(tmp_path, monkeypatch):
    settings = _settings()
    broker = _broker(settings, monkeypatch, [_broker_order("poma:broken")])
    manager = ExecutionManager(broker, OrderStore(tmp_path), settings)

    with pytest.raises(RuntimeError, match="unparseable"):
        manager.reconcile()
    assert any("block execution" in w for w in manager.check_stale_orders("2026-10-15", "run-2").warnings)


def test_broker_identity_conflict_with_terminal_ledger_blocks(tmp_path, monkeypatch):
    settings = _settings()
    store = OrderStore(tmp_path)
    root = "poma:run-1:0:USMC:BUY"
    store.upsert(OrderLedgerEntry(
        ledger_key=root, order_ref=root, run_id="run-1", session_date="2026-10-14",
        ticker="USMC", side=OrderSide.BUY, quantity=251.0, limit_price=36.4,
        lifecycle_state=OrderLifecycleState.FILLED,
    ))
    manager = ExecutionManager(_broker(settings, monkeypatch, [_broker_order()]), store, settings)

    with pytest.raises(RuntimeError, match="conflict"):
        manager.reconcile()
    assert store.load_open_orders() == []


def test_untracked_replacement_of_known_order_fails_closed(tmp_path, monkeypatch):
    settings = _settings()
    store = OrderStore(tmp_path)
    root = "poma:run-1:0:USMC:BUY"
    store.upsert(OrderLedgerEntry(
        ledger_key=root, order_ref=root, run_id="run-1", session_date="2026-10-14",
        ticker="USMC", side=OrderSide.BUY, quantity=251.0, limit_price=36.4,
        lifecycle_state=OrderLifecycleState.UNKNOWN,
    ))
    manager = ExecutionManager(_broker(settings, monkeypatch, [_broker_order(root + ":r1")]), store, settings)

    with pytest.raises(RuntimeError, match="replacement"):
        manager.reconcile()


def test_open_order_query_reconnects_once_after_mid_session_disconnect(monkeypatch):
    settings = _settings()
    broker = IbkrBroker(settings)
    attempts = []

    def read():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("connection reset")
        return [_broker_order()]

    monkeypatch.setattr(broker, "_fetch_open_orders_once", read)
    monkeypatch.setattr("poma.broker.time.sleep", lambda _seconds: None)

    assert broker.fetch_open_order_snapshots()[0].order_id == 615
    assert len(attempts) == 2


def test_failed_open_order_query_exhausts_retry_and_reports_unavailable(monkeypatch):
    settings = _settings()
    broker = IbkrBroker(settings)
    attempts = []

    def fail_read():
        attempts.append(1)
        raise ConnectionError("gateway down")

    monkeypatch.setattr(broker, "_fetch_open_orders_once", fail_read)
    monkeypatch.setattr("poma.broker.time.sleep", lambda _seconds: None)

    with pytest.raises(BrokerUnavailable, match="after reconnect"):
        broker.fetch_open_order_snapshots()
    assert len(attempts) == 2


def test_recent_terminal_order_history_is_available_when_open_snapshot_empty(tmp_path):
    store = OrderStore(tmp_path)
    root = "poma:run-1:0:USMC:BUY"
    entry = OrderLedgerEntry(
        ledger_key=root, order_ref=root, run_id="run-1", session_date="2026-10-14",
        ticker="USMC", side=OrderSide.BUY, quantity=251.0, limit_price=36.4,
        lifecycle_state=OrderLifecycleState.FILLED,
    )
    store.upsert(entry)

    assert store.load_open_orders() == []
    assert store.load_latest_entries()[root] == entry
