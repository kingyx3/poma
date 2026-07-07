from __future__ import annotations

import pytest
from conftest import FakeBroker, make_settings

from poma.data import FixtureMarketDataClient
from poma.engine import RebalanceEngine
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


class EmptyMessageBalanceBroker(FakeBroker):
    def account_snapshot(self) -> AccountSnapshot:
        raise RuntimeError()


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
    assert not any("unable to read broker cash" in warning for warning in outcome.plan.warnings)


def test_paper_rebalance_reports_empty_snapshot_exception_after_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("poma.engine.time.sleep", lambda _seconds: None)
    broker = EmptyMessageBalanceBroker()

    outcome = _paper_engine(broker).run("session", "run")

    assert outcome.blocked
    assert not outcome.executed
    assert broker.submitted is None
    assert any(
        "unable to read broker cash and portfolio balances before rebalancing after 3 attempts; "
        "block execution: RuntimeError" in warning
        for warning in outcome.plan.warnings
    )
