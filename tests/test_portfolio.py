import math

import pytest

from poma.portfolio import (
    CASH_STRATEGY_NAME,
    CURRENT_STRATEGY_NAME,
    DEFAULT_STRATEGY_ALLOCATIONS,
    build_strategy_capital_plan,
    parse_strategy_allocations,
)


def test_default_strategy_allocation_splits_rank_and_cash_sleeves() -> None:
    allocations = parse_strategy_allocations(DEFAULT_STRATEGY_ALLOCATIONS)

    assert allocations == {CURRENT_STRATEGY_NAME: 0.98, CASH_STRATEGY_NAME: 0.02}


def test_percentage_style_allocations_are_supported() -> None:
    allocations = parse_strategy_allocations(f"{CURRENT_STRATEGY_NAME}=50%,future_strategy=0.25")

    assert allocations == {CURRENT_STRATEGY_NAME: 0.5, "future_strategy": 0.25}


def test_strategy_allocations_cannot_exceed_total_portfolio() -> None:
    with pytest.raises(ValueError, match="must not exceed 100%"):
        parse_strategy_allocations(f"{CURRENT_STRATEGY_NAME}=0.75,future_strategy=0.50")


def test_capital_plan_caps_all_strategy_sleeves_at_portfolio_value() -> None:
    plan = build_strategy_capital_plan(
        10_000,
        f"{CURRENT_STRATEGY_NAME}=0.60,future_strategy=0.25,{CASH_STRATEGY_NAME}=0.15",
    )

    assert math.isclose(plan.capital_for(CURRENT_STRATEGY_NAME).capital_usd, 6_000)
    assert math.isclose(plan.capital_for("future_strategy").capital_usd, 2_500)
    assert math.isclose(plan.capital_for(CASH_STRATEGY_NAME).capital_usd, 1_500)
    assert math.isclose(plan.total_allocated_usd, 10_000)
    assert math.isclose(plan.unallocated_usd, 0)
    assert plan.total_allocated_usd <= plan.portfolio_value_usd
