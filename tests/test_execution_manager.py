from __future__ import annotations

from dataclasses import replace as dc_replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import make_settings

from poma.execution_manager import ExecutionManager
from poma.models import (
    AccountSnapshot,
    ExecutionQuote,
    OpenOrderSnapshot,
    OrderResult,
    OrderSide,
    ProposedTrade,
    RebalancePlan,
)
from poma.order_lifecycle import OrderLifecycleState
from poma.order_store import OrderStore


class RecordingBroker:
    """Fake broker that records submission order and returns broker-accepted fills."""

    def __init__(self) -> None:
        self.submitted_batches: list[list[ProposedTrade]] = []
        self.cancelled_order_ids: list[int] = []
        self.open_order_snapshots: list[OpenOrderSnapshot] = []
        self.next_order_id = 1
        self.quotes_override: dict[str, ExecutionQuote] | None = None
        self.execution_quote_requests: list[list[str]] = []
        self.cash_usd = 10_000.0
        self.account_snapshot_calls = 0

    def account_snapshot(self) -> AccountSnapshot:
        self.account_snapshot_calls += 1
        return AccountSnapshot(cash_usd=self.cash_usd, positions=(), positions_market_value_usd=0.0)

    def submit_trades(self, trades, status_callback=None) -> list[OrderResult]:
        self.submitted_batches.append(list(trades))
        results = []
        for trade in trades:
            order_id = self.next_order_id
            self.next_order_id += 1
            result = OrderResult(
                ticker=trade.ticker,
                side=trade.side,
                quantity=trade.quantity,
                notional=trade.notional,
                order_id=order_id,
                status="Submitted",
                filled=0.0,
                average_fill_price=None,
                order_ref=trade.order_ref,
            )
            results.append(result)
            if status_callback is not None:
                status_callback(trade, result)
        return results

    def fetch_open_order_snapshots(self) -> list[OpenOrderSnapshot]:
        return self.open_order_snapshots

    def execution_quotes(self, tickers: list[str]) -> dict[str, ExecutionQuote]:
        self.execution_quote_requests.append(list(tickers))
        if self.quotes_override is not None:
            return {ticker: self.quotes_override[ticker] for ticker in tickers if ticker in self.quotes_override}
        retrieved_at = datetime.now(UTC).isoformat()
        return {
            ticker: ExecutionQuote(
                ticker=ticker,
                source="ibkr",
                retrieved_at_utc=retrieved_at,
                selected_price_as_of_utc=retrieved_at,
                age_seconds=0.0,
                bid=99.95,
                ask=100.05,
                last=100.0,
                spread_bps=10.0,
            )
            for ticker in tickers
        }

    def cancel_order(self, order_id: int) -> bool:
        self.cancelled_order_ids.append(order_id)
        return True

    def replace_order(self, *, order_id, ticker, side, quantity, new_limit_price, order_ref) -> OpenOrderSnapshot:
        return OpenOrderSnapshot(
            order_ref=order_ref,
            order_id=order_id + 1000,
            perm_id=None,
            ticker=ticker,
            side=side,
            raw_status="Submitted",
            filled=0.0,
            remaining=quantity,
            avg_fill_price=None,
        )


def _trade(ticker: str, side: OrderSide, limit_price: float = 100.0) -> ProposedTrade:
    return ProposedTrade(
        ticker=ticker,
        side=side,
        quantity=5.0,
        notional=500.0,
        reference_price=100.0,
        limit_price=limit_price,
        reason="rebalance_to_target_weight",
    )


def _plan(trades: list[ProposedTrade], session_date: str = "2026-07-01", run_id: str = "run-1") -> RebalancePlan:
    return RebalancePlan(
        run_id=run_id,
        session_date=session_date,
        targets=[],
        trades=trades,
        execution_results=[],
        warnings=[],
    )


