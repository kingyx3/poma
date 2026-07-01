from __future__ import annotations

import math

import pytest
from conftest import FakeBroker, make_settings

from poma.data import FixtureMarketDataClient
from poma.engine import RebalanceEngine
from poma.models import CurrentPosition, StrategyTarget, StrategyTargetBook
from poma.portfolio import CURRENT_STRATEGY_NAME
from poma.strategies import StrategyRegistry
from poma.strategies.rank_velocity_size_equal_weight import RankVelocitySizeEqualWeightStrategy

SHADOW_STRATEGY_NAME = "shadow_momentum"


class _FixedAaplStrategy:
    """Test double: always targets its full sleeve capital on AAPL."""

    name = SHADOW_STRATEGY_NAME

    def build_targets(self, context) -> StrategyTargetBook:
        target = StrategyTarget(
            strategy_name=self.name,
            ticker="AAPL",
            sleeve_weight=1.0,
            portfolio_weight=context.allocation_pct,
            target_notional=context.capital_usd,
        )
        return StrategyTargetBook(
            strategy_name=self.name,
            allocation_pct=context.allocation_pct,
            capital_usd=context.capital_usd,
            targets=(target,),
        )


def _multi_strategy_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register(RankVelocitySizeEqualWeightStrategy())
    registry.register(_FixedAaplStrategy())
    return registry


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


def test_build_plan_sizes_current_strategy_from_broker_cash_and_positions() -> None:
    broker = FakeBroker(
        positions=[CurrentPosition("AAPL", quantity=5.0, market_value=5_000.0)],
        cash_usd=15_000.0,
        net_liquidation_usd=20_000.0,
    )
    plan = _engine(
        broker=broker,
        TRADING_MODE="paper",
        IBKR_ACCOUNT="DU1234567",
        DRY_RUN_PORTFOLIO_VALUE_USD=10_000,
        STRATEGY_ALLOCATIONS=f"{CURRENT_STRATEGY_NAME}=0.5",
        MAX_POSITION_PCT=1.0,
        MAX_ORDER_NOTIONAL_USD=100_000.0,
    ).build_plan("session", "rebalance-x")

    assert plan.portfolio_value_usd == 20_000
    assert plan.portfolio_cash_usd == 15_000
    assert plan.portfolio_positions_value_usd == 5_000
    assert plan.portfolio_net_liquidation_usd == 20_000
    assert len(plan.strategy_books) == 1
    assert plan.strategy_books[0].strategy_name == CURRENT_STRATEGY_NAME
    assert plan.strategy_books[0].allocation_pct == 0.5
    assert plan.strategy_books[0].capital_usd == 10_000
    assert plan.total_allocated_usd == 10_000
    assert math.isclose(sum(target.target_notional for target in plan.targets), 10_000)
    assert any("not allocated" in warning for warning in plan.warnings)
    assert plan.broker_total_value_usd == 20_000
    assert plan.cash_sleeve_usd == 0.0
    assert plan.unallocated_capital_usd == 10_000
    assert math.isclose(plan.target_exposure_usd, 10_000)


def test_build_plan_reports_the_cash_sleeve_separately_from_unallocated_capital() -> None:
    broker = FakeBroker(cash_usd=10_000.0)
    plan = _engine(
        broker=broker,
        TRADING_MODE="paper",
        IBKR_ACCOUNT="DU1234567",
        STRATEGY_ALLOCATIONS=f"{CURRENT_STRATEGY_NAME}=0.5,cash=0.3",
        MAX_POSITION_PCT=1.0,
        MAX_ORDER_NOTIONAL_USD=100_000.0,
    ).build_plan("session", "rebalance-x")

    assert plan.cash_sleeve_usd == 3_000.0
    assert plan.unallocated_capital_usd == 2_000.0
    assert any("not allocated" in warning for warning in plan.warnings)


def test_dry_run_keeps_configured_portfolio_value_for_offline_plans() -> None:
    plan = _engine(
        broker=FakeBroker(cash_usd=20_000.0),
        DRY_RUN_PORTFOLIO_VALUE_USD=10_000,
        STRATEGY_ALLOCATIONS=f"{CURRENT_STRATEGY_NAME}=0.5",
        MAX_POSITION_PCT=1.0,
    ).build_plan("session", "rebalance-x")

    assert plan.portfolio_value_usd == 10_000
    assert plan.strategy_books[0].capital_usd == 5_000


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
        IBKR_ACCOUNT="DU1234567",
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
    outcome = _engine(
        broker=broker,
        TRADING_MODE="paper",
        IBKR_ACCOUNT="DU1234567",
        MAX_TURNOVER_PCT=0.0001,
    ).run("session", "run")
    assert outcome.blocked
    assert not outcome.executed
    assert outcome.status == "blocked"
    assert broker.submitted is None


