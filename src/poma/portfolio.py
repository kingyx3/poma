from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

CURRENT_STRATEGY_NAME = "rank_velocity_size_equal_weight"


@dataclass(frozen=True)
class StrategyCapital:
    name: str
    allocation_pct: float
    capital_usd: float


@dataclass(frozen=True)
class PortfolioCapitalPlan:
    portfolio_value_usd: float
    allocations: tuple[StrategyCapital, ...]

    @property
    def total_allocated_pct(self) -> float:
        return sum(allocation.allocation_pct for allocation in self.allocations)

    @property
    def total_allocated_usd(self) -> float:
        return sum(allocation.capital_usd for allocation in self.allocations)

    @property
    def unallocated_pct(self) -> float:
        return max(0.0, 1.0 - self.total_allocated_pct)

    @property
    def unallocated_usd(self) -> float:
        return max(0.0, self.portfolio_value_usd - self.total_allocated_usd)

    def capital_for(self, strategy_name: str) -> StrategyCapital:
        for allocation in self.allocations:
            if allocation.name == strategy_name:
                return allocation
        available = ", ".join(allocation.name for allocation in self.allocations)
        raise KeyError(f"strategy {strategy_name!r} is not allocated; available strategies: {available}")


def _parse_pct(raw_value: str) -> float:
    value = raw_value.strip()
    if not value:
        raise ValueError("strategy allocation percentage must not be empty")
    pct = float(value[:-1].strip()) / 100.0 if value.endswith("%") else float(value)
    if not math.isfinite(pct):
        raise ValueError("strategy allocation percentage must be finite")
    if not 0 <= pct <= 1:
        raise ValueError("strategy allocation percentages must be between 0 and 1; use 1.0 for 100%")
    return pct


def _validate_allocations(allocations: Mapping[str, float]) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for raw_name, raw_pct in allocations.items():
        name = str(raw_name).strip()
        if not name:
            raise ValueError("strategy allocation names must not be empty")
        if name in cleaned:
            raise ValueError(f"duplicate strategy allocation for {name!r}")
        pct = float(raw_pct)
        if not math.isfinite(pct):
            raise ValueError(f"strategy allocation for {name!r} must be finite")
        if not 0 <= pct <= 1:
            raise ValueError(f"strategy allocation for {name!r} must be between 0 and 1")
        cleaned[name] = pct

    if not cleaned:
        raise ValueError("at least one strategy allocation is required")

    total = sum(cleaned.values())
    if total > 1.000001:
        raise ValueError(
            f"strategy allocations sum to {total:.2%}; total allocated capital must not exceed 100%"
        )
    return cleaned


def parse_strategy_allocations(raw_allocations: str) -> dict[str, float]:
    """Parse STRATEGY_ALLOCATIONS as ``name=0.5,other=50%`` style entries."""
    if not raw_allocations or not raw_allocations.strip():
        raise ValueError("STRATEGY_ALLOCATIONS must not be empty")

    parsed: dict[str, float] = {}
    for entry in raw_allocations.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" in entry:
            raw_name, raw_pct = entry.split("=", 1)
        elif ":" in entry:
            raw_name, raw_pct = entry.split(":", 1)
        else:
            raise ValueError(
                "strategy allocations must use name=percentage entries, "
                f"got {entry!r}"
            )
        name = raw_name.strip()
        if name in parsed:
            raise ValueError(f"duplicate strategy allocation for {name!r}")
        parsed[name] = _parse_pct(raw_pct)

    return _validate_allocations(parsed)


def build_strategy_capital_plan(
    portfolio_value_usd: float,
    allocations: str | Mapping[str, float],
) -> PortfolioCapitalPlan:
    """Convert allocation percentages into per-strategy capital sleeves.

    ``portfolio_value_usd`` remains the hard cap for all strategy sleeves combined. Individual
    strategies receive ``portfolio_value_usd * allocation_pct`` and may then apply their own cash
    buffers/risk caps inside that sleeve.
    """
    if portfolio_value_usd <= 0 or not math.isfinite(portfolio_value_usd):
        raise ValueError("portfolio_value_usd must be positive and finite")

    allocation_map = (
        parse_strategy_allocations(allocations)
        if isinstance(allocations, str)
        else _validate_allocations(allocations)
    )
    capital = tuple(
        StrategyCapital(
            name=name,
            allocation_pct=pct,
            capital_usd=portfolio_value_usd * pct,
        )
        for name, pct in allocation_map.items()
    )
    return PortfolioCapitalPlan(portfolio_value_usd=portfolio_value_usd, allocations=capital)
