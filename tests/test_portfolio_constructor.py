from __future__ import annotations

import math

from poma.models import StrategyTarget, StrategyTargetBook
from poma.portfolio_constructor import combine_strategy_target_books


def _book(strategy_name: str, allocation_pct: float, capital_usd: float, targets) -> StrategyTargetBook:
    return StrategyTargetBook(
        strategy_name=strategy_name,
        allocation_pct=allocation_pct,
        capital_usd=capital_usd,
        targets=tuple(targets),
    )


def test_non_overlapping_sleeves_pass_through_independently() -> None:
    books = [
        _book(
            "alpha",
            0.5,
            5_000,
            [StrategyTarget("alpha", "AAPL", 1.0, 0.5, 5_000)],
        ),
        _book(
            "beta",
            0.3,
            3_000,
            [StrategyTarget("beta", "MSFT", 1.0, 0.3, 3_000)],
        ),
    ]

    combined, warnings = combine_strategy_target_books(books, portfolio_value_usd=10_000)

    assert {position.ticker for position in combined} == {"AAPL", "MSFT"}
    assert warnings == []
    aapl = next(position for position in combined if position.ticker == "AAPL")
    assert math.isclose(aapl.target_weight, 0.5)
    assert len(aapl.contributions) == 1


def test_overlapping_sleeves_net_into_one_combined_target() -> None:
    books = [
        _book(
            "alpha",
            0.5,
            5_000,
            [StrategyTarget("alpha", "AAPL", 1.0, 0.5, 5_000)],
        ),
        _book(
            "beta",
            0.3,
            3_000,
            [StrategyTarget("beta", "AAPL", 1.0, 0.3, 3_000)],
        ),
    ]

    combined, warnings = combine_strategy_target_books(books, portfolio_value_usd=10_000)

    assert len(combined) == 1
    aapl = combined[0]
    assert aapl.target_notional == 8_000
    assert math.isclose(aapl.target_weight, 0.8)
    assert {contribution.strategy_name for contribution in aapl.contributions} == {"alpha", "beta"}
    assert any("combines overlapping allocations" in warning for warning in warnings)


def test_combined_notional_exceeding_portfolio_value_warns_and_blocks() -> None:
    books = [
        _book(
            "alpha",
            0.7,
            7_000,
            [StrategyTarget("alpha", "AAPL", 1.0, 0.7, 7_000)],
        ),
        _book(
            "beta",
            0.5,
            5_000,
            [StrategyTarget("beta", "AAPL", 1.0, 0.5, 5_000)],
        ),
    ]

    _, warnings = combine_strategy_target_books(books, portfolio_value_usd=10_000)

    assert any("exceed portfolio value" in warning and "block execution" in warning for warning in warnings)


def test_empty_books_produce_no_targets_or_warnings() -> None:
    combined, warnings = combine_strategy_target_books([], portfolio_value_usd=10_000)

    assert combined == []
    assert warnings == []