def test_submit_plan_submits_sells_before_buys_in_separate_batches(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    trades = [_trade("AAPL", OrderSide.BUY), _trade("MSFT", OrderSide.SELL)]

    results = manager.submit_plan(_plan(trades))

    assert len(broker.submitted_batches) == 2
    assert broker.submitted_batches[0][0].side == OrderSide.SELL
    assert broker.submitted_batches[0][0].ticker == "MSFT"
    assert broker.submitted_batches[1][0].side == OrderSide.BUY
    assert broker.submitted_batches[1][0].ticker == "AAPL"
    # results preserve the original plan.trades order regardless of submission phase order
    assert [result.ticker for result in results] == ["AAPL", "MSFT"]


def test_submit_plan_tags_every_trade_with_a_unique_order_ref(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    trades = [_trade("AAPL", OrderSide.BUY), _trade("MSFT", OrderSide.SELL)]

    manager.submit_plan(_plan(trades))

    refs = {trade.order_ref for batch in broker.submitted_batches for trade in batch}
    assert len(refs) == 2
    assert all(ref.startswith("poma:run-1:") for ref in refs)


def test_submit_plan_records_ledger_entries_for_every_trade(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    trades = [_trade("AAPL", OrderSide.BUY)]

    manager.submit_plan(_plan(trades))

    open_orders = store.load_open_orders()
    assert len(open_orders) == 1
    assert open_orders[0].lifecycle_state == OrderLifecycleState.BROKER_ACCEPTED
    assert open_orders[0].ticker == "AAPL"


def test_check_stale_orders_blocks_on_prior_session_open_orders(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY)], session_date="2026-06-30"))

    check = manager.check_stale_orders("2026-07-01", "run-2")

    assert any("block execution" in warning for warning in check.warnings)
    assert broker.cancelled_order_ids == []


def test_check_stale_orders_cancel_policy_cancels_prior_session_orders(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings(STALE_ORDER_POLICY="cancel"))
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY)], session_date="2026-06-30"))

    check = manager.check_stale_orders("2026-07-01", "run-2")

    assert broker.cancelled_order_ids == [1]
    assert not any("block execution" in warning for warning in check.warnings)
    open_orders = store.load_open_orders()
    assert len(open_orders) == 1
    assert open_orders[0].lifecycle_state == OrderLifecycleState.CANCEL_PENDING


def test_check_stale_orders_does_not_block_on_same_run_open_orders(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY)], session_date="2026-07-01", run_id="run-1"))

    check = manager.check_stale_orders("2026-07-01", "run-1")

    assert not any("block execution" in warning for warning in check.warnings)
    assert any("this run" in warning for warning in check.warnings)


def test_check_stale_orders_blocks_on_same_session_different_run_open_orders(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY)], session_date="2026-07-01", run_id="run-1"))

    check = manager.check_stale_orders("2026-07-01", "run-2")

    assert any("block execution" in warning for warning in check.warnings)
    assert any("different run in this session" in warning for warning in check.warnings)
    assert broker.cancelled_order_ids == []


def test_check_stale_orders_cancel_policy_cancels_same_session_different_run_orders(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings(STALE_ORDER_POLICY="cancel"))
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY)], session_date="2026-07-01", run_id="run-1"))

    check = manager.check_stale_orders("2026-07-01", "run-2")

    assert broker.cancelled_order_ids == [1]
    assert not any("block execution" in warning for warning in check.warnings)


def test_reconcile_replaces_once_after_replace_after_seconds(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    settings = make_settings(REPLACE_AFTER_SECONDS=1, CANCEL_AFTER_SECONDS=600)
    manager = ExecutionManager(broker, store, settings)
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY, limit_price=100.0)]))

    entry = store.load_open_orders()[0]
    stale_time = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    store.upsert(dc_replace(entry, submitted_at=stale_time))

    broker.open_order_snapshots = [
        OpenOrderSnapshot(
            order_ref=entry.order_ref,
            order_id=entry.order_id,
            perm_id=None,
            ticker="AAPL",
            side=OrderSide.BUY,
            raw_status="Submitted",
            filled=0.0,
            remaining=5.0,
            avg_fill_price=None,
        )
    ]

    summary = manager.reconcile()

    assert summary.checked == 1
    assert summary.updates[0].action == "replace"
    updated = store.load_open_orders()[0]
    assert updated.replace_count == 1
    assert updated.limit_price > 100.0


