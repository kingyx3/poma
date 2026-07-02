from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from conftest import make_settings

from poma.broker import IbkrHealth, probe_ibkr
from poma.health import check_ibkr


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
    # What each market data type serves when the probe ladder requests it, keyed by type code
    # (1=live, 2=frozen, 3=delayed, 4=delayed_frozen). Missing keys serve an empty ticker.
    market_data_by_type: dict[int, FakeMarketData] = field(default_factory=dict)
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
        served = self.market_data_by_type.get(self.current_market_data_type)
        if served is not None:
            return served
        return FakeMarketData(contract=contract, marketDataType=self.current_market_data_type)

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


def _set_market_open(monkeypatch: pytest.MonkeyPatch, is_open: bool) -> None:
    monkeypatch.setattr("poma.market_calendar.is_market_open", lambda *_args, **_kwargs: is_open)


def _aapl(**kwargs) -> FakeMarketData:
    return FakeMarketData(contract=FakeContract(symbol="AAPL"), **kwargs)


def test_probe_ibkr_short_circuits_on_a_live_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_market_open(monkeypatch, True)
    fake_ib = FakeIB(market_data_by_type={1: _aapl(bid=200.0, ask=200.2, time=datetime.now(UTC))})
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch))

    assert health.market_data_ok is True
    assert health.market_data_realtime is True
    assert health.market_data_type == "live"
    assert "real-time entitlement confirmed" in health.market_data_message
    # connect requests live, the first ladder step requests live, then the session is restored.
    assert fake_ib.market_data_type_requests == [1, 1, 1]


def test_probe_ibkr_confirms_realtime_entitlement_via_frozen_tick_when_market_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_market_open(monkeypatch, False)
    fake_ib = FakeIB(
        market_data_by_type={2: _aapl(bid=200.0, ask=200.2, time=datetime.now(UTC), marketDataType=2)}
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch))

    assert health.market_data_ok is True
    assert health.market_data_realtime is True
    assert health.market_data_type == "frozen"
    assert "real-time entitlement confirmed" in health.market_data_message
    assert fake_ib.market_data_type_requests == [1, 1, 2, 1]


def test_probe_ibkr_accepts_frozen_price_fields_without_fresh_tick_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_market_open(monkeypatch, False)
    # Frozen snapshots can serve the last session's prices without a fresh ticker.time.
    fake_ib = FakeIB(market_data_by_type={2: _aapl(close=199.5, marketDataType=2)})
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch))

    assert health.market_data_ok is True
    assert health.market_data_realtime is True
    assert health.market_data_type == "frozen"


def test_probe_ibkr_flags_missing_realtime_entitlement_when_only_delayed_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_market_open(monkeypatch, True)
    fake_ib = FakeIB(
        market_data_by_type={3: _aapl(bid=200.0, ask=200.2, time=datetime.now(UTC), marketDataType=3)},
        error_to_emit=(10089, "Requested market data requires additional subscription for API."),
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch, ALLOW_DELAYED_EXECUTION_QUOTES="true"))

    assert health.market_data_ok is True
    assert health.market_data_realtime is False
    assert health.market_data_type == "delayed"
    assert "real-time entitlement MISSING" in health.market_data_message
    assert "10089" in health.market_data_message
    assert fake_ib.market_data_type_requests == [1, 1, 2, 3, 1]


def test_probe_ibkr_fails_delayed_only_when_delayed_quotes_disallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_market_open(monkeypatch, True)
    fake_ib = FakeIB(
        market_data_by_type={3: _aapl(bid=200.0, ask=200.2, time=datetime.now(UTC), marketDataType=3)}
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch, ALLOW_DELAYED_EXECUTION_QUOTES="false"))

    assert health.market_data_ok is False
    assert "execution would block" in health.market_data_message


def test_probe_ibkr_fails_delayed_only_when_live_quotes_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_market_open(monkeypatch, True)
    fake_ib = FakeIB(
        market_data_by_type={3: _aapl(bid=200.0, ask=200.2, time=datetime.now(UTC), marketDataType=3)}
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(
        _settings(
            monkeypatch,
            ALLOW_DELAYED_EXECUTION_QUOTES="true",
            REQUIRE_LIVE_EXECUTION_QUOTES="true",
        )
    )

    assert health.market_data_ok is False
    assert health.market_data_soft_failure is False
    assert "REQUIRE_LIVE_EXECUTION_QUOTES=true" in health.market_data_message


def test_probe_ibkr_fails_market_data_with_ibkr_error_reason_when_no_tick_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_market_open(monkeypatch, False)
    fake_ib = FakeIB(error_to_emit=(354, "Requested market data is not subscribed."))
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch))

    assert health.market_data_ok is False
    assert health.market_data_soft_failure is False
    assert "354: Requested market data is not subscribed." in health.market_data_message


