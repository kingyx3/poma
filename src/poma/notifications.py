from __future__ import annotations

import requests

from poma.config import Settings


def send_alert(settings: Settings, message: str) -> None:
    """Send a best-effort Telegram alert.

    Telegram configuration is mandatory at settings load. Delivery remains best-effort so a
    Telegram outage cannot cause duplicate trading attempts or interfere with state handling.
    """
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": settings.telegram_chat_id, "text": message},
            timeout=15,
        ).raise_for_status()
    except requests.RequestException:
        return
