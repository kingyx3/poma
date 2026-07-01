from __future__ import annotations

import pytest
from conftest import make_settings
from pydantic import ValidationError

from poma.execution_pricing import apply_execution_quotes, build_limit_price, select_execution_price
from poma.models import ExecutionQuote, OrderSide, ProposedTrade

RETRIEVED_AT = "2026-07-01T14:30:00+00:00"


def _quote(**overrides: object) -> ExecutionQuote:
    values: dict[str, object] = {
        "ticker": "AAPL",
        "source": "ibkr",
        "retrieved_at_utc": RETRIEVED_AT,
        "selected_price_as_of_utc": RETRIEVED_AT,
        "age_seconds": 1.0,
        "bid": 199.90,
        "ask": 200.10,
        "last": 200.00,
        "spread_bps": None,
        "is_delayed": False,
    }
    values.update(overrides)
    return ExecutionQuote(**values)


def _trade(ticker: str = "AAPL", side: OrderSide = OrderSide.BUY, notional: float = 1000.0) -> ProposedTrade:
    return ProposedTrade(
        ticker=ticker,
        side=side,
        quantity=notional / 200.0,
        notional=notional,
        reference_price=200.0,
        limit_price=200.2,
        reason="rebalance_to_target_weight",
        order_ref=f"poma:run:0:{ticker}:{side.value}",
    )


def test_side_of_market_buy_uses_ask() -> None:
    settings = make_settings()
    price, warnings = select_execution_price(_quote(), OrderSide.BUY, settings)
    assert warnings == []
    assert price == 200.10


def test_side_of_market_sell_uses_bid() -> None:
    settings = make_settings()
    price, warnings = select_execution_price(_quote(), OrderSide.SELL, settings)
    assert warnings == []
    assert price == 199.90


def test_midpoint_basis_computes_midpoint_when_bid_ask_valid() -> None:
    settings = make_settings(EXECUTION_PRICE_BASIS="midpoint")
    price, warnings = select_execution_price(_quote(), OrderSide.BUY, settings)
    assert warnings == []
    assert price == 200.00


def test_midpoint_basis_blocks_when_bid_missing() -> None:
    settings = make_settings(EXECUTION_PRICE_BASIS="midpoint")
    price, warnings = select_execution_price(_quote(bid=None), OrderSide.BUY, settings)
    assert price is None
    assert "block execution" in warnings[0]


def test_last_price_basis_requires_freshness_and_allow_flag() -> None:
    settings = make_settings(EXECUTION_PRICE_BASIS="last", ALLOW_LAST_PRICE_FALLBACK="true")
    price, warnings = select_execution_price(_quote(), OrderSide.BUY, settings)
    assert warnings == []
    assert price == 200.00

    stale_price, stale_warnings = select_execution_price(
        _quote(age_seconds=999.0), OrderSide.BUY, settings
    )
    assert stale_price is None
    assert "block execution" in stale_warnings[0]


def test_last_price_basis_without_allow_flag_is_rejected_at_config_load() -> None:
    with pytest.raises(ValidationError, match="ALLOW_LAST_PRICE_FALLBACK"):
        make_settings(EXECUTION_PRICE_BASIS="last", ALLOW_LAST_PRICE_FALLBACK="false")


def test_missing_ask_blocks_buy_side_of_market() -> None:
    settings = make_settings()
    price, warnings = select_execution_price(_quote(ask=None), OrderSide.BUY, settings)
    assert price is None
    assert "missing ask" in warnings[0]
    assert "block execution" in warnings[0]


def test_missing_bid_blocks_sell_side_of_market() -> None:
    settings = make_settings()
    price, warnings = select_execution_price(_quote(bid=None), OrderSide.SELL, settings)
    assert price is None
    assert "missing bid" in warnings[0]


def test_stale_quote_blocks() -> None:
    settings = make_settings(EXECUTION_QUOTE_MAX_AGE_SECONDS=60)
    price, warnings = select_execution_price(_quote(age_seconds=184.0), OrderSide.BUY, settings)
    assert price is None
    assert "stale" in warnings[0]
    assert "age=184s" in warnings[0]
    assert "max=60s" in warnings[0]