def test_run_blocks_when_paper_balance_cannot_be_read() -> None:
    class BalanceUnavailableBroker(FakeBroker):
        def account_snapshot(self):  # noqa: ANN201 - test double shape mirrors protocol
            raise RuntimeError("account summary unavailable")

    broker = BalanceUnavailableBroker()
    outcome = _engine(
        broker=broker,
        TRADING_MODE="paper",
        IBKR_ACCOUNT="DU1234567",
        MAX_TURNOVER_PCT=1.0,
        MAX_ORDER_NOTIONAL_USD=100_000.0,
    ).run("session", "run")

    assert outcome.blocked
    assert not outcome.executed
    assert broker.submitted is None
    assert any("account summary unavailable" in warning for warning in outcome.plan.warnings)


def test_build_plan_runs_every_allocated_sleeve_and_nets_overlapping_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("poma.config.default_registry", _multi_strategy_registry)
    settings = make_settings(
        STRATEGY_ALLOCATIONS=f"{CURRENT_STRATEGY_NAME}=0.5,{SHADOW_STRATEGY_NAME}=0.3,cash=0.2",
        MAX_POSITION_PCT=1.0,
        MAX_ORDER_NOTIONAL_USD=100_000.0,
    )
    engine = RebalanceEngine(
        settings,
        data_client=FixtureMarketDataClient(),
        broker=FakeBroker(),
        strategy_registry=_multi_strategy_registry(),
    )
    plan = engine.build_plan("session", "rebalance-x")

    assert {book.strategy_name for book in plan.strategy_books} == {
        CURRENT_STRATEGY_NAME,
        SHADOW_STRATEGY_NAME,
    }
    aapl = next(position for position in plan.combined_targets if position.ticker == "AAPL")
    assert {contribution.strategy_name for contribution in aapl.contributions} == {
        CURRENT_STRATEGY_NAME,
        SHADOW_STRATEGY_NAME,
    }
    expected_notional = sum(contribution.target_notional for contribution in aapl.contributions)
    assert math.isclose(aapl.target_notional, expected_notional)
    assert any("combines overlapping allocations" in warning for warning in plan.warnings)

    # The overlapping sleeves net into a single AAPL order, not one order per strategy.
    aapl_trades = [trade for trade in plan.trades if trade.ticker == "AAPL"]
    assert len(aapl_trades) == 1
    assert math.isclose(aapl_trades[0].notional, expected_notional, rel_tol=1e-6)


def test_managed_cap_mode_broker_total_uses_full_account_value() -> None:
    plan = _engine(
        broker=FakeBroker(cash_usd=50_000.0),
        TRADING_MODE="paper",
        IBKR_ACCOUNT="DU1234567",
        MANAGED_CAP_MODE="broker_total",
        MAX_TURNOVER_PCT=1.0,
        MAX_ORDER_NOTIONAL_USD=100_000.0,
    ).build_plan("session", "rebalance-x")

    assert plan.portfolio_value_usd == 50_000


def test_managed_cap_mode_min_of_broker_total_and_cap_limits_sizing() -> None:
    plan = _engine(
        broker=FakeBroker(cash_usd=50_000.0),
        TRADING_MODE="paper",
        IBKR_ACCOUNT="DU1234567",
        MANAGED_CAP_MODE="min_of_broker_total_and_cap",
        MANAGED_CAP_USD=10_000,
        MAX_TURNOVER_PCT=1.0,
        MAX_ORDER_NOTIONAL_USD=100_000.0,
    ).build_plan("session", "rebalance-x")

    assert plan.portfolio_value_usd == 10_000


def test_current_holdings_reduce_buys_and_generate_sells() -> None:
    broker = FakeBroker(
        positions=[CurrentPosition("AAPL", quantity=1_000.0, market_value=9_800.0)],
        cash_usd=200.0,
    )
    plan = _engine(
        broker=broker,
        TRADING_MODE="paper",
        IBKR_ACCOUNT="DU1234567",
        STRATEGY_ALLOCATIONS=f"{CURRENT_STRATEGY_NAME}=0.98,cash=0.02",
        MAX_POSITION_PCT=1.0,
        MAX_TURNOVER_PCT=1.0,
        MAX_ORDER_NOTIONAL_USD=100_000.0,
    ).build_plan("session", "rebalance-x")

    aapl_trades = [trade for trade in plan.trades if trade.ticker == "AAPL"]
    assert aapl_trades, "an oversized existing AAPL position should trigger a rebalancing trade"
    assert aapl_trades[0].side.value == "SELL"
