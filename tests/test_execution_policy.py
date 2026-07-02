from poma.execution_policy import (
    DEFAULT_RULE_TICKER,
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


def test_default_rule_rounds_buys_to_nearest_whole_share() -> None:
    rules = build_execution_rules("")
    trades = [_trade("AAPL", OrderSide.BUY, 5.02), _trade("MSFT", OrderSide.BUY, 5.6)]
    adjusted, warnings = apply_execution_policy(trades, rules)
    assert warnings == []
    by_ticker = {trade.ticker: trade for trade in adjusted}
    assert by_ticker["AAPL"].quantity == 5
    assert by_ticker["AAPL"].notional == 500.0
    assert by_ticker["MSFT"].quantity == 6
    assert by_ticker["MSFT"].notional == 600.0


def test_default_rule_rounds_sells_down_to_never_oversell() -> None:
    rules = build_execution_rules("")
    trades = [_trade("AAPL", OrderSide.SELL, 5.9)]
    adjusted, warnings = apply_execution_policy(trades, rules)
    assert warnings == []
    assert adjusted[0].quantity == 5
    assert adjusted[0].notional == 500.0


def test_default_rule_drops_trade_below_half_share() -> None:
    rules = build_execution_rules("")
    trades = [_trade("AAPL", OrderSide.BUY, 0.4)]
    adjusted, warnings = apply_execution_policy(trades, rules)
    assert adjusted == []
    assert "AAPL" in warnings[0]
    assert "skipping trade" in warnings[0]


def test_buy_at_or_above_half_share_rounds_up_to_one_share() -> None:
    rules = build_execution_rules("")
    trades = [_trade("AAPL", OrderSide.BUY, 0.6)]
    adjusted, warnings = apply_execution_policy(trades, rules)
    assert warnings == []
    assert adjusted[0].quantity == 1


def test_fractional_shares_mode_leaves_fractional_quantity_untouched() -> None:
    rules = build_execution_rules("", fractional_shares=True)
    trades = [_trade("AAPL", OrderSide.BUY, 5.02)]
    adjusted, warnings = apply_execution_policy(trades, rules)
    assert warnings == []
    assert adjusted[0].quantity == 5.02
    assert adjusted[0].notional == 5.02 * 100.0


def test_fractional_shares_mode_keeps_non_fractional_ticker_overrides() -> None:
    rules = build_execution_rules("aapl", fractional_shares=True)
    trades = [_trade("AAPL", OrderSide.SELL, 5.9), _trade("MSFT", OrderSide.BUY, 5.9)]
    adjusted, _ = apply_execution_policy(trades, rules)
    by_ticker = {trade.ticker: trade.quantity for trade in adjusted}
    assert by_ticker["AAPL"] == 5
    assert by_ticker["MSFT"] == 5.9


def test_build_execution_rules_defaults_every_ticker_to_whole_shares() -> None:
    rules = build_execution_rules("")
    assert set(rules) == {DEFAULT_RULE_TICKER}
    assert rules[DEFAULT_RULE_TICKER].allows_fractional is False
    assert rules[DEFAULT_RULE_TICKER].min_quantity == 1.0


def test_buy_roundups_are_demoted_to_fit_available_cash() -> None:
    rules = build_execution_rules("")
    # Both buys round up (5.6 -> 6 at $200, 2.6 -> 3 at $100): $1,500 total against $1,400 of
    # cash. Demoting the round-up with the largest saving (AAPL, one $200 share) brings buys
    # back to $1,300 <= $1,400 without touching MSFT.
    trades = [
        _trade("AAPL", OrderSide.BUY, 5.6, reference_price=200.0),
        _trade("MSFT", OrderSide.BUY, 2.6),
    ]
    adjusted, warnings = apply_execution_policy(trades, rules, available_cash_usd=1_400.0)
    by_ticker = {trade.ticker: trade.quantity for trade in adjusted}
    assert by_ticker["AAPL"] == 5
    assert by_ticker["MSFT"] == 3
    assert any("demoted" in warning and "AAPL" in warning for warning in warnings)


def test_buy_roundup_demotion_counts_sell_proceeds_toward_the_budget() -> None:
    rules = build_execution_rules("")
    trades = [
        _trade("NVDA", OrderSide.SELL, 4.0),
        _trade("AAPL", OrderSide.BUY, 5.6),
    ]
    # $600 of buys against $250 cash + $400 planned sell proceeds: no demotion needed.
    adjusted, warnings = apply_execution_policy(trades, rules, available_cash_usd=250.0)
    assert warnings == []
    by_ticker = {trade.ticker: trade.quantity for trade in adjusted}
    assert by_ticker["AAPL"] == 6


def test_buy_roundup_demotion_drops_single_share_buys_when_cash_cannot_cover_them() -> None:
    rules = build_execution_rules("")
    trades = [_trade("AAPL", OrderSide.BUY, 0.6)]
    adjusted, warnings = apply_execution_policy(trades, rules, available_cash_usd=50.0)
    assert adjusted == []
    assert any("dropped to fit available cash" in warning for warning in warnings)


def test_rounding_within_cash_budget_is_untouched() -> None:
    rules = build_execution_rules("")
    trades = [_trade("AAPL", OrderSide.BUY, 5.6)]
    adjusted, warnings = apply_execution_policy(trades, rules, available_cash_usd=10_000.0)
    assert warnings == []
    assert adjusted[0].quantity == 6
