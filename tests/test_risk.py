from poma.models import CurrentPosition, OrderSide, TargetPosition
from poma.risk import (
    enforce_order_limits,
    enforce_turnover_limit,
    generate_trades,
    validate_targets,
)


def test_generate_trades_ignores_small_deltas() -> None:
    targets = [TargetPosition("A", 0.5, 500), TargetPosition("B", 0.5, 500)]
    current = [CurrentPosition("A", 1, 490), CurrentPosition("B", 1, 400)]
    trades, warnings = generate_trades(
        targets,
        current,
        latest_prices={"A": 100, "B": 50},
        portfolio_value_usd=1_000,
        min_trade_notional_usd=25,
        min_weight_delta_pct=0.01,
        limit_offset_bps=10,
    )
    assert warnings == []
    assert len(trades) == 1
    assert trades[0].ticker == "B"
    assert trades[0].side == OrderSide.BUY
    assert trades[0].notional == 100
    assert trades[0].quantity == 2
    assert trades[0].reference_price == 50
    assert trades[0].limit_price == 50.05


def test_turnover_limit_blocks_execution() -> None:
    targets = [TargetPosition("A", 0.5, 500)]
    trades, _ = generate_trades(
        targets,
        [],
        latest_prices={"A": 100},
        portfolio_value_usd=1_000,
        min_trade_notional_usd=1,
        min_weight_delta_pct=0,
        limit_offset_bps=10,
    )
    warnings = enforce_turnover_limit(
        trades,
        portfolio_value_usd=1_000,
        max_turnover_pct=0.10,
    )
    assert warnings
    assert "block execution" in warnings[0]


def test_order_limits_block_oversized_orders() -> None:
    targets = [TargetPosition("A", 0.5, 500)]
    trades, _ = generate_trades(
        targets,
        [],
        latest_prices={"A": 100},
        portfolio_value_usd=1_000,
        min_trade_notional_usd=1,
        min_weight_delta_pct=0,
        limit_offset_bps=10,
    )
    warnings = enforce_order_limits(
        trades,
        max_order_notional_usd=100,
        max_daily_trades=30,
    )
    assert warnings
    assert "block execution" in warnings[0]


def test_generate_trades_skips_nan_reference_price() -> None:
    trades, warnings = generate_trades(
        [TargetPosition("A", 0.5, 500)],
        [],
        latest_prices={"A": float("nan")},
        portfolio_value_usd=1_000,
        min_trade_notional_usd=1,
        min_weight_delta_pct=0,
        limit_offset_bps=10,
    )
    assert trades == []
    assert any("missing valid latest price" in w for w in warnings)


def test_validate_targets_warns_empty() -> None:
    assert validate_targets([], max_position_pct=0.1) == ["no target positions generated"]
