"""Direct, registered ETF holdings as strategy sleeves.

Names in INVESTMENT_STRATEGY_WEIGHTS can be ETF tickers rather than an
extra named "core" strategy. Each sleeve holds exactly its own ticker.
We deliberately register only USD instruments with known IBKR routing,
instead of guessing an exchange/currency for unknown symbols.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from poma.models import StrategyTarget, StrategyTargetBook
from poma.strategies.base import StrategyContext, StrategyDataRequirements
from poma.ucits_instruments import USD_LSE_UCITS

if TYPE_CHECKING:
    from poma.config import Settings


# Verified instrument routing/price-symbol configuration lives in ucits_instruments.py.
DIRECT_ETF_TICKERS = frozenset(USD_LSE_UCITS)


@dataclass(frozen=True)
class DirectEtfStrategy:
    ticker: str

    def __post_init__(self) -> None:
        if self.ticker not in DIRECT_ETF_TICKERS:
            raise ValueError(f"direct ETF ticker {self.ticker!r} is not supported")

    @property
    def name(self) -> str:
        return self.ticker

    def data_requirements(self, settings: Settings) -> StrategyDataRequirements:
        return StrategyDataRequirements(
            uses_universe=False,
            price_history_tickers=(self.ticker,),
            price_history_days=10,
        )

    def build_targets(self, context: StrategyContext) -> StrategyTargetBook:
        return StrategyTargetBook(
            strategy_name=self.name,
            allocation_pct=context.allocation_pct,
            capital_usd=context.capital_usd,
            targets=(
                StrategyTarget(
                    strategy_name=self.name,
                    ticker=self.ticker,
                    sleeve_weight=1.0,
                    portfolio_weight=context.allocation_pct,
                    target_notional=context.capital_usd,
                ),
            ),
            diversified_fund_tickers=(self.ticker,),
        )
