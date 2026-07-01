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
class AccountSnapshot:
    """A single consistent read of broker cash, positions, and net liquidation.

    Fetched once per rebalance so every strategy sleeve and the risk engine see the same
    account state, instead of separate cash and positions reads that can race against each
    other during a live rebalance.
    """

    cash_usd: float
    positions: tuple[CurrentPosition, ...]
    positions_market_value_usd: float
    net_liquidation_usd: float | None = None
    account_id: str | None = None
    timestamp_utc: str | None = None

    @property
    def total_value_usd(self) -> float:
        cash_and_positions = self.cash_usd + self.positions_market_value_usd
        if cash_and_positions > 0:
            return cash_and_positions
        if self.net_liquidation_usd is not None:
            return self.net_liquidation_usd
        return cash_and_positions


@dataclass(frozen=True)
class StrategyTarget:
    """One ticker target produced by a single strategy sleeve, before combining sleeves."""

    strategy_name: str
    ticker: str
    sleeve_weight: float
    portfolio_weight: float
    target_notional: float


@dataclass(frozen=True)
class StrategyTargetBook:
    """All targets produced by one strategy sleeve for a single rebalance."""

    strategy_name: str
    allocation_pct: float
    capital_usd: float
    targets: tuple[StrategyTarget, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CombinedTargetPosition:
    """One ticker's final portfolio-level target after combining every strategy sleeve."""

    ticker: str
    target_weight: float
    target_notional: float
    contributions: tuple[StrategyTarget, ...]

    def to_target_position(self) -> TargetPosition:
        return TargetPosition(
            ticker=self.ticker,
            target_weight=self.target_weight,
            target_notional=self.target_notional,
        )


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
class InstrumentExecutionRule:
    """What quantity shape a broker will actually accept for one ticker.

    Defaults assume IBKR's standard fractional-share support for US-listed stocks/ETFs;
    override per-ticker only for instruments confirmed to reject fractional orders.
    """

    ticker: str
    allows_fractional: bool = True
    min_quantity: float = 0.0
    quantity_increment: float = 0.0


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
    portfolio_cash_usd: float = 0.0
    portfolio_positions_value_usd: float = 0.0
    portfolio_net_liquidation_usd: float | None = None
    strategy_books: tuple[StrategyTargetBook, ...] = ()
    combined_targets: tuple[CombinedTargetPosition, ...] = ()
    total_allocated_pct: float = 1.0
    total_allocated_usd: float = 0.0
