"""Regressions for splitting the invested 98% sleeve across registered strategies."""

from __future__ import annotations

import math

import pytest
from conftest import make_settings

from poma.portfolio import (
    build_strategy_capital_plan,
    investment_weights_to_portfolio_allocations,
)


def test_three_investment_weights_keep_two_percent_cash() -> None:
    allocations = investment_weights_to_portfolio_allocations(
        "strategy_a=60%,strategy_b=20%,strategy_c=20%", cash_target_pct=0.02
    )
    assert allocations == pytest.approx({
        "strategy_a": 0.588, "strategy_b": 0.196,
        "strategy_c": 0.196, "cash": 0.02,
    })
    plan = build_strategy_capital_plan(100_000, allocations)
    assert plan.capital_for("strategy_a").capital_usd == pytest.approx(58_800)
    assert plan.capital_for("strategy_b").capital_usd == pytest.approx(19_600)
    assert plan.capital_for("strategy_c").capital_usd == pytest.approx(19_600)
    assert plan.capital_for("cash").capital_usd == pytest.approx(2_000)
    assert plan.total_allocated_pct == pytest.approx(1.0)


def test_default_uses_full_invested_sleeve_and_preserves_cash() -> None:
    settings = make_settings()
    assert settings.strategy_allocation_map() == pytest.approx({
        "core_etf": 0.98, "cash": 0.02,
    })
    assert settings.cash_target_pct == 0.02


def test_two_registered_strategies_can_share_invested_sleeve() -> None:
    settings = make_settings(
        INVESTMENT_STRATEGY_WEIGHTS="core_etf=60%,rank_velocity_size_equal_weight=40%",
        CASH_TARGET_PCT=0.02,
    )
    assert settings.strategy_allocation_map() == pytest.approx({
        "core_etf": 0.588, "rank_velocity_size_equal_weight": 0.392,
        "cash": 0.02,
    })


@pytest.mark.parametrize(
    "weights",
    [
        "core_etf=60%,rank_velocity_size_equal_weight=20%",
        "core_etf=90%,cash=10%",
        "core_etf=0",
        "core_etf=-1",
        "core_etf=nan",
        "core_etf=0.6,core_etf=0.4",
    ],
)
def test_invalid_investment_weights_fail_closed(weights: str) -> None:
    with pytest.raises(ValueError):
        make_settings(INVESTMENT_STRATEGY_WEIGHTS=weights)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.0, 1.1])
def test_invalid_cash_target_is_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        investment_weights_to_portfolio_allocations("core_etf=1.0", value)


def test_unknown_strategy_requires_registration() -> None:
    with pytest.raises(ValueError, match="unregistered strategies"):
        make_settings(INVESTMENT_STRATEGY_WEIGHTS="strategy_a=1.0")


def test_legacy_full_portfolio_allocations_remain_supported() -> None:
    settings = make_settings(STRATEGY_ALLOCATIONS="core_etf=0.80,cash=0.20")
    assert settings.strategy_allocation_map() == pytest.approx({"core_etf": 0.80, "cash": 0.20})


def test_new_and_legacy_allocations_cannot_be_combined() -> None:
    with pytest.raises(ValueError, match="not both"):
        make_settings(
            INVESTMENT_STRATEGY_WEIGHTS="core_etf=1.0",
            STRATEGY_ALLOCATIONS="core_etf=0.98,cash=0.02",
        )


def test_cash_target_is_not_double_counted() -> None:
    settings = make_settings(INVESTMENT_STRATEGY_WEIGHTS="core_etf=100%", CASH_TARGET_PCT=0.05)
    alloc = settings.strategy_allocation_map()
    assert math.isclose(sum(alloc.values()), 1.0)
    assert alloc == pytest.approx({"core_etf": 0.95, "cash": 0.05})
