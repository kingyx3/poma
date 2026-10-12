"""USMC live/paper parity and fail-closed London order execution regressions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import make_settings
from test_broker_order_lifecycle import FakeIB, FakeMarketData, FakeContract

from poma.broker import BrokerUnavailable, IbkrBroker, _live_order_contract
from poma.market_calendar import is_market_open, should_rebalance_now
from poma.models import OrderSide


class QualifyingIB(FakeIB):
    """Simulate IBKR's qualifying the exact USD LSEETF contract to a conId."""

    def qualifyContracts(self, *contracts):  # noqa: N802, ANN201
        for contract in contracts:
            contract.conId = 923456
        return list(contracts)


def _live():
    return make_settings(
        TRADING_MODE="live", IBKR_ACCOUNT="U1234567",
        ALLOW_DELAYED_EXECUTION_QUOTES=False,
    )


def test_same_default_etf_for_paper_and_live_with_separate_broker_modes() -> None:
    paper = make_settings(TRADING_MODE="paper", IBKR_ACCOUNT="DU1234567")
    live = _live()
    assert paper.core_etf_weights == live.core_etf_weights == "USMC=1.0"
    assert paper.market_calendar == live.market_calendar == "LSE"
    assert paper.rebalance_after_open_minutes == live.rebalance_after_open_minutes == 390
    assert paper.allow_delayed_execution_quotes is True
    assert live.allow_delayed_execution_quotes is False


def test_lse_local_session_opens_during_overlap_with_us_market() -> None:
    # Summer: 14:30 London = 13:30 UTC, coinciding with the New York cash open.
    early = datetime(2026, 7, 1, 13, 25, tzinfo=UTC)
    overlap = datetime(2026, 7, 1, 13, 35, tzinfo=UTC)
    assert should_rebalance_now("LSE", 390, False, early).should_run is False
    result = should_rebalance_now("LSE", 390, False, overlap)
    assert result.should_run is True
    assert result.session_date == "2026-07-01"
    assert is_market_open("NASDAQ", overlap) is True

    # Winter: 14:30 London = 14:30 UTC, also coinciding with New York cash open.
    winter = datetime(2026, 12, 1, 14, 35, tzinfo=UTC)
    assert should_rebalance_now("LSE", 390, False, winter).should_run is True
    assert is_market_open("NASDAQ", winter) is True


def test_london_holiday_and_closed_session_are_never_tradeable() -> None:
    assert is_market_open("LSE", datetime(2026, 8, 31, 14, 30, tzinfo=UTC)) is False
    assert should_rebalance_now(
        "LSE", 390, False, datetime(2026, 12, 24, 15, 0, tzinfo=UTC)
    ).should_run is False


def test_live_usmc_contract_qualifies_to_one_correct_usd_london_conid(monkeypatch) -> None:
    monkeypatch.setattr("poma.market_calendar.is_market_open", lambda _calendar: True)
    ib = QualifyingIB()
    contract = _live_order_contract(ib, "USMC", _live())
    assert contract.symbol == "USMC"
    assert contract.currency == "USD"
    assert contract.primaryExchange == "LSEETF"
    assert contract.exchange == "SMART"
    assert contract.conId == 923456


@pytest.mark.parametrize("kind", ["missing", "wrong_currency", "wrong_exchange", "unqualified"])
def test_live_usmc_order_fails_closed_on_broker_ambiguity(monkeypatch, kind: str) -> None:
    monkeypatch.setattr("poma.market_calendar.is_market_open", lambda _calendar: True)

    if kind == "missing":
        broker = FakeIB()
    else:
        class IncorrectIB(QualifyingIB):
            def qualifyContracts(self, *contracts):  # noqa: N802, ANN201
                if kind == "unqualified":
                    return []
                result = super().qualifyContracts(*contracts)
                contract = result[0]
                if kind == "wrong_currency":
                    contract.currency = "EUR"
                else:
                    contract.primaryExchange = "SIX"
                return result
        broker = IncorrectIB()
    with pytest.raises(BrokerUnavailable, match="qualification|did not uniquely|wrong exchange"):
        _live_order_contract(broker, "USMC", _live())


def test_live_order_checks_actual_exchange_is_open_before_placement(monkeypatch) -> None:
    monkeypatch.setattr("poma.market_calendar.is_market_open", lambda calendar: False)
    ib = QualifyingIB()
    with pytest.raises(BrokerUnavailable, match="LSE regular market is closed"):
        _live_order_contract(ib, "USMC", _live())
    assert ib.placed_orders == []


def test_live_replacement_places_usmc_only_on_qualified_contract(monkeypatch) -> None:
    monkeypatch.setattr("poma.market_calendar.is_market_open", lambda _calendar: True)
    ib = QualifyingIB()
    broker = IbkrBroker(_live())
    snapshot = broker._place_limit_order(
        ib, "USMC", OrderSide.BUY, 2.0, 30.12, "poma:live:1:USMC:BUY:r1"
    )
    assert ib.placed_orders[0][0].conId == 923456
    assert ib.placed_orders[0][0].currency == "USD"
    assert snapshot.order_ref == "poma:live:1:USMC:BUY:r1"


def test_live_quote_requires_resolved_usmc_contract_not_symbol_fallback(monkeypatch) -> None:
    fake = FakeIB(
        market_data_by_symbol={
            "USMC": FakeMarketData(
                contract=FakeContract(symbol="USMC"),
                bid=32.0, ask=32.02, last=32.01, time=datetime.now(UTC)
            )
        }
    )
    monkeypatch.setattr("poma.broker.IB", lambda: fake)
    with pytest.raises(BrokerUnavailable, match="not uniquely qualified"):
        IbkrBroker(_live()).execution_quotes(["USMC"])
    assert fake.placed_orders == []
