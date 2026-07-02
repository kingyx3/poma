from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from poma.broker import Broker, OrderStatusCallback
from poma.config import ExecutionPriceSource, Settings, StaleOrderPolicy
from poma.execution_pricing import apply_execution_quotes, build_limit_price, compute_spread_bps, select_execution_price
from poma.models import OrderResult, OrderSide, ProposedTrade, RebalancePlan
from poma.order_lifecycle import (
    BUYING_POWER_BLOCKED_STATUS,
    EXECUTION_QUOTE_BLOCKED_STATUS,
    IDEMPOTENT_REPLAY_STATUS,
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

        Sells are staged first so cash is refreshed from the broker before buys are sized
        against it (see ``_block_buys_for_insufficient_cash``): unfilled limit sells are never
        assumed to provide buying power. Every trade in ``plan.trades`` is tagged with a stable
        ``orderRef`` and recorded in the durable order ledger before submission, so a crash
        mid-run still leaves a trace of what was sent. If a retry of the same run finds a
        non-terminal ledger entry already recorded for a trade's ``orderRef``, that trade is not
        resubmitted; an ``IdempotentReplay`` result is returned instead (see
        ``_idempotent_replay``). Immediately before each phase is sent to the broker, it is
        repriced off a fresh execution quote (see ``_reprice_for_execution``); trades that fail a
        freshness/spread/delayed-quote check are blocked instead of submitted.
        """
        sells = [trade for trade in plan.trades if trade.side == OrderSide.SELL]
        buys = [trade for trade in plan.trades if trade.side == OrderSide.BUY]
        tagged_sells = self._tag(plan.run_id, sells, offset=0)
        tagged_buys = self._tag(plan.run_id, buys, offset=len(sells))
        all_tagged = (*tagged_sells, *tagged_buys)
        latest_by_ref = self.store.get_latest_many(trade.order_ref for trade in all_tagged)

        results_by_ticker: dict[str, OrderResult] = {}
        plan_phase_kwargs = {"plan": plan, "latest_by_ref": latest_by_ref}
        fresh_sells = self._plan_phase(tagged_sells, results_by_ticker, status_callback, **plan_phase_kwargs)
        fresh_buys = self._plan_phase(tagged_buys, results_by_ticker, status_callback, **plan_phase_kwargs)

        self._submit_phase(plan, fresh_sells, results_by_ticker, status_callback)

        if fresh_buys:
            block_reason = self._block_buys_for_insufficient_cash(fresh_buys)
            if block_reason is not None:
                for trade in fresh_buys:
                    result = self._blocked_result(trade, BUYING_POWER_BLOCKED_STATUS, block_reason)
                    results_by_ticker[trade.ticker] = result
                    self._record_result(plan, trade, result)
                    if status_callback is not None:
                        status_callback(trade, result)
            else:
                self._submit_phase(plan, fresh_buys, results_by_ticker, status_callback)

        return [results_by_ticker[trade.ticker] for trade in plan.trades]

    def _plan_phase(
        self,
        trades: list[ProposedTrade],
        results_by_ticker: dict[str, OrderResult],
        status_callback: OrderStatusCallback | None,
        *,
        plan: RebalancePlan,
        latest_by_ref: dict[str, OrderLedgerEntry],
    ) -> list[ProposedTrade]:
        """Record each trade as planned, skipping (and replaying) any already-submitted retry."""
        fresh: list[ProposedTrade] = []
        for trade in trades:
            replay = self._idempotent_replay(trade, latest_by_ref)
            if replay is not None:
                results_by_ticker[trade.ticker] = replay
                if status_callback is not None:
                    status_callback(trade, replay)
                continue
            self._record_planned(plan, trade)
            fresh.append(trade)
        return fresh

    def _idempotent_replay(
        self,
        trade: ProposedTrade,
        latest_by_ref: dict[str, OrderLedgerEntry],
    ) -> OrderResult | None:
        """Return a replay result if this orderRef was already submitted by an earlier attempt.

        Looks up both open and terminal history (see ``OrderStore.get_latest_many``): a same-run
        retry must recognize an order that already reached a terminal state (e.g. filled) just as
        much as one still working, or it would resubmit a duplicate for an order that already
        completed. Only a ``PLANNED``-only entry does not count, since that means a prior attempt
        crashed before reaching the broker, so it is safe (and necessary) to submit it now.
        """
        assert trade.order_ref is not None
        entry = latest_by_ref.get(trade.order_ref)
        if entry is None or entry.lifecycle_state == OrderLifecycleState.PLANNED:
            return None
        return OrderResult(
            ticker=trade.ticker,
            side=trade.side,
            quantity=trade.quantity,
            notional=trade.notional,
            order_id=entry.order_id,
            status=IDEMPOTENT_REPLAY_STATUS,
            filled=entry.filled_qty,
            average_fill_price=entry.avg_fill_price,
            message=(
                f"orderRef {trade.order_ref} already {entry.lifecycle_state.value} from an "
                "earlier attempt of this run; not resubmitted"
            ),
            order_ref=trade.order_ref,
            perm_id=entry.perm_id,
        )

    def _block_buys_for_insufficient_cash(self, buys: list[ProposedTrade]) -> str | None:
        """Refresh broker cash after the sell phase and block buys it cannot cover.

        Unfilled (or partially filled) limit sells are not assumed to provide buying power;
        only cash the broker actually reports after the sell phase counts.
        """
        buy_notional = sum(trade.notional for trade in buys)
        if buy_notional <= 1e-9:
            return None
        try:
            refreshed = self.broker.account_snapshot()
        except Exception as exc:  # noqa: BLE001 - fail closed on an unreadable post-sell cash read
            return f"unable to refresh broker cash before submitting buys; block buys: {exc}"
        if refreshed.cash_usd + 1e-6 < buy_notional:
            return (
                f"refreshed broker cash (${refreshed.cash_usd:,.2f}) does not cover planned buy "
                f"notional (${buy_notional:,.2f}) after the sell phase; unfilled sells are not "
                "assumed to provide buying power"
            )
        return None

    @staticmethod
    def _blocked_result(trade: ProposedTrade, status: str, message: str) -> OrderResult:
        return OrderResult(
            ticker=trade.ticker,
            side=trade.side,
            quantity=trade.quantity,
            notional=trade.notional,
            order_id=None,
            status=status,
            filled=0.0,
            average_fill_price=None,
            message=message,
            order_ref=trade.order_ref,
        )

    def _submit_phase(
        self,
        plan: RebalancePlan,
        trades: list[ProposedTrade],
        results_by_ticker: dict[str, OrderResult],
        status_callback: OrderStatusCallback | None,
    ) -> None:
        if not trades:
            return
        submittable, blocked_results = self._reprice_for_execution(plan, trades, status_callback)
        for trade, result in blocked_results:
            results_by_ticker[trade.ticker] = result
        if not submittable:
            return
        phase_results = self.broker.submit_trades(
            submittable,
            status_callback=self._wrap_callback(plan, status_callback),
        )
        for trade, result in zip(submittable, phase_results, strict=True):
            results_by_ticker[trade.ticker] = result
            self._record_result(plan, trade, result)

    def _reprice_for_execution(
        self,
        plan: RebalancePlan,
        trades: list[ProposedTrade],
        status_callback: OrderStatusCallback | None,
    ) -> tuple[list[ProposedTrade], list[tuple[ProposedTrade, OrderResult]]]:
        """Reprice one submission batch off a fresh broker quote fetched right before sending it.

        Fetching quotes here, immediately before ``broker.submit_trades``, keeps the gap between
        "read the quote" and "place the order" as small as possible. Trades that fail a
        freshness/spread/delayed-quote check are recorded as blocked rather than submitted.
        """
        if self.settings.execution_price_source != ExecutionPriceSource.IBKR or not trades:
            return trades, []

        quotes = self.broker.execution_quotes([trade.ticker for trade in trades])
        repriced, warnings = apply_execution_quotes(trades, quotes, self.settings, self.settings.execution_rules())
        repriced_by_ticker = {trade.ticker: trade for trade in repriced}

        submittable: list[ProposedTrade] = []
        blocked: list[tuple[ProposedTrade, OrderResult]] = []
        for trade in trades:
            updated = repriced_by_ticker.get(trade.ticker)
            if updated is not None:
                self._record_planned(plan, updated)
                submittable.append(updated)
                continue
            reason = next(
                (warning for warning in warnings if trade.ticker in warning),
                "execution quote check failed; block execution",
            )
            result = self._blocked_result(trade, EXECUTION_QUOTE_BLOCKED_STATUS, reason)
            self._record_result(plan, trade, result)
            if status_callback is not None:
                status_callback(trade, result)
            blocked.append((trade, result))
        return submittable, blocked

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
            reference_price=trade.reference_price,
            reference_price_source=trade.reference_price_source,
            reference_price_basis=trade.reference_price_basis,
            reference_price_as_of_utc=trade.reference_price_as_of_utc,
            quote_age_seconds=trade.quote_age_seconds,
            quote_spread_bps=trade.quote_spread_bps,
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
                reference_price=trade.reference_price,
                reference_price_source=trade.reference_price_source,
                reference_price_basis=trade.reference_price_basis,
                reference_price_as_of_utc=trade.reference_price_as_of_utc,
                quote_age_seconds=trade.quote_age_seconds,
                quote_spread_bps=trade.quote_spread_bps,
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

    def check_stale_orders(self, session_date: str, run_id: str) -> StaleOrderCheck:
        """Block (or cancel) unresolved open orders that are not from this exact run.

        Orders from a prior session, or from a *different* run within the same session, are
        foreign to this attempt and are blocked (or cancelled, per ``stale_order_policy``)
        before planning continues. Open orders from *this run* are reported informationally
        only: a legitimate retry of the same run relies on ``submit_plan``'s idempotent replay
        rather than being blocked by its own still-working orders.
        """
        open_entries = [entry for entry in self.store.load_open_orders() if not entry.is_terminal]
        other_session = [entry for entry in open_entries if entry.session_date != session_date]
        same_session_foreign_run = [
            entry for entry in open_entries if entry.session_date == session_date and entry.run_id != run_id
        ]
        same_run = [entry for entry in open_entries if entry.session_date == session_date and entry.run_id == run_id]

        warnings: list[str] = []
        cancelled: list[str] = []
        for group, label in (
            (other_session, "a prior session"),
            (same_session_foreign_run, "a different run in this session"),
        ):
            if not group:
                continue
            tickers = ", ".join(sorted({entry.ticker for entry in group}))
            if self.settings.stale_order_policy == StaleOrderPolicy.CANCEL:
                group_cancelled: list[str] = []
                for entry in group:
                    if entry.order_id is not None and self.broker.cancel_order(entry.order_id):
                        self.store.upsert(
                            replace(
                                entry,
                                lifecycle_state=OrderLifecycleState.CANCEL_PENDING,
                                terminal_reason=f"cancelled: unresolved order from {label}",
                            )
                        )
                        group_cancelled.append(entry.ledger_key)
                cancelled.extend(group_cancelled)
                warnings.append(
                    f"cancelled {len(group_cancelled)} open order(s) from {label} before planning "
                    f"({tickers})"
                )
            else:
                warnings.append(
                    f"{len(group)} open order(s) from {label} are still unresolved "
                    f"({tickers}); run `poma reconcile-orders` or cancel manually before this session "
                    f"can trade; block execution"
                )
        if same_run:
            tickers = ", ".join(sorted({entry.ticker for entry in same_run}))
            warnings.append(
                f"{len(same_run)} open order(s) from this run are still unresolved ({tickers}); "
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
            new_limit, quote_metadata = self._fresh_replace_limit_price(entry)
            if new_limit is None:
                return None
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
                **quote_metadata,
            )
            return replaced, "replace"
        return None

    def _fresh_replace_limit_price(self, entry: OrderLedgerEntry) -> tuple[float | None, dict[str, object]]:
        """Reprice a replacement off a fresh broker quote instead of the order's stale old limit.

        Blindly improving from the previous limit price can chase a quote that has since moved
        the other way (see ``docs/configuration.md``). When no valid fresh quote is available,
        this skips the replace for this reconcile pass rather than repricing off stale data.
        """
        settings = self.settings
        if settings.execution_price_source != ExecutionPriceSource.IBKR:
            if entry.limit_price is None:
                return None, {}
            return (
                more_aggressive_limit_price(entry.side, entry.limit_price, settings.replace_price_improvement_bps),
                {},
            )

        quotes = self.broker.execution_quotes([entry.ticker])
        quote = quotes.get(entry.ticker)
        if quote is None:
            return None, {}
        price, _warnings = select_execution_price(quote, entry.side, settings)
        if price is None:
            return None, {}
        new_limit = build_limit_price(entry.side, price, settings.replace_price_improvement_bps)
        spread_bps = quote.spread_bps if quote.spread_bps is not None else compute_spread_bps(quote.bid, quote.ask)
        metadata: dict[str, object] = {
            "reference_price": price,
            "reference_price_source": settings.execution_price_source.value,
            "reference_price_basis": settings.execution_price_basis.value,
            "reference_price_as_of_utc": quote.selected_price_as_of_utc,
            "quote_age_seconds": quote.age_seconds,
            "quote_spread_bps": spread_bps,
        }
        return new_limit, metadata
