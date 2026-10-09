"""Regressions for the 2026-10-09 DEV rebalance: wide/unknown quotes and a lagging replace cancel."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_settings
from test_broker_order_lifecycle import (
    FakeContract,
    FakeIB,
    FakeMarketData,
    FakeOrder,
    FakeOrderStatus,
    FakeTrade,
    _settings,
)
from test_execution_manager import RecordingBroker, _plan, _snapshot_for_entry, _trade
from test_execution_pricing import _quote
from test_execution_pricing import _trade as _pricing_trade

from poma.broker import CancelNotConfirmed, IbkrBroker
from poma.cli import _retryable_outcome_reason
from poma.engine import RebalanceOutcome
from poma.execution_manager import ExecutionManager
from poma.execution_pricing import (
    WIDE_SPREAD_MIDPOINT_BASIS,
    apply_execution_quotes,
    inside_spread_limit_price,
    price_from_quote,
    select_execution_price,
)
from poma.models import ExecutionQuote, OpenOrderSnapshot, OrderResult, OrderSide, RebalancePlan
from poma.order_lifecycle import (
    CONTRACT_UNRESOLVED_STATUS,
    EXECUTION_QUOTE_BLOCKED_STATUS,
    OrderLifecycleState,
)
from poma.order_store import OrderStore

# --- Wide spreads -------------------------------------------------------------------------------


def test_wide_spread_is_blocked_while_quote_retries_remain() -> None:
    quote = _quote(bid=99.0, ask=99.75)  # ~75bps, like COP in the DEV log
    price, warnings = select_execution_price(quote, OrderSide.BUY, make_settings())
    assert price is None
    assert "wide quote" in warnings[0]


def test_final_attempt_prices_moderately_wide_spread_inside_the_spread() -> None:
    trade = _pricing_trade(notional=200.0)
    repriced, warnings = apply_execution_quotes(
        [trade], {"AAPL": _quote(bid=99.0, ask=99.75)}, make_settings(), allow_wide_spread=True
    )
    assert warnings == []
    assert repriced[0].reference_price == pytest.approx(99.375)
    assert repriced[0].reference_price_basis == WIDE_SPREAD_MIDPOINT_BASIS
    # Halfway from the 99.375 midpoint to the 99.75 ask, rounded down to the cent.
    assert repriced[0].limit_price == 99.56


def test_sell_wide_spread_limit_rests_inside_the_spread() -> None:
    trade = _pricing_trade(side=OrderSide.SELL, notional=200.0)
    repriced, _ = apply_execution_quotes(
        [trade], {"AAPL": _quote(bid=99.0, ask=99.75)}, make_settings(), allow_wide_spread=True
    )
    assert repriced[0].limit_price == 99.19


@pytest.mark.parametrize(
    ("side", "aggression", "expected"),
    [
        (OrderSide.BUY, 0.0, 100.00),
        (OrderSide.BUY, 0.5, 100.02),
        (OrderSide.BUY, 1.0, 100.05),
        (OrderSide.SELL, 0.0, 100.00),
        (OrderSide.SELL, 0.5, 99.98),
        (OrderSide.SELL, 1.0, 99.95),
    ],
)
def test_inside_spread_limit_rounds_passively_and_stays_within_quote(side, aggression, expected) -> None:
    assert inside_spread_limit_price(side, 99.95, 100.05, aggression) == expected


def test_one_tick_spread_buy_rests_on_bid_and_sell_on_ask() -> None:
    assert inside_spread_limit_price(OrderSide.BUY, 50.00, 50.01, 0.5) == 50.00
    assert inside_spread_limit_price(OrderSide.SELL, 50.00, 50.01, 0.5) == 50.01


def test_sub_dollar_quotes_use_sub_penny_ticks() -> None:
    assert inside_spread_limit_price(OrderSide.BUY, 0.5000, 0.5010, 0.5) == 0.5007


def test_full_aggression_takes_the_far_side_without_extra_offset() -> None:
    settings = make_settings(EXECUTION_LIMIT_AGGRESSION=1.0)
    repriced, _ = apply_execution_quotes([_pricing_trade()], {"AAPL": _quote()}, settings)
    assert repriced[0].limit_price == 200.10


def test_replace_crosses_a_wide_spread_exactly_at_the_far_side() -> None:
    pricing = price_from_quote(
        _quote(bid=99.0, ask=99.75), OrderSide.BUY, make_settings(), 15.0, allow_wide_spread=True
    )
    assert pricing.limit_price == 99.75


def test_replace_on_a_normal_spread_still_crosses_with_price_improvement() -> None:
    pricing = price_from_quote(_quote(), OrderSide.BUY, make_settings(), 15.0)
    assert pricing.limit_price == round(200.10 * 1.0015, 2)


def test_limit_aggression_must_be_between_zero_and_one() -> None:
    with pytest.raises(ValueError):
        make_settings(EXECUTION_LIMIT_AGGRESSION=1.5)


def test_spread_beyond_hard_ceiling_is_still_blocked() -> None:
    quote = _quote(bid=95.0, ask=100.0)  # ~513bps, like A in the DEV log
    price, warnings = select_execution_price(quote, OrderSide.SELL, make_settings(), allow_wide_spread=True)
    assert price is None
    assert "max=150bps" in warnings[0]


def test_wide_spread_ceiling_cannot_be_below_normal_limit() -> None:
    with pytest.raises(ValueError, match="EXECUTION_WIDE_SPREAD_MAX_BPS"):
        make_settings(EXECUTION_MAX_SPREAD_BPS=50, EXECUTION_WIDE_SPREAD_MAX_BPS=40)


def test_execution_quotes_tries_next_venue_when_first_venue_is_wide(monkeypatch: pytest.MonkeyPatch) -> None:
    tick_time = datetime.now(UTC)

    class VenueFakeIB(FakeIB):
        def reqMktData(self, contract, *_args, **_kwargs):  # noqa: N802, ANN201
            self.requested_market_data_contracts.append((contract.symbol, contract.exchange))
            if contract.exchange == "IEX":
                return FakeMarketData(contract=contract, bid=95.0, ask=100.0, time=tick_time)
            return FakeMarketData(contract=contract, bid=99.95, ask=100.05, time=tick_time)

    fake_ib = VenueFakeIB()
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    quote = IbkrBroker(_settings(monkeypatch)).execution_quotes(["A"])["A"]

    assert fake_ib.requested_market_data_contracts == [("A", "IEX"), ("A", "SMART")]
    assert quote.bid == 99.95
    assert quote.spread_bps == pytest.approx(10.0)


def test_execution_quotes_keeps_first_venue_when_next_venue_is_wider(monkeypatch: pytest.MonkeyPatch) -> None:
    tick_time = datetime.now(UTC)

    class VenueFakeIB(FakeIB):
        def reqMktData(self, contract, *_args, **_kwargs):  # noqa: N802, ANN201
            if contract.exchange == "IEX":
                return FakeMarketData(contract=contract, bid=99.0, ask=100.0, time=tick_time)
            return FakeMarketData(contract=contract, bid=90.0, ask=100.0, time=tick_time)

    monkeypatch.setattr("poma.broker.IB", lambda: VenueFakeIB())

    quote = IbkrBroker(_settings(monkeypatch)).execution_quotes(["A"])["A"]

    assert quote.bid == 99.0


# --- Unknown contracts --------------------------------------------------------------------------


class QualifyingFakeIB(FakeIB):
    unknown: frozenset[str] = frozenset({"WBD"})

    def qualifyContracts(self, *contracts):  # noqa: N802, ANN201 - mirrors ib_insync API
        qualified = []
        for contract in contracts:
            if contract.symbol in self.unknown:
                self.errorEvent.emit(-1, 200, "No security definition has been found for the request", contract)
                continue
            contract.conId = 1000 + len(qualified)
            contract.primaryExchange = "NASDAQ"
            qualified.append(contract)
        return qualified


def test_execution_quotes_flags_symbol_ibkr_cannot_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = QualifyingFakeIB(
        market_data_by_symbol={
            "E": FakeMarketData(contract=FakeContract(symbol="E"), bid=56.0, ask=56.05, time=datetime.now(UTC)),
        }
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    quotes = IbkrBroker(_settings(monkeypatch)).execution_quotes(["E", "WBD"])

    assert quotes["WBD"].contract_unresolved is True
    assert "200: No security definition" in (quotes["WBD"].broker_error or "")
    assert quotes["E"].contract_unresolved is False
    assert all(symbol != "WBD" for symbol, _ in fake_ib.requested_market_data_contracts)
    price, warnings = select_execution_price(quotes["WBD"], OrderSide.BUY, make_settings())
    assert price is None
    assert "no US stock contract for WBD" in warnings[0]


def test_unresolved_contract_is_not_retried_and_does_not_block_other_retries(tmp_path) -> None:
    broker = RecordingBroker()
    broker.quotes_override = {
        "E": ExecutionQuote(
            ticker="E", source="ibkr", retrieved_at_utc="t", selected_price_as_of_utc="t",
            age_seconds=0.0, bid=99.95, ask=100.05, last=100.0,
        ),
        "WBD": ExecutionQuote(
            ticker="WBD", source="ibkr", retrieved_at_utc="t", contract_unresolved=True,
            broker_error="200: No security definition has been found for the request",
        ),
    }
    manager = ExecutionManager(broker, OrderStore(tmp_path), make_settings())

    results = manager.submit_plan(_plan([_trade("E", OrderSide.BUY), _trade("WBD", OrderSide.BUY)]))

    by_ticker = {result.ticker: result for result in results}
    assert by_ticker["WBD"].status == CONTRACT_UNRESOLVED_STATUS
    assert by_ticker["E"].status == "Submitted"
    assert sum("WBD" in request for request in broker.execution_quote_requests) == 1

    blocked_cop = OrderResult(
        ticker="COP", side=OrderSide.BUY, quantity=1, notional=135, order_id=None,
        status=EXECUTION_QUOTE_BLOCKED_STATUS, filled=0.0, average_fill_price=None, message="wide quote",
    )
    plan = RebalancePlan(
        run_id="run-1", session_date="2026-10-09", targets=[], trades=[],
        execution_results=[by_ticker["WBD"], blocked_cop], warnings=[],
    )
    outcome = RebalanceOutcome(plan=plan, executed=True, blocked=False, status="completed_with_order_issues")
    assert _retryable_outcome_reason(outcome) is not None


# --- Quote freshness ------------------------------------------------------------------------------


class SequencedQuoteBroker(RecordingBroker):
    """COP is wide until the third call; every quote carries a call counter in ``last``."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def execution_quotes(self, tickers):
        self.calls += 1
        self.execution_quote_requests.append(list(tickers))
        quotes = {}
        for ticker in tickers:
            wide = ticker == "COP" and self.calls < 3
            quotes[ticker] = ExecutionQuote(
                ticker=ticker, source="ibkr", retrieved_at_utc=str(self.calls),
                selected_price_as_of_utc=str(self.calls), age_seconds=0.0,
                bid=99.0 if wide else 99.95, ask=100.0 if wide else 100.05, last=float(self.calls),
            )
        return quotes


