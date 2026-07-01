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


class Strategy(Protocol):
    name: str

    def build_targets(self, context: StrategyContext) -> StrategyTargetBook: ...
