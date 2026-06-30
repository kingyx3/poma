from __future__ import annotations

import math

from conftest import FakeBroker, make_settings

from poma.data import FixtureMarketDataClient
from poma.engine import RebalanceEngine
from poma.models import CurrentPosition
from poma.portfolio import CURRENT_STRATEGY_NAME


def _engine(broker: FakeBroker | None = None, **overrides: object) -> RebalanceEngine:
    return RebalanceEngine(
        make_settings(**overrides),
        data_client=FixtureMarketDataClient(),
        broker=broker or FakeBroker(),
    )


def test_build_plan_generates_targets_and_trades() -> None:
    plan = _engine().build_plan("session", "rebalance-x")
    assert plan.targets, "fixture universe should yield target positions"
    assert plan.trades, "empty starting portfolio should produce buy trades"
    assert all(trade.side.value == "BUY" for trade in plan.trades)


def test_build_plan_sizes_current_strategy_from_allocated_sleeve() -> None:
    plan = _engine(
        PORTFOLIO_VALUE_USD=10_000,
        STRATEGY_ALLOCATIONS=f"{CURRENT_STRATEGY_NAME}=0.5",
        MAX_POSITION_PCT=1.0,
    ).build_plan("session", "rebalance-x")

    assert plan.portfolio_value_usd == 10_000
    assert plan.cash_balance_usd is None
    assert plan.positions_value_usd == 0
    assert plan.portfolio_value_source == "configured_PORTFOLIO_VALUE_USD"
    assert plan.strategy_name == CURRENT_STRATEGY_NAME
    assert plan.strategy_allocation_pct == 0.5
    assert plan.strategy_capital_usd == 5_000
    assert plan.total_allocated_usd == 5_000
    assert math.isclose(sum(target.target_notional for target in plan.targets), 5_000)
    assert math.isclose(sum(trade.notional for trade in plan.trades), 5_000)
    assert any("not allocated" in warning for warning in plan.warnings)


def test_build_plan_uses_broker_cash_and_positions_for_paper_sizing() -> None:
    broker = FakeBroker(
        positions=[CurrentPosition(ticker="MSFT", quantity=2, market_value=4_000.0)],
        cash_balance_usd=6_000.0,
    )
    plan = _engine(
        broker=broker,
        TRADING_MODE="paper",
        PORTFOLIO_VALUE_USD=50_000,
        STRATEGY_ALLOCATIONS=f"{CURRENT_STRATEGY_NAME}=0.5",
        MAX_POSITION_PCT=1.0,
    ).build_plan("session", "rebalance-x")

    assert plan.portfolio_value_usd == 10_000
    assert plan.cash_balance_usd == 6_000
    assert plan.positions_value_usd == 4_000
    assert plan.portfolio_value_source == "broker_cash_plus_positions"
    assert plan.strategy_capital_usd == 5_000
    assert plan.total_allocated_usd == 5_000
    assert math.isclose(sum(target.target_notional for target in plan.targets), 5_000)


def test_build_plan_rejects_non_positive_broker_portfolio_value() -> None:
    broker = FakeBroker(cash_balance_usd=0.0)
    engine = _engine(broker=broker, TRADING_MODE="paper")

    try:
        engine.build_plan("session", "rebalance-x")
    except RuntimeError as exc:
        assert "broker-derived portfolio value must be positive" in str(exc)
    else:  # pragma: no cover - defensive assertion clarity
        raise AssertionError("paper rebalances must not size from an empty broker account")


def test_run_dry_run_does_not_execute() -> None:
    broker = FakeBroker()
    outcome = _engine(broker=broker, TRADING_MODE="dry_run").run("session", "run")
    assert not outcome.executed
    assert outcome.status == "dry_run"
    assert broker.submitted is None


def test_run_paper_executes_through_broker() -> None:
    broker = FakeBroker()
    engine = _engine(
        broker=broker,
        TRADING_MODE="paper",
        MAX_TURNOVER_PCT=1.0,
        MAX_ORDER_NOTIONAL_USD=100_000.0,
    )
    outcome = engine.run("session", "run")
    assert outcome.executed
    assert outcome.status == "completed"
    assert broker.submitted is not None
    assert outcome.plan.execution_results


def test_run_blocks_when_turnover_exceeds_limit() -> None:
    broker = FakeBroker()
    outcome = _engine(broker=broker, TRADING_MODE="paper", MAX_TURNOVER_PCT=0.0001).run(
        "session", "run"
    )
    assert outcome.blocked
    assert not outcome.executed
    assert outcome.status == "blocked"
    assert broker.submitted is None
