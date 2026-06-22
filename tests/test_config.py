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
