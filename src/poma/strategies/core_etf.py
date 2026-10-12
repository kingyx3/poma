from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from poma.models import StrategyTarget, StrategyTargetBook
from poma.strategies.base import StrategyContext, StrategyDataRequirements

if TYPE_CHECKING:
    from poma.config import Settings

NAME = "core_etf"

# Enough calendar days to see the latest close even across a long weekend or holiday.
_LATEST_PRICE_DAYS = 10


# The core_etf strategy owns exactly one instrument. Select capital exposure via
# INVESTMENT_STRATEGY_WEIGHTS; there is no second ETF-weight configuration layer.
CORE_ETF_TICKER = "USMC"


# CORE_ETF_SAFE_ASSET values that mean "hold the risk-off weight as cash".
_CASH_SAFE_ASSET_VALUES = {"", "CASH", "NONE"}


def safe_asset(settings: Settings) -> str | None:
    ticker = (settings.core_etf_safe_asset or "").strip().upper()
    return None if ticker in _CASH_SAFE_ASSET_VALUES else ticker


def completed_month_closes(history: pd.Series) -> pd.Series:
    """Month-end closes for calendar months that have finished, oldest first.

    The month containing the latest observation is treated as still in progress and dropped,
    so the trend signal only changes once per month instead of flickering day to day.
    """
    series = pd.to_numeric(history, errors="coerce").dropna()
    series = series[series > 0]
    if series.empty:
        return series
    series.index = pd.DatetimeIndex(series.index)
    month_ends = series.groupby(series.index.to_period("M")).last()
    return month_ends.iloc[:-1]


def trend_is_up(history: pd.Series, sma_months: int) -> bool | None:
    """Whether the last completed month closed above its ``sma_months`` month-end average.

    Returns ``None`` when there are fewer than ``sma_months`` completed months.
    """
    month_ends = completed_month_closes(history)
    if len(month_ends) < sma_months:
        return None
    window = month_ends.iloc[-sma_months:]
    return bool(window.iloc[-1] > window.mean())


class CoreEtfStrategy:
    """Hold the USD London USMC mega-cap fund; optionally apply a monthly trend filter.

    See docs/strategies/core-etf.md for the audit that motivated it and its backtests.
    """

    name = NAME

    def data_requirements(self, settings: Settings) -> StrategyDataRequirements:
        tickers = [CORE_ETF_TICKER]
        safe = safe_asset(settings)
        if safe and safe not in tickers:
            tickers.append(safe)
        days = _LATEST_PRICE_DAYS
        if settings.core_etf_trend_filter:
            # sma_months completed months plus the in-progress month, with slack for holidays.
            days = (settings.core_etf_trend_sma_months + 2) * 31 + _LATEST_PRICE_DAYS
        return StrategyDataRequirements(
            uses_universe=False,
            price_history_tickers=tuple(tickers),
            price_history_days=days,
        )

    def build_targets(self, context: StrategyContext) -> StrategyTargetBook:
        settings = context.settings
        risk_weights = {CORE_ETF_TICKER: 1.0}
        safe = safe_asset(settings)
        warnings: list[str] = []
        sleeve_weights: dict[str, float] = {}

        for ticker, weight in risk_weights.items():
            invested = True
            if settings.core_etf_trend_filter:
                history = _history_for(context.price_history, ticker)
                trend = trend_is_up(history, settings.core_etf_trend_sma_months)
                if trend is None:
                    warnings.append(
                        f"{ticker}: fewer than {settings.core_etf_trend_sma_months} completed months "
                        "of price history for the trend filter; holding the ETF"
                    )
                else:
                    invested = trend
            if invested:
                sleeve_weights[ticker] = sleeve_weights.get(ticker, 0.0) + weight
            elif safe:
                sleeve_weights[safe] = sleeve_weights.get(safe, 0.0) + weight
                warnings.append(f"{ticker}: below its trend average; moving its weight to {safe}")
            else:
                warnings.append(f"{ticker}: below its trend average; holding its weight in cash")

        targets = tuple(
            StrategyTarget(
                strategy_name=self.name,
                ticker=ticker,
                sleeve_weight=weight,
                portfolio_weight=weight * context.allocation_pct,
                target_notional=weight * context.capital_usd,
            )
            for ticker, weight in sorted(sleeve_weights.items())
        )
        fund_tickers = tuple(sorted({*risk_weights, *([safe] if safe else [])}))
        return StrategyTargetBook(
            strategy_name=self.name,
            allocation_pct=context.allocation_pct,
            capital_usd=context.capital_usd,
            targets=targets,
            warnings=tuple(warnings),
            diversified_fund_tickers=fund_tickers,
        )


def _history_for(price_history: pd.DataFrame | None, ticker: str) -> pd.Series:
    if price_history is None or ticker not in price_history.columns:
        return pd.Series(dtype=float)
    return price_history[ticker]