def test_reconcile_cancels_after_cancel_after_seconds(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    settings = make_settings(REPLACE_AFTER_SECONDS=1, CANCEL_AFTER_SECONDS=5)
    manager = ExecutionManager(broker, store, settings)
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY, limit_price=100.0)]))

    entry = store.load_open_orders()[0]
    from datetime import UTC, datetime, timedelta

    stale_time = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    from dataclasses import replace as dc_replace

    store.upsert(dc_replace(entry, submitted_at=stale_time))

    broker.open_order_snapshots = [
        OpenOrderSnapshot(
            order_ref=entry.order_ref,
            order_id=entry.order_id,
            perm_id=None,
            ticker="AAPL",
            side=OrderSide.BUY,
            raw_status="Submitted",
            filled=0.0,
            remaining=5.0,
            avg_fill_price=None,
        )
    ]

    summary = manager.reconcile()

    assert summary.updates[0].action == "cancel"
    assert broker.cancelled_order_ids == [entry.order_id]


def test_reconcile_leaves_unmatched_orders_unmodified(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY)]))
    broker.open_order_snapshots = []

    summary = manager.reconcile()

    assert summary.checked == 1
    assert summary.updates[0].matched is False
    assert summary.updates[0].action is None


def test_reconcile_with_no_open_orders_is_a_noop(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())

    summary = manager.reconcile()

    assert summary.checked == 0
    assert summary.updates == ()


def _quote(ticker: str, **overrides: object) -> ExecutionQuote:
    values: dict[str, object] = {
        "ticker": ticker,
        "source": "ibkr",
        "retrieved_at_utc": "2026-07-01T14:30:00+00:00",
        "selected_price_as_of_utc": "2026-07-01T14:30:00+00:00",
        "age_seconds": 0.0,
        "bid": 99.95,
        "ask": 100.05,
        "last": 100.0,
    }
    values.update(overrides)
    return ExecutionQuote(**values)


def test_submit_plan_reprices_trades_off_fresh_broker_quotes(tmp_path: Path) -> None:
    broker = RecordingBroker()
    broker.quotes_override = {"AAPL": _quote("AAPL", bid=199.90, ask=200.10)}
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    trade = _trade("AAPL", OrderSide.BUY)

    manager.submit_plan(_plan([trade]))

    submitted = broker.submitted_batches[0][0]
    assert submitted.reference_price == 200.10
    assert submitted.reference_price_source == "ibkr"
    assert submitted.reference_price_basis == "side_of_market"
    entry = store.load_open_orders()[0]
    assert entry.reference_price == 200.10
    assert entry.reference_price_source == "ibkr"


def test_submit_plan_blocks_trade_with_no_execution_quote(tmp_path: Path) -> None:
    broker = RecordingBroker()
    broker.quotes_override = {}
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    trade = _trade("AAPL", OrderSide.BUY)

    results = manager.submit_plan(_plan([trade]))

    assert results[0].status == "QuoteBlocked"
    assert "missing ibkr execution quote for AAPL" in results[0].message
    assert broker.submitted_batches == []
    assert store.load_open_orders() == []


def test_submit_plan_blocks_only_the_ticker_with_a_bad_quote(tmp_path: Path) -> None:
    broker = RecordingBroker()
    broker.quotes_override = {
        "AAPL": _quote("AAPL"),
        "MSFT": _quote("MSFT", age_seconds=None),
    }
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    trades = [_trade("AAPL", OrderSide.BUY), _trade("MSFT", OrderSide.BUY)]

    results = manager.submit_plan(_plan(trades))

    results_by_ticker = {result.ticker: result for result in results}
    assert results_by_ticker["AAPL"].status == "Submitted"
    assert results_by_ticker["MSFT"].status == "QuoteBlocked"
    assert [trade.ticker for trade in broker.submitted_batches[0]] == ["AAPL"]


def test_submit_plan_skips_repricing_when_execution_price_source_is_snapshot(tmp_path: Path) -> None:
    broker = RecordingBroker()
    broker.quotes_override = {}
    store = OrderStore(tmp_path)
    settings = make_settings(EXECUTION_PRICE_SOURCE="snapshot")
    manager = ExecutionManager(broker, store, settings)
    trade = _trade("AAPL", OrderSide.BUY)

    manager.submit_plan(_plan([trade]))

    assert broker.execution_quote_requests == []
    assert broker.submitted_batches[0][0].reference_price == 100.0


