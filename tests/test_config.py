import pytest
from pydantic import ValidationError

from poma.config import Settings


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


def test_default_strategy_is_us_top_market_cap_top_30() -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
    )
    assert settings.universe == "us_top_market_cap"
    assert settings.rank_lookback_days == 90
    assert settings.max_holdings == 30
    assert settings.max_daily_trades == 100
    assert settings.min_weight_delta_pct == 0.0025
