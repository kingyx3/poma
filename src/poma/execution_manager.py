from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from poma.broker import (
    BROKER_UNAVAILABLE_STATUS,
    ORDER_NOT_ACCEPTED_STATUS,
    Broker,
    CancelNotConfirmed,
    IbkrBroker,
    OrderStatusCallback,
)
from poma.config import ExecutionPriceSource, Settings, StaleOrderPolicy
from poma.execution_pricing import apply_execution_quotes, compute_spread_bps, price_from_quote
from poma.ibkr_order_history import fetch_completed_order_snapshots as fetch_ibkr_completed_order_snapshots
from poma.models import OpenOrderSnapshot, OrderResult, OrderSide, ProposedTrade, RebalancePlan
from poma.order_lifecycle import (
    BUYING_POWER_BLOCKED_STATUS,
    CONTRACT_UNRESOLVED_STATUS,
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

# Spreads are widest right after the open, so retries back off rather than re-sampling the same
# few seconds of a thin book. Only the final attempt may fall back to wide-spread midpoint pricing.
EXECUTION_QUOTE_RETRY_DELAYS_SECONDS = (5.0, 10.0, 20.0)
EXECUTION_QUOTE_ATTEMPTS = len(EXECUTION_QUOTE_RETRY_DELAYS_SECONDS) + 1
_RETRYABLE_PRE_ACCEPTANCE_STATUSES = frozenset(
    {
        EXECUTION_QUOTE_BLOCKED_STATUS,
        BUYING_POWER_BLOCKED_STATUS,
        BROKER_UNAVAILABLE_STATUS,
        ORDER_NOT_ACCEPTED_STATUS,
    }
)


@dataclass(frozen=True)
class ReconcileUpdate:
    entry: OrderLedgerEntry
    action: str | None  # "replace", "replace_deferred", "cancel", "closed", "unverified", "error", or None
    matched: bool
    detail: str | None = None


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
        broker-submitted ledger entry already recorded for a trade's ``orderRef``, that trade is
        not resubmitted; an ``IdempotentReplay`` result is returned instead (see
        ``_idempotent_replay``). Local pre-acceptance blocks remain retryable under the same
        orderRef because no broker order exists yet. Immediately before each phase is sent to the
        broker, it is repriced off a fresh execution quote (see ``_reprice_for_execution``).
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
            repriced_buys, blocked_results = self._reprice_for_execution(plan, fresh_buys, status_callback)
            for trade, result in blocked_results:
                results_by_ticker[trade.ticker] = result

            block_reason = self._block_buys_for_insufficient_cash(repriced_buys)
            if block_reason is not None:
                for trade in repriced_buys:
                    result = self._blocked_result(trade, BUYING_POWER_BLOCKED_STATUS, block_reason)
                    results_by_ticker[trade.ticker] = result
                    self._record_result(plan, trade, result)
                    if status_callback is not None:
                        status_callback(trade, result)
            else:
                self._submit_phase(plan, repriced_buys, results_by_ticker, status_callback, reprice=False)

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
        """Return a replay result if this orderRef already reached the broker.

        Quote/cash blocks and broker-unavailable/not-accepted outcomes can be durable ledger rows,
        but they have no broker order id and no fill. They are safe to retry with the same
        orderRef. Any other non-PLANNED row is treated as potentially broker-visible and is never
        resubmitted blindly, including terminal and ``UNKNOWN`` rows.
        """
        assert trade.order_ref is not None
        entry = latest_by_ref.get(trade.order_ref)
        if entry is None or entry.lifecycle_state == OrderLifecycleState.PLANNED:
            return None
        if (
            entry.raw_status in _RETRYABLE_PRE_ACCEPTANCE_STATUSES
            and entry.order_id is None
            and entry.filled_qty <= 1e-9
        ):
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
        buy_cash_required = sum(trade.buy_cash_required_usd for trade in buys)
        if buy_cash_required <= 1e-9:
            return None
        try:
            refreshed = self.broker.account_snapshot()
        except Exception as exc:  # noqa: BLE001 - fail closed on an unreadable post-sell cash read
            return f"unable to refresh broker cash before submitting buys; block buys: {exc}"
        if not math.isfinite(refreshed.cash_usd):
            return "broker returned non-finite cash; block buys"
        if refreshed.cash_usd + 1e-6 < buy_cash_required:
            return (
                f"refreshed broker cash (${refreshed.cash_usd:,.2f}) does not cover planned buy "
                f"limit cash requirement (${buy_cash_required:,.2f}) after execution repricing; "
                "unfilled sells are not assumed to provide buying power"
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
        *,
        reprice: bool = True,
    ) -> None:
        if not trades:
            return
        submittable, blocked_results = (
            self._reprice_for_execution(plan, trades, status_callback) if reprice else (trades, [])
        )
        for trade, result in blocked_results:
            results_by_ticker[trade.ticker] = result
        if not submittable:
            return
        # Persist uncertainty BEFORE crossing the network boundary. A process killed after
        # placeOrder but before its first callback must never leave a retryable PLANNED row.
        for trade in submittable:
            self._record_result(plan, trade, self._blocked_result(
                trade, "SubmissionUnconfirmed", "submission started; broker outcome requires reconciliation"
            ))
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
        """Reprice a batch off fresh broker quotes, retrying transient quote-quality failures.

        A single wide/stale/missing snapshot should not consume the whole day's rebalance. Failed
        tickers are sampled again with backoff while already-valid tickers are kept; the final
        attempt may price a moderately wide spread passively off the midpoint. A symbol IBKR
        cannot resolve at all is not retried. If any retry happened, the already-valid trades are
        re-quoted once more right before submission, so no order goes out on a quote that sat
        waiting for the others. Only after the budget is exhausted is a trade recorded
        ``QuoteBlocked``; monitor can then retry that local pre-acceptance block on a later tick
        using the same run/orderRef.
        """
        if self.settings.execution_price_source != ExecutionPriceSource.IBKR or not trades:
            return trades, []

        pending = list(trades)
        repriced_by_ticker: dict[str, ProposedTrade] = {}
        warning_by_ticker: dict[str, str] = {}
        unresolved_tickers: set[str] = set()
        rules = self.settings.execution_rules()
        quoted_on_attempt: dict[str, int] = {}
        attempt = 0
        for attempt in range(1, EXECUTION_QUOTE_ATTEMPTS + 1):
            final_attempt = attempt >= EXECUTION_QUOTE_ATTEMPTS
            quotes = self.broker.execution_quotes([trade.ticker for trade in pending])
            unresolved_tickers |= {ticker for ticker, quote in quotes.items() if quote.contract_unresolved}
            repriced, warnings = apply_execution_quotes(
                pending, quotes, self.settings, rules, allow_wide_spread=final_attempt
            )
            for updated in repriced:
                repriced_by_ticker[updated.ticker] = updated
                quoted_on_attempt[updated.ticker] = attempt
            self._note_quote_warnings(pending, repriced_by_ticker, warnings, warning_by_ticker)
            pending = [
                trade for trade in pending
                if trade.ticker not in repriced_by_ticker and trade.ticker not in unresolved_tickers
            ]
            if not pending or final_attempt:
                break
            time.sleep(EXECUTION_QUOTE_RETRY_DELAYS_SECONDS[attempt - 1])

        # Valid quotes from earlier attempts are now as old as the retry backoff; refresh them.
        originals = [trade for trade in trades if quoted_on_attempt.get(trade.ticker, attempt) < attempt]
        if originals:
            quotes = self.broker.execution_quotes([trade.ticker for trade in originals])
            refreshed, warnings = apply_execution_quotes(
                originals, quotes, self.settings, rules, allow_wide_spread=True
            )
            refreshed_by_ticker = {trade.ticker: trade for trade in refreshed}
            for trade in originals:
                if trade.ticker in refreshed_by_ticker:
                    repriced_by_ticker[trade.ticker] = refreshed_by_ticker[trade.ticker]
                else:
                    del repriced_by_ticker[trade.ticker]
            self._note_quote_warnings(originals, refreshed_by_ticker, warnings, warning_by_ticker, suffix=" on pre-submit refresh")

        submittable: list[ProposedTrade] = []
        blocked: list[tuple[ProposedTrade, OrderResult]] = []
        for trade in trades:
            updated = repriced_by_ticker.get(trade.ticker)
            if updated is not None:
                self._record_planned(plan, updated)
                submittable.append(updated)
                continue
            reason = warning_by_ticker.get(
                trade.ticker,
                "execution quote check failed; block execution",
            )
            if trade.ticker in unresolved_tickers:
                status = CONTRACT_UNRESOLVED_STATUS
            else:
                status = EXECUTION_QUOTE_BLOCKED_STATUS
                reason = f"{reason} after {EXECUTION_QUOTE_ATTEMPTS} quote attempts"
            result = self._blocked_result(trade, status, reason)
            self._record_result(plan, trade, result)
            if status_callback is not None:
                status_callback(trade, result)
            blocked.append((trade, result))
        return submittable, blocked

    @staticmethod
    def _note_quote_warnings(
        trades: list[ProposedTrade],
        accepted: dict[str, ProposedTrade],
        warnings: list[str],
        warning_by_ticker: dict[str, str],
        *,
        suffix: str = "",
    ) -> None:
        for trade in trades:
            if trade.ticker in accepted:
                continue
            # Whole-symbol match: a one-letter ticker such as ``E`` must not claim another
            # ticker's warning just because the letter appears somewhere in it.
            pattern = re.compile(rf"(?<![A-Za-z0-9.]){re.escape(trade.ticker)}(?![A-Za-z0-9])")
            reason = next(
                (warning for warning in warnings if pattern.search(warning)),
                "execution quote check failed; block execution",
            )
            warning_by_ticker[trade.ticker] = f"{reason}{suffix}"

    def _tag(self, run_id: str, trades: list[ProposedTrade], *, offset: int) -> list[ProposedTrade]:
        """Attach stable orderRefs, reusing the first ref for a ticker/side on same-run retries.

        Residual plans can shrink after fills, which changes list offsets. If orderRef identity
        depended only on the rebuilt sequence, a still-existing trade could receive a new ref and
        be submitted twice. The ledger therefore wins over the current sequence for any ticker/
        side already seen in this run; new trades still use the original deterministic format.
        """
        prior_by_trade = self.store.get_latest_run_trades(run_id)
        tagged: list[ProposedTrade] = []
        for index, trade in enumerate(trades):
            prior = prior_by_trade.get((trade.ticker, trade.side.value))
            order_ref = prior.ledger_key if prior is not None else build_order_ref(
                run_id,
                offset + index,
                trade.ticker,
                trade.side,
            )
            tagged.append(replace(trade, order_ref=order_ref))
        return tagged

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

        Before deciding, refresh the local open-order ledger against IBKR. Operators may cancel
        orders manually in TWS/Gateway, and those terminal changes do not update POMA's local
        ``open_orders.jsonl`` until a reconciliation pass sees that the broker no longer reports
        the order as open. Without this refresh, a cancelled broker order can keep blocking future
        rebalances solely because the local ledger is stale.
        """
        open_entries = self._open_ledger_entries()
        if open_entries:
            try:
                self.reconcile()
            except Exception as exc:  # noqa: BLE001 - fail closed if the broker cannot confirm state
                tickers = ", ".join(sorted({entry.ticker for entry in open_entries}))
                return StaleOrderCheck(
                    warnings=(
                        "unable to refresh local open orders against IBKR before planning "
                        f"({len(open_entries)} local order(s): {tickers}); block execution: {exc}",
                    )
                )
            open_entries = self._open_ledger_entries()

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
                    f"requested cancellation of {len(group_cancelled)} open order(s) from {label} before planning "
                    f"({tickers})"
                )
                if group_cancelled:
                    warnings.append("cancellation awaiting broker terminal confirmation; block execution")
                unresolved_count = len(group) - len(group_cancelled)
                if unresolved_count:
                    warnings.append(
                        f"{unresolved_count} open order(s) from {label} could not be confirmed cancelled "
                        f"({tickers}); block execution"
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
        """Reconcile open orders, then use completed broker history before declaring UNKNOWN.

        Each ledger entry is matched to broker orders by its current orderRef, by a reserved
        replacement orderRef, and by permId/orderId within its own orderRef family, so an entry
        whose replace was interrupted still finds whichever order really exists. A failure while
        acting on one entry (e.g. a broker error during a replace) is reported on that entry and
        does not abort reconciliation of the others.
        """
        open_entries = self._open_ledger_entries()
        if not open_entries:
            return ReconcileSummary(checked=0, updates=())

        open_snapshots = [snapshot for snapshot in self.broker.fetch_open_order_snapshots() if snapshot.order_ref]
        unmatched = [entry for entry in open_entries if _match_snapshot(entry, open_snapshots)[0] is None]
        completed_snapshots: list[OpenOrderSnapshot] = []
        if unmatched:
            wanted_refs = {ref for entry in unmatched for ref in _entry_refs(entry)}
            fetch_completed = getattr(self.broker, "fetch_completed_order_snapshots", None)
            try:
                if callable(fetch_completed):
                    completed = fetch_completed()
                elif isinstance(self.broker, IbkrBroker):
                    completed = fetch_ibkr_completed_order_snapshots(self.settings)
                else:
                    completed = []
                completed_snapshots = [
                    snapshot for snapshot in completed
                    if snapshot.order_ref and any(snapshot.order_ref.startswith(ref) for ref in wanted_refs)
                ]
            except Exception:  # noqa: BLE001 - history recovery can fail while UNKNOWN remains fail-closed
                completed_snapshots = []

        now = datetime.now(UTC)
        updates: list[ReconcileUpdate] = []
        for entry in open_entries:
            try:
                updates.append(self._reconcile_entry(entry, open_snapshots, completed_snapshots, now))
            except Exception as exc:  # noqa: BLE001 - one order's failure must not hide the others
                current = self.store.get(entry.ledger_key) or entry
                updates.append(ReconcileUpdate(entry=current, action="error", matched=False, detail=str(exc)))
        return ReconcileSummary(checked=len(open_entries), updates=tuple(updates))

    def _reconcile_entry(
        self,
        entry: OrderLedgerEntry,
        open_snapshots: list[OpenOrderSnapshot],
        completed_snapshots: list[OpenOrderSnapshot],
        now: datetime,
    ) -> ReconcileUpdate:
        snapshot, is_replacement = _match_snapshot(entry, open_snapshots)
        if snapshot is not None:
            if is_replacement:
                # The replacement reached the broker even though its confirmation was lost.
                adopted = self._adopt_replacement(entry, snapshot)
                self.store.upsert(adopted)
                return ReconcileUpdate(entry=adopted, action="replace", matched=True)
            updated = entry.with_snapshot(snapshot)
            if entry.replacement_order_ref and updated.lifecycle_state == OrderLifecycleState.CANCELLED:
                resumed = self._resume_replacement(updated, entry.replacement_order_ref, now)
                if resumed is not None:
                    self.store.upsert(resumed)
                    return ReconcileUpdate(entry=resumed, action="replace", matched=True)
                updated = replace(updated, replacement_order_ref=None)
            if entry.replacement_order_ref and updated.lifecycle_state in WORKING_LIFECYCLE_STATES:
                # The original is working again (cancel rejected); drop the unplaced replacement.
                updated = replace(updated, replacement_order_ref=None)
            action_name: str | None = None
            action_taken = self._apply_timeout_policy(updated, now)
            if action_taken is not None:
                updated, action_name = action_taken
            self.store.upsert(updated)
            return ReconcileUpdate(entry=updated, action=action_name, matched=True)

        completed, is_replacement = _match_snapshot(entry, completed_snapshots)
        if completed is not None and is_replacement:
            adopted = self._adopt_replacement(entry, completed)
            self.store.upsert(adopted)
            return ReconcileUpdate(entry=adopted, action="closed" if adopted.is_terminal else "replace", matched=True)
        if completed is not None:
            completed_entry = entry.with_snapshot(completed)
            if completed_entry.is_terminal:
                if entry.replacement_order_ref and completed_entry.lifecycle_state == OrderLifecycleState.CANCELLED:
                    resumed = self._resume_replacement(completed_entry, entry.replacement_order_ref, now)
                    if resumed is not None:
                        self.store.upsert(resumed)
                        return ReconcileUpdate(entry=resumed, action="replace", matched=True)
                completed_entry = replace(completed_entry, replacement_order_ref=None)
                self.store.upsert(completed_entry)
                return ReconcileUpdate(entry=completed_entry, action="closed", matched=True)

        if entry.lifecycle_state == OrderLifecycleState.UNKNOWN and entry.raw_status == "NotOpenUnverified":
            return ReconcileUpdate(entry=entry, action=None, matched=False)
        updated = self._close_unreported_open_entry(entry, now)
        self.store.upsert(updated)
        action = "closed" if updated.is_terminal else "unverified"
        return ReconcileUpdate(entry=updated, action=action, matched=False)

    @staticmethod
    def _adopt_replacement(entry: OrderLedgerEntry, snapshot: OpenOrderSnapshot) -> OrderLedgerEntry:
        assert entry.replacement_order_ref is not None
        return replace(entry, order_ref=entry.replacement_order_ref, replacement_order_ref=None).with_snapshot(snapshot)

    def _resume_replacement(
        self,
        cancelled: OrderLedgerEntry,
        replacement_ref: str,
        now: datetime,
    ) -> OrderLedgerEntry | None:
        """Finish a replace whose cancel was confirmed only after ``replace_order`` gave up.

        Safe against duplicates: the original is broker-confirmed cancelled, the reserved
        replacement orderRef was found neither open nor in completed history, and the broker
        re-checks that orderRef before placing. Skipped (the entry simply closes as cancelled)
        once the order is past ``CANCEL_AFTER_SECONDS`` or nothing remains to buy/sell.
        """
        submit = getattr(self.broker, "submit_replacement", None)
        elapsed = seconds_since(cancelled.submitted_at, now)
        remaining = cancelled.remaining_qty or max(cancelled.quantity - cancelled.filled_qty, 0.0)
        if not callable(submit) or remaining <= 1e-9:
            return None
        if elapsed is None or elapsed >= self.settings.cancel_after_seconds:
            return None
        new_limit, quote_metadata = self._fresh_replace_limit_price(cancelled)
        if new_limit is None:
            return None
        snapshot = submit(
            ticker=cancelled.ticker,
            side=cancelled.side,
            quantity=remaining,
            new_limit_price=new_limit,
            order_ref=replacement_ref,
        )
        resumed = replace(cancelled, order_ref=replacement_ref, replacement_order_ref=None, terminal_reason=None)
        return replace(
            resumed.with_snapshot(snapshot),
            limit_price=new_limit,
            submitted_at=now.isoformat(),
            **quote_metadata,
        )

    def _open_ledger_entries(self) -> list[OrderLedgerEntry]:
        return [entry for entry in self.store.load_open_orders() if not entry.is_terminal]

    @staticmethod
    def _close_unreported_open_entry(entry: OrderLedgerEntry, now: datetime) -> OrderLedgerEntry:
        """Disappearance is not proof of cancellation: a fill can race a cancel request."""
        lifecycle_state = OrderLifecycleState.UNKNOWN
        raw_status = "NotOpenUnverified"
        remaining_qty = entry.remaining_qty or max(entry.quantity - entry.filled_qty, 0.0)
        terminal_reason = (
            "broker no longer reports this POMA order as open, but its final state is unverified; "
            "keeping it unresolved to prevent duplicate resubmission"
        )
        return replace(
            entry,
            lifecycle_state=lifecycle_state,
            raw_status=raw_status,
            remaining_qty=remaining_qty,
            last_status_at=now.isoformat(),
            terminal_reason=terminal_reason,
        )

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
            if entry.order_id is None or not self.broker.cancel_order(entry.order_id):
                return None
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
            # Reserve the replacement identity before cancel-and-submit crosses the network, but
            # keep ``order_ref`` on the original until the replacement is confirmed: if the
            # process dies or the cancel lags, reconciliation can still find either order.
            intent = replace(
                entry, lifecycle_state=OrderLifecycleState.REPLACE_PENDING,
                raw_status="ReplacementUnconfirmed", replace_count=entry.replace_count + 1,
                replacement_order_ref=new_ref,
            )
            self.store.upsert(intent)
            try:
                snapshot = self.broker.replace_order(
                    order_id=entry.order_id,
                    ticker=entry.ticker,
                    side=entry.side,
                    quantity=entry.remaining_qty or entry.quantity,
                    new_limit_price=new_limit,
                    order_ref=new_ref,
                )
            except CancelNotConfirmed as exc:
                # The cancel is still in flight and nothing new was placed. The next reconcile
                # sees the original as cancelled (then places the replacement) or filled.
                deferred = replace(
                    intent, lifecycle_state=OrderLifecycleState.CANCEL_PENDING,
                    raw_status="PendingCancel", terminal_reason=str(exc),
                )
                return deferred, "replace_deferred"
            if snapshot.order_ref != new_ref:
                # The original filled while the cancel was in flight; no replacement was placed.
                return replace(entry.with_snapshot(snapshot), replace_count=intent.replace_count), "closed"
            replaced = replace(intent, order_ref=new_ref, replacement_order_ref=None).with_snapshot(snapshot)
            replaced = replace(
                replaced,
                limit_price=new_limit,
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
        pricing = price_from_quote(
            quote, entry.side, settings, settings.replace_price_improvement_bps, allow_wide_spread=True
        )
        price, new_limit = pricing.reference_price, pricing.limit_price
        if price is None or new_limit is None:
            return None, {}
        spread_bps = quote.spread_bps if quote.spread_bps is not None else compute_spread_bps(quote.bid, quote.ask)
        metadata: dict[str, object] = {
            "reference_price": price,
            "reference_price_source": settings.execution_price_source.value,
            "reference_price_basis": pricing.basis,
            "reference_price_as_of_utc": quote.selected_price_as_of_utc,
            "quote_age_seconds": quote.age_seconds,
            "quote_spread_bps": spread_bps,
        }
        return new_limit, metadata


def _entry_refs(entry: OrderLedgerEntry) -> tuple[str, ...]:
    refs = [entry.order_ref, entry.ledger_key]
    if entry.replacement_order_ref:
        refs.append(entry.replacement_order_ref)
    return tuple(dict.fromkeys(refs))


def _match_snapshot(
    entry: OrderLedgerEntry,
    snapshots: list[OpenOrderSnapshot],
) -> tuple[OpenOrderSnapshot | None, bool]:
    """Find the broker order behind a ledger entry; the flag says it is the reserved replacement.

    Exact orderRef wins. Otherwise a snapshot from the same orderRef family (the ledger key and
    its ``:rN`` replacements) matches on permId or on the entry's orderId, which recovers entries
    whose ``order_ref`` was switched to a replacement that was never actually placed.
    """
    for snapshot in snapshots:
        if snapshot.order_ref == entry.order_ref:
            return snapshot, False
    if entry.replacement_order_ref:
        for snapshot in snapshots:
            if snapshot.order_ref == entry.replacement_order_ref:
                return snapshot, True
    for snapshot in snapshots:
        if not (snapshot.order_ref or "").startswith(entry.ledger_key):
            continue
        if snapshot.ticker != entry.ticker or snapshot.side != entry.side:
            continue
        if entry.perm_id and snapshot.perm_id == entry.perm_id:
            return snapshot, False
        if entry.order_id is not None and snapshot.order_id == entry.order_id:
            return snapshot, False
    return None, False
