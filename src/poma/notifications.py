from __future__ import annotations

import requests

from poma.config import Settings


def send_alert(settings: Settings, message: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    requests.post(
        url,
        json={"chat_id": settings.telegram_chat_id, "text": message},
        timeout=15,
    ).raise_for_status()
