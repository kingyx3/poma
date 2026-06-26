from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from poma.broker import Broker, build_broker
from poma.config import Settings, TradingMode
from poma.data import MarketDataClient, build_data_client
from poma.history import CapSnapshotHistory
from poma.models import RebalancePlan
from poma.risk import (
    enforce_order_limits,
    enforce_turnover_limit,
    generate_trades,
    validate_targets,
)
from poma.strategy import build_market_cap_targets, select_top_market_cap

BLOCK_MARKER = "block execution"


@dataclass(frozen=True)
class RebalanceOutcome:
    plan: RebalancePlan
    executed: bool
    blocked: bool
    status: str


class RebalanceEngine:
    """Pure orchestration for a single rebalance.

    Data and broker clients are injectable so the engine can be unit-tested without a live
    IBKR gateway or market-data provider. Side effects that are not part of the trading
    decision (report files, notifications, console output) live in the caller.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        data_client: MarketDataClient | None = None,
        broker: Broker | None = None,
        history: CapSnapshotHistory | None = None,
    ) -> None:
        self.settings = settings
        self.data_client = data_client or build_data_client(settings)
        self.broker = broker or build_broker(settings)
        self.history = history

    def build_plan(self, session_date: str, run_id: str) -> RebalancePlan:
        settings = self.settings
        current = self.data_client.current_universe_snapshot()
        if self.history is not None:
            self.history.save(current, datetime.now(UTC).date())
        selected = select_top_market_cap(current, settings.max_holdings)
        targets = build_market_cap_targets(
            selected=selected,
            portfolio_value_usd=settings.portfolio_value_usd,
            cash_buffer_pct=settings.cash_buffer_pct,
            max_position_pct=settings.max_position_pct,
        )

        prices = {
            str(row.ticker): float(row.price)
            for row in current.itertuples()
            if getattr(row, "price", None) is not None
        }
        positions = self.broker.positions()
        trades, trade_warnings = generate_trades(
            targets=targets,
            current_positions=positions,
            latest_prices=prices,
            portfolio_value_usd=settings.portfolio_value_usd,
            min_trade_notional_usd=settings.min_trade_notional_usd,
            min_weight_delta_pct=settings.min_weight_delta_pct,
            limit_offset_bps=settings.limit_offset_bps,
        )

        warnings = validate_targets(targets, settings.max_position_pct)
        warnings.extend(trade_warnings)
        warnings.extend(
            enforce_turnover_limit(trades, settings.portfolio_value_usd, settings.max_turnover_pct)
        )
        warnings.extend(
            enforce_order_limits(
                trades, settings.max_order_notional_usd, settings.max_daily_trades
            )
        )

        return RebalancePlan(
            run_id=run_id,
            session_date=session_date,
            targets=targets,
            trades=trades,
            execution_results=[],
            warnings=warnings,
        )

    def is_blocked(self, plan: RebalancePlan) -> bool:
        return any(BLOCK_MARKER in warning for warning in plan.warnings)

    def execute(self, plan: RebalancePlan) -> RebalancePlan:
        results = self.broker.submit_trades(plan.trades)
        return replace(plan, execution_results=results)

    def run(self, session_date: str, run_id: str) -> RebalanceOutcome:
        plan = self.build_plan(session_date, run_id)
        blocked = self.is_blocked(plan)
        if self.settings.trading_mode == TradingMode.DRY_RUN:
            return RebalanceOutcome(plan=plan, executed=False, blocked=blocked, status="dry_run")
        if blocked:
            return RebalanceOutcome(plan=plan, executed=False, blocked=True, status="blocked")
        plan = self.execute(plan)
        return RebalanceOutcome(plan=plan, executed=True, blocked=False, status="completed")
