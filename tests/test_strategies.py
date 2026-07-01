from __future__ import annotations

import math

import pandas as pd
import pytest
from conftest import make_settings

from poma.strategies import StrategyContext, StrategyRegistry, default_registry
from poma.strategies.rank_velocity_size_equal_weight import NAME as RANK_STRATEGY_NAME
from poma.strategies.rank_velocity_size_equal_weight import RankVelocitySizeEqualWeightStrategy


def _snapshot(tickers: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"ticker": ticker, "market_cap": 1_000 - index} for index, ticker in enumerate(tickers)]
    )


def test_default_registry_only_contains_rank_velocity_strategy() -> None:
    registry = default_registry()

    assert registry.names() == (RANK_STRATEGY_NAME,)
    assert registry.get(RANK_STRATEGY_NAME).name == RANK_STRATEGY_NAME


def test_registry_get_unknown_strategy_lists_available_names() -> None:
    registry = default_registry()

    with pytest.raises(KeyError, match="available strategies: " + RANK_STRATEGY_NAME):
        registry.get("does_not_exist")


def test_registry_rejects_duplicate_registration() -> None:
    registry = StrategyRegistry()
    registry.register(RankVelocitySizeEqualWeightStrategy())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(RankVelocitySizeEqualWeightStrategy())


def test_rank_strategy_falls_back_to_current_market_cap_without_history() -> None:
    strategy = RankVelocitySizeEqualWeightStrategy()
    context = StrategyContext(
        strategy_name=RANK_STRATEGY_NAME,
        allocation_pct=0.5,
        capital_usd=1_000.0,
        current_universe=_snapshot(["A", "B"]),
        historical_universe=None,
        settings=make_settings(MAX_HOLDINGS=2, MAX_POSITION_PCT=1.0),
    )

    book = strategy.build_targets(context)

    assert book.strategy_name == RANK_STRATEGY_NAME
    assert book.allocation_pct == 0.5
    assert book.capital_usd == 1_000.0
    assert len(book.targets) == 2
    assert any("falling back to current market-cap selection" in warning for warning in book.warnings)
    assert math.isclose(sum(target.target_notional for target in book.targets), 1_000.0)
    for target in book.targets:
        assert target.strategy_name == RANK_STRATEGY_NAME
        assert math.isclose(target.portfolio_weight, target.sleeve_weight * 0.5)


def test_rank_strategy_uses_combined_factor_with_history() -> None:
    strategy = RankVelocitySizeEqualWeightStrategy()
    context = StrategyContext(
        strategy_name=RANK_STRATEGY_NAME,
        allocation_pct=1.0,
        capital_usd=1_000.0,
        current_universe=_snapshot(["A", "B"]),
        historical_universe=_snapshot(["B", "A"]),
        settings=make_settings(MAX_HOLDINGS=2, MAX_POSITION_PCT=1.0),
    )

    book = strategy.build_targets(context)

    assert book.warnings == ()
    assert len(book.targets) == 2
