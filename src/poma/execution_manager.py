from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from poma.broker import Broker, OrderStatusCallback
from poma.config import Settings, StaleOrderPolicy
from poma.models import OrderResult, OrderSide, ProposedTrade, RebalancePlan
from poma.order_lifecycle import (
    WORKING_LIFECYCLE_STATES,
    OrderLedgerEntry,
    OrderLifecycleState,
    build_order_ref,
    more_aggressive_limit_price,
    seconds_since,
)
from poma.order_store import OrderStore


@dataclass(frozen=True)
class ReconcileUpdate:
    entry: OrderLedgerEntry
    action: str | None  # "replace", "cancel", or None (status refresh only, or no broker match)
    matched: bool


@dataclass(frozen=True)
class ReconcileSummary:
    checked: int
    updates: tuple[ReconcileUpdate, ...]


@dataclass(frozen=True)
class StaleOrderCheck:
    """Result of checking the order ledger for unresolved orders before a new rebalance."""

    warnings: tuple[str, ...]
    cancelled_ledger_keys: tuple[str, ...] = ()


class ExecutionManager:
    """Owns execution policy: staged submission, the durable order ledger, and reconciliation.

    ``IbkrBroker`` stays a thin adapter that only knows how to submit/cancel/replace/query
    broker orders; every lifecycle and sequencing decision lives here.
    """

    def __init__(self, broker: Broker, store: OrderStore, settings: Settings) -> None:
        self.broker = broker
        self.store = store
        self.settings = settings

    # --- Staged submission -------------------------------------------------------------

    def submit_plan(
        self,
        plan: RebalancePlan,
        status_callback: OrderStatusCallback | None = None,
    ) -> list[OrderResult]:
        """Submit sells before buys, tagging every order with an idempotent orderRef.

        Sells are staged first so a rebalance does not rely on buying power that has not yet
        been confirmed by the broker. Every trade in ``plan.trades`` is tagged with a stable
        ``orderRef`` and recorded in the durable order ledger before submission, so a crash
        mid-run still leaves a trace of what was sent.
        """
        sells = [trade for trade in plan.trades if trade.side == OrderSide.SELL]
        buys = [trade for trade in plan.trades if trade.side == OrderSide.BUY]
        tagged_sells = self._tag(plan.run_id, sells, offset=0)
        tagged_buys = self._tag(plan.run_id, buys, offset=len(sells))

        for trade in (*tagged_sells, *tagged_buys):
            self._record_planned(plan, trade)

        results_by_ticker: dict[str, OrderResult] = {}
        for phase_trades in (tagged_sells, tagged_buys):
            if not phase_trades:
                continue
            phase_results = self.broker.submit_trades(
                phase_trades,
                status_callback=self._wrap_callback(plan, status_callback),
            )
            for trade, result in zip(phase_trades, phase_results, strict=True):
                results_by_ticker[trade.ticker] = result
                self._record_result(plan, trade, result)

        return [results_by_ticker[trade.ticker] for trade in plan.trades]

    def _tag(self, run_id: str, trades: list[ProposedTrade], *, offset: int) -> list[ProposedTrade]:
        return [
            replace(trade, order_ref=build_order_ref(run_id, offset + index, trade.ticker, trade.side))
            for index, trade in enumerate(trades)
        ]

    def _record_planned(self, plan: RebalancePlan, trade: ProposedTrade) -> None:
        assert trade.order_ref is not None
        entry = OrderLedgerEntry(
            ledger_key=trade.order_ref,
            order_ref=trade.order_ref,
            run_id=plan.run_id,
            session_date=plan.session_date,
            ticker=trade.ticker,
            side=trade.side,
            quantity=trade.quantity,
            limit_price=trade.limit_price,
            lifecycle_state=OrderLifecycleState.PLANNED,
        )
        self.store.upsert(entry)

    def _record_result(self, plan: RebalancePlan, trade: ProposedTrade, result: OrderResult) -> None:
        assert trade.order_ref is not None
        entry = self.store.get(trade.order_ref)
        if entry is None:
            entry = OrderLedgerEntry(
                ledger_key=trade.order_ref,
                order_ref=trade.order_ref,
                run_id=plan.run_id,
                session_date=plan.session_date,
                ticker=trade.ticker,
                side=trade.side,
                quantity=trade.quantity,
                limit_price=trade.limit_price,
            )
        self.store.upsert(entry.with_order_result(result))

    def _wrap_callback(
        self,
        plan: RebalancePlan,
        status_callback: OrderStatusCallback | None,
    ) -> OrderStatusCallback:
        def _inner(trade: ProposedTrade, result: OrderResult) -> None:
            self._record_result(plan, trade, result)
            if status_callback is not None:
                status_callback(trade, result)

        return _inner

    # --- Stale-order check before a new rebalance ---------------------------------------

    def check_stale_orders(self, session_date: str) -> StaleOrderCheck:
        """Block (or cancel) unresolved open orders from a prior session before replanning.

        Open orders from *this* session are reported informationally only: a legitimate retry
        of the same session should not be blocked by its own still-working orders.
        """
        open_entries = [entry for entry in self.store.load_open_orders() if not entry.is_terminal]
        other_session = [entry for entry in open_entries if entry.session_date != session_date]
        same_session = [entry for entry in open_entries if entry.session_date == session_date]

        warnings: list[str] = []
        cancelled: list[str] = []
        if other_session:
            tickers = ", ".join(sorted({entry.ticker for entry in other_session}))
            if self.settings.stale_order_policy == StaleOrderPolicy.CANCEL:
                for entry in other_session:
                    if entry.order_id is not None and self.broker.cancel_order(entry.order_id):
                        self.store.upsert(
                            replace(
                                entry,
                                lifecycle_state=OrderLifecycleState.CANCEL_PENDING,
                                terminal_reason="cancelled: unresolved order from a prior session",
                            )
                        )
                        cancelled.append(entry.ledger_key)
                warnings.append(
                    f"cancelled {len(cancelled)} open order(s) from a prior session before planning "
                    f"({tickers})"
                )
            else:
                warnings.append(
                    f"{len(other_session)} open order(s) from a prior session are still unresolved "
                    f"({tickers}); run `poma reconcile-orders` or cancel manually before this session "
                    f"can trade; block execution"
                )
        if same_session:
            tickers = ", ".join(sorted({entry.ticker for entry in same_session}))
            warnings.append(
                f"{len(same_session)} open order(s) from this session are still unresolved ({tickers}); "
                "run `poma reconcile-orders` to follow up"
            )
        return StaleOrderCheck(warnings=tuple(warnings), cancelled_ledger_keys=tuple(cancelled))

    # --- Reconciliation after the rebalance process exits --------------------------------

    def reconcile(self) -> ReconcileSummary:
        """Poll the broker for open orders and apply the replace-once/cancel timeout policy."""
        open_entries = [entry for entry in self.store.load_open_orders() if not entry.is_terminal]
        if not open_entries:
            return ReconcileSummary(checked=0, updates=())

        snapshots = {
            snapshot.order_ref: snapshot for snapshot in self.broker.fetch_open_order_snapshots() if snapshot.order_ref
        }
        now = datetime.now(UTC)
        updates: list[ReconcileUpdate] = []
        for entry in open_entries:
            snapshot = snapshots.get(entry.order_ref)
            if snapshot is None:
                updates.append(ReconcileUpdate(entry=entry, action=None, matched=False))
                continue
            updated = entry.with_snapshot(snapshot)
            action_taken = self._apply_timeout_policy(updated, now)
            action_name: str | None = None
            if action_taken is not None:
                updated, action_name = action_taken
            self.store.upsert(updated)
            updates.append(ReconcileUpdate(entry=updated, action=action_name, matched=True))
        return ReconcileSummary(checked=len(open_entries), updates=tuple(updates))

    def _apply_timeout_policy(
        self,
        entry: OrderLedgerEntry,
        now: datetime,
    ) -> tuple[OrderLedgerEntry, str] | None:
        if entry.lifecycle_state not in WORKING_LIFECYCLE_STATES:
            return None
        elapsed = seconds_since(entry.submitted_at, now)
        if elapsed is None:
            return None
        if elapsed >= self.settings.cancel_after_seconds:
            if entry.order_id is not None:
                self.broker.cancel_order(entry.order_id)
            return (
                replace(
                    entry,
                    lifecycle_state=OrderLifecycleState.CANCEL_PENDING,
                    terminal_reason=f"cancelled after {self.settings.cancel_after_seconds}s unfilled",
                ),
                "cancel",
            )
        if elapsed >= self.settings.replace_after_seconds and entry.replace_count < 1 and entry.order_id is not None:
            if entry.limit_price is None:
                return None
            new_limit = more_aggressive_limit_price(
                entry.side,
                entry.limit_price,
                self.settings.replace_price_improvement_bps,
            )
            new_ref = f"{entry.ledger_key}:r{entry.replace_count + 1}"
            snapshot = self.broker.replace_order(
                order_id=entry.order_id,
                ticker=entry.ticker,
                side=entry.side,
                quantity=entry.remaining_qty or entry.quantity,
                new_limit_price=new_limit,
                order_ref=new_ref,
            )
            replaced = entry.with_snapshot(snapshot)
            replaced = replace(
                replaced,
                order_ref=new_ref,
                limit_price=new_limit,
                replace_count=entry.replace_count + 1,
                submitted_at=now.isoformat(),
            )
            return replaced, "replace"
        return None
