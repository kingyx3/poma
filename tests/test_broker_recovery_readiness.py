"""Production recovery cannot be declared from a socket probe alone."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from conftest import make_settings

from poma import recovery_readiness as readiness
from poma.broker import BrokerUnavailable
from poma.models import AccountSnapshot, OpenOrderSnapshot, OrderSide
from poma.order_lifecycle import OrderLedgerEntry, OrderLifecycleState
from poma.order_store import OrderStore


def _settings(tmp_path):
    return make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU1234567", STATE_DIR=tmp_path)


def _entry(state=OrderLifecycleState.FILLED):
    return OrderLedgerEntry(
        ledger_key="poma:run-1:0:USMC:BUY",
        order_ref="poma:run-1:0:USMC:BUY",
        run_id="run-1",
        session_date="2026-10-17",
        ticker="USMC",
        side=OrderSide.BUY,
        quantity=2.0,
        limit_price=40,
        order_id=52,
        perm_id=552,
        lifecycle_state=state,
        raw_status="Filled" if state == OrderLifecycleState.FILLED else "NotOpenUnverified",
    )


def _completed(ref="poma:run-1:0:USMC:BUY"):
    return OpenOrderSnapshot(
        order_ref=ref, order_id=52, perm_id=552, ticker="USMC",
        side=OrderSide.BUY, raw_status="Filled",
        filled=2.0, remaining=0.0, avg_fill_price=39.9,
    )


def _broker(account="DU1234567"):
    return SimpleNamespace(account_snapshot=lambda: AccountSnapshot(
        cash_usd=10000, positions=(), positions_market_value_usd=0,
        account_id=account,
    ))


def test_recovery_gate_blocks_live_or_paper_planning_until_audited(tmp_path):
    path = tmp_path / "cron-alert-reconcile-orders.json"
    readiness.assert_trading_recovery_ready(tmp_path)
    path.write_text('{"active":true,"count":4}')
    with pytest.raises(BrokerUnavailable, match="trading blocked"):
        readiness.assert_trading_recovery_ready(tmp_path)
    path.write_text("damaged incident file")
    with pytest.raises(BrokerUnavailable, match="unreadable"):
        readiness.assert_trading_recovery_ready(tmp_path)
    path.write_text(json.dumps({"active": False}))
    readiness.assert_trading_recovery_ready(tmp_path)


def test_read_only_recovery_requires_cash_positions_and_recent_completed_history(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    store = OrderStore(tmp_path)
    store.upsert(_entry())
    monkeypatch.setattr(readiness, "fetch_completed_order_snapshots", lambda _: [_completed()])
    summary = SimpleNamespace(updates=())
    message = readiness.verify_broker_recovery(_broker(), store, settings, summary)
    assert "audit passed" in message
    assert "Historical broker order finality" in message

    with pytest.raises(BrokerUnavailable, match="account identity"):
        readiness.verify_broker_recovery(_broker("DU9999999"), store, settings, summary)

    monkeypatch.setattr(readiness, "fetch_completed_order_snapshots", lambda _: [_completed("poma:unknown:0:USMC:BUY")])
    with pytest.raises(BrokerUnavailable, match="absent from the durable ledger"):
        readiness.verify_broker_recovery(_broker(), store, settings, summary)

    def unavailable(_):
        raise TimeoutError("broker history query timeout")
    monkeypatch.setattr(readiness, "fetch_completed_order_snapshots", unavailable)
    with pytest.raises(BrokerUnavailable, match="evidence unavailable"):
        readiness.verify_broker_recovery(_broker(), store, settings, summary)


def test_unknown_or_unverified_local_order_never_clears_recovery(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    store = OrderStore(tmp_path)
    store.upsert(_entry(OrderLifecycleState.UNKNOWN))
    monkeypatch.setattr(readiness, "fetch_completed_order_snapshots", lambda _: [])
    with pytest.raises(BrokerUnavailable, match="unknown local order"):
        readiness.verify_broker_recovery(_broker(), store, settings, SimpleNamespace(updates=()))
    with pytest.raises(BrokerUnavailable, match="unresolved order updates"):
        readiness.verify_broker_recovery(
            _broker(), store, settings,
            SimpleNamespace(updates=[SimpleNamespace(action="unverified")]),
        )


def test_cli_rebalance_does_not_plan_orders_while_recovery_incident_active(tmp_path, monkeypatch):
    from poma import cli
    from poma.config import TradingMode

    settings = _settings(tmp_path)
    (tmp_path / "cron-alert-reconcile-orders.json").write_text('{"active":true}')
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        cli, "RebalanceEngine",
        lambda *a, **k: pytest.fail("must not build plan or place orders during recovery"),
    )
    with pytest.raises(BrokerUnavailable, match="blocked pending"):
        cli._run_rebalance("2026-10-17", "run-1", force_dry_run=False,
                           allow_outside_market_hours=True)
    assert settings.trading_mode == TradingMode.PAPER
