import pandas as pd

from poma.strategy import (
    build_equal_weight_targets,
    deduplicate_share_classes,
    rank_by_market_cap,
    select_by_combined_factor,
    select_top_market_cap,
)


def test_rank_by_market_cap_descending() -> None:
    frame = pd.DataFrame(
        [
            {"ticker": "A", "market_cap": 100},
            {"ticker": "B", "market_cap": 300},
            {"ticker": "C", "market_cap": 200},
        ]
    )
    ranked = rank_by_market_cap(frame)
    assert ranked["ticker"].tolist() == ["B", "C", "A"]
    assert ranked["market_cap_rank"].tolist() == [1, 2, 3]


def test_rank_by_market_cap_deduplicates_same_market_cap_share_classes() -> None:
    frame = pd.DataFrame(
        [
            {"ticker": "LOWV", "market_cap": 500, "price": 10, "volume": 1_000},
            {"ticker": "HIGHV", "market_cap": 500, "price": 10, "volume": 10_000},
            {"ticker": "OTHER", "market_cap": 300, "price": 10, "volume": 50_000},
        ]
    )

    ranked = rank_by_market_cap(frame)

    assert ranked["ticker"].tolist() == ["HIGHV", "OTHER"]
    assert ranked["market_cap_rank"].tolist() == [1, 2]


def test_deduplicate_share_classes_prefers_explicit_dollar_volume() -> None:
    frame = pd.DataFrame(
        [
            {"ticker": "A", "market_cap": 100, "dollar_volume": 100_000},
            {"ticker": "B", "market_cap": 100, "dollar_volume": 250_000},
        ]
    )

    deduped = deduplicate_share_classes(frame)

    assert deduped["ticker"].tolist() == ["B"]


def test_select_top_market_cap_caps_holdings() -> None:
    current = pd.DataFrame(
        [
            {"ticker": "A", "market_cap": 100},
            {"ticker": "B", "market_cap": 500},
            {"ticker": "C", "market_cap": 400},
            {"ticker": "D", "market_cap": 300},
        ]
    )
    selected = select_top_market_cap(current, max_holdings=2)
    assert selected["ticker"].tolist() == ["B", "C"]
    assert len(selected) == 2


def test_select_by_combined_factor_blends_size_and_momentum() -> None:
    historical = pd.DataFrame(
        [
            {"ticker": "A", "market_cap": 400},
            {"ticker": "B", "market_cap": 300},
            {"ticker": "C", "market_cap": 200},
        ]
    )
    current = pd.DataFrame(
        [
            {"ticker": "A", "market_cap": 200},
            {"ticker": "B", "market_cap": 500},
            {"ticker": "C", "market_cap": 300},
        ]
    )

    selected = select_by_combined_factor(current, historical, max_holdings=1)

    # B is both the largest now (rank 1) and climbed one rank, so it wins the blended score.
    assert selected["ticker"].tolist() == ["B"]
    assert selected.iloc[0]["previous_market_cap_rank"] == 2
    assert selected.iloc[0]["market_cap_rank"] == 1
    assert selected.iloc[0]["rank_improvement_score"] == 1
    assert "combined_score" in selected.columns


def test_select_by_combined_factor_uses_size_not_only_momentum() -> None:
    # D barely climbs (small momentum) but is by far the largest; E climbs hard from the bottom.
    # A pure rank-momentum strategy would pick E, but the size factor should lift the mega-cap D.
    historical = pd.DataFrame(
        [
            {"ticker": "D", "market_cap": 1_000},
            {"ticker": "F", "market_cap": 900},
            {"ticker": "G", "market_cap": 800},
            {"ticker": "E", "market_cap": 10},
        ]
    )
    current = pd.DataFrame(
        [
            {"ticker": "D", "market_cap": 5_000},
            {"ticker": "F", "market_cap": 900},
            {"ticker": "G", "market_cap": 800},
            {"ticker": "E", "market_cap": 850},
        ]
    )

    selected = select_by_combined_factor(current, historical, max_holdings=1)

    assert selected["ticker"].tolist() == ["D"]


def test_select_by_combined_factor_does_not_double_count_duplicate_share_classes() -> None:
    historical = pd.DataFrame(
        [
            {"ticker": "LOWV", "market_cap": 500, "price": 10, "volume": 1_000},
            {"ticker": "HIGHV", "market_cap": 500, "price": 10, "volume": 10_000},
            {"ticker": "OTHER", "market_cap": 300, "price": 10, "volume": 50_000},
        ]
    )
    current = pd.DataFrame(
        [
            {"ticker": "LOWV", "market_cap": 500, "price": 10, "volume": 1_000},
            {"ticker": "HIGHV", "market_cap": 500, "price": 10, "volume": 10_000},
            {"ticker": "OTHER", "market_cap": 300, "price": 10, "volume": 50_000},
        ]
    )

    selected = select_by_combined_factor(current, historical, max_holdings=3)

    assert selected["ticker"].tolist() == ["HIGHV", "OTHER"]


def test_build_equal_weight_targets_allocates_evenly() -> None:
    selected = pd.DataFrame([{"ticker": t, "market_cap": cap} for t, cap in
                             [("A", 900), ("B", 100), ("C", 50), ("D", 10)]])
    targets = build_equal_weight_targets(selected, 1_000, 0.0, 0.50)
    # Equal weighting ignores the (very different) market caps: every name gets 1/N.
    assert all(abs(t.target_weight - 0.25) < 1e-9 for t in targets)
    assert all(abs(t.target_notional - 250) < 1e-9 for t in targets)


def test_build_equal_weight_targets_enforces_cap_and_holds_remainder_as_cash() -> None:
    selected = pd.DataFrame([{"ticker": t, "market_cap": 100} for t in "ABCD"])
    # 1/4 = 0.25 each would exceed the 0.10 cap, so the cap binds on every name and the
    # uninvested remainder stays in cash rather than concentrating.
    targets = build_equal_weight_targets(selected, 1_000, 0.0, 0.10)
    assert all(abs(t.target_weight - 0.10) < 1e-9 for t in targets)
    assert sum(t.target_weight for t in targets) <= 0.40 + 1e-9


def test_build_equal_weight_targets_respects_cash_buffer() -> None:
    selected = pd.DataFrame(
        [
            {"ticker": "A", "market_cap": 100},
            {"ticker": "B", "market_cap": 100},
        ]
    )
    targets = build_equal_weight_targets(selected, 1_000, 0.02, 0.60)
    assert sum(t.target_notional for t in targets) == 980
    assert all(t.target_weight <= 0.60 for t in targets)
