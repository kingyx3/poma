from __future__ import annotations

import json

from poma.journal import ExecutionJournal
from poma.models import (
    AccountSnapshot,
    CombinedTargetPosition,
    CurrentPosition,
    OrderResult,
    OrderSide,
    ProposedTrade,
    RebalancePlan,
    StrategyTarget,
    StrategyTargetBook,
    TargetPosition,
)


def _plan() -> RebalancePlan:
    contribution = StrategyTarget(
        strategy_name="rank_velocity_size_equal_weight",
        ticker="AAPL",
        sleeve_weight=1.0,
        portfolio_weight=0.5,
        target_notional=5_000.0,
    )
    trade = ProposedTrade(
        ticker="AAPL",
        side=OrderSide.BUY,
        quantity=25.0,
        notional=5_000.0,
        reference_price=200.0,
        limit_price=200.2,
        reason="rebalance_to_target_weight",
    )
    result = OrderResult(
        ticker="AAPL",
        side=OrderSide.BUY,
        quantity=25.0,
        notional=5_000.0,
        order_id=1,
        status="Filled",
        filled=25.0,
        average_fill_price=200.0,
    )
    return RebalancePlan(
        run_id="run-1",
        session_date="2026-07-01",
        targets=[TargetPosition("AAPL", 0.5, 5_000.0)],
        trades=[trade],
        execution_results=[result],
        warnings=["some warning"],
        portfolio_value_usd=10_000.0,
        portfolio_cash_usd=5_000.0,
        portfolio_positions_value_usd=5_000.0,
        portfolio_net_liquidation_usd=10_000.0,
        strategy_books=(
            StrategyTargetBook(
                strategy_name="rank_velocity_size_equal_weight",
                allocation_pct=0.5,
                capital_usd=5_000.0,
                targets=(contribution,),
            ),
        ),
        combined_targets=(
            CombinedTargetPosition(
                ticker="AAPL",
                target_weight=0.5,
                target_notional=5_000.0,
                contributions=(contribution,),
            ),
        ),
        total_allocated_pct=0.5,
        total_allocated_usd=5_000.0,
    )


def test_record_planned_writes_expected_orders_journal(tmp_path) -> None:
    journal = ExecutionJournal(tmp_path)
    plan = _plan()

    path = journal.record_planned(plan)

    assert path == tmp_path / "orders" / "run-1.json"
    payload = json.loads(path.read_text())
    assert payload["run_id"] == "run-1"
    assert len(payload["strategy_attribution"]) == 1
    assert payload["strategy_attribution"][0]["strategy_name"] == "rank_velocity_size_equal_weight"
    assert len(payload["planned_trades"]) == 1
    assert payload["expected_account_snapshot"]["cash_usd"] == 5_000.0
    assert "target_book_hash" in payload and payload["target_book_hash"]


def test_record_planned_hash_is_stable_for_the_same_targets(tmp_path) -> None:
    journal = ExecutionJournal(tmp_path)
    plan_a = _plan()
    plan_b = _plan()

    path_a = journal.record_planned(plan_a)
    payload_a = json.loads(path_a.read_text())
    payload_b = json.loads(journal.record_planned(plan_b).read_text())

    assert payload_a["target_book_hash"] == payload_b["target_book_hash"]


def test_record_reconciliation_writes_post_trade_snapshot(tmp_path) -> None:
    journal = ExecutionJournal(tmp_path)
    plan = _plan()
    snapshot = AccountSnapshot(
        cash_usd=1_000.0,
        positions=(CurrentPosition("AAPL", 25.0, 5_000.0),),
        positions_market_value_usd=5_000.0,
        net_liquidation_usd=6_000.0,
    )

    path = journal.record_reconciliation(plan, snapshot)

    assert path == tmp_path / "reconciliations" / "run-1.json"
    payload = json.loads(path.read_text())
    assert len(payload["order_results"]) == 1
    assert payload["post_trade_account_snapshot"]["cash_usd"] == 1_000.0
    assert payload["post_trade_account_snapshot"]["positions"][0]["ticker"] == "AAPL"
    assert payload["post_trade_snapshot_error"] is None


def test_record_reconciliation_captures_snapshot_error(tmp_path) -> None:
    journal = ExecutionJournal(tmp_path)
    plan = _plan()

    path = journal.record_reconciliation(plan, None, "broker unavailable")

    payload = json.loads(path.read_text())
    assert payload["post_trade_account_snapshot"] is None
    assert payload["post_trade_snapshot_error"] == "broker unavailable"
