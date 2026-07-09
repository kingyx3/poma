from __future__ import annotations

import pytest
from conftest import FakeBroker, make_settings

from poma.data import FixtureMarketDataClient
from poma.engine import ACCOUNT_SNAPSHOT_ATTEMPTS, RebalanceEngine
from poma.models import AccountSnapshot


class FlakyBalanceBroker(FakeBroker):
    def __init__(self) -> None:
        super().__init__()
        self.account_snapshot_calls = 0

    def account_snapshot(self) -> AccountSnapshot:
        self.account_snapshot_calls += 1
        if self.account_snapshot_calls == 1:
            raise RuntimeError("transient account summary unavailable")
        return super().account_snapshot()


class TimeoutBalanceBroker(FakeBroker):
    def __init__(self) -> None:
        super().__init__()
        self.account_snapshot_calls = 0

    def account_snapshot(self) -> AccountSnapshot:
        self.account_snapshot_calls += 1
        raise TimeoutError()


def _paper_engine(broker: FakeBroker) -> RebalanceEngine:
    return RebalanceEngine(
        make_settings(
            TRADING_MODE="paper",
            IBKR_ACCOUNT="DU1234567",
            MAX_POSITION_PCT=1.0,
            MAX_TURNOVER_PCT=1.0,
            MAX_ORDER_NOTIONAL_USD=100_000.0,
        ),
        data_client=FixtureMarketDataClient(),
        broker=broker,
    )


def test_paper_rebalance_retries_transient_account_snapshot_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("poma.engine.time.sleep", lambda _seconds: None)
    broker = FlakyBalanceBroker()

    outcome = _paper_engine(broker).run("session", "run")

    assert broker.account_snapshot_calls == 2
    assert not outcome.blocked
    assert outcome.executed
    assert broker.submitted is not None
    assert any("balances read succeeded after 2 attempts" in warning for warning in outcome.plan.warnings)
    assert any(
        "broker cash and portfolio balances read succeeded after 2 attempts "
        "endpoint=broker.account_snapshot duration_seconds=" in warning
        for warning in outcome.plan.warnings
    )
    assert not any("unable to read broker cash" in warning for warning in outcome.plan.warnings)


def test_paper_rebalance_reports_snapshot_attempt_diagnostics_after_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("poma.engine.time.sleep", lambda _seconds: None)
    broker = TimeoutBalanceBroker()

    outcome = _paper_engine(broker).run("session", "run")

    assert broker.account_snapshot_calls == ACCOUNT_SNAPSHOT_ATTEMPTS
    assert outcome.blocked
    assert not outcome.executed
    assert broker.submitted is None

    attempt_warnings = [
        warning
        for warning in outcome.plan.warnings
        if warning.startswith("broker cash and portfolio balances read failed endpoint=broker.account_snapshot")
    ]
    assert len(attempt_warnings) == ACCOUNT_SNAPSHOT_ATTEMPTS
    assert "attempt=1/5" in attempt_warnings[0]
    assert "duration_seconds=" in attempt_warnings[0]
    assert "retrying in 5.00s: TimeoutError" in attempt_warnings[0]
    assert "attempt=5/5" in attempt_warnings[-1]
    assert "no retries left: TimeoutError" in attempt_warnings[-1]
    assert any(
        "unable to read broker cash and portfolio balances before rebalancing after 5 attempts; "
        "block execution: TimeoutError" in warning
        for warning in outcome.plan.warnings
    )
