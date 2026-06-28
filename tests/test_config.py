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
    assert settings.active_strategy == CURRENT_STRATEGY_NAME
    assert settings.strategy_allocation_map() == {
        CURRENT_STRATEGY_NAME: 0.98,
        CASH_STRATEGY_NAME: 0.02,
    }
    assert settings.max_daily_trades == 100
    assert settings.min_weight_delta_pct == 0.0025


def test_strategy_allocations_must_include_active_strategy() -> None:
    with pytest.raises(ValidationError, match="must be present"):
        Settings(
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat",
            ACTIVE_STRATEGY=CURRENT_STRATEGY_NAME,
            STRATEGY_ALLOCATIONS="future_strategy=1.0",
        )


def test_strategy_allocations_cannot_exceed_portfolio_cap() -> None:
    with pytest.raises(ValidationError, match="must not exceed 100%"):
        Settings(
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat",
            STRATEGY_ALLOCATIONS=f"{CURRENT_STRATEGY_NAME}=0.75,future_strategy=0.50",
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
