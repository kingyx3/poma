from __future__ import annotations

from conftest import FakeBroker, make_settings

from poma.data import FixtureMarketDataClient
from poma.engine import RebalanceEngine
from poma.models import AccountSnapshot


class UnavailableBalanceBroker(FakeBroker):
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


def test_paper_rebalance_blocks_when_account_snapshot_is_unavailable() -> None:
    broker = UnavailableBalanceBroker()

    outcome = _paper_engine(broker).run("session", "run")

    assert broker.account_snapshot_calls == 1
    assert outcome.blocked
    assert not outcome.executed
    assert broker.submitted is None
    assert any(
        "unable to read broker cash and portfolio balances before rebalancing; "
        "block execution: TimeoutError" in warning
        for warning in outcome.plan.warnings
    )
    assert not any("attempt" in warning or "retrying" in warning for warning in outcome.plan.warnings)
