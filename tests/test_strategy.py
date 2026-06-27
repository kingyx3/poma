import pandas as pd

from poma.strategy import (
    build_market_cap_targets,
    rank_by_market_cap,
    select_rank_improvers,
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


def test_select_rank_improvers_compares_current_rank_to_history() -> None:
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

    selected = select_rank_improvers(current, historical, max_holdings=1)

    assert selected["ticker"].tolist() == ["B"]
    assert selected.iloc[0]["previous_market_cap_rank"] == 2
    assert selected.iloc[0]["market_cap_rank"] == 1
    assert selected.iloc[0]["rank_improvement_score"] == 1


def test_build_market_cap_targets_enforces_cap_when_it_binds_on_all_names() -> None:
    selected = pd.DataFrame([{"ticker": t, "market_cap": 100} for t in "ABCD"])
    targets = build_market_cap_targets(selected, 1_000, 0.0, 0.10)
    assert all(t.target_weight <= 0.10 + 1e-9 for t in targets)
    assert sum(t.target_weight for t in targets) <= 0.40 + 1e-9


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
