from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import pandas as pd

from poma.models import StrategyTargetBook

if TYPE_CHECKING:
    from poma.config import Settings


@dataclass(frozen=True)
class StrategyContext:
    """Everything a strategy sleeve needs to build its targets for one rebalance.

    ``capital_usd``/``allocation_pct`` come from the sleeve's share of the shared
    ``PortfolioCapitalPlan`` for this run, not from a strategy-specific broker read, so every
    sleeve sizes against the same account snapshot.
    """

    strategy_name: str
    allocation_pct: float
    capital_usd: float
    current_universe: pd.DataFrame
    historical_universe: pd.DataFrame | None
    settings: Settings
    # Daily closes (DatetimeIndex rows, one column per ticker) for the tickers the strategy asked
    # for via ``data_requirements``; ``None`` when it asked for none.
    price_history: pd.DataFrame | None = None


@dataclass(frozen=True)
class StrategyDataRequirements:
    """Market data a strategy needs the engine to load before ``build_targets``.

    ``uses_universe`` asks for the provider's market-cap universe snapshot (and its saved
    history). ``price_history_tickers`` asks for ``price_history_days`` calendar days of daily
    closes for instruments outside that universe, such as ETFs. Strategies that do not define
    ``data_requirements`` get the universe only, which is the original contract.
    """

    uses_universe: bool = True
    price_history_tickers: tuple[str, ...] = ()
    price_history_days: int = 0


DEFAULT_DATA_REQUIREMENTS = StrategyDataRequirements()


class Strategy(Protocol):
    name: str

    def build_targets(self, context: StrategyContext) -> StrategyTargetBook: ...


def strategy_data_requirements(strategy: Strategy, settings: Settings) -> StrategyDataRequirements:
    requirements = getattr(strategy, "data_requirements", None)
    if requirements is None:
        return DEFAULT_DATA_REQUIREMENTS
    return requirements(settings)
