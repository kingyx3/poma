from __future__ import annotations

from pathlib import Path

from conftest import FakeBroker, make_settings

from poma.data import FixtureMarketDataClient
from poma.engine import RebalanceEngine
from poma.models import OrderSide
from poma.order_lifecycle import OrderLedgerEntry
from poma.order_store import OrderStore


def _engine(tmp_path: Path, broker: FakeBroker | None = None, **overrides: object) -> RebalanceEngine:
    return RebalanceEngine(
        make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU1234567", MAX_POSITION_PCT=1.0, **overrides),
        data_client=FixtureMarketDataClient(),
        broker=broker or FakeBroker(),
        order_store=OrderStore(tmp_path),
    )


def test_build_plan_blocks_when_prior_session_orders_are_unresolved(tmp_path: Path) -> None:
    engine = _engine(tmp_path, MAX_TURNOVER_PCT=1.0, MAX_ORDER_NOTIONAL_USD=100_000.0)
    engine.order_store.upsert(
        OrderLedgerEntry(
            ledger_key="poma:prior-run:0:AAPL:BUY",
            order_ref="poma:prior-run:0:AAPL:BUY",
            run_id="prior-run",
            session_date="2026-06-30",
            ticker="AAPL",
            side=OrderSide.BUY,
            quantity=5.0,
            limit_price=100.0,
        )
    )

    plan = engine.build_plan("2026-07-01", "run-1")

    assert any("block execution" in warning for warning in plan.warnings)
    assert engine.is_blocked(plan)


def test_build_plan_blocks_when_same_session_different_run_orders_are_unresolved(tmp_path: Path) -> None:
    engine = _engine(tmp_path, MAX_TURNOVER_PCT=1.0, MAX_ORDER_NOTIONAL_USD=100_000.0)
    engine.order_store.upsert(
        OrderLedgerEntry(
            ledger_key="poma:earlier-run:0:AAPL:BUY",
            order_ref="poma:earlier-run:0:AAPL:BUY",
            run_id="earlier-run",
            session_date="2026-07-01",
            ticker="AAPL",
            side=OrderSide.BUY,
            quantity=5.0,
            limit_price=100.0,
        )
    )

    plan = engine.build_plan("2026-07-01", "run-1")

    assert any("block execution" in warning for warning in plan.warnings)
    assert engine.is_blocked(plan)


def test_build_plan_does_not_block_when_same_run_id_open_orders_exist(tmp_path: Path) -> None:
    engine = _engine(tmp_path, MAX_TURNOVER_PCT=1.0, MAX_ORDER_NOTIONAL_USD=100_000.0)
    engine.order_store.upsert(
        OrderLedgerEntry(
            ledger_key="poma:run-1:0:AAPL:BUY",
            order_ref="poma:run-1:0:AAPL:BUY",
            run_id="run-1",
            session_date="2026-07-01",
            ticker="AAPL",
            side=OrderSide.BUY,
            quantity=5.0,
            limit_price=100.0,
        )
    )

    plan = engine.build_plan("2026-07-01", "run-1")

    assert not engine.is_blocked(plan)


def test_build_plan_does_not_block_when_no_order_store_is_configured() -> None:
    engine = RebalanceEngine(
        make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU1234567", MAX_ORDER_NOTIONAL_USD=100_000.0),
        data_client=FixtureMarketDataClient(),
        broker=FakeBroker(),
    )

    plan = engine.build_plan("2026-07-01", "run-1")

    assert not engine.is_blocked(plan)


def test_execute_with_order_store_submits_sells_before_buys_and_records_ledger(tmp_path: Path) -> None:
    broker = FakeBroker()
    engine = _engine(tmp_path, broker=broker, MAX_TURNOVER_PCT=1.0, MAX_ORDER_NOTIONAL_USD=100_000.0)
    plan = engine.build_plan("2026-07-01", "run-1")

    executed = engine.execute(plan)

    assert executed.execution_results
    assert [result.ticker for result in executed.execution_results] == [trade.ticker for trade in plan.trades]
    open_orders = engine.order_store.load_open_orders()
    assert all(order.session_date == "2026-07-01" for order in open_orders)
