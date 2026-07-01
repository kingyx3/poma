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
from poma.config import ManagedCapMode, Settings, TradingMode
from poma.data import MarketDataClient, build_data_client
from poma.history import CapSnapshotHistory
from poma.models import AccountSnapshot, OrderResult, RebalancePlan, StrategyTargetBook
from poma.portfolio import build_strategy_capital_plan
from poma.portfolio_constructor import combine_strategy_target_books
from poma.risk import (
    enforce_buying_power,
    enforce_order_limits,
    enforce_turnover_limit,
    generate_trades,
    validate_targets,
)
from poma.strategies import StrategyContext, StrategyRegistry, default_registry

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
    """Pure orchestration for a single rebalance across every allocated strategy sleeve."""

    def __init__(
        self,
        settings: Settings,
        *,
        data_client: MarketDataClient | None = None,
        broker: Broker | None = None,
        history: CapSnapshotHistory | None = None,
        strategy_registry: StrategyRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.data_client = data_client or build_data_client(settings)
        self.broker = broker or build_broker(settings)
        self.history = history
        self.strategy_registry = strategy_registry or default_registry()

    def build_plan(self, session_date: str, run_id: str) -> RebalancePlan:
        settings = self.settings
        warnings: list[str] = []
        account_snapshot = self._account_snapshot(warnings)
        portfolio_value_usd = self._resolve_portfolio_value_usd(account_snapshot)
        capital_plan = build_strategy_capital_plan(
            portfolio_value_usd,
            settings.strategy_allocations,
        )
        if capital_plan.unallocated_pct > 1e-9:
            warnings.append(
                f"{capital_plan.unallocated_pct:.2%} of portfolio value is not allocated "
                "to any strategy sleeve"
            )

        current = self.data_client.current_universe_snapshot()
        historical = None
        today = datetime.now(UTC).date()
        if self.history is not None:
            target_date = today - timedelta(days=settings.rank_lookback_days)
            historical = self.history.load_asof(target_date)
            self.history.save(current, today)

        strategy_books: list[StrategyTargetBook] = []
        for sleeve in capital_plan.tradeable_sleeves():
            strategy = self.strategy_registry.get(sleeve.name)
            context = StrategyContext(
                strategy_name=sleeve.name,
                allocation_pct=sleeve.allocation_pct,
                capital_usd=sleeve.capital_usd,
                current_universe=current,
                historical_universe=historical,
                settings=settings,
            )
            book = strategy.build_targets(context)
            strategy_books.append(book)
            warnings.extend(book.warnings)

        combined_targets, combine_warnings = combine_strategy_target_books(
            strategy_books,
            portfolio_value_usd,
        )
        warnings.extend(combine_warnings)
        targets = [position.to_target_position() for position in combined_targets]

        prices = {
            str(row.ticker): float(row.price)
            for row in current.itertuples()
            if getattr(row, "price", None) is not None
        }
        positions = list(account_snapshot.positions)
        trades, trade_warnings = generate_trades(
            targets=targets,
            current_positions=positions,
            latest_prices=prices,
            portfolio_value_usd=portfolio_value_usd,
            min_trade_notional_usd=settings.min_trade_notional_usd,
            min_weight_delta_pct=settings.min_weight_delta_pct,
            limit_offset_bps=settings.limit_offset_bps,
        )

        warnings.extend(validate_targets(targets, settings.max_position_pct))
        warnings.extend(trade_warnings)
        warnings.extend(
            enforce_turnover_limit(
                trades,
                portfolio_value_usd,
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
        warnings.extend(enforce_buying_power(trades, account_snapshot.cash_usd))

        return RebalancePlan(
            run_id=run_id,
            session_date=session_date,
            targets=targets,
            trades=trades,
            execution_results=[],
            warnings=warnings,
            portfolio_value_usd=portfolio_value_usd,
            portfolio_cash_usd=account_snapshot.cash_usd,
            portfolio_positions_value_usd=account_snapshot.positions_market_value_usd,
            portfolio_net_liquidation_usd=account_snapshot.net_liquidation_usd,
            strategy_books=tuple(strategy_books),
            combined_targets=tuple(combined_targets),
            total_allocated_pct=capital_plan.total_allocated_pct,
            total_allocated_usd=capital_plan.total_allocated_usd,
        )

    def _account_snapshot(self, warnings: list[str]) -> AccountSnapshot:
        """Read broker cash/positions once per rebalance; block on any unsafe read.

        A single snapshot backs both target sizing and trade generation so every strategy
        sleeve and the risk engine see the same cash and holdings for this run.
        """
        settings = self.settings
        fallback = AccountSnapshot(
            cash_usd=settings.dry_run_portfolio_value_usd,
            positions=(),
            positions_market_value_usd=0.0,
            net_liquidation_usd=settings.dry_run_portfolio_value_usd,
        )
        if settings.trading_mode == TradingMode.DRY_RUN:
            return fallback

        try:
            snapshot = self.broker.account_snapshot()
        except Exception as exc:  # noqa: BLE001 - broker detail is safer as a blocking warning
            warnings.append(
                "unable to read broker cash and portfolio balances before rebalancing; "
                f"{BLOCK_MARKER}: {exc}"
            )
            return fallback

        if snapshot.total_value_usd <= 0:
            warnings.append(
                "broker cash and portfolio balances produced a non-positive portfolio value; "
                f"{BLOCK_MARKER}"
            )
            return fallback
        return snapshot

    def _resolve_portfolio_value_usd(self, account_snapshot: AccountSnapshot) -> float:
        settings = self.settings
        if settings.trading_mode == TradingMode.DRY_RUN:
            return account_snapshot.total_value_usd
        if settings.managed_cap_mode == ManagedCapMode.BROKER_TOTAL:
            return account_snapshot.total_value_usd
        return min(account_snapshot.total_value_usd, settings.managed_cap_usd)

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
