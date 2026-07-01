from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from conftest import make_settings

from poma.broker import probe_ibkr


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
class FakeContract:
    symbol: str


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
class FakeWhatIfState:
    warningText: str = ""
    initMarginChange: str = ""


@dataclass
class FakeIB:
    connected: bool = False
    RequestTimeout: float | None = None
    errorEvent: FakeEvent = field(default_factory=FakeEvent)
    market_data: FakeMarketData | None = None
    market_data_after_delayed: FakeMarketData | None = None
    error_to_emit: tuple[int, str] | None = None
    current_market_data_type: int = 1
    market_data_type_requests: list[int] = field(default_factory=list)

    def connect(self, *_args, **_kwargs) -> None:
        self.connected = True

    def isConnected(self) -> bool:  # noqa: N802 - mirrors ib_insync API
        return self.connected

    def managedAccounts(self) -> list[str]:  # noqa: N802
        return ["DU1234567"]

    def reqCurrentTime(self) -> str:  # noqa: N802
        return "2026-07-01T13:40:00Z"

    def portfolio(self) -> list:
        return []

    def whatIfOrder(self, *_args, **_kwargs):  # noqa: N802, ANN201 - ib_insync shape
        return FakeWhatIfState()

    def reqMarketDataType(self, data_type: int) -> None:  # noqa: N802
        self.market_data_type_requests.append(data_type)
        self.current_market_data_type = data_type

    def reqMktData(self, contract, *_args, **_kwargs):  # noqa: N802, ANN201 - mirrors ib_insync API
        if self.error_to_emit is not None:
            code, message = self.error_to_emit
            self.errorEvent.emit(-1, code, message, contract)
        if self.current_market_data_type == 3 and self.market_data_after_delayed is not None:
            return self.market_data_after_delayed
        return self.market_data

    def cancelMktData(self, _contract) -> None:  # noqa: N802
        return None

    def sleep(self, _seconds: float) -> None:
        return None

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


def test_probe_ibkr_reports_market_data_ok_when_a_live_tick_arrives(monkeypatch: pytest.MonkeyPatch) -> None:
    tick_time = datetime.now(UTC)
    fake_ib = FakeIB(
        market_data=FakeMarketData(contract=FakeContract(symbol="AAPL"), bid=200.0, ask=200.2, time=tick_time)
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch))

    assert health.market_data_ok is True
    assert "AAPL" in health.market_data_message
    assert fake_ib.market_data_type_requests == [1]


def test_probe_ibkr_fails_market_data_with_ibkr_error_reason_when_no_tick_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ib = FakeIB(
        market_data=FakeMarketData(contract=FakeContract(symbol="AAPL"), time=None),
        error_to_emit=(354, "Requested market data is not subscribed."),
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch))

    assert health.market_data_ok is False
    assert "354: Requested market data is not subscribed." in health.market_data_message


def test_probe_ibkr_falls_back_to_delayed_data_when_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    tick_time = datetime.now(UTC)
    fake_ib = FakeIB(
        market_data=FakeMarketData(contract=FakeContract(symbol="AAPL"), time=None),
        market_data_after_delayed=FakeMarketData(
            contract=FakeContract(symbol="AAPL"), bid=200.0, ask=200.2, time=tick_time, marketDataType=3
        ),
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch, ALLOW_DELAYED_EXECUTION_QUOTES="true"))

    assert health.market_data_ok is True
    assert "delayed" in health.market_data_message


def test_probe_ibkr_skips_market_data_check_when_execution_price_source_is_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ib = FakeIB(market_data=FakeMarketData(contract=FakeContract(symbol="AAPL"), time=None))
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch, EXECUTION_PRICE_SOURCE="snapshot"))

    assert health.market_data_ok is True
    assert "skipped" in health.market_data_message