def test_already_valid_trade_is_requoted_before_submit_after_other_tickers_retry(tmp_path) -> None:
    broker = SequencedQuoteBroker()
    manager = ExecutionManager(broker, OrderStore(tmp_path), make_settings())

    manager.submit_plan(_plan([_trade("NEM", OrderSide.BUY), _trade("COP", OrderSide.BUY)]))

    assert broker.execution_quote_requests == [["NEM", "COP"], ["COP"], ["COP"], ["NEM"]]
    submitted = {trade.ticker: trade for trade in broker.submitted_batches[0]}
    assert submitted["NEM"].reference_price_as_of_utc == "4"
    assert submitted["COP"].reference_price_as_of_utc == "3"


def test_valid_trade_failing_its_pre_submit_refresh_is_quote_blocked(tmp_path) -> None:
    class WideningBroker(SequencedQuoteBroker):
        def execution_quotes(self, tickers):
            quotes = super().execution_quotes(tickers)
            if self.calls == 4:
                quotes["NEM"] = replace(quotes["NEM"], bid=90.0, ask=100.0)
            return quotes

    broker = WideningBroker()
    manager = ExecutionManager(broker, OrderStore(tmp_path), make_settings())

    results = manager.submit_plan(_plan([_trade("NEM", OrderSide.BUY), _trade("COP", OrderSide.BUY)]))

    by_ticker = {result.ticker: result for result in results}
    assert by_ticker["NEM"].status == EXECUTION_QUOTE_BLOCKED_STATUS
    assert "pre-submit refresh" in (by_ticker["NEM"].message or "")
    assert by_ticker["COP"].status == "Submitted"


