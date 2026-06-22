import pandas as pd

from poma.strategy import build_market_cap_targets, rank_by_market_cap, select_maintained_or_improved


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


def test_select_maintained_or_improved() -> None:
    current = pd.DataFrame(
        [
            {"ticker": "A", "market_cap": 100},
            {"ticker": "B", "market_cap": 300},
            {"ticker": "C", "market_cap": 200},
        ]
    )
    previous = pd.DataFrame(
        [
            {"ticker": "A", "market_cap": 300},
            {"ticker": "B", "market_cap": 200},
            {"ticker": "C", "market_cap": 100},
        ]
    )
    selected = select_maintained_or_improved(current, previous)
    assert selected["ticker"].tolist() == ["B", "C"]


def test_build_market_cap_targets_respects_cash_buffer() -> None:
    selected = pd.DataFrame(
        [
            {"ticker": "A", "market_cap": 100},
            {"ticker": "B", "market_cap": 100},
        ]
    )
    targets = build_market_cap_targets(selected, 1_000, 0.02, 0.60)
    assert sum(t.target_notional for t in targets) == 980
    assert all(t.target_weight <= 0.60 for t in targets)
