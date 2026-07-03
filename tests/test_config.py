import pytest
from pydantic import ValidationError

from poma.config import Settings
from poma.portfolio import CASH_STRATEGY_NAME, CURRENT_STRATEGY_NAME


def test_telegram_config_is_required() -> None:
    with pytest.raises(ValidationError):
        Settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")


def test_settings_accepts_telegram_config() -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
    )
    assert settings.telegram_bot_token == "token"
    assert settings.telegram_chat_id == "chat"


def test_default_strategy_is_us_top_market_cap_top_50() -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
    )
    assert settings.universe == "us_top_market_cap"
    assert settings.rank_lookback_days == 90
    assert settings.max_holdings == 50
    assert settings.strategy_allocation_map() == {
        CURRENT_STRATEGY_NAME: 0.98,
        CASH_STRATEGY_NAME: 0.02,
    }
    assert settings.max_daily_trades == 100
    assert settings.min_weight_delta_pct == 0.0025


def test_execution_rules_default_every_instrument_to_whole_shares() -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
    )
    assert settings.fractional_shares is False
    rules = settings.execution_rules()
    assert set(rules) == {"*"}
    assert rules["*"].allows_fractional is False
    assert rules["*"].min_quantity == 1.0


def test_fractional_shares_mode_restores_fractional_default_with_ticker_overrides() -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        FRACTIONAL_SHARES="true",
        NON_FRACTIONAL_TICKERS="aapl, MSFT",
    )
    rules = settings.execution_rules()
    assert set(rules) == {"*", "AAPL", "MSFT"}
    assert rules["*"].allows_fractional is True
    assert rules["AAPL"].allows_fractional is False


def test_strategy_allocations_reject_unregistered_strategy_names() -> None:
    with pytest.raises(ValidationError, match="unregistered strategies"):
        Settings(
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat",
            STRATEGY_ALLOCATIONS="future_strategy=1.0",
        )


def test_strategy_allocations_cannot_exceed_portfolio_cap() -> None:
    with pytest.raises(ValidationError, match="must not exceed 100%"):
        Settings(
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat",
            STRATEGY_ALLOCATIONS=f"{CURRENT_STRATEGY_NAME}=0.75,future_strategy=0.50",
        )


def test_managed_cap_mode_defaults_to_broker_total() -> None:
    settings = Settings(TELEGRAM_BOT_TOKEN="token", TELEGRAM_CHAT_ID="chat")

    assert settings.managed_cap_mode.value == "broker_total"
    assert settings.managed_cap_usd == 0.0


def test_managed_cap_mode_min_of_broker_total_and_cap_requires_positive_cap() -> None:
    with pytest.raises(ValidationError, match="MANAGED_CAP_USD must be greater than 0"):
        Settings(
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat",
            MANAGED_CAP_MODE="min_of_broker_total_and_cap",
            MANAGED_CAP_USD=0,
        )


def test_paper_live_execution_requires_ibkr_account() -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        TRADING_MODE="paper",
        IBKR_ACCOUNT="",
    )

    with pytest.raises(RuntimeError, match="paper trading requires IBKR_ACCOUNT"):
        settings.assert_safe_for_execution()


def test_execution_pricing_defaults_favor_fresh_side_of_market_ibkr_quotes() -> None:
    settings = Settings(TELEGRAM_BOT_TOKEN="token", TELEGRAM_CHAT_ID="chat")

    assert settings.execution_price_source.value == "ibkr"
    assert settings.execution_price_basis.value == "side_of_market"
    assert settings.execution_quote_max_age_seconds == 60
    assert settings.execution_max_spread_bps == 50.0
    # Unset, delayed-quote tolerance defaults per trading mode: dry_run/paper accounts commonly
    # lack the separate IBKR "API market data" real-time opt-in, live must never price off
    # delayed data.
    assert settings.allow_delayed_execution_quotes is True
    assert settings.allow_last_price_fallback is False


def test_delayed_execution_quotes_default_by_trading_mode() -> None:
    paper = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        TRADING_MODE="paper",
    )
    assert paper.allow_delayed_execution_quotes is True

    live = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        TRADING_MODE="live",
        ALLOW_LIVE_TRADING=True,
    )
    assert live.allow_delayed_execution_quotes is False

    explicit = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        TRADING_MODE="paper",
        ALLOW_DELAYED_EXECUTION_QUOTES=False,
    )
    assert explicit.allow_delayed_execution_quotes is False


def test_live_trading_rejects_delayed_execution_quotes_even_when_explicit() -> None:
    with pytest.raises(ValidationError, match="ALLOW_DELAYED_EXECUTION_QUOTES=false"):
        Settings(
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat",
            TRADING_MODE="live",
            ALLOW_LIVE_TRADING=True,
            ALLOW_DELAYED_EXECUTION_QUOTES=True,
        )


def test_ibkr_market_data_exchanges_default_by_trading_mode() -> None:
    paper = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        TRADING_MODE="paper",
    )
    assert paper.ibkr_market_data_exchange_list() == ("IEX", "SMART")

    live = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        TRADING_MODE="live",
        ALLOW_LIVE_TRADING=True,
    )
    assert live.ibkr_market_data_exchange_list() == ("SMART",)

    explicit = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        IBKR_MARKET_DATA_EXCHANGES="iex, smart",
    )
    assert explicit.ibkr_market_data_exchange_list() == ("IEX", "SMART")


def test_last_price_basis_requires_explicit_fallback_opt_in() -> None:
    with pytest.raises(ValidationError, match="ALLOW_LAST_PRICE_FALLBACK"):
        Settings(
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat",
            EXECUTION_PRICE_BASIS="last",
        )

    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        EXECUTION_PRICE_BASIS="last",
        ALLOW_LAST_PRICE_FALLBACK=True,
    )
    assert settings.execution_price_basis.value == "last"


def test_live_trading_blocks_snapshot_execution_price_source_by_default() -> None:
    with pytest.raises(ValidationError, match="ALLOW_UNSAFE_EXECUTION_PRICE_SOURCE"):
        Settings(
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat",
            TRADING_MODE="live",
            ALLOW_LIVE_TRADING=True,
            EXECUTION_PRICE_SOURCE="snapshot",
        )

    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        TRADING_MODE="live",
        ALLOW_LIVE_TRADING=True,
        EXECUTION_PRICE_SOURCE="snapshot",
        ALLOW_UNSAFE_EXECUTION_PRICE_SOURCE=True,
    )
    assert settings.execution_price_source.value == "snapshot"