# --- Cancel/replace -------------------------------------------------------------------------------


class LaggingCancelIB(FakeIB):
    """IBKR acknowledges the cancel but leaves the order in PendingCancel."""

    final_status: str = "PendingCancel"

    def cancelOrder(self, order):  # noqa: N802
        self.cancelled_orders.append(order.orderId)
        for trade in self.open_trades:
            if trade.order.orderId == order.orderId:
                trade.orderStatus.status = self.final_status
                if self.final_status == "Filled":
                    trade.orderStatus.filled = trade.orderStatus.filled + trade.orderStatus.remaining
                    trade.orderStatus.remaining = 0.0


def _itub_trade() -> FakeTrade:
    return FakeTrade(
        order=FakeOrder(orderId=608, action="SELL", orderRef="poma:run-1:0:ITUB:SELL", account="DU1234567"),
        orderStatus=FakeOrderStatus(status="Submitted", remaining=20.0),
        contract=FakeContract(symbol="ITUB"),
    )


def _replace_itub(broker: IbkrBroker):
    return broker.replace_order(
        order_id=608, ticker="ITUB", side=OrderSide.SELL, quantity=20.0,
        new_limit_price=9.9, order_ref="poma:run-1:0:ITUB:SELL:r1",
    )


def test_replace_order_does_not_place_replacement_while_cancel_is_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = LaggingCancelIB(open_trades=[_itub_trade()])
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    with pytest.raises(CancelNotConfirmed, match="status=PendingCancel"):
        _replace_itub(IbkrBroker(_settings(monkeypatch)))

    assert fake_ib.placed_orders == []


