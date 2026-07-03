from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from conftest import make_settings

from poma.broker import (
    IbkrBroker,
    order_results_have_issues,
    order_results_have_no_accepted_orders,
)
from poma.models import OrderResult, OrderSide


@dataclass
class FakeOrder:
    orderId: int
    action: str
    orderRef: str = ""
    account: str = ""
    permId: int | None = None


@dataclass
class FakeOrderStatus:
    status: str = "Submitted"
    filled: float = 0.0
    remaining: float = 5.0
    avgFillPrice: float = 0.0


@dataclass
class FakeContract:
    symbol: str
    secType: str = "STK"
    currency: str = "USD"


@dataclass
class FakeAccountValue:
    tag: str
    value: str
    currency: str
    account: str = "DU1234567"


@dataclass
class FakePortfolioItem:
    contract: FakeContract
    position: float
    marketValue: float
    account: str = "DU1234567"


@dataclass
class FakeTrade:
    order: FakeOrder
    orderStatus: FakeOrderStatus
    contract: FakeContract


@dataclass
class FakeMarketData:
    contract: object
    bid: float = float("nan")
    ask: float = float("nan")
    last: float = float("nan")
    close: float = float("nan")
    time: object = None
    marketDataType: int = 1


@dataclass
class FakeEvent:
    """Minimal stand-in for eventkit's ``Event``: supports ``+=``/``-=`` and firing listeners."""

    listeners: list = field(default_factory=list)

    def __iadd__(self, listener):
        self.listeners.append(listener)
        return self

    def __isub__(self, listener):
        if listener in self.listeners:
            self.listeners.remove(listener)
        return self

    def emit(self, *args) -> None:
        for listener in list(self.listeners):
            listener(*args)


@dataclass
class FakeIB:
    open_trades: list[FakeTrade] = field(default_factory=list)
    connected: bool = False
    RequestTimeout: float | None = None
    cancelled_orders: list[int] = field(default_factory=list)
    placed_orders: list[tuple[object, object]] = field(default_factory=list)
    next_order_id: int = 100
    market_data_by_symbol: dict[str, FakeMarketData] = field(default_factory=dict)
    market_data_after_delayed_by_symbol: dict[str, FakeMarketData] = field(default_factory=dict)
    requested_market_data_contracts: list[tuple[str, str]] = field(default_factory=list)
    cancelled_market_data_symbols: list[str] = field(default_factory=list)
    market_data_type_requests: list[int] = field(default_factory=list)
    current_market_data_type: int = 1
    market_data_errors_by_symbol: dict[str, tuple[int, str]] = field(default_factory=dict)
    general_market_data_errors: list[tuple[int, str]] = field(default_factory=list)
    general_errors_emitted: bool = False
    errorEvent: FakeEvent = field(default_factory=FakeEvent)
    account_summary_rows: list[FakeAccountValue] = field(default_factory=list)
    account_value_rows: list[FakeAccountValue] = field(default_factory=list)
    portfolio_items: list[FakePortfolioItem] = field(default_factory=list)

    def connect(self, *_args, **_kwargs) -> None:
        self.connected = True

    def isConnected(self) -> bool:  # noqa: N802 - mirrors ib_insync API
        return self.connected

    def managedAccounts(self) -> list[str]:  # noqa: N802
        return ["DU1234567"]

    def reqCurrentTime(self) -> str:  # noqa: N802
        return "2026-07-01T13:40:00Z"

    def reqAllOpenOrders(self) -> None:  # noqa: N802
        return None

    def openTrades(self) -> list[FakeTrade]:  # noqa: N802
        return self.open_trades

    def cancelOrder(self, order: FakeOrder) -> None:  # noqa: N802
        self.cancelled_orders.append(order.orderId)
        for trade in self.open_trades:
            if trade.order.orderId == order.orderId:
                trade.orderStatus.status = "Cancelled"

    def placeOrder(self, contract, order):  # noqa: N802, ANN201
        order.orderId = self.next_order_id
        self.next_order_id += 1
        self.placed_orders.append((contract, order))
        status = FakeOrderStatus(status="Submitted", filled=0.0, remaining=order.totalQuantity)
        trade = FakeTrade(order=order, orderStatus=status, contract=FakeContract(symbol=contract.symbol))
        self.open_trades.append(trade)
        return trade

    def sleep(self, _seconds: float) -> None:
        return None

    def reqMktData(self, contract, *_args, **_kwargs):  # noqa: N802, ANN201 - mirrors ib_insync API
        self.requested_market_data_contracts.append((contract.symbol, getattr(contract, "exchange", "")))
        if not self.general_errors_emitted and self.general_market_data_errors:
            for code, message in self.general_market_data_errors:
                self.errorEvent.emit(-1, code, message, None)
            self.general_errors_emitted = True
        error = self.market_data_errors_by_symbol.get(contract.symbol)
        if error is not None:
            code, message = error
            self.errorEvent.emit(-1, code, message, contract)
        if self.current_market_data_type == 3 and contract.symbol in self.market_data_after_delayed_by_symbol:
            return self.market_data_after_delayed_by_symbol[contract.symbol]
        return self.market_data_by_symbol[contract.symbol]

    def reqMarketDataType(self, data_type: int) -> None:  # noqa: N802
        self.market_data_type_requests.append(data_type)
        self.current_market_data_type = data_type

    def cancelMktData(self, contract) -> None:  # noqa: N802
        self.cancelled_market_data_symbols.append(contract.symbol)

    def accountSummary(self, _account: str = "") -> list[FakeAccountValue]:  # noqa: N802
        return self.account_summary_rows

    def accountValues(self, _account: str = "") -> list[FakeAccountValue]:  # noqa: N802
        return self.account_value_rows

    def portfolio(self) -> list[FakePortfolioItem]:
        return self.portfolio_items

    def disconnect(self) -> None:
        self.connected = False


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: str):
    env = {
        "APP_ENV": "test",
        "TRADING_MODE": "paper",
        "ALLOW_LIVE_TRADING": "false",
        "DATA_PROVIDER": "fixture",
        "IBKR_ACCOUNT": "DU1234567",
        "TELEGRAM_BOT_TOKEN": "token",
        "TELEGRAM_CHAT_ID": "123456",
    }
    env.update(overrides)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return make_settings(**{k: v for k, v in env.items()})


