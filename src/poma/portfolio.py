from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from poma.ucits_instruments import USD_LSE_UCITS

# The original daily top-market-cap stock strategy. Still registered; select it through
# STRATEGY_ALLOCATIONS to restore the pre-core_etf behavior.
CURRENT_STRATEGY_NAME = "rank_velocity_size_equal_weight"
DEFAULT_STRATEGY_NAME = "USMC"
CASH_STRATEGY_NAME = "cash"
DEFAULT_STRATEGY_ALLOCATIONS = f"{DEFAULT_STRATEGY_NAME}=0.98,{CASH_STRATEGY_NAME}=0.02"


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

    def tradeable_sleeves(self) -> tuple[StrategyCapital, ...]:
        """Every allocated sleeve the engine should execute: non-cash, with positive capital."""
        return tuple(
            allocation
            for allocation in self.allocations
            if allocation.name != CASH_STRATEGY_NAME and allocation.capital_usd > 0
        )


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



def investment_weights_to_portfolio_allocations(
    raw_weights: str,
    cash_target_pct: float = 0.02,
) -> dict[str, float]:
    """Turn a 100%-of-investment strategy mix into whole-portfolio allocations.

    For 2% cash, a 60/20/20 investment mix becomes 58.8/19.6/19.6% of
    *total* account equity, plus a 2% dedicated cash sleeve. Unknown strategy
    names are validated separately against the registered strategy catalog.
    """
    if not math.isfinite(cash_target_pct) or not 0 <= cash_target_pct < 1:
        raise ValueError("CASH_TARGET_PCT must be finite and between 0 (inclusive) and 1 (exclusive)")
    raw_parsed = parse_strategy_allocations(raw_weights)
    # Registered direct ETF IDs are canonical uppercase symbols. Accept the familiar
    # lower-case input "usmc=1.0", and reject case-colliding duplicate allocations.
    weights: dict[str, float] = {}
    for name, weight in raw_parsed.items():
        canonical_name = name.upper() if name.upper() in USD_LSE_UCITS else name
        if canonical_name in weights:
            raise ValueError(f"duplicate investment strategy allocation for {canonical_name!r}")
        weights[canonical_name] = weight
    if CASH_STRATEGY_NAME in weights:
        raise ValueError("INVESTMENT_STRATEGY_WEIGHTS must not include cash; use CASH_TARGET_PCT")
    if not any(weight > 0 for weight in weights.values()):
        raise ValueError("INVESTMENT_STRATEGY_WEIGHTS must allocate positive investment weight")
    total = sum(weights.values())
    if not math.isclose(total, 1.0, abs_tol=1e-8, rel_tol=0):
        raise ValueError(
            "INVESTMENT_STRATEGY_WEIGHTS must sum to 100% of the investment portfolio "
            f"(got {total:.4%})"
        )
    investable_pct = 1.0 - cash_target_pct
    return {
        **{name: weight / total * investable_pct for name, weight in weights.items()},
        CASH_STRATEGY_NAME: cash_target_pct,
    }


def build_strategy_capital_plan(
    portfolio_value_usd: float,
    allocations: str | Mapping[str, float],
) -> PortfolioCapitalPlan:
    """Convert allocation percentages into per-strategy capital sleeves.

    ``portfolio_value_usd`` is the effective account value for this plan. Paper/live runs pass
    broker-derived USD cash plus USD-denominated portfolio balances; dry-runs pass the
    configured fallback.
    Individual strategies receive ``portfolio_value_usd * allocation_pct``. Passive cash is modeled
    as its own ``cash`` allocation sleeve rather than as a hidden buffer inside an active strategy.
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
