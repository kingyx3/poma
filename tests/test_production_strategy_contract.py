from __future__ import annotations

import math

import pandas as pd

from poma.strategy import (
    build_equal_weight_targets,
    deduplicate_share_classes,
    select_by_combined_factor,
)


def _snapshot_with_ranks(ranked_tickers: list[str]) -> pd.DataFrame:
    rows = []
    total = len(ranked_tickers)
    for index, ticker in enumerate(ranked_tickers):
        market_cap = (total - index) * 1_000_000_000
        rows.append(
            {
                "ticker": ticker,
                "name": f"{ticker} Inc",
                "market_cap": market_cap,
                "price": 100,
                "volume": 1_000_000,
            }
        )
    return pd.DataFrame(rows)


def test_dual_score_selects_top_100_from_top_500_universe() -> None:
    current_order = [f"T{i:03d}" for i in range(1, 501)]
    # Push the last 100 current names down in the historical ordering so they have the strongest
    # positive rank-rising velocity, while the current rank still contributes as the size factor.
    historical_order = current_order[100:400] + current_order[:100] + current_order[400:]

    selected = select_by_combined_factor(
        current=_snapshot_with_ranks(current_order),
        historical=_snapshot_with_ranks(historical_order),
        max_holdings=100,
    )

    assert len(selected) == 100
    assert selected["ticker"].is_unique
    assert set(selected.columns) >= {
        "market_cap_rank",
        "previous_market_cap_rank",
        "rank_improvement_score",
        "size_score",
        "rank_velocity_score",
        "momentum_score",
        "combined_score",
    }
    assert selected["combined_score"].is_monotonic_decreasing
    assert selected["market_cap_rank"].between(1, 500).all()


def test_share_class_dedupe_prefers_more_liquid_same_issuer() -> None:
    snapshot = pd.DataFrame(
        [
            {
                "ticker": "GOOGL",
                "name": "Alphabet Inc Class A",
                "market_cap": 2_000_000_000_000,
                "price": 180,
                "volume": 1_000_000,
            },
            {
                "ticker": "GOOG",
                "name": "Alphabet Inc Class C",
                "market_cap": 2_000_000_000_000,
                "price": 181,
                "volume": 2_000_000,
            },
            {
                "ticker": "MSFT",
                "name": "Microsoft Corporation",
                "market_cap": 3_000_000_000_000,
                "price": 420,
                "volume": 1_500_000,
            },
        ]
    )

    deduped = deduplicate_share_classes(snapshot)

    assert deduped["ticker"].tolist() == ["MSFT", "GOOG"]


def test_selected_names_are_equal_weighted_within_strategy_sleeve() -> None:
    selected = pd.DataFrame({"ticker": [f"T{i:03d}" for i in range(1, 101)]})

    targets = build_equal_weight_targets(
        selected=selected,
        portfolio_value_usd=10_000,
        max_position_pct=0.10,
    )

    assert len(targets) == 100
    assert all(math.isclose(target.target_weight, 0.01) for target in targets)
    assert math.isclose(sum(target.target_weight for target in targets), 1.0)
    assert all(math.isclose(target.target_notional, 100.0) for target in targets)
