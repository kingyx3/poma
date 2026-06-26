from __future__ import annotations

from conftest import FakeBroker, make_settings

from poma.data import FixtureMarketDataClient
from poma.engine import RebalanceEngine


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
