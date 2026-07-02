from poma.execution_policy import (
    WHOLE_SHARE_EXECUTION_RULE,
    apply_execution_policy,
    build_execution_rules,
)
from poma.models import OrderSide, ProposedTrade


def _trade(ticker: str, side: OrderSide, quantity: float, reference_price: float = 100.0) -> ProposedTrade:
    return ProposedTrade(
        ticker=ticker,
        side=side,
        quantity=quantity,
        notional=quantity * reference_price,
        reference_price=reference_price,
        limit_price=reference_price,
        reason="rebalance_to_target_weight",
    )


def test_default_rule_leaves_fractional_quantity_untouched() -> None:
    trades = [_trade("AAPL", OrderSide.BUY, 5.02)]
    adjusted, warnings = apply_execution_policy(trades, rules={})
    assert warnings == []
    assert adjusted[0].quantity == 5.02
    assert adjusted[0].notional == 5.02 * 100.0


def test_non_fractional_rule_rounds_down_to_whole_shares() -> None:
    rules = build_execution_rules("AAPL")
    trades = [_trade("AAPL", OrderSide.BUY, 5.9, reference_price=100.0)]
    adjusted, warnings = apply_execution_policy(trades, rules)
    assert warnings == []
    assert adjusted[0].quantity == 5
    assert adjusted[0].notional == 500.0


def test_non_fractional_rule_drops_trade_below_one_share() -> None:
    rules = build_execution_rules("aapl")
    trades = [_trade("AAPL", OrderSide.BUY, 0.4)]
    adjusted, warnings = apply_execution_policy(trades, rules)
    assert adjusted == []
    assert "AAPL" in warnings[0]
    assert "skipping trade" in warnings[0]


def test_rules_are_per_ticker_other_tickers_stay_fractional() -> None:
    rules = build_execution_rules("AAPL")
    trades = [_trade("AAPL", OrderSide.BUY, 5.9), _trade("MSFT", OrderSide.BUY, 5.9)]
    adjusted, _ = apply_execution_policy(trades, rules)
    by_ticker = {trade.ticker: trade.quantity for trade in adjusted}
    assert by_ticker["AAPL"] == 5
    assert by_ticker["MSFT"] == 5.9


def test_quantity_increment_rounds_down_to_nearest_increment() -> None:
    rules = build_execution_rules("AAPL")
    # 5.9 shares floored to whole shares by non-fractional rule, increment has no further effect here.
    trades = [_trade("AAPL", OrderSide.SELL, 5.9)]
    adjusted, warnings = apply_execution_policy(trades, rules)
    assert warnings == []
    assert adjusted[0].quantity == 5


def test_build_execution_rules_empty_string_yields_no_rules() -> None:
    assert build_execution_rules("") == {}
    assert build_execution_rules("   ") == {}


def test_whole_share_default_rule_floors_every_ticker() -> None:
    trades = [_trade("AAPL", OrderSide.BUY, 5.9), _trade("MSFT", OrderSide.SELL, 2.4)]
    adjusted, warnings = apply_execution_policy(trades, rules={}, default_rule=WHOLE_SHARE_EXECUTION_RULE)
    assert warnings == []
    assert [trade.quantity for trade in adjusted] == [5, 2]


def test_whole_share_default_rule_drops_trades_below_one_share() -> None:
    trades = [_trade("ASML", OrderSide.BUY, 0.07)]
    adjusted, warnings = apply_execution_policy(trades, rules={}, default_rule=WHOLE_SHARE_EXECUTION_RULE)
    assert adjusted == []
    assert "ASML" in warnings[0]
    assert "skipping trade" in warnings[0]
