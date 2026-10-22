from __future__ import annotations

import pandas as pd
import pytest
from conftest import make_settings
from pydantic import ValidationError

from poma.portfolio import build_strategy_capital_plan
from poma.portfolio_constructor import combine_strategy_target_books
from poma.strategies import StrategyContext, default_registry, strategy_data_requirements
from poma.strategies.core_satellite import NAME, parse_satellites, resolve_plan
from poma.ucits_instruments import USD_LSE_UCITS, price_symbol, ucits_listing

SATELLITE_WEIGHTS = "core_satellite=1.0"


def _settings(**overrides: object):
    return make_settings(INVESTMENT_STRATEGY_WEIGHTS=SATELLITE_WEIGHTS, **overrides)


def _context(settings, *, allocation_pct: float = 0.98, capital: float = 9_800.0) -> StrategyContext:
    return StrategyContext(
        strategy_name=NAME,
        allocation_pct=allocation_pct,
        capital_usd=capital,
        current_universe=pd.DataFrame(),
        historical_universe=None,
        settings=settings,
    )


def test_default_is_70_core_with_equal_weight_cyber_and_staples_satellites() -> None:
    plan = resolve_plan(_settings())
    assert plan.core_ticker == "USMC"
    assert plan.weights == pytest.approx({"USMC": 0.70, "USPY": 0.15, "IUCS": 0.15})


def test_targets_scale_by_sleeve_allocation_and_capital() -> None:
    settings = _settings()
    book = default_registry().get(NAME).build_targets(_context(settings))
    by_ticker = {t.ticker: t for t in book.targets}
    assert set(by_ticker) == {"USMC", "USPY", "IUCS"}
    assert by_ticker["USMC"].portfolio_weight == pytest.approx(0.70 * 0.98)
    assert by_ticker["USPY"].target_notional == pytest.approx(0.15 * 9_800)
    assert sum(t.target_notional for t in book.targets) == pytest.approx(9_800)
    assert set(book.diversified_fund_tickers) == {"USMC", "USPY", "IUCS"}


def test_variables_drive_split_and_instruments() -> None:
    settings = _settings(
        CORE_SATELLITE_CORE_TICKER="sp1",
        CORE_SATELLITE_CORE_PCT="0.6",
        CORE_SATELLITE_SATELLITES="USPY=75%,IUCS=25%",
    )
    assert resolve_plan(settings).weights == pytest.approx({"SP1": 0.60, "USPY": 0.30, "IUCS": 0.10})


def test_data_requirements_ask_for_every_etf_without_the_stock_universe() -> None:
    requirements = strategy_data_requirements(default_registry().get(NAME), _settings())
    assert requirements.uses_universe is False
    assert set(requirements.price_history_tickers) == {"USMC", "USPY", "IUCS"}
    assert requirements.price_history_days > 0


def test_combines_into_portfolio_orders_with_cash_reserve() -> None:
    settings = _settings()
    capital = build_strategy_capital_plan(10_000, settings.strategy_allocation_map())
    sleeve = capital.capital_for(NAME)
    book = default_registry().get(NAME).build_targets(
        _context(settings, allocation_pct=sleeve.allocation_pct, capital=sleeve.capital_usd)
    )
    positions, warnings = combine_strategy_target_books([book], 10_000)
    combined = {c.ticker: c.target_notional for c in positions}
    assert warnings == []
    assert combined["USMC"] == pytest.approx(10_000 * 0.98 * 0.70)
    assert sum(combined.values()) == pytest.approx(9_800)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("USPY,IUCS", {"USPY": 0.5, "IUCS": 0.5}),
        (" uspy , iucs , bugg ", {"USPY": 1 / 3, "IUCS": 1 / 3, "BUGG": 1 / 3}),
        ("USPY=0.5,IUCS=0.5", {"USPY": 0.5, "IUCS": 0.5}),
    ],
)
def test_parse_satellites(raw: str, expected: dict[str, float]) -> None:
    assert parse_satellites(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "at least one"),
        ("USPY,USPY", "repeat"),
        ("USPY=0.5,IUCS", "either list bare tickers"),
        ("USPY=0.5,IUCS=0.4", "sum to 100%"),
        ("USPY=1.2,IUCS=-0.2", "positive"),
        ("USPY=abc", "must be a number"),
    ],
)
def test_parse_satellites_rejects_bad_input(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_satellites(raw)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"CORE_SATELLITE_CORE_PCT": "1.0"}, "CORE_SATELLITE_CORE_PCT"),
        ({"CORE_SATELLITE_CORE_PCT": "0"}, "CORE_SATELLITE_CORE_PCT"),
        ({"CORE_SATELLITE_CORE_TICKER": "VTI"}, "not a registered USD UCITS ETF"),
        ({"CORE_SATELLITE_SATELLITES": "USPY,XYZ"}, "not a registered USD UCITS ETF"),
        ({"CORE_SATELLITE_SATELLITES": "USMC,IUCS"}, "both the core and a satellite"),
    ],
)
def test_invalid_variables_fail_config_validation(overrides: dict[str, str], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(**overrides)


def test_unused_core_satellite_variables_are_not_validated() -> None:
    # Defaults must not block deployments that do not allocate the sleeve.
    make_settings(CORE_SATELLITE_CORE_PCT="5")


def test_non_irish_domicile_is_rejected_unless_explicitly_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    listing = USD_LSE_UCITS["IUCS"]
    luxembourg = type(listing)(**{**listing.__dict__, "ticker": "LUXX", "isin": "LU0000000000"})
    monkeypatch.setitem(USD_LSE_UCITS, "LUXX", luxembourg)
    with pytest.raises(ValueError, match="not Ireland-domiciled"):
        resolve_plan(_settings(CORE_SATELLITE_SATELLITES="USPY,LUXX"))
    allowed = resolve_plan(
        _settings(CORE_SATELLITE_SATELLITES="USPY,LUXX", CORE_SATELLITE_REQUIRE_IRISH_DOMICILE="false")
    )
    assert "LUXX" in allowed.weights


def test_all_registered_funds_are_irish_usd_lines_with_yahoo_mapping() -> None:
    for ticker, listing in USD_LSE_UCITS.items():
        assert listing.domicile == "IE", ticker
        assert listing.currency == "USD"
        assert listing.name and listing.theme
        assert price_symbol(ticker) == f"{ticker}.L"
    assert ucits_listing("USPY").theme == "cybersecurity"
    assert ucits_listing("IUCS").theme == "consumer_staples"


def test_london_calendar_is_selected_for_satellite_sleeve() -> None:
    settings = _settings()
    assert settings.market_calendar == "LSE"
    assert settings.rebalance_after_open_minutes == 390
    with pytest.raises(ValidationError, match="require MARKET_CALENDAR=LSE"):
        _settings(MARKET_CALENDAR="NASDAQ")


def test_direct_satellite_tickers_also_work_in_investment_weights() -> None:
    settings = make_settings(INVESTMENT_STRATEGY_WEIGHTS="USMC=0.70,USPY=0.15,IUCS=0.15")
    assert settings.strategy_allocation_map() == pytest.approx(
        {"USMC": 0.686, "USPY": 0.147, "IUCS": 0.147, "cash": 0.02}
    )
