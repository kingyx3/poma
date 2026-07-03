from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


def _resolve_account_total_value(
    cash_usd: float,
    positions_market_value_usd: float,
    net_liquidation_usd: float | None,
) -> float:
    """Cash plus positions when available; net liquidation is the fallback when it is not."""
    cash_and_positions = cash_usd + positions_market_value_usd
    if cash_and_positions > 0:
        return cash_and_positions
    if net_liquidation_usd is not None:
        return net_liquidation_usd
    return cash_and_positions


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
    """A single consistent read of broker cash, positions, and net liquidation, in USD.

    Fetched once per rebalance so every strategy sleeve and the risk engine see the same
    account state, instead of separate cash and positions reads that can race against each
    other during a live rebalance. All monetary fields are actual USD-denominated values;
    non-USD cash and positions are excluded from rebalancing decisions.
    """

    cash_usd: float
    positions: tuple[CurrentPosition, ...]
    positions_market_value_usd: float
    net_liquidation_usd: float | None = None
    account_id: str | None = None
    timestamp_utc: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def total_value_usd(self) -> float:
        return _resolve_account_total_value(self.cash_usd, self.positions_market_value_usd, self.net_liquidation_usd)


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
    order_ref: str | None = None
    reference_price_source: str = "snapshot"
    reference_price_basis: str = "snapshot_price"
    reference_price_as_of_utc: str | None = None
    quote_age_seconds: float | None = None
    quote_spread_bps: float | None = None

    @property
    def buy_cash_required_usd(self) -> float:
        """Maximum cash this BUY can consume, using the submitted limit when present."""
        if self.side != OrderSide.BUY:
            return 0.0
        if self.limit_price is not None and self.limit_price > 0:
            return abs(self.quantity) * self.limit_price
        return self.notional

    @property
    def sell_cash_credit_usd(self) -> float:
        """Conservative cash credit for a SELL, using the limit as the minimum fill price."""
        if self.side != OrderSide.SELL:
            return 0.0
        if self.limit_price is not None and self.limit_price > 0:
            return abs(self.quantity) * self.limit_price
        return self.notional


@dataclass(frozen=True)
class ExecutionQuote:
    """One broker-side market data read for a ticker, captured just before order submission.

    This is the execution-time counterpart to the Yahoo screener snapshot: the snapshot still
    drives universe selection and target sizing, but paper/live order pricing is anchored on
    this fresher, broker-sourced quote instead.
    """

    ticker: str
    source: str
    retrieved_at_utc: str
    basis: str = "raw"
    selected_price: float | None = None
    selected_price_as_of_utc: str | None = None
    age_seconds: float | None = None
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    close: float | None = None
    spread_bps: float | None = None
    is_delayed: bool = False
    raw_market_data_type: str | None = None
    broker_error: str | None = None


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
    order_ref: str | None = None
    perm_id: int | None = None


@dataclass(frozen=True)
class OpenOrderSnapshot:
    """One broker-reported open order, as returned by a lifecycle reconciliation poll."""

    order_ref: str | None
    order_id: int | None
    perm_id: int | None
    ticker: str
    side: OrderSide
    raw_status: str
    filled: float
    remaining: float
    avg_fill_price: float | None


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
    cash_sleeve_usd: float = 0.0

    @property
    def broker_total_value_usd(self) -> float:
        """Raw broker equity this plan sized against, before any MANAGED_CAP_MODE cap."""
        return _resolve_account_total_value(
            self.portfolio_cash_usd,
            self.portfolio_positions_value_usd,
            self.portfolio_net_liquidation_usd,
        )

    @property
    def unallocated_capital_usd(self) -> float:
        """Managed value not assigned to any strategy sleeve, including the cash sleeve."""
        return max(0.0, self.portfolio_value_usd - self.total_allocated_usd)

    @property
    def target_exposure_usd(self) -> float:
        """Total planned notional across every combined portfolio-level target."""
        return sum(position.target_notional for position in self.combined_targets)

    def broker_account_snapshot_json(self) -> dict[str, object]:
        """The USD-only broker read this plan sized against, shared by reports and journal."""
        return {
            "cash_usd": self.portfolio_cash_usd,
            "positions_market_value_usd": self.portfolio_positions_value_usd,
            "net_liquidation_usd": self.portfolio_net_liquidation_usd,
            "total_value_usd": self.broker_total_value_usd,
        }
