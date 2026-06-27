from __future__ import annotations

import pandas as pd

from poma.models import TargetPosition

_LIQUIDITY_COLUMNS = [
    "dollar_volume",
    "regular_market_volume",
    "volume",
    "average_volume",
    "average_volume_10d",
    "float_shares",
    "shares_outstanding",
]


def _liquidity_score(snapshot: pd.DataFrame) -> pd.Series:
    score = pd.Series(0.0, index=snapshot.index)
    if "dollar_volume" in snapshot.columns:
        score = score.where(score > 0, pd.to_numeric(snapshot["dollar_volume"], errors="coerce"))
    if "volume" in snapshot.columns and "price" in snapshot.columns:
        volume = pd.to_numeric(snapshot["volume"], errors="coerce")
        price = pd.to_numeric(snapshot["price"], errors="coerce")
        score = score.where(score > 0, volume * price)
    for column in _LIQUIDITY_COLUMNS:
        if column in snapshot.columns:
            score = score.where(score > 0, pd.to_numeric(snapshot[column], errors="coerce"))
    return score.fillna(0.0)


def deduplicate_share_classes(snapshot: pd.DataFrame) -> pd.DataFrame:
    """Keep one ticker per exact market-cap bucket, preferring the most liquid share class.

    Some stock-level screeners can return multiple share classes for one company with the same
    company-level market cap. Ranking those rows separately double-counts the company and can
    allocate to two share classes. Exact duplicate market caps are treated as a duplicate issuer
    bucket; the row with the best liquidity proxy is retained.
    """
    if snapshot.empty:
        return snapshot.copy()
    if "market_cap" not in snapshot.columns:
        raise ValueError("snapshot missing required columns: ['market_cap']")

    frame = snapshot.copy()
    frame["market_cap"] = pd.to_numeric(frame["market_cap"], errors="coerce")
    frame = frame.dropna(subset=["market_cap"])
    if frame.empty:
        return frame

    frame["_liquidity_score"] = _liquidity_score(frame)
    frame["_original_order"] = range(len(frame))
    frame = frame.sort_values(
        ["market_cap", "_liquidity_score", "_original_order"],
        ascending=[False, False, True],
    )
    frame = frame.drop_duplicates(subset=["market_cap"], keep="first")
    return frame.drop(columns=["_liquidity_score", "_original_order"]).reset_index(drop=True)


def rank_by_market_cap(snapshot: pd.DataFrame) -> pd.DataFrame:
    required = {"ticker", "market_cap"}
    missing = required - set(snapshot.columns)
    if missing:
        raise ValueError(f"snapshot missing required columns: {sorted(missing)}")
    ranked = deduplicate_share_classes(snapshot)
    ranked["ticker"] = ranked["ticker"].astype(str).str.upper().str.strip()
    ranked["market_cap"] = pd.to_numeric(ranked["market_cap"], errors="coerce")
    ranked = ranked.dropna(subset=["ticker", "market_cap"])
    ranked = ranked[ranked["ticker"] != ""]
    ranked = ranked[ranked["market_cap"] > 0]
    ranked["market_cap_rank"] = ranked["market_cap"].rank(ascending=False, method="first").astype(int)
    return ranked.sort_values("market_cap_rank")


def select_top_market_cap(current: pd.DataFrame, max_holdings: int) -> pd.DataFrame:
    """Select the largest `max_holdings` names by current market cap."""
    if max_holdings <= 0:
        raise ValueError("max_holdings must be positive")
    return rank_by_market_cap(current).head(max_holdings)


def _zscore(values: pd.Series) -> pd.Series:
    """Standardize to mean 0 / std 1, returning zeros when the series has no spread."""
    std = float(values.std(ddof=0))
    if std == 0.0 or pd.isna(std):
        return pd.Series(0.0, index=values.index)
    return (values - values.mean()) / std


def select_by_combined_factor(
    current: pd.DataFrame,
    historical: pd.DataFrame,
    max_holdings: int,
) -> pd.DataFrame:
    """Select names by an equal-weighted blend of market-cap size and rank momentum.

    Two factors are combined so the strategy favours companies that are both large and climbing:

    * ``size`` -- larger companies score higher. Market cap is heavily right-skewed, so the
      market-cap *rank* (1 = largest) is used instead of the raw cap; that keeps the size factor on
      the same uniform scale as momentum rather than letting a few mega-caps swamp the blend.
    * ``momentum`` -- companies that climbed the market-cap ranking versus the lookback snapshot
      score higher. A positive ``rank_improvement_score`` means the current rank number is smaller
      than the historical one, i.e. the company moved up.

    Each factor is z-scored and summed with equal weight to form ``combined_score``; the top
    ``max_holdings`` names by that score are selected.
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
    merged["rank_improvement_score"] = merged["previous_market_cap_rank"] - merged["market_cap_rank"]

    # Smaller market-cap rank = larger company, so negate it before z-scoring so that "bigger" maps
    # to a higher score, matching momentum's "higher is better" orientation.
    merged["size_score"] = _zscore(-merged["market_cap_rank"].astype(float))
    merged["momentum_score"] = _zscore(merged["rank_improvement_score"].astype(float))
    merged["combined_score"] = merged["size_score"] + merged["momentum_score"]

    return merged.sort_values(
        ["combined_score", "market_cap_rank"],
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


def build_equal_weight_targets(
    selected: pd.DataFrame,
    portfolio_value_usd: float,
    cash_buffer_pct: float,
    max_position_pct: float,
) -> list[TargetPosition]:
    """Allocate the investable value equally across the selected names.

    Every constituent targets ``1 / N`` of the investable value. When that equal weight would
    exceed ``max_position_pct`` (i.e. the basket is small), the cap binds on every name and the
    uninvested remainder is held as cash rather than concentrated into fewer positions.
    """
    if selected.empty:
        return []
    investable_value = portfolio_value_usd * (1 - cash_buffer_pct)
    tickers = selected["ticker"].astype(str).tolist()
    equal_weight = 1.0 / len(tickers)
    raw_weights = pd.Series(equal_weight, index=tickers)
    weights = _apply_max_weight_cap(raw_weights, max_position_pct)
    return [
        TargetPosition(
            ticker=str(ticker),
            target_weight=float(weight),
            target_notional=float(weight * investable_value),
        )
        for ticker, weight in weights.items()
    ]