def test_reconcile_replace_reprices_from_fresh_quote_not_stale_old_limit(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    settings = make_settings(REPLACE_AFTER_SECONDS=1, CANCEL_AFTER_SECONDS=600)
    manager = ExecutionManager(broker, store, settings)
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY, limit_price=100.10)]))

    entry = store.load_open_orders()[0]
    stale_time = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    store.upsert(dc_replace(entry, submitted_at=stale_time))

    broker.open_order_snapshots = [
        OpenOrderSnapshot(
            order_ref=entry.order_ref,
            order_id=entry.order_id,
            perm_id=None,
            ticker="AAPL",
            side=OrderSide.BUY,
            raw_status="Submitted",
            filled=0.0,
            remaining=5.0,
            avg_fill_price=None,
        )
    ]
    # Ask has since dropped well below the original stale limit price; a fresh-quote replace
    # should follow the market down instead of blindly improving off the stale $100.10 limit.
    broker.quotes_override = {"AAPL": _quote("AAPL", bid=79.80, ask=79.90)}

    summary = manager.reconcile()

    assert summary.updates[0].action == "replace"
    updated = store.load_open_orders()[0]
    assert updated.limit_price < 90.0
    assert updated.reference_price == 79.90
    assert updated.reference_price_source == "ibkr"


def test_reconcile_replace_is_skipped_when_no_fresh_quote_is_available(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    settings = make_settings(REPLACE_AFTER_SECONDS=1, CANCEL_AFTER_SECONDS=600)
    manager = ExecutionManager(broker, store, settings)
    manager.submit_plan(_plan([_trade("AAPL", OrderSide.BUY, limit_price=100.0)]))

    entry = store.load_open_orders()[0]
    stale_time = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    store.upsert(dc_replace(entry, submitted_at=stale_time))

    broker.open_order_snapshots = [
        OpenOrderSnapshot(
            order_ref=entry.order_ref,
            order_id=entry.order_id,
            perm_id=None,
            ticker="AAPL",
            side=OrderSide.BUY,
            raw_status="Submitted",
            filled=0.0,
            remaining=5.0,
            avg_fill_price=None,
        )
    ]
    broker.quotes_override = {}

    summary = manager.reconcile()

    assert summary.updates[0].action is None
    assert store.load_open_orders()[0].replace_count == 0


def test_submit_plan_retry_of_same_run_does_not_resubmit_and_returns_idempotent_replay(
    tmp_path: Path,
) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    plan = _plan([_trade("AAPL", OrderSide.BUY)], run_id="run-1")

    first_results = manager.submit_plan(plan)
    second_results = manager.submit_plan(plan)

    assert first_results[0].status == "Submitted"
    assert len(broker.submitted_batches) == 1
    assert second_results[0].status == "IdempotentReplay"
    assert second_results[0].order_id == first_results[0].order_id
    open_orders = store.load_open_orders()
    assert len(open_orders) == 1


def test_submit_plan_blocks_buys_when_refreshed_cash_is_insufficient_after_sells(
    tmp_path: Path,
) -> None:
    broker = RecordingBroker()
    broker.cash_usd = 100.0
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    trades = [_trade("AAPL", OrderSide.BUY), _trade("MSFT", OrderSide.SELL)]

    results = manager.submit_plan(_plan(trades))

    results_by_ticker = {result.ticker: result for result in results}
    assert results_by_ticker["MSFT"].status == "Submitted"
    assert results_by_ticker["AAPL"].status == "BuyingPowerBlocked"
    assert "refreshed broker cash" in results_by_ticker["AAPL"].message
    assert len(broker.submitted_batches) == 1
    assert broker.submitted_batches[0][0].side == OrderSide.SELL
    open_orders = store.load_open_orders()
    assert all(order.ticker != "AAPL" for order in open_orders)


def test_submit_plan_refreshes_cash_after_sell_phase_before_buys(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    trades = [_trade("AAPL", OrderSide.BUY), _trade("MSFT", OrderSide.SELL)]

    manager.submit_plan(_plan(trades))

    assert broker.account_snapshot_calls == 1


def test_submit_plan_does_not_refresh_cash_when_there_are_no_buys(tmp_path: Path) -> None:
    broker = RecordingBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    trades = [_trade("MSFT", OrderSide.SELL)]

    manager.submit_plan(_plan(trades))

    assert broker.account_snapshot_calls == 0
