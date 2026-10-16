from __future__ import annotations

import json

from poma.models import AccountSnapshot, CurrentPosition, OrderSide, TargetPosition
from poma.order_lifecycle import OrderLedgerEntry, OrderLifecycleState
from poma.rebalance_quality import assess_rebalance, assess_saved_run


def _order(*, state=OrderLifecycleState.BROKER_ACCEPTED, filled=0.0):
    return OrderLedgerEntry(
        ledger_key="poma:run:0:USMC:BUY",
        order_ref="poma:run:0:USMC:BUY",
        run_id="run",
        session_date="2026-10-16",
        ticker="USMC",
        side=OrderSide.BUY,
        quantity=90.0,
        limit_price=100.0,
        lifecycle_state=state,
        filled_qty=filled,
        remaining_qty=90.0 - filled,
    )


def _snapshot(shares: float, cash: float):
    return AccountSnapshot(
        cash_usd=cash,
        positions=(CurrentPosition("USMC", shares, shares * 100.0),),
        positions_market_value_usd=shares * 100.0,
        net_liquidation_usd=cash + shares * 100.0,
    )


def _assess(snapshot, order):
    return assess_rebalance(
        targets=[TargetPosition("USMC", 0.90, 9_000.0)],
        planned_value_usd=10_000.0,
        snapshot=snapshot,
        order_entries=[order],
        min_trade_notional_usd=25,
        min_weight_delta_pct=0.0025,
    )


def test_accepted_order_is_pending_not_complete():
    report = _assess(_snapshot(0, 10_000), _order())
    assert report["status"] == "pending"
    assert report["fill_ratio"] == 0.0
    assert report["target_error_pct"] == 0.9


def test_partially_executed_then_cancelled_is_incomplete():
    report = _assess(_snapshot(36, 6_400), _order(state=OrderLifecycleState.CANCELLED, filled=36))
    assert report["status"] == "incomplete"
    assert report["fill_ratio"] == 0.4
    assert report["cash_drift_usd"] == 5_400


def test_completed_order_and_portfolio_target_reached():
    report = _assess(_snapshot(90, 1_000), _order(state=OrderLifecycleState.FILLED, filled=90))
    assert report["status"] == "target_reached"
    assert report["fill_ratio"] == 1.0
    assert report["target_error_usd"] == 0


def test_missing_broker_snapshot_or_unknown_order_never_certifies_target():
    assert _assess(None, _order(state=OrderLifecycleState.FILLED, filled=90))["status"] == "unknown"
    assert _assess(_snapshot(90, 1_000), _order(state=OrderLifecycleState.UNKNOWN, filled=90))["status"] == "unknown"


def test_asset_move_comparison_uses_current_portfolio_equity():
    report = _assess(_snapshot(92, 1_000), _order(state=OrderLifecycleState.FILLED, filled=90))
    assert report["status"] == "incomplete"
    assert report["target_error_usd"] == 20.0


def test_managed_cap_outside_assets_cannot_certify_all_account_balance():
    assert _assess(_snapshot(90, 5_000), _order(state=OrderLifecycleState.FILLED, filled=90))["status"] == "unknown"


def test_saved_targets_are_reused_without_strategy_recalculation(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({
        "run_id": "run",
        "targets": [{"ticker": "USMC", "target_weight": 0.9, "target_notional": 9000}],
        "portfolio_value_usd": 10000,
    }))
    report = assess_saved_run(
        path, _snapshot(90, 1000), [_order(state=OrderLifecycleState.FILLED, filled=90)],
        min_trade_notional_usd=25, min_weight_delta_pct=0.0025,
    )
    assert report["status"] == "target_reached"