def test_missing_quote_timestamp_blocks() -> None:
    settings = make_settings()
    price, warnings = select_execution_price(_quote(age_seconds=None), OrderSide.BUY, settings)
    assert price is None
    assert "block execution" in warnings[0]


def test_missing_quote_timestamp_includes_broker_error_when_available() -> None:
    settings = make_settings()
    price, warnings = select_execution_price(
        _quote(age_seconds=None, broker_error="354: Requested market data is not subscribed."),
        OrderSide.BUY,
        settings,
    )
    assert price is None
    assert "354: Requested market data is not subscribed." in warnings[0]
    assert "block execution" in warnings[0]


def test_wide_spread_blocks() -> None:
    settings = make_settings(EXECUTION_MAX_SPREAD_BPS=50.0)
    price, warnings = select_execution_price(
        _quote(bid=95.0, ask=105.0), OrderSide.BUY, settings
    )
    assert price is None
    assert "wide quote" in warnings[0]
    assert "block execution" in warnings[0]


def test_delayed_quote_blocks_unless_allowed() -> None:
    settings = make_settings()
    price, warnings = select_execution_price(_quote(is_delayed=True), OrderSide.BUY, settings)
    assert price is None
    assert "delayed execution quote" in warnings[0]

    allowed_settings = make_settings(ALLOW_DELAYED_EXECUTION_QUOTES="true")
    allowed_price, allowed_warnings = select_execution_price(_quote(is_delayed=True), OrderSide.BUY, allowed_settings)
    assert allowed_warnings == []
    assert allowed_price == 200.10


def test_build_limit_price_applies_offset_after_reference_price_selection() -> None:
    assert build_limit_price(OrderSide.BUY, 200.10, 10.0) == 200.30
    assert build_limit_price(OrderSide.SELL, 199.90, 10.0) == 199.70


def test_apply_execution_quotes_recomputes_quantity_from_final_price() -> None:
    settings = make_settings()
    trade = _trade(notional=1000.0)
    quotes = {"AAPL": _quote()}

    repriced, warnings = apply_execution_quotes([trade], quotes, settings)

    assert warnings == []
    assert len(repriced) == 1
    updated = repriced[0]
    assert updated.reference_price == 200.10
    assert updated.quantity == 1000.0 / 200.10
    assert updated.limit_price == build_limit_price(OrderSide.BUY, 200.10, settings.limit_offset_bps)
    assert updated.reference_price_source == "ibkr"
    assert updated.reference_price_basis == "side_of_market"
    assert updated.quote_age_seconds == 1.0


def test_apply_execution_quotes_blocks_trade_missing_quote() -> None:
    settings = make_settings()
    trade = _trade()

    repriced, warnings = apply_execution_quotes([trade], {}, settings)

    assert repriced == []
    assert "missing ibkr execution quote for AAPL" in warnings[0]
    assert "block execution" in warnings[0]


def test_apply_execution_quotes_blocks_stale_quote() -> None:
    settings = make_settings(EXECUTION_QUOTE_MAX_AGE_SECONDS=60)
    trade = _trade()
    quotes = {"AAPL": _quote(age_seconds=200.0)}

    repriced, warnings = apply_execution_quotes([trade], quotes, settings)

    assert repriced == []
    assert any("stale" in warning for warning in warnings)


def test_apply_execution_quotes_only_blocks_the_failing_ticker() -> None:
    settings = make_settings()
    good_trade = _trade(ticker="AAPL")
    bad_trade = _trade(ticker="MSFT")
    quotes = {"AAPL": _quote(ticker="AAPL"), "MSFT": _quote(ticker="MSFT", age_seconds=None)}

    repriced, warnings = apply_execution_quotes([good_trade, bad_trade], quotes, settings)

    assert [trade.ticker for trade in repriced] == ["AAPL"]
    assert any("MSFT" in warning for warning in warnings)
