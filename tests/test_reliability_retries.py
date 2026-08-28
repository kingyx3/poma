from __future__ import annotations

from datetime import UTC, datetime

from conftest import make_settings
from poma.cli import _retryable_outcome_reason
from poma.engine import RebalanceOutcome
from poma.execution_manager import ExecutionManager
from poma.execution_pricing import apply_execution_quotes
from poma.models import ExecutionQuote, OrderResult, OrderSide, ProposedTrade, RebalancePlan
from poma.order_lifecycle import (
    BUYING_POWER_BLOCKED_STATUS,
    EXECUTION_QUOTE_BLOCKED_STATUS,
    IDEMPOTENT_REPLAY_STATUS,
    OrderLedgerEntry,
    OrderLifecycleState,
)
from poma.order_store import OrderStore
from poma.state import RETRY_WAIT_STATUS, LocalState


def _trade(
    ticker: str,
    side: OrderSide,
    *,
    quantity: float = 1.0,
    notional: float = 100.0,
    order_ref: str | None = None,
) -> ProposedTrade:
    return ProposedTrade(
        ticker=ticker,
        side=side,
        quantity=quantity,
        notional=notional,
        reference_price=100.0,
        limit_price=100.0,
        reason="test",
        order_ref=order_ref,
    )


def _quote(ticker: str, *, bid: float, ask: float, spread_bps: float) -> ExecutionQuote:
    now = datetime.now(UTC).isoformat()
    return ExecutionQuote(
        ticker=ticker,
        source="ibkr",
        retrieved_at_utc=now,
        selected_price_as_of_utc=now,
        age_seconds=0.0,
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2,
        spread_bps=spread_bps,
    )


def test_execution_repricing_preserves_one_share_sell_after_price_rise() -> None:
    settings = make_settings(EXECUTION_PRICE_SOURCE="ibkr", FRACTIONAL_SHARES=False)
    trade = _trade("NVS", OrderSide.SELL, quantity=1.0, notional=150.0)
    quotes = {"NVS": _quote("NVS", bid=150.06, ask=150.08, spread_bps=1.3)}

    repriced, warnings = apply_execution_quotes(
        [trade],
        quotes,
        settings,
        settings.execution_rules(),
    )

    assert warnings == []
    assert len(repriced) == 1
    assert repriced[0].quantity == 1.0
    assert repriced[0].notional == 150.06


def test_local_quote_block_is_retryable_under_same_order_ref() -> None:
    trade = _trade("PM", OrderSide.BUY, order_ref="poma:run-1:0:PM:BUY")
    entry = OrderLedgerEntry(
        ledger_key=trade.order_ref or "",
        order_ref=trade.order_ref or "",
        run_id="run-1",
        session_date="2026-08-20",
        ticker="PM",
        side=OrderSide.BUY,
        quantity=1.0,
        limit_price=100.0,
        lifecycle_state=OrderLifecycleState.REJECTED,
        raw_status=EXECUTION_QUOTE_BLOCKED_STATUS,
        filled_qty=0.0,
        remaining_qty=1.0,
    )

    manager = object.__new__(ExecutionManager)
    replay = manager._idempotent_replay(trade, {entry.order_ref: entry})

    assert replay is None


def test_unknown_broker_state_is_never_resubmitted() -> None:
    trade = _trade("PM", OrderSide.BUY, order_ref="poma:run-1:0:PM:BUY")
    entry = OrderLedgerEntry(
        ledger_key=trade.order_ref or "",
        order_ref=trade.order_ref or "",
        run_id="run-1",
        session_date="2026-08-20",
        ticker="PM",
        side=OrderSide.BUY,
        quantity=1.0,
        limit_price=100.0,
        order_id=123,
        lifecycle_state=OrderLifecycleState.UNKNOWN,
        raw_status="NotOpenUnverified",
        filled_qty=0.0,
        remaining_qty=1.0,
    )

    manager = object.__new__(ExecutionManager)
    replay = manager._idempotent_replay(trade, {entry.order_ref: entry})

    assert replay is not None
    assert replay.status == IDEMPOTENT_REPLAY_STATUS
    assert replay.order_id == 123


class _QuoteSequenceBroker:
    def __init__(self) -> None:
        self.calls = 0

    def execution_quotes(self, tickers: list[str]) -> dict[str, ExecutionQuote]:
        self.calls += 1
        if self.calls < 3:
            return {ticker: _quote(ticker, bid=99.0, ask=100.0, spread_bps=100.5) for ticker in tickers}
        return {ticker: _quote(ticker, bid=99.95, ask=100.05, spread_bps=10.0) for ticker in tickers}


def test_execution_manager_retries_transient_wide_quote(monkeypatch, tmp_path) -> None:
    settings = make_settings(EXECUTION_PRICE_SOURCE="ibkr", EXECUTION_MAX_SPREAD_BPS=50)
    broker = _QuoteSequenceBroker()
    manager = ExecutionManager(broker, OrderStore(tmp_path), settings)
    monkeypatch.setattr("poma.execution_manager.time.sleep", lambda _seconds: None)
    trade = _trade("PM", OrderSide.BUY, order_ref="poma:run-1:0:PM:BUY")
    plan = RebalancePlan(
        run_id="run-1",
        session_date="2026-08-20",
        targets=[],
        trades=[trade],
        execution_results=[],
        warnings=[],
    )

    repriced, blocked = manager._reprice_for_execution(plan, [trade], None)

    assert broker.calls == 3
    assert blocked == []
    assert len(repriced) == 1
    assert repriced[0].ticker == "PM"


def test_completed_with_buying_power_block_is_retryable_after_accepted_sell() -> None:
    sell = OrderResult(
        ticker="ARM",
        side=OrderSide.SELL,
        quantity=1.0,
        notional=250.0,
        order_id=199,
        status="Submitted",
        filled=0.0,
        average_fill_price=None,
    )
    buy = OrderResult(
        ticker="PM",
        side=OrderSide.BUY,
        quantity=1.0,
        notional=190.0,
        order_id=None,
        status=BUYING_POWER_BLOCKED_STATUS,
        filled=0.0,
        average_fill_price=None,
        message="sell proceeds not confirmed yet",
    )
    plan = RebalancePlan(
        run_id="run-1",
        session_date="2026-08-20",
        targets=[],
        trades=[],
        execution_results=[sell, buy],
        warnings=[],
    )
    outcome = RebalanceOutcome(
        plan=plan,
        executed=True,
        blocked=False,
        status="completed_with_order_issues",
    )

    reason = _retryable_outcome_reason(outcome)

    assert reason is not None
    assert BUYING_POWER_BLOCKED_STATUS in reason


def test_retry_state_preserves_run_and_counts_attempts(tmp_path) -> None:
    state = LocalState(tmp_path)

    assert state.begin_session("2026-08-20", "run-1") == 1
    state.mark_retry_wait("2026-08-20", "run-1", reason="wide quote")
    assert state.session_status("2026-08-20") == RETRY_WAIT_STATUS
    assert state.session_run_id("2026-08-20") == "run-1"
    assert state.begin_session("2026-08-20", "run-1") == 2
    assert state.session_attempt_count("2026-08-20") == 2
