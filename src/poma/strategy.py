from __future__ import annotations

import pandas as pd

from poma.models import TargetPosition


def rank_by_market_cap(snapshot: pd.DataFrame) -> pd.DataFrame:
    required = {"ticker", "market_cap"}
    missing = required - set(snapshot.columns)
    if missing:
        raise ValueError(f"snapshot missing required columns: {sorted(missing)}")
    ranked = snapshot.copy()
    ranked["market_cap"] = pd.to_numeric(ranked["market_cap"], errors="coerce")
    ranked = ranked.dropna(subset=["ticker", "market_cap"])
    ranked = ranked[ranked["market_cap"] > 0]
    ranked["market_cap_rank"] = (
        ranked["market_cap"].rank(ascending=False, method="first").astype(int)
    )
    return ranked.sort_values("market_cap_rank")


def select_top_market_cap(current: pd.DataFrame, max_holdings: int) -> pd.DataFrame:
    """Select the largest `max_holdings` names by current market cap."""
    if max_holdings <= 0:
        raise ValueError("max_holdings must be positive")
    return rank_by_market_cap(current).head(max_holdings)


def select_rank_improvers(
    current: pd.DataFrame,
    historical: pd.DataFrame,
    max_holdings: int,
) -> pd.DataFrame:
    """Select names with the strongest market-cap-rank improvement versus history.

    Rank 1 is the largest company. A positive score means the current rank number is smaller
    than the historical rank number, so the company moved up the market-cap ranking.
    """
    if max_holdings <= 0:
        raise ValueError("max_holdings must be positive")

    current_ranked = rank_by_market_cap(current)
    historical_ranked = rank_by_market_cap(historical)
    previous_ranks = historical_ranked[["ticker", "market_cap_rank"]].rename(
        columns={"market_cap_rank": "previous_market_cap_rank"}
    )
    merged = current_ranked.merge(previous_ranks, on="ticker", how="left")

    # Missing historical names are usually newly added symbols. Do not artificially boost them;
    # treat them as unchanged until enough point-in-time history has accumulated.
    merged["previous_market_cap_rank"] = merged["previous_market_cap_rank"].fillna(
        merged["market_cap_rank"]
    )
    merged["previous_market_cap_rank"] = merged["previous_market_cap_rank"].astype(int)
    merged["rank_improvement_score"] = (
        merged["previous_market_cap_rank"] - merged["market_cap_rank"]
    )
    return merged.sort_values(
        ["rank_improvement_score", "market_cap_rank"],
        ascending=[False, True],
    ).head(max_holdings)


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
    # Only scale down when weights overshoot 1; when the cap binds on every name they sum to
    # < 1 and the remainder stays in cash. Renormalizing that case up would scale capped
    # weights back above max_weight and silently violate the cap.
    return capped / total if total > 1.0 else capped


def build_market_cap_targets(
    selected: pd.DataFrame,
    portfolio_value_usd: float,
    cash_buffer_pct: float,
    max_position_pct: float,
) -> list[TargetPosition]:
    if selected.empty:
        return []
    investable_value = portfolio_value_usd * (1 - cash_buffer_pct)
    market_caps = selected.set_index("ticker")["market_cap"]
    raw_weights = market_caps / selected["market_cap"].sum()
    weights = _apply_max_weight_cap(raw_weights, max_position_pct)
    return [
        TargetPosition(
            ticker=str(ticker),
            target_weight=float(weight),
            target_notional=float(weight * investable_value),
        )
        for ticker, weight in weights.sort_values(ascending=False).items()
    ]
