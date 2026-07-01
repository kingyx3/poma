from __future__ import annotations

from poma.strategies.base import Strategy
from poma.strategies.rank_velocity_size_equal_weight import RankVelocitySizeEqualWeightStrategy


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> None:
        if strategy.name in self._strategies:
            raise ValueError(f"strategy {strategy.name!r} is already registered")
        self._strategies[strategy.name] = strategy

    def get(self, name: str) -> Strategy:
        try:
            return self._strategies[name]
        except KeyError:
            available = ", ".join(self.names()) or "none"
            raise KeyError(
                f"strategy {name!r} is not registered; available strategies: {available}"
            ) from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._strategies))


def default_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register(RankVelocitySizeEqualWeightStrategy())
    return registry
