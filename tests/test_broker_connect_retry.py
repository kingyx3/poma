from __future__ import annotations

import pytest
from conftest import make_settings

from poma.broker import LIVE_MARKET_DATA_TYPE, BrokerUnavailable, IbkrBroker, _connect_ib


def _settings(**overrides: object) -> object:
    values: dict[str, object] = {
        "TRADING_MODE": "paper",
        "IBKR_ACCOUNT": "DU1234567",
    }
    values.update(overrides)
    return make_settings(**values)


class FlakyConnectIB:
    """Fake ib_insync.IB whose connect fails a configurable number of times."""

    connect_failures = 0
    instances: list[FlakyConnectIB] = []

    def __init__(self) -> None:
        self.connected = False
        self.disconnect_calls = 0
        self.market_data_types: list[int] = []
        self.RequestTimeout: float | None = None
        type(self).instances.append(self)

    def connect(self, _host: str, _port: int, **kwargs: object) -> None:
        cls = type(self)
        if cls.connect_failures > 0:
            cls.connect_failures -= 1
            raise TimeoutError()
        self.connected = True
        self.connect_kwargs = kwargs

    def isConnected(self) -> bool:  # noqa: N802 - mirrors ib_insync API
        return self.connected

    def reqMarketDataType(self, data_type: int) -> None:  # noqa: N802 - mirrors ib_insync API
        self.market_data_types.append(data_type)

    def disconnect(self) -> None:
        self.connected = False
        self.disconnect_calls += 1


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr("poma.broker.time.sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


@pytest.fixture
def flaky_ib(monkeypatch: pytest.MonkeyPatch) -> type[FlakyConnectIB]:
    FlakyConnectIB.connect_failures = 0
    FlakyConnectIB.instances = []
    monkeypatch.setattr("poma.broker.IB", FlakyConnectIB)
    return FlakyConnectIB


def test_connect_retries_on_fresh_socket_after_timeout(flaky_ib: type[FlakyConnectIB]) -> None:
    flaky_ib.connect_failures = 2

    ib = _connect_ib(_settings(), client_id=101)

    assert ib.isConnected()
    assert len(flaky_ib.instances) == 3
    assert ib is flaky_ib.instances[-1]
    assert all(failed.disconnect_calls == 1 for failed in flaky_ib.instances[:-1])
    assert ib.RequestTimeout == 45.0
    assert ib.market_data_types == [LIVE_MARKET_DATA_TYPE]


def test_connect_failure_is_reported_with_connection_details(flaky_ib: type[FlakyConnectIB]) -> None:
    flaky_ib.connect_failures = 99

    with pytest.raises(BrokerUnavailable) as exc_info:
        _connect_ib(_settings(IBKR_HOST="10.0.0.5", IBKR_PORT=4002), client_id=101)

    message = str(exc_info.value)
    assert "unable to connect to IBKR API at 10.0.0.5:4002" in message
    assert "clientId=101" in message
    assert "after 3 attempt(s)" in message
    assert "TimeoutError" in message
    assert isinstance(exc_info.value.__cause__, TimeoutError)
    assert len(flaky_ib.instances) == 3


def test_connect_attempts_and_timeout_are_configurable(flaky_ib: type[FlakyConnectIB]) -> None:
    flaky_ib.connect_failures = 99
    settings = _settings(IBKR_CONNECT_ATTEMPTS=1, IBKR_CONNECT_TIMEOUT_SECONDS=7.5)

    with pytest.raises(BrokerUnavailable) as exc_info:
        _connect_ib(settings, client_id=101)

    assert "after 1 attempt(s)" in str(exc_info.value)
    assert "timeout=7.5s" in str(exc_info.value)
    assert len(flaky_ib.instances) == 1


def test_broker_connect_survives_one_connect_timeout(flaky_ib: type[FlakyConnectIB]) -> None:
    flaky_ib.connect_failures = 1

    ib = IbkrBroker(_settings())._connect()

    assert ib.isConnected()
    assert len(flaky_ib.instances) == 2
    assert flaky_ib.instances[0].disconnect_calls == 1