def test_fetch_open_order_snapshots_only_returns_poma_tagged_orders_for_the_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ib = FakeIB(
        open_trades=[
            FakeTrade(
                order=FakeOrder(orderId=1, action="BUY", orderRef="poma:run-1:0:AAPL:BUY", account="DU1234567"),
                orderStatus=FakeOrderStatus(status="Submitted", filled=0.0, remaining=5.0),
                contract=FakeContract(symbol="AAPL"),
            ),
            FakeTrade(
                order=FakeOrder(orderId=2, action="SELL", orderRef="", account="DU1234567"),
                orderStatus=FakeOrderStatus(status="Submitted", filled=0.0, remaining=3.0),
                contract=FakeContract(symbol="MSFT"),
            ),
            FakeTrade(
                order=FakeOrder(orderId=3, action="BUY", orderRef="poma:run-1:1:NVDA:BUY", account="DU7654321"),
                orderStatus=FakeOrderStatus(status="Submitted", filled=0.0, remaining=1.0),
                contract=FakeContract(symbol="NVDA"),
            ),
        ]
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    snapshots = broker.fetch_open_order_snapshots()

    assert len(snapshots) == 1
    assert snapshots[0].order_ref == "poma:run-1:0:AAPL:BUY"
    assert snapshots[0].ticker == "AAPL"
    assert snapshots[0].side == OrderSide.BUY


def test_cancel_order_cancels_matching_open_order(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = FakeIB(
        open_trades=[
            FakeTrade(
                order=FakeOrder(orderId=7, action="BUY", orderRef="poma:run-1:0:AAPL:BUY", account="DU1234567"),
                orderStatus=FakeOrderStatus(status="Submitted"),
                contract=FakeContract(symbol="AAPL"),
            ),
        ]
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    cancelled = broker.cancel_order(7)

    assert cancelled is True
    assert fake_ib.cancelled_orders == [7]


def test_cancel_order_returns_false_when_order_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = FakeIB(open_trades=[])
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    assert broker.cancel_order(999) is False


def test_replace_order_cancels_old_order_and_places_a_new_one(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = FakeIB(
        open_trades=[
            FakeTrade(
                order=FakeOrder(orderId=7, action="BUY", orderRef="poma:run-1:0:AAPL:BUY", account="DU1234567"),
                orderStatus=FakeOrderStatus(status="Submitted"),
                contract=FakeContract(symbol="AAPL"),
            ),
        ]
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    snapshot = broker.replace_order(
        order_id=7,
        ticker="AAPL",
        side=OrderSide.BUY,
        quantity=5.0,
        new_limit_price=101.5,
        order_ref="poma:run-1:0:AAPL:BUY:r1",
    )

    assert fake_ib.cancelled_orders == [7]
    assert snapshot.order_ref == "poma:run-1:0:AAPL:BUY:r1"
    assert snapshot.ticker == "AAPL"
    assert len(fake_ib.placed_orders) == 1


def test_execution_quotes_reads_bid_ask_and_marks_freshness(monkeypatch: pytest.MonkeyPatch) -> None:
    tick_time = datetime.now(UTC)
    fake_ib = FakeIB(
        market_data_by_symbol={
            "AAPL": FakeMarketData(
                contract=FakeContract(symbol="AAPL"), bid=199.90, ask=200.10, last=200.00, time=tick_time
            ),
        }
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    quotes = broker.execution_quotes(["AAPL"])

    assert set(quotes) == {"AAPL"}
    quote = quotes["AAPL"]
    assert quote.bid == 199.90
    assert quote.ask == 200.10
    assert quote.last == 200.00
    assert quote.source == "ibkr"
    assert quote.is_delayed is False
    assert quote.age_seconds is not None
    assert quote.age_seconds < 5.0
    assert fake_ib.requested_market_data_contracts == [("AAPL", "IEX")]
    assert fake_ib.cancelled_market_data_symbols == ["AAPL"]


def test_execution_quotes_falls_back_to_smart_when_direct_venue_has_no_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tick_time = datetime.now(UTC)

    class ExchangeAwareFakeIB(FakeIB):
        def reqMktData(self, contract, *_args, **_kwargs):  # noqa: N802, ANN201
            self.requested_market_data_contracts.append((contract.symbol, getattr(contract, "exchange", "")))
            if contract.exchange == "IEX":
                return FakeMarketData(contract=contract, time=None)
            return FakeMarketData(contract=contract, bid=199.90, ask=200.10, time=tick_time)

    fake_ib = ExchangeAwareFakeIB()
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    quotes = broker.execution_quotes(["AAPL"])

    assert quotes["AAPL"].is_delayed is False
    assert fake_ib.requested_market_data_contracts == [("AAPL", "IEX"), ("AAPL", "SMART")]
    assert fake_ib.cancelled_market_data_symbols == ["AAPL", "AAPL"]


def test_execution_quotes_marks_delayed_market_data_type(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = FakeIB(
        market_data_by_symbol={
            "MSFT": FakeMarketData(
                contract=FakeContract(symbol="MSFT"),
                bid=400.0,
                ask=400.20,
                time=datetime.now(UTC),
                marketDataType=3,
            ),
        }
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    quotes = broker.execution_quotes(["MSFT"])

    assert quotes["MSFT"].is_delayed is True
    assert quotes["MSFT"].raw_market_data_type == "delayed"


def test_execution_quotes_treats_missing_tick_time_as_unknown_age(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = FakeIB(
        market_data_by_symbol={
            "NVDA": FakeMarketData(contract=FakeContract(symbol="NVDA"), bid=100.0, ask=100.20, time=None),
        }
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch, ALLOW_DELAYED_EXECUTION_QUOTES="false"))

    quotes = broker.execution_quotes(["NVDA"])

    assert quotes["NVDA"].age_seconds is None
    assert quotes["NVDA"].selected_price_as_of_utc is None
    # No delayed retry is attempted; the final live request restores the session default.
    assert fake_ib.market_data_type_requests == [1, 1, 1]


def test_connect_requests_live_market_data_type_on_every_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = FakeIB(open_trades=[])
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    broker.cancel_order(999)

    assert fake_ib.market_data_type_requests == [1]


def test_execution_quotes_falls_back_to_delayed_data_when_allowed_and_no_live_tick_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tick_time = datetime.now(UTC)
    fake_ib = FakeIB(
        market_data_by_symbol={
            "NVDA": FakeMarketData(contract=FakeContract(symbol="NVDA"), time=None),
        },
        market_data_after_delayed_by_symbol={
            "NVDA": FakeMarketData(
                contract=FakeContract(symbol="NVDA"), bid=100.0, ask=100.20, time=tick_time, marketDataType=3
            ),
        },
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch, ALLOW_DELAYED_EXECUTION_QUOTES="true"))

    quotes = broker.execution_quotes(["NVDA"])

    assert quotes["NVDA"].is_delayed is True
    assert quotes["NVDA"].age_seconds is not None
    # live on connect, live for the venue batch, delayed for the retry, then back to live for
    # any later request this session.
    assert fake_ib.market_data_type_requests == [1, 1, 3, 1]
    # Frozen data types (2/4) are readiness-probe-only: stale-by-definition quotes must never
    # feed execution pricing.
    assert not {2, 4} & set(fake_ib.market_data_type_requests)


def test_execution_quotes_does_not_retry_tickers_that_already_have_a_live_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tick_time = datetime.now(UTC)
    fake_ib = FakeIB(
        market_data_by_symbol={
            "AAPL": FakeMarketData(
                contract=FakeContract(symbol="AAPL"), bid=199.90, ask=200.10, last=200.00, time=tick_time
            ),
        }
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch, ALLOW_DELAYED_EXECUTION_QUOTES="true"))

    quotes = broker.execution_quotes(["AAPL"])

    assert quotes["AAPL"].is_delayed is False
    # No missing tickers, so the delayed-data retry path is never entered.
    assert fake_ib.market_data_type_requests == [1, 1, 1]


def test_execution_quotes_captures_ibkr_error_when_no_tick_arrives(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = FakeIB(
        market_data_by_symbol={
            "AAPL": FakeMarketData(contract=FakeContract(symbol="AAPL"), time=None),
        },
        market_data_errors_by_symbol={"AAPL": (354, "Requested market data is not subscribed.")},
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch, ALLOW_DELAYED_EXECUTION_QUOTES="false"))

    quotes = broker.execution_quotes(["AAPL"])

    assert quotes["AAPL"].age_seconds is None
    assert quotes["AAPL"].broker_error == "354: Requested market data is not subscribed."


def test_execution_quotes_falls_back_to_general_broker_error_when_no_per_symbol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ib = FakeIB(
        market_data_by_symbol={
            "AAPL": FakeMarketData(contract=FakeContract(symbol="AAPL"), time=None),
        },
        general_market_data_errors=[(2103, "Market data farm connection is broken:usfarm")],
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    quotes = broker.execution_quotes(["AAPL"])

    assert quotes["AAPL"].age_seconds is None
    assert quotes["AAPL"].broker_error == "2103: Market data farm connection is broken:usfarm"


def test_execution_quotes_does_not_report_broker_error_when_a_tick_arrives(monkeypatch: pytest.MonkeyPatch) -> None:
    tick_time = datetime.now(UTC)
    fake_ib = FakeIB(
        market_data_by_symbol={
            "AAPL": FakeMarketData(
                contract=FakeContract(symbol="AAPL"), bid=199.90, ask=200.10, last=200.00, time=tick_time
            ),
        },
        general_market_data_errors=[(2104, "Market data farm connection is OK:usfarm")],
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    quotes = broker.execution_quotes(["AAPL"])

    assert quotes["AAPL"].age_seconds is not None
    assert quotes["AAPL"].broker_error is None


def _result(status: str, *, filled: float = 0.0, message: str | None = None) -> OrderResult:
    return OrderResult(
        ticker="AAPL",
        side=OrderSide.BUY,
        quantity=5.0,
        notional=500.0,
        order_id=1,
        status=status,
        filled=filled,
        average_fill_price=None,
        message=message,
    )


def test_idempotent_replay_only_results_are_not_treated_as_no_orders_accepted() -> None:
    """A same-run retry that only replays already-accepted-but-unfilled orders is not a failure.

    Without this, engine.execution_status() would misclassify a benign crash-recovery retry as
    NO_ORDERS_ACCEPTED_STATUS, which src/poma/state.py treats as terminal and blocks any further
    automatic retry of that session.
    """
    results = [_result("IdempotentReplay", filled=0.0, message="already broker_accepted; not resubmitted")]

    assert order_results_have_no_accepted_orders(results) is False


def test_idempotent_replay_only_results_are_not_treated_as_issues() -> None:
    results = [_result("IdempotentReplay", filled=0.0, message="already broker_accepted; not resubmitted")]

    assert order_results_have_issues(results) is False


def test_a_real_failure_alongside_an_idempotent_replay_is_still_flagged_as_an_issue() -> None:
    results = [
        _result("IdempotentReplay", filled=0.0, message="already broker_accepted; not resubmitted"),
        _result("Failed", message="order not accepted by broker"),
    ]

    assert order_results_have_issues(results) is True
    assert order_results_have_no_accepted_orders(results) is False


def _sgd_account_rows(cash_sgd: str = "13100", net_liq_sgd: str = "13100") -> dict:
    return {
        "account_summary_rows": [
            FakeAccountValue(tag="TotalCashValue", value=cash_sgd, currency="SGD"),
            FakeAccountValue(tag="NetLiquidation", value=net_liq_sgd, currency="SGD"),
        ],
        "account_value_rows": [],
    }


def test_account_snapshot_ignores_sgd_base_balances_for_usd_rebalancing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ib = FakeIB(**_sgd_account_rows())
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    snapshot = broker.account_snapshot()

    assert snapshot.cash_usd == 0.0
    assert snapshot.net_liquidation_usd is None
    assert snapshot.total_value_usd == 0.0
    assert any("ignored non-USD/BASE account balance rows" in warning for warning in snapshot.warnings)
    assert any("treating available USD cash as $0.00" in warning for warning in snapshot.warnings)


def test_account_snapshot_ignores_non_usd_gross_position_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _sgd_account_rows()
    rows["account_summary_rows"].append(FakeAccountValue(tag="GrossPositionValue", value="6550", currency="SGD"))
    fake_ib = FakeIB(
        **rows,
        portfolio_items=[FakePortfolioItem(contract=FakeContract(symbol="AAPL"), position=25.0, marketValue=5000.0)],
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    snapshot = broker.account_snapshot()

    # USD-denominated stock positions pass through unchanged; the non-USD gross summary is
    # informational only and must not override the USD position read.
    assert snapshot.positions[0].market_value == 5000.0
    assert snapshot.positions_market_value_usd == pytest.approx(5000.0)
    assert any("SGD" in warning for warning in snapshot.warnings)


def test_account_snapshot_usd_base_account_needs_no_exchange_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = FakeIB(
        account_summary_rows=[
            FakeAccountValue(tag="TotalCashValue", value="10000", currency="USD"),
            FakeAccountValue(tag="NetLiquidation", value="10000", currency="USD"),
        ],
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    snapshot = broker.account_snapshot()

    assert snapshot.cash_usd == 10_000.0
    assert snapshot.warnings == ()


def test_account_snapshot_does_not_need_exchange_rate_for_ignored_non_usd_balances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ib = FakeIB(
        account_summary_rows=[FakeAccountValue(tag="TotalCashValue", value="13100", currency="SGD")],
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    snapshot = broker.account_snapshot()

    assert snapshot.cash_usd == 0.0
    assert snapshot.net_liquidation_usd is None
    assert any("SGD" in warning for warning in snapshot.warnings)


def test_account_snapshot_uses_only_usd_cash_when_mixed_currency_balances_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ib = FakeIB(
        account_value_rows=[
            FakeAccountValue(tag="TotalCashBalance", value="500", currency="USD"),
            FakeAccountValue(tag="TotalCashBalance", value="12000", currency="SGD"),
            FakeAccountValue(tag="TotalCashBalance", value="13100", currency="BASE"),
        ],
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    snapshot = broker.account_snapshot()

    assert snapshot.cash_usd == 500.0
    assert snapshot.total_value_usd == 500.0
    assert any("BASE" in warning and "SGD" in warning for warning in snapshot.warnings)


def test_account_snapshot_excludes_non_usd_stock_positions(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ib = FakeIB(
        account_summary_rows=[
            FakeAccountValue(tag="TotalCashValue", value="100", currency="USD"),
            FakeAccountValue(tag="NetLiquidation", value="5100", currency="USD"),
        ],
        portfolio_items=[
            FakePortfolioItem(contract=FakeContract(symbol="AAPL"), position=25.0, marketValue=5000.0),
            FakePortfolioItem(
                contract=FakeContract(symbol="SAP", currency="EUR"),
                position=10.0,
                marketValue=1800.0,
            ),
        ],
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))

    snapshot = broker.account_snapshot()

    assert [position.ticker for position in snapshot.positions] == ["AAPL"]
    assert snapshot.positions_market_value_usd == 5000.0
    assert snapshot.total_value_usd == 5100.0
    assert any("ignored non-USD stock positions" in warning and "EUR" in warning for warning in snapshot.warnings)
