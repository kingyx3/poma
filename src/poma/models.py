from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class TargetPosition:
    ticker: str
    target_weight: float
    target_notional: float


@dataclass(frozen=True)
class CurrentPosition:
    ticker: str
    quantity: float
    market_value: float


@dataclass(frozen=True)
class ProposedTrade:
    ticker: str
    side: OrderSide
    quantity: float
    notional: float
    reference_price: float
    limit_price: float | None
    reason: str


@dataclass(frozen=True)
class OrderResult:
    ticker: str
    side: OrderSide
    quantity: float
    notional: float
    order_id: int | None
    status: str
    filled: float
    average_fill_price: float | None
    message: str | None = None


@dataclass(frozen=True)
class RebalancePlan:
    run_id: str
    session_date: str
    targets: list[TargetPosition]
    trades: list[ProposedTrade]
    execution_results: list[OrderResult]
    warnings: list[str]
    portfolio_value_usd: float = 0.0
    strategy_name: str = ""
    strategy_allocation_pct: float = 1.0
    strategy_capital_usd: float = 0.0
    total_allocated_pct: float = 1.0
    total_allocated_usd: float = 0.0
    cash_balance_usd: float | None = None
    positions_value_usd: float = 0.0
    portfolio_value_source: str = "configured"
