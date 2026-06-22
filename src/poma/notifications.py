from __future__ import annotations

import requests

from poma.config import Settings


def send_alert(settings: Settings, message: str) -> None:
    """Send a best-effort Telegram alert.

    Alert delivery must never break rebalance state handling or cause duplicate cron retries.
    Failures are intentionally swallowed; check cron logs for operational debugging.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": settings.telegram_chat_id, "text": message},
            timeout=15,
        ).raise_for_status()
    except requests.RequestException:
        return
