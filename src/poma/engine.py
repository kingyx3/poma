from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from poma.broker import (
    Broker,
    OrderStatusCallback,
    build_broker,
    order_results_have_issues,
    order_results_have_no_accepted_orders,
)
from poma.config import Settings, TradingMode
from poma.data import MarketDataClient, build_data_client
from poma.history import CapSnapshotHistory
from poma.models import OrderResult, RebalancePlan
from poma.portfolio import build_strategy_capital_plan
from poma.risk import (
    enforce_order_limits,
    enforce_turnover_limit,
    generate_trades,
    validate_targets,
)
from poma.strategy import (
    build_equal_weight_targets,
    select_by_combined_factor,
    select_top_market_cap,
)

BLOCK_MARKER = "block execution"
COMPLETED_STATUS = "completed"
COMPLETED_WITH_ORDER_ISSUES_STATUS = "completed_with_order_issues"
NO_ORDERS_ACCEPTED_STATUS = "no_orders_accepted"


@dataclass(frozen=True)
class RebalanceOutcome:
    plan: RebalancePlan
    executed: bool
    blocked: bool
    status: str


class RebalanceEngine:
    """Pure orchestration for a single rebalance."""

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
        capital_plan = build_strategy_capital_plan(
            settings.portfolio_value_usd,
            settings.strategy_allocations,
        )
        strategy_capital = capital_plan.capital_for(settings.active_strategy)
        current = self.data_client.current_universe_snapshot()
        warnings: list[str] = []
        if capital_plan.unallocated_pct > 1e-9:
            warnings.append(
                f"{capital_plan.unallocated_pct:.2%} of PORTFOLIO_VALUE_USD is not allocated "
                "to active strategies"
            )
        historical = None
        today = datetime.now(UTC).date()
        if self.history is not None:
            target_date = today - timedelta(days=settings.rank_lookback_days)
            historical = self.history.load_asof(target_date)
            self.history.save(current, today)

        if historical is None:
            selected = select_top_market_cap(current, settings.max_holdings)
            if self.history is not None:
                warnings.append(
                    "no historical market-cap snapshot found for lookback window; "
                    "falling back to current market-cap selection"
                )
        else:
            selected = select_by_combined_factor(current, historical, settings.max_holdings)
        targets = build_equal_weight_targets(
            selected=selected,
            portfolio_value_usd=strategy_capital.capital_usd,
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
            portfolio_value_usd=strategy_capital.capital_usd,
            min_trade_notional_usd=settings.min_trade_notional_usd,
            min_weight_delta_pct=settings.min_weight_delta_pct,
            limit_offset_bps=settings.limit_offset_bps,
        )

        warnings.extend(validate_targets(targets, settings.max_position_pct))
        warnings.extend(trade_warnings)
        warnings.extend(
            enforce_turnover_limit(
                trades,
                strategy_capital.capital_usd,
                settings.max_turnover_pct,
            )
        )
        warnings.extend(
            enforce_order_limits(
                trades,
                settings.max_order_notional_usd,
                settings.max_daily_trades,
            )
        )

        return RebalancePlan(
            run_id=run_id,
            session_date=session_date,
            targets=targets,
            trades=trades,
            execution_results=[],
            warnings=warnings,
            portfolio_value_usd=settings.portfolio_value_usd,
            strategy_name=strategy_capital.name,
            strategy_allocation_pct=strategy_capital.allocation_pct,
            strategy_capital_usd=strategy_capital.capital_usd,
            total_allocated_pct=capital_plan.total_allocated_pct,
            total_allocated_usd=capital_plan.total_allocated_usd,
        )

    def is_blocked(self, plan: RebalancePlan) -> bool:
        return any(BLOCK_MARKER in warning for warning in plan.warnings)

    def execute(
        self,
        plan: RebalancePlan,
        order_status_callback: OrderStatusCallback | None = None,
    ) -> RebalancePlan:
        if order_status_callback is None:
            results = self.broker.submit_trades(plan.trades)
        else:
            try:
                results = self.broker.submit_trades(plan.trades, status_callback=order_status_callback)
            except TypeError as exc:
                if "status_callback" not in str(exc):
                    raise
                results = self.broker.submit_trades(plan.trades)
                for trade, result in zip(plan.trades, results, strict=True):
                    order_status_callback(trade, result)
        return replace(plan, execution_results=results)

    def execution_status(self, results: list[OrderResult]) -> str:
        if order_results_have_no_accepted_orders(results):
            return NO_ORDERS_ACCEPTED_STATUS
        if order_results_have_issues(results):
            return COMPLETED_WITH_ORDER_ISSUES_STATUS
        return COMPLETED_STATUS

    def run(self, session_date: str, run_id: str) -> RebalanceOutcome:
        plan = self.build_plan(session_date, run_id)
        blocked = self.is_blocked(plan)
        if self.settings.trading_mode == TradingMode.DRY_RUN:
            return RebalanceOutcome(plan=plan, executed=False, blocked=blocked, status="dry_run")
        if blocked:
            return RebalanceOutcome(plan=plan, executed=False, blocked=True, status="blocked")
        plan = self.execute(plan)
        return RebalanceOutcome(
            plan=plan,
            executed=True,
            blocked=False,
            status=self.execution_status(plan.execution_results),
        )
