from __future__ import annotations

import pandas as pd

from poma.models import TargetPosition


def rank_by_market_cap(snapshot: pd.DataFrame) -> pd.DataFrame:
    required = {"ticker", "market_cap"}
    missing = required - set(snapshot.columns)
    if missing:
        raise ValueError(f"snapshot missing required columns: {sorted(missing)}")
    ranked = snapshot.copy()
    ranked["market_cap_rank"] = (
        ranked["market_cap"].rank(ascending=False, method="first").astype(int)
    )
    return ranked.sort_values("market_cap_rank")


def select_maintained_or_improved(
    current: pd.DataFrame,
    previous: pd.DataFrame,
) -> pd.DataFrame:
    current_ranked = rank_by_market_cap(current)
    previous_ranked = rank_by_market_cap(previous)[["ticker", "market_cap_rank"]].rename(
        columns={"market_cap_rank": "previous_rank"}
    )
    joined = current_ranked.merge(previous_ranked, on="ticker", how="inner")
    selected = joined[joined["market_cap_rank"] <= joined["previous_rank"]].copy()
    return selected.sort_values("market_cap_rank")


def _apply_max_weight_cap(weights: pd.Series, max_weight: float) -> pd.Series:
    if weights.empty:
        return weights
    if max_weight <= 0:
        raise ValueError("max_weight must be positive")
    capped = weights.copy().astype(float)
    for _ in range(100):
        over = capped > max_weight
        if not over.any():
            break
        excess = (capped[over] - max_weight).sum()
        capped[over] = max_weight
        under = ~over
        if not under.any() or excess <= 1e-12:
            break
        capped[under] += excess * capped[under] / capped[under].sum()
    total = capped.sum()
    if total <= 0:
        raise ValueError("capped weights sum to zero")
    return capped / total


def build_market_cap_targets(
    selected: pd.DataFrame,
    portfolio_value_usd: float,
    cash_buffer_pct: float,
    max_position_pct: float,
) -> list[TargetPosition]:
    if selected.empty:
        return []
    investable_value = portfolio_value_usd * (1 - cash_buffer_pct)
    raw_weights = selected.set_index("ticker")["market_cap"] / selected["market_cap"].sum()
    weights = _apply_max_weight_cap(raw_weights, max_position_pct)
    return [
        TargetPosition(
            ticker=str(ticker),
            target_weight=float(weight),
            target_notional=float(weight * investable_value),
        )
        for ticker, weight in weights.sort_values(ascending=False).items()
    ]
