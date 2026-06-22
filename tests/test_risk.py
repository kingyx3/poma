from poma.models import CurrentPosition, OrderSide, TargetPosition
from poma.risk import enforce_turnover_limit, generate_trades, validate_targets


def test_generate_trades_ignores_small_deltas() -> None:
    targets = [TargetPosition("A", 0.5, 500), TargetPosition("B", 0.5, 500)]
    current = [CurrentPosition("A", 1, 490), CurrentPosition("B", 1, 400)]
    trades = generate_trades(targets, current, min_trade_notional_usd=25)
    assert len(trades) == 1
    assert trades[0].ticker == "B"
    assert trades[0].side == OrderSide.BUY
    assert trades[0].notional == 100


def test_turnover_limit_blocks_execution() -> None:
    targets = [TargetPosition("A", 0.5, 500)]
    trades = generate_trades(targets, [], min_trade_notional_usd=1)
    warnings = enforce_turnover_limit(trades, portfolio_value_usd=1_000, max_turnover_pct=0.10)
    assert warnings
    assert "block execution" in warnings[0]


def test_validate_targets_warns_empty() -> None:
    assert validate_targets([], max_position_pct=0.1) == ["no target positions generated"]
