from __future__ import annotations

from poma.models import StrategyTarget, StrategyTargetBook
from poma.strategies.base import StrategyContext
from poma.strategy import build_equal_weight_targets, select_by_combined_factor, select_top_market_cap

NAME = "rank_velocity_size_equal_weight"


class RankVelocitySizeEqualWeightStrategy:
    """US top-market-cap dual-score strategy: see docs/strategy-contract.md."""

    name = NAME

    def build_targets(self, context: StrategyContext) -> StrategyTargetBook:
        warnings: list[str] = []
        if context.historical_universe is None:
            selected = select_top_market_cap(context.current_universe, context.settings.max_holdings)
            warnings.append(
                "no historical market-cap snapshot found for lookback window; "
                "falling back to current market-cap selection"
            )
        else:
            selected = select_by_combined_factor(
                context.current_universe,
                context.historical_universe,
                context.settings.max_holdings,
            )

        raw_targets = build_equal_weight_targets(
            selected=selected,
            portfolio_value_usd=context.capital_usd,
            max_position_pct=context.settings.max_position_pct,
        )
        targets = tuple(
            StrategyTarget(
                strategy_name=self.name,
                ticker=target.ticker,
                sleeve_weight=target.target_weight,
                portfolio_weight=target.target_weight * context.allocation_pct,
                target_notional=target.target_notional,
            )
            for target in raw_targets
        )
        return StrategyTargetBook(
            strategy_name=self.name,
            allocation_pct=context.allocation_pct,
            capital_usd=context.capital_usd,
            targets=targets,
            warnings=tuple(warnings),
        )
