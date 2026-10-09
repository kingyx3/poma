from dataclasses import replace

import pytest
from conftest import make_settings
from pydantic import ValidationError
from test_broker_order_lifecycle import FakeIB, _settings
from test_execution_manager import RecordingBroker, _plan, _trade
from test_execution_pricing import _quote
from test_order_store import _entry

from poma.broker import IbkrBroker
from poma.data import _normalise_snapshot
from poma.execution_manager import ExecutionManager
from poma.execution_pricing import select_execution_price
from poma.models import OrderSide
from poma.order_lifecycle import OrderLifecycleState
from poma.order_store import OrderStore
from poma.persistence import atomic_write_text


@pytest.mark.parametrize('field', ['bid', 'ask', 'last', 'age_seconds', 'spread_bps'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_quotes_fail_closed(field, value):
    price, warnings = select_execution_price(_quote(**{field: value}), OrderSide.BUY, make_settings())
    assert price is None
    assert 'block execution' in warnings[0]


@pytest.mark.parametrize('values', [{'bid': 201, 'ask': 200}, {'age_seconds': -1}, {'spread_bps': -1}])
def test_invalid_quote_metadata_fails_closed(values):
    assert select_execution_price(_quote(**values), OrderSide.BUY, make_settings())[0] is None


@pytest.mark.parametrize('field', ['MAX_ORDER_NOTIONAL_USD', 'LIMIT_OFFSET_BPS', 'MANAGED_CAP_USD'])
@pytest.mark.parametrize('value', ['NaN', 'Infinity'])
def test_nonfinite_configuration_is_rejected(field, value):
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_market_data_discards_missing_symbols_and_infinite_values():
    frame = _normalise_snapshot([
        {'ticker': None, 'market_cap': 100, 'price': 10},
        {'ticker': 'BAD', 'market_cap': float('inf'), 'price': 10},
        {'ticker': 'BAD2', 'market_cap': 100, 'price': float('inf')},
        {'ticker': 'GOOD', 'market_cap': 100, 'price': 10},
    ], require_price=True)
    assert frame.ticker.tolist() == ['GOOD']


def test_atomic_write_preserves_old_state_on_replace_failure(tmp_path, monkeypatch):
    path = tmp_path / 'state.json'
    path.write_text('{"old": true}')
    def fail(*args):
        raise OSError('disk failure')
    monkeypatch.setattr('poma.persistence.os.replace', fail)
    with pytest.raises(OSError):
        atomic_write_text(path, '{"new": true}')
    assert path.read_text() == '{"old": true}'
    assert list(tmp_path.iterdir()) == [path]


def test_event_log_recovers_terminal_transition_when_snapshot_write_fails(tmp_path, monkeypatch):
    store = OrderStore(tmp_path)
    entry = _entry()
    store.upsert(entry)
    def fail(*args):
        raise OSError('snapshot failure')
    monkeypatch.setattr(store, '_save_open_orders', fail)
    with pytest.raises(OSError):
        store.upsert(replace(entry, lifecycle_state=OrderLifecycleState.FILLED, filled_qty=5))
    recovered = OrderStore(tmp_path)
    assert recovered.load_open_orders() == []
    assert recovered.get_latest_many([entry.ledger_key])[entry.ledger_key].is_terminal


def test_crash_at_submission_boundary_never_retries_as_planned(tmp_path):
    class CrashBroker(RecordingBroker):
        def submit_trades(self, trades, status_callback=None):
            raise KeyboardInterrupt('process terminated after socket write')
    store = OrderStore(tmp_path)
    plan = _plan([_trade('AAPL', OrderSide.BUY)])
    with pytest.raises(KeyboardInterrupt):
        ExecutionManager(CrashBroker(), store, make_settings()).submit_plan(plan)
    broker = RecordingBroker()
    results = ExecutionManager(broker, OrderStore(tmp_path), make_settings()).submit_plan(plan)
    assert results[0].status == 'IdempotentReplay'
    assert broker.submitted_batches == []


@pytest.mark.parametrize('cash', [float('nan'), float('inf')])
def test_nonfinite_cash_never_funds_buys(tmp_path, cash):
    broker = RecordingBroker()
    broker.cash_usd = cash
    results = ExecutionManager(broker, OrderStore(tmp_path), make_settings()).submit_plan(
        _plan([_trade('AAPL', OrderSide.BUY)])
    )
    assert results[0].status == 'BuyingPowerBlocked'
    assert broker.submitted_batches == []


def test_socket_failure_inside_place_order_keeps_uncertain_submission(monkeypatch):
    class DisconnectIB(FakeIB):
        def placeOrder(self, contract, order):  # noqa: N802
            super().placeOrder(contract, order)
            self.connected = False
            raise ConnectionError('lost after write')
    ib = DisconnectIB()
    broker = IbkrBroker(_settings(monkeypatch))
    monkeypatch.setattr(broker, '_connect', lambda: ib)
    monkeypatch.setattr(broker, '_assert_ready_for_orders', lambda ib: None)
    ib.connected = True
    results = broker.submit_trades([_trade('AAPL', OrderSide.BUY), _trade('MSFT', OrderSide.BUY)])
    assert len(ib.placed_orders) == 1
    assert results[0].status == 'SubmissionUnconfirmed'
    assert results[1].status == 'BrokerUnavailable'


def test_broker_cancel_does_not_touch_other_accounts(monkeypatch):
    from test_broker_order_lifecycle import FakeContract, FakeOrder, FakeOrderStatus, FakeTrade
    ib = FakeIB(open_trades=[FakeTrade(
        FakeOrder(7, 'BUY', 'poma:other', 'U_OTHER'), FakeOrderStatus(), FakeContract('AAPL')
    )])
    monkeypatch.setattr('poma.broker.IB', lambda: ib)
    assert IbkrBroker(_settings(monkeypatch)).cancel_order(7) is False
    assert ib.cancelled_orders == []


def test_replacement_crash_keeps_new_identity_unresolved(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from test_execution_manager import _snapshot_for_entry

    store = OrderStore(tmp_path)
    broker = RecordingBroker()
    manager = ExecutionManager(broker, store, make_settings(REPLACE_AFTER_SECONDS=1, CANCEL_AFTER_SECONDS=600))
    manager.submit_plan(_plan([_trade('AAPL', OrderSide.BUY)]))
    entry = replace(store.load_open_orders()[0], submitted_at=(datetime.now(UTC) - timedelta(seconds=10)).isoformat())
    store.upsert(entry)
    broker.open_order_snapshots = [_snapshot_for_entry(entry)]
    def crash(**kwargs):
        raise KeyboardInterrupt('crashed during replacement')
    monkeypatch.setattr(broker, 'replace_order', crash)
    with pytest.raises(KeyboardInterrupt):
        manager.reconcile()
    pending = OrderStore(tmp_path).load_open_orders()[0]
    assert pending.lifecycle_state == OrderLifecycleState.REPLACE_PENDING
    # The original stays the tracked identity; the replacement ref is reserved alongside it so
    # reconciliation can find whichever order actually exists after the crash.
    assert pending.order_ref == entry.order_ref
    assert pending.replacement_order_ref == entry.order_ref + ':r1'
    assert not pending.is_terminal


def test_fresh_sell_price_cannot_bypass_order_notional_cap():
    from poma.execution_pricing import apply_execution_quotes
    trade = _trade('AAPL', OrderSide.SELL)
    trades, warnings = apply_execution_quotes(
        [trade], {'AAPL': _quote(bid=1000, ask=1001)}, make_settings(MAX_ORDER_NOTIONAL_USD=2000)
    )
    assert trades == []
    assert 'notional safety limits' in warnings[0]