def test_probe_ibkr_treats_silence_as_hard_failure_when_market_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_market_open(monkeypatch, True)
    fake_ib = FakeIB()
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch))

    assert health.market_data_ok is False
    assert health.market_data_soft_failure is False
    assert "US market is open" in health.market_data_message
    assert fake_ib.market_data_type_requests == [1, 1, 2, 3, 4, 1]


def test_probe_ibkr_treats_silence_as_soft_failure_when_market_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_market_open(monkeypatch, False)
    fake_ib = FakeIB()
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch))

    assert health.market_data_ok is False
    assert health.market_data_soft_failure is True
    assert "market closed" in health.market_data_message
    assert "inconclusive" in health.market_data_message
    assert "warming up" not in health.market_data_message


def test_probe_ibkr_market_closed_silence_is_hard_when_live_quotes_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_market_open(monkeypatch, False)
    fake_ib = FakeIB()
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch, REQUIRE_LIVE_EXECUTION_QUOTES="true"))

    assert health.market_data_ok is False
    assert health.market_data_soft_failure is False
    assert "REQUIRE_LIVE_EXECUTION_QUOTES=true" in health.market_data_message


def test_probe_ibkr_survives_market_calendar_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_calendar(*_args, **_kwargs) -> bool:
        raise RuntimeError("calendar download failed")

    monkeypatch.setattr("poma.market_calendar.is_market_open", broken_calendar)
    fake_ib = FakeIB()
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch))

    # An unreadable calendar is treated like "market closed": inconclusive, not a crash.
    assert health.market_data_ok is False
    assert health.market_data_soft_failure is True


def test_probe_ibkr_skips_market_data_check_when_execution_price_source_is_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ib = FakeIB()
    monkeypatch.setattr("poma.broker.IB", lambda: fake_ib)

    health = probe_ibkr(_settings(monkeypatch, EXECUTION_PRICE_SOURCE="snapshot"))

    assert health.market_data_ok is True
    assert "skipped" in health.market_data_message


def test_check_ibkr_treats_market_closed_silence_as_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_probe(_settings, *, timeout: float = 20.0):
        return IbkrHealth(
            connected=True,
            accounts=["DU1234567"],
            server_time="2026-07-02T04:02:09Z",
            stock_positions=0,
            trading_permissions_ok=True,
            trading_permissions_message="what-if order preview accepted for AAPL",
            market_data_ok=False,
            market_data_message=(
                "market closed -- probe inconclusive (no market data tick received for AAPL "
                "at any market data type (live/frozen/delayed/delayed_frozen))"
            ),
            market_data_soft_failure=True,
        )

    monkeypatch.setattr("poma.broker.probe_ibkr", fake_probe)

    check = check_ibkr(_settings(monkeypatch, ALLOW_DELAYED_EXECUTION_QUOTES="true"))

    assert check.ok is True
    assert "market_data_warning=inconclusive" in check.detail
    assert "realtime_entitlement=no" in check.detail


def test_check_ibkr_keeps_explicit_market_data_error_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_probe(_settings, *, timeout: float = 20.0):
        return IbkrHealth(
            connected=True,
            accounts=["DU1234567"],
            server_time="2026-07-02T04:02:09Z",
            stock_positions=0,
            trading_permissions_ok=True,
            trading_permissions_message="what-if order preview accepted for AAPL",
            market_data_ok=False,
            market_data_message=(
                "no market data tick received for AAPL at any market data type; "
                "ibkr said: 354: Requested market data is not subscribed."
            ),
            market_data_soft_failure=False,
        )

    monkeypatch.setattr("poma.broker.probe_ibkr", fake_probe)

    check = check_ibkr(_settings(monkeypatch))

    assert check.ok is False
    assert "354: Requested market data is not subscribed." in check.detail


def test_check_ibkr_reports_realtime_entitlement_verdict_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_probe(_settings, *, timeout: float = 20.0):
        return IbkrHealth(
            connected=True,
            accounts=["DU1234567"],
            server_time="2026-07-02T14:35:00Z",
            stock_positions=0,
            trading_permissions_ok=True,
            trading_permissions_message="what-if order preview accepted for AAPL",
            market_data_ok=True,
            market_data_message="received live tick for AAPL (real-time entitlement confirmed)",
            market_data_type="live",
            market_data_realtime=True,
        )

    monkeypatch.setattr("poma.broker.probe_ibkr", fake_probe)

    check = check_ibkr(_settings(monkeypatch))

    assert check.ok is True
    assert "market_data_type=live" in check.detail
    assert "realtime_entitlement=yes" in check.detail