def test_replace_order_returns_original_when_it_fills_during_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = LaggingCancelIB(open_trades=[_itub_trade()])
    fake_ib.final_status = "Filled"
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    snapshot = _replace_itub(IbkrBroker(_settings(monkeypatch)))

    assert fake_ib.placed_orders == []
    assert snapshot.order_ref == "poma:run-1:0:ITUB:SELL"
    assert snapshot.raw_status == "Filled"
    assert snapshot.filled == 20.0


def test_submit_replacement_is_idempotent_on_order_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = FakeIB()
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))
    kwargs = {
        "ticker": "ITUB", "side": OrderSide.SELL, "quantity": 20.0,
        "new_limit_price": 9.9, "order_ref": "poma:run-1:0:ITUB:SELL:r1",
    }

    first = broker.submit_replacement(**kwargs)
    second = broker.submit_replacement(**kwargs)

    assert len(fake_ib.placed_orders) == 1
    assert first.order_id == second.order_id


class ReplaceBroker(RecordingBroker):
    def __init__(self) -> None:
        super().__init__()
        self.replace_error: Exception | None = None
        self.completed: list[OpenOrderSnapshot] = []
        self.replacements: list[dict] = []

    def replace_order(self, **kwargs):
        if self.replace_error is not None:
            raise self.replace_error
        return super().replace_order(**kwargs)

    def fetch_completed_order_snapshots(self):
        return self.completed

    def submit_replacement(self, **kwargs):
        self.replacements.append(kwargs)
        return OpenOrderSnapshot(
            order_ref=kwargs["order_ref"], order_id=9001, perm_id=None, ticker=kwargs["ticker"],
            side=kwargs["side"], raw_status="Submitted", filled=0.0, remaining=kwargs["quantity"],
            avg_fill_price=None,
        )


def _aged_entry(store: OrderStore, manager: ExecutionManager, ticker: str = "ITUB", seconds: int = 150):
    manager.submit_plan(_plan([_trade(ticker, OrderSide.SELL)]))
    entry = next(entry for entry in store.load_open_orders() if entry.ticker == ticker)
    entry = replace(entry, submitted_at=(datetime.now(UTC) - timedelta(seconds=seconds)).isoformat())
    store.upsert(entry)
    return entry


def test_lagging_replace_cancel_is_deferred_not_raised_then_completed(tmp_path) -> None:
    broker = ReplaceBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    entry = _aged_entry(store, manager)
    broker.open_order_snapshots = [_snapshot_for_entry(entry)]
    broker.replace_error = CancelNotConfirmed("cancel not confirmed (status=PendingCancel)")

    first = manager.reconcile()

    deferred = first.updates[0].entry
    assert first.updates[0].action == "replace_deferred"
    assert deferred.lifecycle_state == OrderLifecycleState.CANCEL_PENDING
    assert deferred.order_ref == entry.order_ref
    assert deferred.replacement_order_ref == f"{entry.ledger_key}:r1"

    # IBKR finishes the cancel after the replace gave up: the order leaves the open list and
    # shows up Cancelled in completed history. The replacement is then placed exactly once.
    broker.open_order_snapshots = []
    broker.completed = [_snapshot_for_entry(entry, raw_status="Cancelled")]
    second = manager.reconcile()

    resumed = second.updates[0].entry
    assert second.updates[0].action == "replace"
    assert resumed.order_ref == f"{entry.ledger_key}:r1"
    assert resumed.replacement_order_ref is None
    assert resumed.order_id == 9001
    assert resumed.lifecycle_state == OrderLifecycleState.BROKER_ACCEPTED
    assert [call["order_ref"] for call in broker.replacements] == [f"{entry.ledger_key}:r1"]

    broker.open_order_snapshots = [_snapshot_for_entry(resumed)]
    third = manager.reconcile()
    assert third.updates[0].matched is True
    assert len(broker.replacements) == 1


