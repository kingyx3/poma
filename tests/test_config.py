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


def test_default_strategy_is_us_top_market_cap_top_100() -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
    )
    assert settings.universe == "us_top_market_cap"
    assert settings.rank_lookback_days == 90
    assert settings.max_holdings == 100
    assert settings.strategy_allocation_map() == {
        CURRENT_STRATEGY_NAME: 0.98,
        CASH_STRATEGY_NAME: 0.02,
    }
    assert settings.max_daily_trades == 100
    assert settings.min_weight_delta_pct == 0.0025


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