def test_lagging_cancel_that_fills_closes_as_filled_without_replacement(tmp_path) -> None:
    broker = ReplaceBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    entry = _aged_entry(store, manager)
    broker.open_order_snapshots = [_snapshot_for_entry(entry)]
    broker.replace_error = CancelNotConfirmed("pending")
    manager.reconcile()

    broker.open_order_snapshots = []
    broker.completed = [replace(_snapshot_for_entry(entry, raw_status="Filled"), filled=5.0, remaining=0.0)]
    summary = manager.reconcile()

    assert summary.updates[0].entry.lifecycle_state == OrderLifecycleState.FILLED
    assert broker.replacements == []


def test_replacement_found_open_after_crash_is_adopted(tmp_path) -> None:
    broker = ReplaceBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    entry = _aged_entry(store, manager)
    store.upsert(replace(
        entry, lifecycle_state=OrderLifecycleState.REPLACE_PENDING, raw_status="ReplacementUnconfirmed",
        replace_count=1, replacement_order_ref=f"{entry.ledger_key}:r1",
    ))
    broker.open_order_snapshots = [
        replace(_snapshot_for_entry(entry), order_ref=f"{entry.ledger_key}:r1", order_id=777)
    ]

    summary = manager.reconcile()

    adopted = summary.updates[0].entry
    assert adopted.order_ref == f"{entry.ledger_key}:r1"
    assert adopted.order_id == 777
    assert adopted.lifecycle_state == OrderLifecycleState.BROKER_ACCEPTED
    assert broker.replacements == []


def test_legacy_unverified_order_from_failed_replace_resolves_from_history(tmp_path) -> None:
    """Order 608: the ledger switched to the ``:r1`` ref before the cancel failed, so the
    original order could never be matched again and sat in ``unknown`` forever."""
    broker = ReplaceBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    entry = _aged_entry(store, manager)
    store.upsert(replace(
        entry, order_ref=f"{entry.ledger_key}:r1", replace_count=1,
        lifecycle_state=OrderLifecycleState.UNKNOWN, raw_status="NotOpenUnverified",
    ))
    broker.open_order_snapshots = []
    broker.completed = [_snapshot_for_entry(entry, raw_status="Cancelled")]

    summary = manager.reconcile()

    assert summary.updates[0].action == "closed"
    assert summary.updates[0].entry.lifecycle_state == OrderLifecycleState.CANCELLED
    assert store.load_open_orders() == [] or all(e.is_terminal for e in store.load_open_orders())


def test_one_failing_replace_does_not_abort_reconcile_or_block_planning(tmp_path) -> None:
    broker = ReplaceBroker()
    store = OrderStore(tmp_path)
    manager = ExecutionManager(broker, store, make_settings())
    manager.submit_plan(_plan([_trade("ITUB", OrderSide.SELL), _trade("NEM", OrderSide.BUY)]))
    aged = (datetime.now(UTC) - timedelta(seconds=150)).isoformat()
    entries = []
    for entry in store.load_open_orders():
        entry = replace(entry, submitted_at=aged if entry.ticker == "ITUB" else entry.submitted_at)
        store.upsert(entry)
        entries.append(entry)
    broker.open_order_snapshots = [_snapshot_for_entry(entry) for entry in entries]
    broker.replace_error = RuntimeError("gateway hiccup")

    summary = manager.reconcile()

    updates = {update.entry.ticker: update for update in summary.updates}
    assert updates["ITUB"].action == "error"
    assert "gateway hiccup" in (updates["ITUB"].detail or "")
    assert updates["NEM"].matched is True
    itub_key = next(e.ledger_key for e in entries if e.ticker == "ITUB")
    pending = store.get(itub_key)
    assert pending is not None and pending.lifecycle_state == OrderLifecycleState.REPLACE_PENDING

    # Planning only warns about this run's own unresolved orders; it no longer blocks.
    check = manager.check_stale_orders("2026-07-01", "run-1")
    assert not any("block execution" in warning for warning in check.warnings)

    # The original was still working at the broker, so the unplaced replacement is dropped and
    # the single allowed replace is spent rather than retried.
    restored = store.get(itub_key)
    assert restored is not None
    assert restored.lifecycle_state == OrderLifecycleState.BROKER_ACCEPTED
    assert restored.replacement_order_ref is None
    assert restored.replace_count == 1
